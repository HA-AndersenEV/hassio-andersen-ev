"""Tests for running the live status subscription in the coordinator."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Generator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from andersen_ev import AndersenEvCoordinator
from andersen_ev.const import LIVE_STATUS_TIMEOUT, SUBSCRIPTION_RETRY_MAX, SUBSCRIPTION_RETRY_MIN
from andersen_ev.konnect.device import KonnectDevice
from andersen_ev.konnect.exceptions import AndersenAuthError, AndersenSubscriptionError


@pytest.fixture(autouse=True)
def call_later() -> Generator[MagicMock]:
    """Stand in for async_call_later, which needs a running Home Assistant."""
    with patch("andersen_ev.async_call_later") as call_later:
        call_later.side_effect = lambda _hass, _delay, _action: MagicMock()
        yield call_later


INITIAL = {"id": "test_device_123", "evseState": 1, "chargeStatus": {"chargePower": 0, "duration": 10}}


class _Harness:
    """A coordinator built without DataUpdateCoordinator.__init__, with its mocked collaborators."""

    def __init__(self) -> None:
        self.entry = MagicMock(entry_id="test_entry_id")
        self.listeners = MagicMock()
        self.coordinator = AndersenEvCoordinator.__new__(AndersenEvCoordinator)
        self.coordinator.devices = []
        self.coordinator._subscription_tasks = {}
        self.coordinator._subscription_failing = set()
        self.coordinator._live_status_received = {}
        self.coordinator._live_timeout_unsubs = {}
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
    assert f"deviceStatusUpdated for Test Device (initial): {INITIAL}" in caplog.text
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

    assert "deviceStatusUpdated for Test Device: chargeStatus.chargePower 0 -> 7120, evseState 1 -> 3" in caplog.text
    assert "(initial)" not in caplog.text
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

    assert "deviceStatusUpdated" not in caplog.text
    harness.listeners.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(AndersenSubscriptionError("Connection was closed"), id="dropped"),
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


async def test_refused_login_starts_reauth_and_stops(
    mock_device: KonnectDevice, caplog: pytest.LogCaptureFixture, call_later: MagicMock
) -> None:
    """Retrying refused credentials can't help, so the loop asks for reauthentication and ends."""
    harness = _Harness()
    subscription = _attach_subscription(mock_device, _connection(INITIAL, AndersenAuthError("refused")))

    sleep = AsyncMock()
    with patch("andersen_ev.asyncio.sleep", sleep):
        await harness.coordinator._async_run_subscription(mock_device)

    harness.entry.async_start_reauth.assert_called_once_with(harness.coordinator.hass)
    sleep.assert_not_awaited()
    assert "refused the login" in caplog.text
    assert mock_device.subscription_connected is False
    assert mock_device.subscription_disconnected_since is not None
    # The countdown to unavailable runs as for any other loss.
    assert mock_device.device_id in harness.coordinator._live_timeout_unsubs
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


async def test_removed_device_state_is_dropped(mock_device: KonnectDevice) -> None:
    """A device that leaves the account loses its task, pending timeout and failure and payload tracking."""
    harness = _Harness()
    coordinator = harness.coordinator
    task = MagicMock()
    coordinator._subscription_tasks = {mock_device.device_id: task}
    coordinator._mark_subscription_lost(mock_device, "down", 5)
    coordinator._live_status_event(mock_device.device_id)
    unsub = cast(MagicMock, coordinator._live_timeout_unsubs[mock_device.device_id])

    coordinator.async_start_subscriptions()

    task.cancel.assert_called_once()
    unsub.assert_called_once()
    assert coordinator._subscription_tasks == {}
    assert coordinator._live_timeout_unsubs == {}
    assert coordinator._subscription_failing == set()
    assert coordinator._live_status_received == {}


async def test_stop_subscriptions_cancels_and_waits() -> None:
    """Stopping cancels every loop and waits for it to finish."""
    harness = _Harness()
    coordinator = harness.coordinator
    task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
    coordinator._subscription_tasks = {"a": task}

    await coordinator.async_stop_subscriptions()

    assert task.cancelled()
    assert coordinator._subscription_tasks == {}


def test_payload_feeds_solar_status(mock_device: KonnectDevice) -> None:
    """Solar switches and grid source power read the solar cache, which follows the live status."""
    harness = _Harness()

    harness.coordinator._handle_subscription_payload(
        mock_device,
        {
            "solarOverride": True,
            "solarChargeAlways": True,
            "solarMaxGridChargePercent": 40,
            "chargeOutsideSchedules": False,
        },
    )

    assert mock_device.last_solar_status == {
        "boost": True,
        "solarChargeAlways": True,
        "solarMaxGridChargePercent": 40,
        "solarChargeOutsideSchedules": False,
    }


async def test_live_timeout_refreshes_entities_after_five_minutes(
    mock_device: KonnectDevice, call_later: MagicMock
) -> None:
    """Losing the subscription schedules a listener update at the five-minute mark, so entities go unavailable."""
    harness = _Harness()
    _attach_subscription(mock_device, _connection(AndersenSubscriptionError("down")))

    await _run_until_sleeps(harness.coordinator, mock_device, 1)

    call_later.assert_called_once()
    assert call_later.call_args.args[1] == LIVE_STATUS_TIMEOUT.total_seconds()
    call_later.call_args.args[2](None)
    harness.listeners.assert_called_once()
    assert harness.coordinator._live_timeout_unsubs == {}


def test_live_timeout_cancelled_when_payload_arrives(mock_device: KonnectDevice, call_later: MagicMock) -> None:
    """A payload after a loss cancels the pending timeout."""
    harness = _Harness()
    harness.coordinator._mark_subscription_lost(mock_device, "down", 5)
    unsub = cast(MagicMock, harness.coordinator._live_timeout_unsubs[mock_device.device_id])

    harness.coordinator._handle_subscription_payload(mock_device, INITIAL)

    unsub.assert_called_once()
    assert harness.coordinator._live_timeout_unsubs == {}


async def test_stop_subscriptions_cancels_live_timeouts(mock_device: KonnectDevice) -> None:
    """Unloading cancels pending timeouts."""
    harness = _Harness()
    harness.coordinator._mark_subscription_lost(mock_device, "down", 5)
    unsub = cast(MagicMock, harness.coordinator._live_timeout_unsubs[mock_device.device_id])

    await harness.coordinator.async_stop_subscriptions()

    unsub.assert_called_once()
    assert harness.coordinator._live_timeout_unsubs == {}


async def test_wait_for_live_status_returns_after_first_payload(mock_device: KonnectDevice) -> None:
    """Setup continues as soon as every device has had a payload."""
    harness = _Harness()
    harness.coordinator.devices = [mock_device]
    asyncio.get_running_loop().call_soon(harness.coordinator._handle_subscription_payload, mock_device, INITIAL)

    await asyncio.wait_for(harness.coordinator.async_wait_for_live_status(10), 1)

    assert mock_device.last_subscription_status == INITIAL


async def test_wait_for_live_status_times_out_quietly(
    mock_device: KonnectDevice, caplog: pytest.LogCaptureFixture
) -> None:
    """Without a payload, setup carries on after the timeout instead of failing."""
    harness = _Harness()
    harness.coordinator.devices = [mock_device]
    caplog.set_level(logging.DEBUG, logger="andersen_ev")

    await harness.coordinator.async_wait_for_live_status(0.01)

    assert "No live status for every device" in caplog.text


def test_rescheduling_live_timeout_cancels_the_previous_one(mock_device: KonnectDevice) -> None:
    """A new subscription loop for the same device replaces its pending timeout rather than adding another."""
    harness = _Harness()
    harness.coordinator._schedule_live_timeout(mock_device)
    first = cast(MagicMock, harness.coordinator._live_timeout_unsubs[mock_device.device_id])

    harness.coordinator._schedule_live_timeout(mock_device)

    first.assert_called_once()
    assert harness.coordinator._live_timeout_unsubs[mock_device.device_id] is not first


DEVICE_INFO = {
    "sysProductId": "30",
    "sysProductName": "Thurlestone",
    "sysHwVersion": "4",
    "sysFwVersion": "630",
    "konnectSerial": "1234567890",
}


@pytest.fixture
def device_registry() -> Generator[MagicMock]:
    """Stand in for the device registry, holding a registered entry for each charger."""
    registry = MagicMock()
    registry.async_get_device_by_identifier.return_value = MagicMock(id="registry_id")
    with patch("andersen_ev.dr.async_get", return_value=registry):
        yield registry


def test_device_info_change_updates_registry(mock_device: KonnectDevice, device_registry: MagicMock) -> None:
    """A firmware update reported by the charger reaches the device page."""
    harness = _Harness()
    harness.coordinator._handle_subscription_payload(mock_device, {**INITIAL, **DEVICE_INFO})
    device_registry.async_update_device.reset_mock()

    harness.coordinator._handle_subscription_payload(mock_device, {"sysFwVersion": "631"})

    device_registry.async_get_device_by_identifier.assert_called_with(
        ("andersen_ev", "test_device_123"), "test_entry_id"
    )
    device_registry.async_update_device.assert_called_once_with(
        "registry_id",
        model="A2",
        model_id="30",
        hw_version="4",
        sw_version="631",
        serial_number="1234567890",
    )


def test_other_changes_leave_registry_alone(mock_device: KonnectDevice, device_registry: MagicMock) -> None:
    harness = _Harness()
    harness.coordinator._handle_subscription_payload(mock_device, {**INITIAL, **DEVICE_INFO})
    device_registry.async_update_device.reset_mock()

    harness.coordinator._handle_subscription_payload(mock_device, {"evseState": 3})

    device_registry.async_update_device.assert_not_called()


def test_update_device_registry_skips_unregistered_and_empty(
    mock_device: KonnectDevice, mock_api: MagicMock, device_registry: MagicMock
) -> None:
    """Devices not registered yet, or without a live status, are left for their entities to fill in."""
    harness = _Harness()
    unregistered = KonnectDevice(api=mock_api, device_id="other", friendly_name="Other", user_lock=False)
    unregistered.apply_subscription_update(DEVICE_INFO)
    mock_device.apply_subscription_update(DEVICE_INFO)
    no_status = KonnectDevice(api=mock_api, device_id="empty", friendly_name="Empty", user_lock=False)
    harness.coordinator.devices = [mock_device, unregistered, no_status]
    device_registry.async_get_device_by_identifier.side_effect = lambda identifier, config_entry_id: (
        None if identifier == ("andersen_ev", "other") else MagicMock(id=f"registry_{identifier}")
    )

    harness.coordinator.async_update_device_registry()

    device_registry.async_update_device.assert_called_once()
    assert device_registry.async_update_device.call_args.kwargs["sw_version"] == "630"
