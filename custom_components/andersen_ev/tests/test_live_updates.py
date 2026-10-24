"""Tests for running the live status subscription in the coordinator."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from andersen_ev import AndersenEvCoordinator
from andersen_ev.const import SUBSCRIPTION_RETRY_MAX, SUBSCRIPTION_RETRY_MIN
from andersen_ev.konnect.device import KonnectDevice
from andersen_ev.konnect.exceptions import AndersenAuthError, AndersenSubscriptionError

INITIAL = {"id": "test_device_123", "evseState": 1, "chargeStatus": {"chargePower": 0, "duration": 10}}


class _Harness:
    """A coordinator built without DataUpdateCoordinator.__init__, with its mocked collaborators."""

    def __init__(self) -> None:
        self.entry = MagicMock()
        self.listeners = MagicMock()
        self.coordinator = AndersenEvCoordinator.__new__(AndersenEvCoordinator)
        self.coordinator.devices = []
        self.coordinator._subscription_tasks = {}
        self.coordinator._subscription_failing = set()
        self.coordinator.hass = MagicMock()
        self.coordinator.config_entry = self.entry
        setattr(self.coordinator, "async_update_listeners", self.listeners)


def _status(device: KonnectDevice) -> dict[str, Any]:
    """Return the device's subscription cache, which the test expects to be filled."""
    assert device.last_subscription_status is not None
    return device.last_subscription_status


def _connection(*items: dict[str, Any] | BaseException) -> AsyncGenerator[dict[str, Any]]:
    """Return an updates() stream that yields the payloads and raises any exception among them."""

    async def _updates() -> AsyncGenerator[dict[str, Any]]:
        for item in items:
            if isinstance(item, BaseException):
                raise item
            yield item

    return _updates()


def _attach_subscription(device: KonnectDevice, *connections: AsyncGenerator[dict[str, Any]]) -> MagicMock:
    """Give the device a mock subscription whose updates() returns each connection in turn."""
    subscription = MagicMock()
    subscription.updates = MagicMock(side_effect=list(connections))
    subscription.close = AsyncMock()
    device._subscription = subscription
    return subscription


async def _run_until_sleeps(coordinator: AndersenEvCoordinator, device: KonnectDevice, sleeps: int) -> AsyncMock:
    """Run the subscription loop until it has slept the given number of times, then stop it."""
    sleep = AsyncMock(side_effect=[None] * (sleeps - 1) + [asyncio.CancelledError()])
    with patch("andersen_ev.asyncio.sleep", sleep), pytest.raises(asyncio.CancelledError):
        await coordinator._async_run_subscription(device)
    return sleep


def test_initial_payload_is_stored_and_logged_in_full(
    mock_device: KonnectDevice, caplog: pytest.LogCaptureFixture
) -> None:
    """The first payload fills the cache, marks the device connected and is logged in full."""
    harness = _Harness()
    coordinator = harness.coordinator
    caplog.set_level(logging.DEBUG, logger="andersen_ev")

    coordinator._handle_subscription_payload(mock_device, INITIAL)

    assert mock_device.last_subscription_status == INITIAL
    assert mock_device.subscription_connected is True
    assert mock_device.subscription_disconnected_since is None
    assert mock_device.last_subscription_update is not None
    assert f"Initial live status for Test Device: {INITIAL}" in caplog.text
    harness.listeners.assert_called_once()


def test_later_payload_logs_only_changes(mock_device: KonnectDevice, caplog: pytest.LogCaptureFixture) -> None:
    """After the first payload, only changed fields are logged, with nested fields dotted."""
    harness = _Harness()
    coordinator = harness.coordinator
    coordinator._handle_subscription_payload(mock_device, INITIAL)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="andersen_ev")

    coordinator._handle_subscription_payload(
        mock_device, {**INITIAL, "evseState": 3, "chargeStatus": {"chargePower": 7120, "duration": 10}}
    )

    assert "Live status update for Test Device: evseState 1 -> 3, chargeStatus.chargePower 0 -> 7120" in caplog.text
    assert "Initial live status" not in caplog.text
    assert _status(mock_device)["chargeStatus"] == {"chargePower": 7120, "duration": 10}


def test_unchanged_payload_logs_nothing(mock_device: KonnectDevice, caplog: pytest.LogCaptureFixture) -> None:
    """A payload identical to the cache is not logged and does not notify listeners."""
    harness = _Harness()
    coordinator = harness.coordinator
    coordinator._handle_subscription_payload(mock_device, INITIAL)
    harness.listeners.reset_mock()
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="andersen_ev")

    coordinator._handle_subscription_payload(mock_device, dict(INITIAL))

    assert "Live status update" not in caplog.text
    harness.listeners.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(AndersenSubscriptionError("Connection was closed"), id="dropped"),
        pytest.param(AndersenAuthError("refused"), id="auth"),
        pytest.param(None, id="server_ended"),
    ],
)
async def test_resubscribes_after_failure_and_logs_once(
    mock_device: KonnectDevice, caplog: pytest.LogCaptureFixture, failure: Exception | None
) -> None:
    """A lost subscription warns once, retries with backoff, and logs once when restored."""
    harness = _Harness()
    coordinator = harness.coordinator
    first = (INITIAL, failure) if failure else (INITIAL,)
    second = (failure,) if failure else ()
    # The third connection stays open until the loop is cancelled.
    subscription = _attach_subscription(
        mock_device,
        _connection(*first),
        _connection(*second),
        _connection({**INITIAL, "evseState": 2}, asyncio.CancelledError()),
    )
    caplog.set_level(logging.DEBUG, logger="andersen_ev")

    sleep = AsyncMock()
    with patch("andersen_ev.asyncio.sleep", sleep), pytest.raises(asyncio.CancelledError):
        await coordinator._async_run_subscription(mock_device)

    assert [c.args[0] for c in sleep.call_args_list] == [SUBSCRIPTION_RETRY_MIN, SUBSCRIPTION_RETRY_MIN * 2]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO and "restored" in r.getMessage()]
    assert len(warnings) == 1
    assert "Live status subscription for Test Device lost" in warnings[0].getMessage()
    assert len(infos) == 1
    assert "still unavailable" in caplog.text
    assert _status(mock_device)["evseState"] == 2
    assert mock_device.subscription_connected is False
    subscription.close.assert_awaited_once()


async def test_backoff_is_capped(mock_device: KonnectDevice) -> None:
    """The retry delay doubles per failure up to the maximum."""
    harness = _Harness()
    coordinator = harness.coordinator
    _attach_subscription(mock_device, *(_connection(AndersenSubscriptionError("down")) for _ in range(8)))

    sleep = await _run_until_sleeps(coordinator, mock_device, 8)

    assert [c.args[0] for c in sleep.call_args_list] == [5, 10, 20, 40, 80, 160, SUBSCRIPTION_RETRY_MAX, 300]


async def test_backoff_resets_after_a_payload(mock_device: KonnectDevice) -> None:
    """A payload on a new connection resets the retry delay to the minimum."""
    harness = _Harness()
    coordinator = harness.coordinator
    _attach_subscription(
        mock_device,
        _connection(AndersenSubscriptionError("down")),
        _connection(AndersenSubscriptionError("down")),
        _connection(INITIAL, AndersenSubscriptionError("down")),
        _connection(AndersenSubscriptionError("down")),
    )

    sleep = await _run_until_sleeps(coordinator, mock_device, 4)

    assert [c.args[0] for c in sleep.call_args_list] == [5, 10, 5, 10]


async def test_disconnected_since_set_until_first_payload(mock_device: KonnectDevice) -> None:
    """The device counts as disconnected from the start of the loop until a payload arrives."""
    harness = _Harness()
    coordinator = harness.coordinator
    _attach_subscription(mock_device, _connection(AndersenSubscriptionError("down")))

    await _run_until_sleeps(coordinator, mock_device, 1)

    assert mock_device.subscription_disconnected_since is not None
    assert mock_device.subscription_connected is False


async def test_unexpected_error_is_logged_once_and_retried(
    mock_device: KonnectDevice, caplog: pytest.LogCaptureFixture
) -> None:
    """An unexpected exception is logged with a traceback once, then retried like any failure."""
    harness = _Harness()
    coordinator = harness.coordinator
    _attach_subscription(mock_device, _connection(ValueError("bad payload")), _connection(ValueError("bad payload")))

    await _run_until_sleeps(coordinator, mock_device, 2)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None


async def test_start_subscriptions_follows_devices(mock_api: MagicMock) -> None:
    """A task is started per new device, kept for existing ones, and cancelled for removed ones."""
    harness = _Harness()
    coordinator = harness.coordinator
    device_a = KonnectDevice(api=mock_api, device_id="a", friendly_name="A", user_lock=False)
    device_b = KonnectDevice(api=mock_api, device_id="b", friendly_name="B", user_lock=False)
    tasks = {"a": MagicMock(), "b": MagicMock()}

    def _create_task(_hass: Any, coro: Any, name: str) -> MagicMock:
        coro.close()
        return tasks[name.rsplit(" ", 1)[1]]

    harness.entry.async_create_background_task = MagicMock(side_effect=_create_task)

    coordinator.devices = [device_a, device_b]
    coordinator.async_start_subscriptions()
    coordinator.async_start_subscriptions()
    coordinator.devices = [device_a]
    coordinator.async_start_subscriptions()

    assert harness.entry.async_create_background_task.call_count == 2
    assert coordinator._subscription_tasks == {"a": tasks["a"]}
    tasks["b"].cancel.assert_called_once()
    tasks["a"].cancel.assert_not_called()


async def test_stop_subscriptions_cancels_and_waits() -> None:
    """Stopping cancels every loop and waits for it to finish."""
    harness = _Harness()
    coordinator = harness.coordinator
    task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
    coordinator._subscription_tasks = {"a": task}

    await coordinator.async_stop_subscriptions()

    assert task.cancelled()
    assert coordinator._subscription_tasks == {}
