"""Tests for Andersen EV solar control entities."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import UnitOfPower
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity

from andersen_ev.grid_source import (
    grid_source_kw_to_percent,
    grid_source_percent_to_kw,
    max_grid_source_power_kw,
)
from andersen_ev.number import AndersenEvGridSourcePowerNumber
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
    device.last_status = {
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
    device.update_solar_status_cache.side_effect = device._last_solar_status.update
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
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_boost_switch_turn_off(self, mock_coordinator, mock_device):
        """Test turning off boost switch."""
        mock_coordinator.data = [mock_device]

        switch = AndersenEvBoostSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()
        await switch.async_turn_off()

        mock_device.set_solar.assert_called_once_with(boost=False)
        mock_coordinator.async_request_refresh.assert_called_once()

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
        assert mock_device._last_solar_status["solarChargeAlways"] is True
        assert mock_device._last_solar_status["solarMaxGridChargePercent"] == expected_percent

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
        assert mock_device._last_solar_status["solarChargeAlways"] is False
        assert mock_device._last_solar_status["solarMaxGridChargePercent"] == 0

    @pytest.mark.asyncio
    async def test_turn_on_without_max_amps(self, mock_coordinator: MagicMock, mock_device: MagicMock) -> None:
        """Nothing is sent when the minimum percent can't be worked out."""
        mock_coordinator.data = [mock_device]
        mock_device.last_status = {}

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
    [(32, 7.2), (16, 3.6), (30, 6.8), (6, 1.4)],
)
def test_max_grid_source_power_kw(max_amps: float, expected_kw: float) -> None:
    """Max power is max amps * 0.225, rounded to the nearest 0.1 kW."""
    assert max_grid_source_power_kw(max_amps) == expected_kw


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
        ("last_status", "percent"),
        [
            pytest.param({"sysHwVersion": "1.0"}, 50, id="no_max_amps"),
            pytest.param({"cfgChargeAmpMax": 32}, None, id="no_percent"),
        ],
    )
    @pytest.mark.asyncio
    async def test_value_unknown(self, mock_coordinator, mock_device, last_status, percent):
        """Test the value is unknown without both the percent and max amps."""
        mock_coordinator.data = [mock_device]
        mock_device.last_status = last_status
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
        assert mock_device._last_solar_status["solarMaxGridChargePercent"] == expected_percent
        assert mock_device._last_solar_status["solarChargeAlways"] is charge_always
        mock_coordinator.async_request_refresh.assert_called_once()

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
        mock_device.last_status = {}

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
    async def test_unique_id_and_unit(self, mock_coordinator, mock_device):
        """Test unique ID and unit."""
        number = AndersenEvGridSourcePowerNumber(mock_coordinator, mock_device)
        assert number.unique_id == "test_device_123_grid_source_power"
        assert number.native_unit_of_measurement == UnitOfPower.KILO_WATT
