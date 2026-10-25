"""Tests for the Andersen EV user max charge current and power numbers."""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.number import NumberDeviceClass
from homeassistant.const import UnitOfElectricCurrent, UnitOfPower
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from andersen_ev.konnect.device import KonnectDevice
from andersen_ev.konnect.exceptions import AndersenConnectionError
from andersen_ev.number import AndersenEvUserMaxCurrentNumber, AndersenEvUserMaxPowerNumber
from andersen_ev.user_max_current import (
    allowed_user_max_amps,
    nearest_allowed_amps,
    nearest_allowed_amps_for_kw,
)

ALL_ALLOWED = [10, 16, 24, 32]


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = []
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.fixture
def mock_device(mock_coordinator: MagicMock) -> MagicMock:
    """Create a mock device with a 32 A installer limit and a 16 A user max."""
    device = MagicMock()
    device.device_id = "test_device_123"
    device.friendly_name = "Test Charger"
    device.subscription_disconnected_since = None
    device.live_status = {"cfgChargeAmpMax": 32, "sysUserMaxChargeAmpSet": 16}
    device.set_user_max_amps = AsyncMock(return_value=True)
    mock_coordinator.data = [device]
    return device


@pytest.mark.parametrize(
    ("max_amps", "expected"),
    [(32, ALL_ALLOWED), (20, [10, 16]), (24, [10, 16, 24]), (6, [])],
)
def test_allowed_user_max_amps(max_amps: float, expected: list[int]) -> None:
    """Only allowed currents within the installer limit can be set."""
    assert allowed_user_max_amps(max_amps) == expected


@pytest.mark.parametrize(
    ("amps", "allowed", "expected"),
    [
        pytest.param(9, ALL_ALLOWED, 10, id="below_minimum"),
        pytest.param(14, ALL_ALLOWED, 16, id="nearest_up"),
        pytest.param(13, ALL_ALLOWED, 10, id="tie_10_16_rounds_down"),
        pytest.param(20, ALL_ALLOWED, 16, id="tie_16_24_rounds_down"),
        pytest.param(28, ALL_ALLOWED, 24, id="tie_24_32_rounds_down"),
        pytest.param(29, ALL_ALLOWED, 32, id="nearest_32"),
        pytest.param(40, ALL_ALLOWED, 32, id="above_maximum"),
        pytest.param(32, [10, 16], 16, id="capped_by_installer_limit"),
    ],
)
def test_nearest_allowed_amps(amps: float, allowed: list[int], expected: int) -> None:
    """Currents round to the nearest allowed value, and down on a tie."""
    assert nearest_allowed_amps(amps, allowed) == expected


@pytest.mark.parametrize(
    ("power_kw", "allowed", "expected"),
    [
        pytest.param(1.0, ALL_ALLOWED, 10, id="below_minimum"),
        pytest.param(2.95, ALL_ALLOWED, 10, id="tie_2.3_3.6_rounds_down"),
        pytest.param(3.0, ALL_ALLOWED, 16, id="nearest_3.6"),
        pytest.param(4.5, ALL_ALLOWED, 16, id="tie_3.6_5.4_rounds_down"),
        pytest.param(5.0, ALL_ALLOWED, 24, id="nearest_5.4"),
        pytest.param(6.3, ALL_ALLOWED, 24, id="tie_5.4_7.2_rounds_down"),
        pytest.param(6.4, ALL_ALLOWED, 32, id="nearest_7.2"),
        pytest.param(7.2, [10, 16], 16, id="capped_by_installer_limit"),
    ],
)
def test_nearest_allowed_amps_for_kw(power_kw: float, allowed: list[int], expected: int) -> None:
    """Power rounds to the allowed current with the nearest kW, and down on a tie."""
    assert nearest_allowed_amps_for_kw(power_kw, allowed) == expected


@pytest.mark.parametrize(
    ("live_status", "expected_amps", "expected_kw"),
    [
        pytest.param({"cfgChargeAmpMax": 32, "sysUserMaxChargeAmpSet": 16}, 16, 3.6, id="user_set"),
        pytest.param({"cfgChargeAmpMax": 32, "sysUserMaxChargeAmpSet": 10}, 10, 2.3, id="minimum"),
        pytest.param(
            {"cfgChargeAmpMax": 32, "sysUserMaxChargeAmpSet": None}, 32, 7.2, id="null_falls_back_to_installer_limit"
        ),
        pytest.param(
            {"cfgChargeAmpMax": 32, "sysUserMaxChargeAmpSet": 0}, 32, 7.2, id="zero_falls_back_to_installer_limit"
        ),
        pytest.param({}, None, None, id="before_first_payload"),
    ],
)
def test_native_value(
    mock_coordinator: MagicMock,
    mock_device: MagicMock,
    live_status: dict,
    expected_amps: float | None,
    expected_kw: float | None,
) -> None:
    """Both entities show the user max, in A and kW."""
    mock_device.live_status = live_status

    assert AndersenEvUserMaxCurrentNumber(mock_coordinator, mock_device).native_value == expected_amps
    assert AndersenEvUserMaxPowerNumber(mock_coordinator, mock_device).native_value == expected_kw


async def poll(device: KonnectDevice, status: dict) -> None:
    """Return status from the next REST getDevice poll."""
    with patch.object(device.api, "getDevice", AsyncMock(return_value={"status": status})):
        await device.get_user_device()


async def live(device: KonnectDevice, status: dict) -> None:
    """Deliver status as a deviceStatusUpdated payload."""
    device.apply_subscription_update(status)


@pytest.mark.parametrize(
    ("updates", "expected_field", "expected_kw"),
    [
        pytest.param(
            [(poll, {"userSetChargeAmpMax": 24, "sysUserMaxChargeAmpSet": 16}), (live, {"sysUserMaxChargeAmpSet": 16})],
            "userSetChargeAmpMax",
            5.4,
            id="old_firmware_prefers_polled_setting",
        ),
        pytest.param(
            [
                (poll, {"userSetChargeAmpMax": 24, "sysUserMaxChargeAmpSet": 16}),
                (live, {"sysUserMaxChargeAmpSet": 16}),
                (poll, {"userSetChargeAmpMax": 10, "sysUserMaxChargeAmpSet": 16}),
            ],
            "userSetChargeAmpMax",
            2.3,
            id="old_firmware_follows_polled_change",
        ),
        pytest.param(
            [(live, {"sysUserMaxChargeAmpSet": 16}), (poll, {"userSetChargeAmpMax": 24})],
            "userSetChargeAmpMax",
            5.4,
            id="polled_setting_preferred_when_seen_after_live",
        ),
        pytest.param(
            [
                (poll, {"sysUserMaxChargeAmpSet": 32}),
                (live, {"sysUserMaxChargeAmpSet": 32}),
                (live, {"sysUserMaxChargeAmpSet": 16}),
            ],
            "sysUserMaxChargeAmpSet",
            3.6,
            id="new_firmware_follows_live_change",
        ),
        pytest.param(
            [
                (poll, {"userSetChargeAmpMax": 24}),
                (live, {"sysUserMaxChargeAmpSet": 24}),
                (live, {"sysUserMaxChargeAmpSet": 16}),
                (poll, {"userSetChargeAmpMax": 24}),
            ],
            "sysUserMaxChargeAmpSet",
            3.6,
            id="live_change_wins_over_unchanged_poll",
        ),
        pytest.param(
            [
                (poll, {"userSetChargeAmpMax": 24}),
                (live, {"sysUserMaxChargeAmpSet": 24}),
                (live, {"sysUserMaxChargeAmpSet": 16}),
                (poll, {"userSetChargeAmpMax": 10}),
            ],
            "userSetChargeAmpMax",
            2.3,
            id="later_polled_change_wins",
        ),
        pytest.param(
            [(poll, {"userSetChargeAmpMax": None}), (live, {"sysUserMaxChargeAmpSet": 16})],
            "sysUserMaxChargeAmpSet",
            3.6,
            id="null_polled_setting_ignored",
        ),
    ],
)
@pytest.mark.asyncio
async def test_native_value_follows_last_changed_field(
    mock_coordinator: MagicMock,
    updates: list[tuple[Callable[[KonnectDevice, dict], Awaitable[None]], dict]],
    expected_field: str,
    expected_kw: float,
) -> None:
    """The user max is read from whichever of the polled and live fields last changed."""
    device = KonnectDevice(api=MagicMock(), device_id="test_device_123", friendly_name="Test Charger", user_lock=False)
    device.apply_subscription_update({"cfgChargeAmpMax": 32})
    mock_coordinator.data = [device]

    for update, status in updates:
        await update(device, status)

    assert device.user_max_amps_field == expected_field
    assert AndersenEvUserMaxPowerNumber(mock_coordinator, device).native_value == expected_kw


@pytest.mark.parametrize(
    ("live_status", "expected_max_amps", "expected_max_kw"),
    [
        pytest.param({"cfgChargeAmpMax": 32}, 32, 7.2, id="32A_install"),
        pytest.param({"cfgChargeAmpMax": 20}, 16, 3.6, id="20A_install"),
        pytest.param({}, 32, 7.2, id="installer_limit_unknown"),
        pytest.param({"cfgChargeAmpMax": 6}, 10, 2.3, id="install_below_minimum"),
    ],
)
def test_min_max(
    mock_coordinator: MagicMock,
    mock_device: MagicMock,
    live_status: dict,
    expected_max_amps: float,
    expected_max_kw: float,
) -> None:
    """The maximum is the highest allowed current within the installer limit."""
    mock_device.live_status = live_status
    current = AndersenEvUserMaxCurrentNumber(mock_coordinator, mock_device)
    power = AndersenEvUserMaxPowerNumber(mock_coordinator, mock_device)

    assert (current.native_min_value, current.native_max_value, current.native_step) == (10, expected_max_amps, 1)
    assert (power.native_min_value, power.native_max_value, power.native_step) == (2.3, expected_max_kw, 0.1)


@pytest.mark.parametrize(
    ("entity_class", "value", "unit", "live_status", "expected_amps"),
    [
        pytest.param(AndersenEvUserMaxCurrentNumber, 24, "A", {"cfgChargeAmpMax": 32}, 24, id="current_allowed"),
        pytest.param(AndersenEvUserMaxCurrentNumber, 20, "A", {"cfgChargeAmpMax": 32}, 16, id="current_rounded"),
        pytest.param(AndersenEvUserMaxCurrentNumber, 32, "A", {"cfgChargeAmpMax": 20}, 16, id="current_capped"),
        pytest.param(AndersenEvUserMaxPowerNumber, 5.0, "kW", {"cfgChargeAmpMax": 32}, 24, id="power_rounded"),
        pytest.param(AndersenEvUserMaxPowerNumber, 7.2, "kW", {"cfgChargeAmpMax": 24}, 24, id="power_capped"),
    ],
)
@pytest.mark.asyncio
async def test_set_value_sends_allowed_amps(
    mock_coordinator: MagicMock,
    mock_device: MagicMock,
    entity_class: type,
    value: float,
    unit: str,
    live_status: dict,
    expected_amps: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Setting either entity sends the allowed current; the value changes only when the subscription reports it."""
    caplog.set_level(logging.DEBUG, logger="andersen_ev.number")
    mock_device.live_status = live_status
    entity = entity_class(mock_coordinator, mock_device)

    await entity.async_set_native_value(value)

    mock_device.set_user_max_amps.assert_awaited_once_with(expected_amps)
    assert (
        f"Requested {value} {unit} for Test Charger, setting user max charge current to {expected_amps} A"
        in caplog.text
    )
    assert "sysUserMaxChargeAmpSet" not in mock_device.live_status
    mock_coordinator.async_update_listeners.assert_not_called()
    mock_coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("entity_class", [AndersenEvUserMaxCurrentNumber, AndersenEvUserMaxPowerNumber])
@pytest.mark.parametrize(
    ("live_status", "set_user_max_amps", "translation_key"),
    [
        pytest.param({}, AsyncMock(return_value=True), "user_max_current_limit_unknown", id="limit_unknown"),
        pytest.param(
            {"cfgChargeAmpMax": 6}, AsyncMock(return_value=True), "user_max_current_limit_too_low", id="limit_too_low"
        ),
        pytest.param({"cfgChargeAmpMax": 32}, AsyncMock(return_value=False), "user_max_current_failed", id="rejected"),
        pytest.param(
            {"cfgChargeAmpMax": 32},
            AsyncMock(side_effect=AndersenConnectionError("down")),
            "user_max_current_error",
            id="api_error",
        ),
    ],
)
@pytest.mark.asyncio
async def test_set_value_failure_raises(
    mock_coordinator: MagicMock,
    mock_device: MagicMock,
    entity_class: type,
    live_status: dict,
    set_user_max_amps: AsyncMock,
    translation_key: str,
) -> None:
    """A write that can't be made or isn't applied is reported to the caller."""
    mock_device.live_status = live_status
    mock_device.set_user_max_amps = set_user_max_amps
    entity = entity_class(mock_coordinator, mock_device)

    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.async_set_native_value(10)

    assert exc_info.value.translation_key == translation_key
    mock_coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize(
    ("entity_class", "key", "device_class", "unit", "enabled_default"),
    [
        pytest.param(
            AndersenEvUserMaxCurrentNumber,
            "user_max_current",
            NumberDeviceClass.CURRENT,
            UnitOfElectricCurrent.AMPERE,
            False,
            id="current",
        ),
        pytest.param(
            AndersenEvUserMaxPowerNumber,
            "user_max_power",
            NumberDeviceClass.POWER,
            UnitOfPower.KILO_WATT,
            True,
            id="power",
        ),
    ],
)
def test_entity_description(
    mock_coordinator: MagicMock,
    mock_device: MagicMock,
    entity_class: type,
    key: str,
    device_class: NumberDeviceClass,
    unit: str,
    enabled_default: bool,
) -> None:
    """Name comes from the translation, and only the power entity is enabled by default."""
    entity = entity_class(mock_coordinator, mock_device)

    assert entity.unique_id == f"test_device_123_{key}"
    assert entity.translation_key == key
    assert entity.has_entity_name is True
    assert entity.device_class == device_class
    assert entity.native_unit_of_measurement == unit
    assert entity.entity_registry_enabled_default is enabled_default
    assert entity.entity_category is None
    assert entity.device_info["identifiers"] == {("andersen_ev", "test_device_123")}


@pytest.mark.parametrize("entity_class", [AndersenEvUserMaxCurrentNumber, AndersenEvUserMaxPowerNumber])
@pytest.mark.parametrize(
    ("disconnected_for", "available"),
    [
        pytest.param(None, True, id="connected"),
        pytest.param(timedelta(minutes=6), False, id="disconnected_over_five_minutes"),
    ],
)
def test_available_follows_live_status(
    mock_coordinator: MagicMock,
    mock_device: MagicMock,
    entity_class: type,
    disconnected_for: timedelta | None,
    available: bool,
) -> None:
    """The entity goes unavailable once the subscription has been down for five minutes."""
    mock_device.subscription_disconnected_since = (
        None if disconnected_for is None else dt_util.utcnow() - disconnected_for
    )

    assert entity_class(mock_coordinator, mock_device).available is available
