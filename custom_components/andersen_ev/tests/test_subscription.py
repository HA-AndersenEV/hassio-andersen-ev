"""Tests for the deviceStatusUpdated subscription client."""

import logging
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gql.transport.exceptions import (
    TransportClosed,
    TransportConnectionFailed,
    TransportProtocolError,
    TransportQueryError,
    TransportServerError,
)
from gql.transport.websockets_protocol import WebsocketsProtocolTransportBase

from andersen_ev.konnect import const
from andersen_ev.konnect.device import KonnectDevice
from andersen_ev.konnect.exceptions import AndersenAuthError, AndersenSubscriptionError
from andersen_ev.konnect.subscription import (
    HEARTBEAT_SECONDS,
    DeviceStatusSubscription,
    _TokenRedactFilter,
)

DEVICE_ID = "test_device_123"
TOKEN_REFUSED = TransportServerError("Server error: '{'message': 'Invalid/expired login token'}'")
UNAUTHENTICATED = TransportQueryError("unauthenticated", errors=[{"extensions": {"code": "UNAUTHENTICATED"}}])


def _payload(evse_state: int) -> dict[str, Any]:
    return {"deviceStatusUpdated": {"id": DEVICE_ID, "evseState": evse_state}}


def _mock_client(*messages: dict[str, Any] | Exception, connect_error: Exception | None = None) -> MagicMock:
    """Build a mock gql Client whose subscription yields messages, raising any exception among them."""

    async def _subscribe(_request: Any) -> AsyncIterator[dict[str, Any]]:
        for message in messages:
            if isinstance(message, Exception):
                raise message
            yield message

    session = MagicMock()
    session.subscribe = MagicMock(side_effect=_subscribe)
    client = MagicMock()
    client.connect_async = AsyncMock(return_value=session, side_effect=connect_error)
    client.close_async = AsyncMock()
    return client


class _Tokens:
    """Token provider and refresh callback that hand out a new token on each refresh."""

    def __init__(self) -> None:
        self.token = "token-1"
        self.refresh = AsyncMock(side_effect=self._refresh)

    def provide(self) -> str:
        return self.token

    async def _refresh(self) -> tuple[str, float | None]:
        self.token = f"token-{int(self.token.split('-')[1]) + 1}"
        return self.token, None


@pytest.fixture
def tokens() -> _Tokens:
    """Return a token provider/refresh pair."""
    return _Tokens()


@pytest.fixture
def subscription(tokens: _Tokens) -> DeviceStatusSubscription:
    """Return a subscription for the test device."""
    return DeviceStatusSubscription(DEVICE_ID, token_provider=tokens.provide, token_refresh=tokens.refresh)


async def _collect(subscription: DeviceStatusSubscription) -> list[dict[str, Any]]:
    return [payload async for payload in subscription.updates()]


async def test_yields_payloads_in_order(subscription: DeviceStatusSubscription) -> None:
    """Payloads are yielded in order and the client is closed when the server ends the stream."""
    client = _mock_client(_payload(1), {"somethingElse": {}}, _payload(3))
    with patch("andersen_ev.konnect.subscription.Client", return_value=client):
        payloads = await _collect(subscription)

    assert payloads == [{"id": DEVICE_ID, "evseState": 1}, {"id": DEVICE_ID, "evseState": 3}]
    client.close_async.assert_awaited_once()
    request = client.connect_async.return_value.subscribe.call_args.args[0]
    assert request.variable_values == {"id": DEVICE_ID}
    assert request.operation_name == "deviceStatusUpdated"


async def test_transport_settings(subscription: DeviceStatusSubscription) -> None:
    """The websocket uses the Apollo protocol, sends the token in connection_init and pings."""
    with (
        patch("andersen_ev.konnect.subscription.AIOHTTPWebsocketsTransport") as transport_cls,
        patch("andersen_ev.konnect.subscription.Client", return_value=_mock_client()),
    ):
        await _collect(subscription)

    transport_cls.assert_called_once_with(
        url=const.GRAPHQL_WS_URL,
        subprotocols=[WebsocketsProtocolTransportBase.APOLLO_SUBPROTOCOL],
        init_payload={"authToken": "Bearer token-1"},
        heartbeat=HEARTBEAT_SECONDS,
    )


@pytest.mark.parametrize(
    "auth_error",
    [
        pytest.param(TOKEN_REFUSED, id="connection_error"),
        pytest.param(UNAUTHENTICATED, id="unauthenticated"),
    ],
)
async def test_refreshes_token_once_on_auth_error(
    subscription: DeviceStatusSubscription, tokens: _Tokens, auth_error: Exception
) -> None:
    """A refused token is refreshed and the subscription reconnects with the new token."""
    clients = [_mock_client(auth_error), _mock_client(_payload(2))]
    with (
        patch("andersen_ev.konnect.subscription.AIOHTTPWebsocketsTransport") as transport_cls,
        patch("andersen_ev.konnect.subscription.Client", side_effect=clients),
    ):
        payloads = await _collect(subscription)

    assert payloads == [{"id": DEVICE_ID, "evseState": 2}]
    tokens.refresh.assert_awaited_once()
    assert [call.kwargs["init_payload"] for call in transport_cls.call_args_list] == [
        {"authToken": "Bearer token-1"},
        {"authToken": "Bearer token-2"},
    ]


async def test_auth_error_after_refresh_raises(subscription: DeviceStatusSubscription, tokens: _Tokens) -> None:
    """A token refused again straight after a refresh raises AndersenAuthError."""
    clients = [_mock_client(connect_error=TOKEN_REFUSED), _mock_client(connect_error=TOKEN_REFUSED)]
    with (
        patch("andersen_ev.konnect.subscription.Client", side_effect=clients),
        pytest.raises(AndersenAuthError),
    ):
        await _collect(subscription)

    tokens.refresh.assert_awaited_once()


async def test_refresh_allowed_again_after_a_payload(subscription: DeviceStatusSubscription, tokens: _Tokens) -> None:
    """Each payload received resets the single retry, so a later expiry is refreshed too."""
    clients = [
        _mock_client(_payload(1), UNAUTHENTICATED),
        _mock_client(_payload(2), UNAUTHENTICATED),
        _mock_client(_payload(3)),
    ]
    with patch("andersen_ev.konnect.subscription.Client", side_effect=clients):
        payloads = await _collect(subscription)

    assert [payload["evseState"] for payload in payloads] == [1, 2, 3]
    assert tokens.refresh.await_count == 2


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(TransportServerError("Server error: boom"), id="server_error"),
        pytest.param(TransportQueryError("bad field", errors=[{"message": "bad field"}]), id="query_error"),
        pytest.param(TransportClosed("closed"), id="closed"),
        pytest.param(TransportConnectionFailed("Connection was closed"), id="connection_failed"),
        pytest.param(TransportProtocolError("unexpected"), id="protocol_error"),
        pytest.param(OSError("network down"), id="os_error"),
        pytest.param(TimeoutError(), id="timeout"),
    ],
)
async def test_errors_raise_subscription_error(
    subscription: DeviceStatusSubscription, tokens: _Tokens, error: Exception
) -> None:
    """Connection and protocol errors raise AndersenSubscriptionError without a token refresh."""
    client = _mock_client(_payload(1), error)
    with (
        patch("andersen_ev.konnect.subscription.Client", return_value=client),
        pytest.raises(AndersenSubscriptionError),
    ):
        await _collect(subscription)

    tokens.refresh.assert_not_awaited()
    client.close_async.assert_awaited_once()


async def test_connect_failure_raises_subscription_error(subscription: DeviceStatusSubscription) -> None:
    """A failure to connect raises AndersenSubscriptionError."""
    client = _mock_client(connect_error=TransportConnectionFailed("refused"))
    with (
        patch("andersen_ev.konnect.subscription.Client", return_value=client),
        pytest.raises(AndersenSubscriptionError),
    ):
        await _collect(subscription)


async def test_close_without_connection(subscription: DeviceStatusSubscription) -> None:
    """Closing before connecting does nothing."""
    await subscription.close()


async def test_close_during_stream_is_idempotent(subscription: DeviceStatusSubscription) -> None:
    """close() closes the open connection once, and a second call does nothing."""
    client = _mock_client(_payload(1), _payload(2))
    with patch("andersen_ev.konnect.subscription.Client", return_value=client):
        updates = subscription.updates()
        await anext(updates)
        await subscription.close()
        await subscription.close()
        await updates.aclose()

    client.close_async.assert_awaited_once()


async def test_close_error_is_logged(subscription: DeviceStatusSubscription, caplog: pytest.LogCaptureFixture) -> None:
    """An error while closing is logged at debug and not raised."""
    client = _mock_client(_payload(1))
    client.close_async.side_effect = OSError("already gone")
    caplog.set_level(logging.DEBUG, logger="andersen_ev.konnect.subscription")
    with patch("andersen_ev.konnect.subscription.Client", return_value=client):
        await _collect(subscription)

    assert "Error closing subscription" in caplog.text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param(
            '>>> {"type": "connection_init", "payload": {"authToken": "Bearer eyJ.abc-123_x"}}',
            '>>> {"type": "connection_init", "payload": {"authToken": "Bearer **REDACTED**"}}',
            id="init",
        ),
        pytest.param('<<< {"type":"connection_ack"}', '<<< {"type":"connection_ack"}', id="ack"),
    ],
)
def test_bearer_token_is_redacted_from_logs(message: str, expected: str) -> None:
    """gql's log of connection_init is kept, with the bearer token replaced."""
    record = logging.LogRecord("gql.transport.common.base", logging.DEBUG, __file__, 1, "%s", (message,), None)

    assert _TokenRedactFilter().filter(record) is True
    assert record.getMessage() == expected


async def test_device_subscription_uses_account_token(mock_device: KonnectDevice) -> None:
    """The device creates one subscription, reading the account's current token on each connect."""
    subscription = mock_device.subscription

    assert mock_device.subscription is subscription
    assert subscription.device_id == "test_device_123"
    mock_device.api.token = "rotated_token"
    assert mock_device._current_token() == "rotated_token"


async def test_device_close_closes_subscription(mock_device: KonnectDevice) -> None:
    """Closing the device closes its subscription."""
    with patch.object(DeviceStatusSubscription, "close", AsyncMock()) as close:
        _ = mock_device.subscription
        await mock_device.close()

    close.assert_awaited_once()
