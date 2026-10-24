"""Live device status updates over the deviceStatusUpdated GraphQL subscription."""

import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from typing import Any

from gql import Client, GraphQLRequest, gql
from gql.transport.aiohttp_websockets import AIOHTTPWebsocketsTransport
from gql.transport.exceptions import (
    TransportClosed,
    TransportConnectionFailed,
    TransportProtocolError,
    TransportQueryError,
    TransportServerError,
)
from gql.transport.websockets_protocol import WebsocketsProtocolTransportBase

from . import const
from .exceptions import AndersenAuthError, AndersenSubscriptionError
from .graphql_client import has_unauthenticated_error

_LOGGER = logging.getLogger(__name__)

# The server closes a connection after about 60 s without traffic, so ping well inside that.
HEARTBEAT_SECONDS = 20

_SUBSCRIPTION_DOCUMENT = gql(const.GRAPHQL_DEVICE_STATUS_SUBSCRIPTION)


_BEARER_TOKEN = re.compile(r"(Bearer )[^\s\"']+")


class _TokenRedactFilter(logging.Filter):  # pylint: disable=too-few-public-methods
    """Redact the bearer token from gql's debug log of the connection_init message."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Replace any bearer token in the record, keeping the record."""
        message = record.getMessage()
        redacted = _BEARER_TOKEN.sub(r"\1**REDACTED**", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


logging.getLogger("gql.transport.common.base").addFilter(_TokenRedactFilter())


class _AuthRefused(Exception):
    """Internal signal that the server refused the token."""


class DeviceStatusSubscription:
    """Subscribe to deviceStatusUpdated for one device.

    Only opens the connection and yields payloads; retrying after a dropped
    connection is left to the caller.
    """

    def __init__(
        self,
        device_id: str,
        token_provider: Callable[[], str],
        token_refresh: Callable[[], Awaitable[tuple[str, float | None]]],
        url: str = const.GRAPHQL_WS_URL,
    ) -> None:
        """Initialize the subscription."""
        self.device_id = device_id
        self.url = url
        self._token_provider = token_provider
        self._token_refresh = token_refresh
        self._client: Client | None = None

    async def updates(self) -> AsyncGenerator[dict[str, Any]]:
        """Yield each deviceStatusUpdated payload until the connection ends.

        The first payload arrives straight after subscribing and holds every field.
        A refused token is refreshed and retried once, then raises AndersenAuthError.
        Any other failure raises AndersenSubscriptionError. Returns if the server
        ends the subscription.
        """
        refreshed = False
        while True:
            try:
                async with aclosing(self._stream()) as stream:
                    async for payload in stream:
                        refreshed = False
                        yield payload
            except _AuthRefused as err:
                if refreshed:
                    raise AndersenAuthError(f"Subscription refused the refreshed token: {err}") from err
                _LOGGER.debug("Subscription refused the token for %s, refreshing and retrying", self.device_id)
                await self._token_refresh()
                refreshed = True
                continue
            return

    async def _stream(self) -> AsyncGenerator[dict[str, Any]]:
        """Open one connection and yield its payloads."""
        transport = AIOHTTPWebsocketsTransport(
            url=self.url,
            subprotocols=[WebsocketsProtocolTransportBase.APOLLO_SUBPROTOCOL],
            init_payload={"authToken": f"Bearer {self._token_provider()}"},
            heartbeat=HEARTBEAT_SECONDS,
        )
        client = Client(transport=transport, fetch_schema_from_transport=False)
        self._client = client
        request = GraphQLRequest(
            _SUBSCRIPTION_DOCUMENT,
            variable_values={"id": self.device_id},
            operation_name="deviceStatusUpdated",
        )
        try:
            session = await client.connect_async()
            _LOGGER.debug("Subscribed to deviceStatusUpdated for %s", self.device_id)
            async for result in session.subscribe(request):
                if (payload := result.get("deviceStatusUpdated")) is None:
                    _LOGGER.debug("Subscription message for %s had no deviceStatusUpdated: %s", self.device_id, result)
                    continue
                yield payload
        except TransportServerError as err:
            # A refused token arrives as connection_error: "Invalid/expired login token".
            if "token" in str(err).lower():
                raise _AuthRefused(str(err)) from err
            raise AndersenSubscriptionError(f"Subscription refused: {err}") from err
        except TransportQueryError as err:
            if has_unauthenticated_error(err):
                raise _AuthRefused(str(err)) from err
            raise AndersenSubscriptionError(f"Subscription error: {err}") from err
        except (TransportClosed, TransportConnectionFailed, TransportProtocolError, OSError, TimeoutError) as err:
            raise AndersenSubscriptionError(f"Subscription connection lost: {err}") from err
        finally:
            # close() may already have closed this client from another task.
            if self._client is client:
                self._client = None
                await self._close_client(client)

    async def close(self) -> None:
        """Close the connection if one is open."""
        client, self._client = self._client, None
        if client is not None:
            await self._close_client(client)

    async def _close_client(self, client: Client) -> None:
        try:
            await client.close_async()
        except (TransportClosed, TransportConnectionFailed, TransportProtocolError, OSError) as err:
            _LOGGER.debug("Error closing subscription for %s: %s", self.device_id, err)
