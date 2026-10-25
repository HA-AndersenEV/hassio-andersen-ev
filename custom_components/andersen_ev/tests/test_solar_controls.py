"""Tests for Andersen EV solar control entities."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import UnitOfPower
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity
from homeassistant.util import dt as dt_util

from andersen_ev.grid_source import (
    amps_to_kw,
    effective_max_amps,
    grid_source_kw_to_percent,
    grid_source_percent_to_kw,
)
from andersen_ev.konnect.exceptions import AndersenAuthError
from andersen_ev.number import AndersenEvGridSourcePowerNumber, async_setup_entry
from andersen_ev.switch import (
    AndersenEvBoostSwitch,
    AndersenEvSolarChargeAlwaysSwitch,
    AndersenEvSolarChargeOutsideScheduleSwitch,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    return MagicMock()


@pytest.fixture
def mock_coordinator(mock_hass):
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.hass = mock_hass
    coordinator.data = []
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.fixture
def mock_device():
    """Create a mock device with solar methods."""
    device = MagicMock()
    device.device_id = "test_device_123"
    device.friendly_name = "Test Charger"
    device.user_lock = False
    device.subscription_disconnected_since = None
    device.live_status = {
        "sysProductName": "Andersen A2",
        "sysHwVersion": "1.0",
        "cfgChargeAmpMax": 32,
    }
    device._last_solar_status = {
        "boost": False,
        "solarChargeAlways": False,
        "solarChargeOutsideSchedules": True,
        "solarMaxGridChargePercent": 50,
    }
    device.last_solar_status = device._last_solar_status
    device.last_grid_charge_percent = None
    device.set_solar = AsyncMock(return_value=True)
    device.model_name = "Andersen A2"
    return device


class TestAndersenEvBoostSwitch:
    """Test AndersenEvBoostSwitch entity."""

    @pytest.mark.asyncio
    async def test_boost_switch_is_on(self, mock_coordinator, mock_device):
        """Test boost switch reads on state correctly."""
        mock_coordinator.data = [mock_device]
        mock_device._last_solar_status["boost"] = True

        switch = AndersenEvBoostSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()
        assert switch.is_on is True

    @pytest.mark.asyncio
    async def test_boost_switch_is_off(self, mock_coordinator, mock_device):
        """Test boost switch reads off state correctly."""
        mock_coordinator.data = [mock_device]
        mock_device._last_solar_status["boost"] = False

        switch = AndersenEvBoostSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_boost_switch_turn_on(self, mock_coordinator, mock_device):
        """Test turning on boost switch."""
        mock_coordinator.data = [mock_device]

        switch = AndersenEvBoostSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()
        await switch.async_turn_on()

        mock_device.set_solar.assert_called_once_with(boost=True)
        mock_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_boost_switch_turn_off(self, mock_coordinator, mock_device):
        """Test turning off boost switch."""
        mock_coordinator.data = [mock_device]

        switch = AndersenEvBoostSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()
        await switch.async_turn_off()

        mock_device.set_solar.assert_called_once_with(boost=False)
        mock_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_boost_switch_unique_id(self, mock_coordinator, mock_device):
        """Test boost switch has correct unique ID."""
        switch = AndersenEvBoostSwitch(mock_coordinator, mock_device)
        assert switch.unique_id == "test_device_123_solar_boost"


class TestAndersenEvSolarChargeAlwaysSwitch:
    """Test AndersenEvSolarChargeAlwaysSwitch entity."""

    @pytest.mark.parametrize(
        ("last_percent", "expected_percent"),
        [
            pytest.param(None, 19, id="nothing_remembered_uses_minimum"),
            pytest.param(50, 50, id="restores_previous"),
            pytest.param(10, 19, id="previous_below_minimum_uses_minimum"),
        ],
    )
    @pytest.mark.asyncio
    async def test_turn_on_sets_percent(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        last_percent: int | None,
        expected_percent: int,
    ) -> None:
        """Turning on sends chargeAlways with the previous percent, at least 1.4 kW."""
        mock_coordinator.data = [mock_device]
        mock_device.last_grid_charge_percent = last_percent
        mock_device._last_solar_status["solarMaxGridChargePercent"] = 0

        switch = AndersenEvSolarChargeAlwaysSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_on()

        mock_device.set_solar.assert_called_once_with(charge_always=True, max_grid_charge_percent=expected_percent)
        # Shown only once the subscription reports it.
        assert mock_device._last_solar_status["solarChargeAlways"] is False

    @pytest.mark.asyncio
    async def test_turn_on_minimum_follows_user_max(self, mock_coordinator: MagicMock, mock_device: MagicMock) -> None:
        """The 1.4 kW minimum is a percent of the user max power (3.6 kW at 16 A)."""
        mock_coordinator.data = [mock_device]
        mock_device.live_status["sysUserMaxChargeAmpSet"] = 16

        switch = AndersenEvSolarChargeAlwaysSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_on()

        mock_device.set_solar.assert_called_once_with(charge_always=True, max_grid_charge_percent=39)

    @pytest.mark.asyncio
    async def test_turn_off_zeroes_percent(self, mock_coordinator: MagicMock, mock_device: MagicMock) -> None:
        """Turning off sends chargeAlways off with a zero percent."""
        mock_coordinator.data = [mock_device]
        mock_device._last_solar_status["solarChargeAlways"] = True

        switch = AndersenEvSolarChargeAlwaysSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        with patch.object(switch, "async_write_ha_state"):
            await switch.async_turn_off()

        mock_device.set_solar.assert_called_once_with(charge_always=False, max_grid_charge_percent=0)
        assert mock_device._last_solar_status["solarChargeAlways"] is True

    @pytest.mark.asyncio
    async def test_turn_on_without_max_amps(self, mock_coordinator: MagicMock, mock_device: MagicMock) -> None:
        """Nothing is sent when the minimum percent can't be worked out."""
        mock_coordinator.data = [mock_device]
        mock_device.live_status = {}

        switch = AndersenEvSolarChargeAlwaysSwitch(mock_coordinator, mock_device)
        await switch.async_turn_on()

        mock_device.set_solar.assert_not_called()

    @pytest.mark.parametrize(
        ("current_percent", "stored_data", "expected"),
        [
            pytest.param(None, {"last_grid_charge_percent": 40}, 40, id="restores_stored"),
            pytest.param(60, {"last_grid_charge_percent": 40}, 60, id="keeps_value_read_from_charger"),
            pytest.param(None, None, None, id="nothing_stored"),
        ],
    )
    @pytest.mark.asyncio
    async def test_restores_last_grid_charge_percent(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        current_percent: int | None,
        stored_data: dict | None,
        expected: int | None,
    ) -> None:
        """The percent to restore survives a restart, unless the charger already reported one."""
        mock_device.last_grid_charge_percent = current_percent
        switch = AndersenEvSolarChargeAlwaysSwitch(mock_coordinator, mock_device)
        last_extra_data = None if stored_data is None else RestoredExtraData(stored_data)

        with (
            patch.object(RestoreEntity, "async_added_to_hass", AsyncMock()),
            patch.object(switch, "async_get_last_extra_data", AsyncMock(return_value=last_extra_data)),
        ):
            await switch.async_added_to_hass()

        assert mock_device.last_grid_charge_percent == expected

    def test_extra_restore_state_data(self, mock_coordinator: MagicMock, mock_device: MagicMock) -> None:
        """The device's remembered percent is what gets stored."""
        mock_device.last_grid_charge_percent = 45
        switch = AndersenEvSolarChargeAlwaysSwitch(mock_coordinator, mock_device)

        assert switch.extra_restore_state_data.as_dict() == {"last_grid_charge_percent": 45}


class TestAndersenEvSolarChargeOutsideScheduleSwitch:
    """Test AndersenEvSolarChargeOutsideScheduleSwitch entity."""

    @pytest.mark.parametrize("enabled", [True, False])
    @pytest.mark.asyncio
    async def test_is_on_reads_solar_status(self, mock_coordinator, mock_device, enabled):
        mock_coordinator.data = [mock_device]
        mock_device._last_solar_status["solarChargeOutsideSchedules"] = enabled

        switch = AndersenEvSolarChargeOutsideScheduleSwitch(mock_coordinator, mock_device)

        assert switch.is_on is enabled
        assert switch.unique_id == "test_device_123_solar_solarChargeOutsideSchedules"

    @pytest.mark.parametrize(("method", "value"), [("async_turn_on", True), ("async_turn_off", False)])
    @pytest.mark.asyncio
    async def test_turn_on_off_calls_set_solar(self, mock_coordinator, mock_device, method, value):
        mock_coordinator.data = [mock_device]
        switch = AndersenEvSolarChargeOutsideScheduleSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()

        await getattr(switch, method)()

        mock_device.set_solar.assert_called_once_with(charge_outside_schedules=value)


@pytest.mark.parametrize(
    ("max_amps", "expected_kw"),
    [(32, 7.2), (24, 5.4), (16, 3.6), (10, 2.3), (30, 6.8), (6, 1.4)],
)
def test_amps_to_kw(max_amps: float, expected_kw: float) -> None:
    """kW is amps * 0.225 rounded half up to 0.1 kW, so 10 A matches the app's 2.3 kW."""
    assert amps_to_kw(max_amps) == expected_kw


@pytest.mark.parametrize(
    ("live_status", "expected"),
    [
        pytest.param({"sysUserMaxChargeAmpSet": 16, "cfgChargeAmpMax": 32}, 16, id="user_max"),
        pytest.param({"sysUserMaxChargeAmpSet": None, "cfgChargeAmpMax": 32}, 32, id="null_user_max"),
        pytest.param({"sysUserMaxChargeAmpSet": 0, "cfgChargeAmpMax": 32}, 32, id="zero_user_max"),
        pytest.param({"cfgChargeAmpMax": 32}, 32, id="no_user_max"),
        pytest.param({}, None, id="neither_known"),
    ],
)
def test_effective_max_amps(mock_device: MagicMock, live_status: dict, expected: float | None) -> None:
    """Grid source power uses the user max, falling back to the installer limit."""
    mock_device.live_status = live_status

    assert effective_max_amps(mock_device) == expected


@pytest.mark.parametrize(
    ("percent", "max_amps", "expected_kw"),
    [(0, 32, 0.0), (50, 32, 3.6), (100, 32, 7.2), (22, 32, 1.6), (100, 16, 3.6)],
)
def test_grid_source_percent_to_kw(percent: float, max_amps: float, expected_kw: float) -> None:
    """Percent from the API converts to kW of the charger's max power."""
    assert grid_source_percent_to_kw(percent, max_amps) == expected_kw


@pytest.mark.parametrize(
    ("power_kw", "max_amps", "expected_percent"),
    [(0.0, 32, 0), (3.6, 32, 50), (7.2, 32, 100), (1.4, 32, 19), (9.0, 32, 100), (1.8, 16, 50)],
)
def test_grid_source_kw_to_percent(power_kw: float, max_amps: float, expected_percent: int) -> None:
    """kW converts to a whole percent of the charger's max power, clamped to 0-100."""
    assert grid_source_kw_to_percent(power_kw, max_amps) == expected_percent


class TestAndersenEvGridSourcePowerNumber:
    """Test AndersenEvGridSourcePowerNumber entity."""

    @pytest.mark.asyncio
    async def test_value_converted_from_percent(self, mock_coordinator, mock_device):
        """Test the API percent is reported in kW."""
        mock_coordinator.data = [mock_device]
        mock_device._last_solar_status["solarMaxGridChargePercent"] = 75

        number = AndersenEvGridSourcePowerNumber(mock_coordinator, mock_device)
        assert number.native_value == 5.4

    @pytest.mark.parametrize(
        ("live_status", "percent"),
        [
            pytest.param({"sysHwVersion": "1.0"}, 50, id="no_max_amps"),
            pytest.param({"cfgChargeAmpMax": 32}, None, id="no_percent"),
        ],
    )
    @pytest.mark.asyncio
    async def test_value_unknown(self, mock_coordinator, mock_device, live_status, percent):
        """Test the value is unknown without both the percent and max amps."""
        mock_coordinator.data = [mock_device]
        mock_device.live_status = live_status
        mock_device._last_solar_status["solarMaxGridChargePercent"] = percent

        number = AndersenEvGridSourcePowerNumber(mock_coordinator, mock_device)
        assert number.native_value is None

    @pytest.mark.parametrize(
        ("value", "expected_percent", "charge_always"),
        [
            pytest.param(3.6, 50, True, id="valid"),
            pytest.param(1.4, 19, True, id="minimum"),
            pytest.param(0.0, 0, False, id="zero"),
            pytest.param(1.0, 0, False, id="below_minimum_rounded_to_zero"),
        ],
    )
    @pytest.mark.asyncio
    async def test_set_value_converted_to_percent(
        self, mock_coordinator, mock_device, value, expected_percent, charge_always
    ):
        """Test setting kW writes the equivalent percent to the API."""
        mock_coordinator.data = [mock_device]

        number = AndersenEvGridSourcePowerNumber(mock_coordinator, mock_device)
        number.hass = mock_coordinator.hass
        number.async_write_ha_state = MagicMock()
        await number.async_set_native_value(value)

        mock_device.set_solar.assert_called_once_with(
            charge_always=charge_always, max_grid_charge_percent=expected_percent
        )
        assert mock_device._last_solar_status["solarMaxGridChargePercent"] == 50
        mock_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_value_below_minimum_logs_warning(self, mock_coordinator, mock_device, caplog):
        """Test a value between 0 and 1.4 kW logs a warning."""
        mock_coordinator.data = [mock_device]

        number = AndersenEvGridSourcePowerNumber(mock_coordinator, mock_device)
        number.hass = mock_coordinator.hass
        number.async_write_ha_state = MagicMock()
        await number.async_set_native_value(0.5)

        assert "Invalid grid source power 0.5 kW" in caplog.text

    @pytest.mark.asyncio
    async def test_set_value_without_max_amps(self, mock_coordinator, mock_device):
        """Test nothing is written when max amps is unknown."""
        mock_coordinator.data = [mock_device]
        mock_device.live_status = {}

        number = AndersenEvGridSourcePowerNumber(mock_coordinator, mock_device)
        await number.async_set_native_value(3.6)

        mock_device.set_solar.assert_not_called()

    @pytest.mark.asyncio
    async def test_min_max(self, mock_coordinator, mock_device):
        """Test min is 0 and max is the charger's max power."""
        mock_coordinator.data = [mock_device]
        number = AndersenEvGridSourcePowerNumber(mock_coordinator, mock_device)
        assert number.native_min_value == 0.0
        assert number.native_max_value == 7.2
        assert number.native_step == 0.1

    @pytest.mark.asyncio
    async def test_follows_user_max(self, mock_coordinator: MagicMock, mock_device: MagicMock) -> None:
        """With a 16 A user max, percentages are of 3.6 kW rather than the installer's 7.2 kW."""
        mock_coordinator.data = [mock_device]
        mock_device.live_status["sysUserMaxChargeAmpSet"] = 16
        mock_device._last_solar_status["solarMaxGridChargePercent"] = 50

        number = AndersenEvGridSourcePowerNumber(mock_coordinator, mock_device)
        number.hass = mock_coordinator.hass
        assert number.native_max_value == 3.6
        assert number.native_value == 1.8

        with patch.object(number, "async_write_ha_state"):
            await number.async_set_native_value(2.7)

        mock_device.set_solar.assert_called_once_with(charge_always=True, max_grid_charge_percent=75)

    @pytest.mark.asyncio
    async def test_unique_id_and_unit(self, mock_coordinator, mock_device):
        """Test unique ID and unit."""
        number = AndersenEvGridSourcePowerNumber(mock_coordinator, mock_device)
        assert number.unique_id == "test_device_123_grid_source_power"
        assert number.native_unit_of_measurement == UnitOfPower.KILO_WATT


SOLAR_ENTITY_CLASSES = [
    pytest.param(AndersenEvBoostSwitch, id="boost_switch"),
    pytest.param(AndersenEvSolarChargeAlwaysSwitch, id="charge_always_switch"),
    pytest.param(AndersenEvSolarChargeOutsideScheduleSwitch, id="charge_outside_schedule_switch"),
    pytest.param(AndersenEvGridSourcePowerNumber, id="grid_source_power_number"),
]


async def _write_solar_entity(entity) -> None:
    if isinstance(entity, AndersenEvGridSourcePowerNumber):
        await entity.async_set_native_value(3.6)
    else:
        await entity.async_turn_on()


@pytest.mark.parametrize(
    ("entity_class", "translation_key"),
    [
        pytest.param(AndersenEvBoostSwitch, "boost", id="boost_switch"),
        pytest.param(AndersenEvSolarChargeAlwaysSwitch, "solar_charge_always", id="charge_always_switch"),
        pytest.param(
            AndersenEvSolarChargeOutsideScheduleSwitch,
            "solar_charge_outside_schedules",
            id="charge_outside_schedule_switch",
        ),
        pytest.param(AndersenEvGridSourcePowerNumber, "grid_source_power", id="grid_source_power_number"),
    ],
)
def test_solar_entity_name_comes_from_translation(
    mock_coordinator: MagicMock, mock_device: MagicMock, entity_class: type, translation_key: str
) -> None:
    """Home Assistant adds the device name, so the entity name must not include it."""
    entity = entity_class(mock_coordinator, mock_device)

    assert entity.has_entity_name is True
    assert entity.translation_key == translation_key
    assert entity.device_info["name"] == "Test Charger"


@pytest.mark.parametrize("entity_class", SOLAR_ENTITY_CLASSES)
@pytest.mark.parametrize(
    ("disconnected_for", "available"),
    [
        pytest.param(None, True, id="connected"),
        pytest.param(timedelta(minutes=6), False, id="disconnected_over_five_minutes"),
    ],
)
def test_solar_entity_available_follows_live_status(
    mock_coordinator: MagicMock,
    mock_device: MagicMock,
    entity_class: type,
    disconnected_for: timedelta | None,
    available: bool,
) -> None:
    """The entity goes unavailable once the subscription has been down for five minutes."""
    mock_coordinator.data = [mock_device]
    mock_device.subscription_disconnected_since = (
        None if disconnected_for is None else dt_util.utcnow() - disconnected_for
    )

    assert entity_class(mock_coordinator, mock_device).available is available


@pytest.mark.parametrize("entity_class", SOLAR_ENTITY_CLASSES)
@pytest.mark.parametrize(
    ("set_solar", "translation_key"),
    [
        pytest.param(AsyncMock(return_value=False), "solar_update_failed", id="rejected"),
        pytest.param(AsyncMock(side_effect=AndersenAuthError("expired")), "solar_update_error", id="api_error"),
    ],
)
@pytest.mark.asyncio
async def test_solar_entity_write_failure_raises(
    mock_coordinator: MagicMock,
    mock_device: MagicMock,
    entity_class: type,
    set_solar: AsyncMock,
    translation_key: str,
) -> None:
    """A write the charger didn't apply is reported to the caller and leaves the cache untouched."""
    mock_coordinator.data = [mock_device]
    mock_device.set_solar = set_solar
    cached = dict(mock_device._last_solar_status)
    entity = entity_class(mock_coordinator, mock_device)

    with pytest.raises(HomeAssistantError) as exc_info:
        await _write_solar_entity(entity)

    assert exc_info.value.translation_key == translation_key
    assert mock_device._last_solar_status == cached
    mock_coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_number_setup_adds_entities_for_new_devices(mock_coordinator: MagicMock, mock_device: MagicMock) -> None:
    """Chargers discovered after setup get their number entities without a reload."""
    mock_coordinator.data = []
    entry = MagicMock()
    entry.runtime_data = mock_coordinator
    async_add_entities = MagicMock()

    await async_setup_entry(mock_coordinator.hass, entry, async_add_entities)
    async_add_entities.assert_called_once_with([])
    listener = mock_coordinator.async_add_listener.call_args.args[0]

    mock_coordinator.data = [mock_device]
    listener()
    listener()

    assert async_add_entities.call_count == 2
    (added,) = async_add_entities.call_args.args
    assert [entity.unique_id for entity in added] == [
        "test_device_123_grid_source_power",
        "test_device_123_user_max_current",
        "test_device_123_user_max_power",
    ]
