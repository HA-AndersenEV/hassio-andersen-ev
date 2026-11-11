"""Tests for the Andersen EV sensor platform."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from andersen_ev.charge_status import CHARGE_STATUS_OPTIONS
from andersen_ev.sensor import (
    AndersenEvChargerStatusSensor,
    AndersenEvChargeStatusSensor,
    AndersenEvConnectorSensor,
    AndersenEvCostSensor,
    AndersenEvDailyEnergySensor,
    AndersenEvEnergySensor,
    AndersenEvLifetimeEnergySensor,
    AndersenEvLiveSensor,
    AndersenEvSolarPowerSensor,
    DailyEnergyExtraStoredData,
    _build_entities_for_device,
    async_setup_entry,
)

# Disconnected for longer than LIVE_STATUS_TIMEOUT, so live entities are unavailable.
DISCONNECTED_LONG_AGO = dt_util.utcnow() - timedelta(minutes=6)


def _make_device(
    device_id="device_1",
    friendly_name="Device",
    live_status=None,
    disconnected_since=None,
    last_charge=None,
    currency=None,
):
    """Build a mock KonnectDevice."""
    device = MagicMock()
    device.device_id = device_id
    device.friendly_name = friendly_name
    device.live_status = live_status or {}
    device.last_rest_info = {}
    device.subscription_disconnected_since = disconnected_since
    device.last_charge = last_charge
    device.currency = currency
    device.get_last_charge = AsyncMock(return_value=last_charge)
    device.get_device_info = AsyncMock(return_value=None)
    return device


def _make_coordinator(devices=None, last_update_success=True):
    """Build a mock AndersenEvCoordinator."""
    coordinator = MagicMock()
    coordinator.data = devices or []
    coordinator.last_update_success = last_update_success
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


class TestAsyncSetupEntry:
    """Tests for async_setup_entry()."""

    @pytest.mark.asyncio
    async def test_creates_all_sensor_types_per_device(self):
        device_a = _make_device(device_id="device_1")
        device_b = _make_device(device_id="device_2")
        coordinator = _make_coordinator([device_a, device_b])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        entities = async_add_entities.call_args.args[0]
        assert len(entities) == 50
        assert sum(isinstance(e, AndersenEvEnergySensor) for e in entities) == 8
        assert sum(isinstance(e, AndersenEvCostSensor) for e in entities) == 8
        assert sum(isinstance(e, AndersenEvConnectorSensor) for e in entities) == 2
        assert sum(isinstance(e, AndersenEvChargerStatusSensor) for e in entities) == 2
        assert sum(isinstance(e, AndersenEvChargeStatusSensor) for e in entities) == 12
        assert sum(isinstance(e, AndersenEvLiveSensor) for e in entities) == 14
        assert sum(isinstance(e, AndersenEvSolarPowerSensor) for e in entities) == 2
        assert sum(isinstance(e, AndersenEvLifetimeEnergySensor) for e in entities) == 2
        assert sum(isinstance(e, AndersenEvDailyEnergySensor) for e in entities) == 2
        assert not any(e.unique_id.endswith("_grid_power") and "sys" not in e.unique_id for e in entities)

    @pytest.mark.asyncio
    async def test_no_devices_creates_no_entities(self):
        coordinator = _make_coordinator([])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        assert async_add_entities.call_args.args[0] == []


class TestDynamicDevices:
    """Tests for the dynamic-devices coordinator listener registered in async_setup_entry()."""

    @pytest.mark.asyncio
    async def test_new_device_added_without_reload(self):
        device_a = _make_device(device_id="device_1")
        coordinator = _make_coordinator([device_a])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        assert async_add_entities.call_count == 1
        entry.async_on_unload.assert_called_once()
        listener = coordinator.async_add_listener.call_args.args[0]

        device_b = _make_device(device_id="device_2")
        coordinator.data = [device_a, device_b]
        listener()

        assert async_add_entities.call_count == 2
        new_entities = async_add_entities.call_args.args[0]
        assert len(new_entities) == 25
        assert all(entity._device.device_id == "device_2" for entity in new_entities)

    @pytest.mark.asyncio
    async def test_no_new_devices_does_not_call_add_entities_again(self):
        device_a = _make_device(device_id="device_1")
        coordinator = _make_coordinator([device_a])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)
        listener = coordinator.async_add_listener.call_args.args[0]

        listener()

        assert async_add_entities.call_count == 1

    @pytest.mark.asyncio
    async def test_device_readded_after_disappearing_gets_new_entities(self):
        """A device_id that drops out and reappears must not be treated as already known."""
        device_a = _make_device(device_id="device_1")
        coordinator = _make_coordinator([device_a])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)
        listener = coordinator.async_add_listener.call_args.args[0]

        # device_a drops out of the coordinator's data for a cycle.
        coordinator.data = []
        listener()
        assert async_add_entities.call_count == 1

        # ...then reappears with the same device_id.
        coordinator.data = [device_a]
        listener()

        assert async_add_entities.call_count == 2
        new_entities = async_add_entities.call_args.args[0]
        assert len(new_entities) == 25
        assert all(entity._device.device_id == "device_1" for entity in new_entities)


class TestBaseSensorInit:
    """Tests for AndersenEvBaseSensor.__init__() via AndersenEvEnergySensor."""

    def test_sets_attributes(self):
        device = _make_device(device_id="device_1", friendly_name="My Charger")
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_unique_id == "device_1_energy"
        assert sensor._attr_translation_key == "energy"
        assert sensor._attr_device_info["name"] == "My Charger"
        assert sensor._attr_has_entity_name is True


class TestBaseSensorUpdateDeviceInfoFromStatus:
    """Tests for AndersenEvBaseSensor._update_device_info_from_status()."""

    def test_known_product_id_maps_to_the_marketed_model_name(self):
        device = _make_device(live_status={"sysProductName": "Thurlestone", "sysProductId": 30})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_device_info["model"] == "A2"

    def test_integer_product_id_rendered_as_string(self):
        device = _make_device(live_status={"sysProductId": 99})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_device_info["model"] == "99"

    def test_hw_fw_and_serial_populated(self):
        device = _make_device(live_status={"sysHwVersion": "4", "sysFwVersion": "314", "konnectSerial": "1234567890"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_device_info["hw_version"] == "4"
        assert sensor._attr_device_info["sw_version"] == "314"
        assert sensor._attr_device_info["serial_number"] == "1234567890"

    def test_no_status_keeps_default_device_info(self):
        device = _make_device(live_status=None)
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert "model" not in sensor._attr_device_info
        assert "hw_version" not in sensor._attr_device_info
        assert "sw_version" not in sensor._attr_device_info
        assert sensor._attr_device_info["serial_number"] == device.device_id


class TestBaseSensorAvailable:
    """Tests for AndersenEvBaseSensor.available."""

    def test_available_when_update_success_and_last_charge_present(self):
        device = _make_device(last_charge={"chargeEnergyTotal": 1.0})
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor.available is True

    def test_unavailable_when_last_charge_none(self):
        device = _make_device(last_charge=None)
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor.available is False

    def test_unavailable_when_coordinator_update_failed(self):
        device = _make_device(last_charge={"chargeEnergyTotal": 1.0})
        coordinator = _make_coordinator([device], last_update_success=False)
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor.available is False


class TestBaseSensorLastReset:
    """Tests for AndersenEvBaseSensor.last_reset."""

    def test_keeps_session_start_wall_clock_in_the_ha_timezone(self):
        """The API sends a naive local time, so the wall clock must survive the conversion.

        Pinned against a non-UTC zone deliberately: with the timezone left at UTC this
        assertion would also pass if the naive value were wrongly treated as UTC.
        """
        original_tz = dt_util.DEFAULT_TIME_ZONE
        dt_util.set_default_time_zone(dt_util.get_time_zone("America/New_York"))
        try:
            device = _make_device(last_charge={"startDateTimeLocal": "2024-02-19T10:30:00"})
            coordinator = _make_coordinator([device])
            sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

            last_reset = sensor.last_reset

            assert last_reset is not None
            assert last_reset.tzinfo is not None
            assert last_reset.utcoffset() == timedelta(hours=-5)
            assert (last_reset.year, last_reset.month, last_reset.day) == (2024, 2, 19)
            assert (last_reset.hour, last_reset.minute) == (10, 30)
        finally:
            dt_util.set_default_time_zone(original_tz)

    def test_returns_none_when_no_last_charge(self):
        device = _make_device(last_charge=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor.last_reset is None

    def test_returns_none_when_key_missing(self):
        device = _make_device(last_charge={"chargeEnergyTotal": 15.5})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor.last_reset is None

    def test_returns_none_when_unparseable(self):
        device = _make_device(last_charge={"startDateTimeLocal": "not-a-date"})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor.last_reset is None


class TestEnergySensorNativeValue:
    """Tests for AndersenEvEnergySensor.native_value."""

    def test_returns_value_from_last_charge(self):
        device = _make_device(last_charge={"chargeEnergyTotal": 15.5})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor.native_value == 15.5

    def test_returns_none_when_key_missing(self):
        device = _make_device(last_charge={"other": 1})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor.native_value is None

    def test_returns_none_when_no_last_charge(self):
        device = _make_device(last_charge=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor.native_value is None

    def test_reflects_device_last_charge_updated_after_construction(self):
        """native_value must read live, not a snapshot taken at __init__."""
        device = _make_device(last_charge=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")
        assert sensor.native_value is None

        device.last_charge = {"chargeEnergyTotal": 42.0}

        assert sensor.native_value == 42.0


class TestCostSensorNativeValue:
    """Tests for AndersenEvCostSensor.native_value."""

    def test_returns_value_from_last_charge(self):
        device = _make_device(last_charge={"chargeCostTotal": 4.5})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")

        assert sensor.native_value == 4.5

    def test_returns_none_when_key_missing(self):
        device = _make_device(last_charge={"other": 1})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")

        assert sensor.native_value is None

    def test_returns_none_when_no_last_charge(self):
        device = _make_device(last_charge=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")

        assert sensor.native_value is None

    def test_exposes_last_reset_from_session_start(self):
        device = _make_device(last_charge={"chargeCostTotal": 4.5, "startDateTimeLocal": "2024-02-19T10:30:00"})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")

        last_reset = sensor.last_reset

        assert last_reset is not None
        assert last_reset.tzinfo is not None


class TestCostSensorCurrency:
    """Tests for AndersenEvCostSensor currency handling."""

    def test_native_unit_of_measurement_uses_device_currency(self):
        device = _make_device(currency="EUR")
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")

        assert sensor.native_unit_of_measurement == "EUR"

    def test_native_unit_of_measurement_defaults_to_gbp_when_unknown(self):
        device = _make_device(currency=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")

        assert sensor.native_unit_of_measurement == "GBP"

    @pytest.mark.asyncio
    async def test_async_added_to_hass_fetches_currency_when_unknown(self):
        device = _make_device(currency=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")

        await sensor.async_added_to_hass()

        device.get_device_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_skips_fetch_when_currency_known(self):
        device = _make_device(currency="EUR")
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")

        await sensor.async_added_to_hass()

        device.get_device_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_unit_reflects_fetched_currency_after_added_to_hass(self, mock_device, graphql_device_info_response):
        """End-to-end: a real KonnectDevice.get_device_info() call actually changes the reported unit."""
        graphql_device_info_response["getDevice"]["deviceInfo"]["currency"] = "USD"
        mock_device.graphql_client.execute_query = AsyncMock(return_value=graphql_device_info_response)
        coordinator = _make_coordinator([mock_device])
        sensor = AndersenEvCostSensor(coordinator, mock_device, "cost", "chargeCostTotal")

        assert sensor.native_unit_of_measurement == "GBP"

        await sensor.async_added_to_hass()

        assert sensor.native_unit_of_measurement == "USD"


class TestChargerStatusSensor:
    """Tests for AndersenEvChargerStatusSensor."""

    def test_entity_description(self) -> None:
        device = _make_device()
        sensor = AndersenEvChargerStatusSensor(_make_coordinator([device]), device)

        assert sensor.unique_id == "device_1_charge_status"
        assert sensor.translation_key == "charge_status"
        assert sensor.device_class == SensorDeviceClass.ENUM
        assert sensor.options == CHARGE_STATUS_OPTIONS

    def test_unknown_before_live_status(self) -> None:
        device = _make_device()
        sensor = AndersenEvChargerStatusSensor(_make_coordinator([device]), device)

        assert sensor.native_value is None

    @pytest.mark.parametrize(
        ("status", "info", "expected"),
        [
            pytest.param({"evseState": 3}, {}, "charging", id="charging"),
            pytest.param({"evseState": 255, "sysPlugged": True, "sysForceStop": True}, {}, "stopped", id="stopped"),
            pytest.param({"evseState": 255, "online": False}, {}, "offline", id="offline"),
            pytest.param(
                {"evseState": 3, "sysFwVersion": 520},
                {"NotifiedDeviceOfflineAt": "2026-10-21T10:00:00.000Z"},
                "offline",
                id="offline_from_rest_info",
            ),
        ],
    )
    def test_native_value(self, status: dict, info: dict, expected: str) -> None:
        device = _make_device(live_status={"online": True, "lastEvent": dt_util.utcnow().isoformat(), **status})
        device.last_rest_info = info
        sensor = AndersenEvChargerStatusSensor(_make_coordinator([device]), device)

        assert sensor.native_value == expected

    @pytest.mark.parametrize(
        ("disconnected_since", "expected"),
        [
            pytest.param(None, True, id="connected"),
            pytest.param(DISCONNECTED_LONG_AGO, False, id="disconnected"),
        ],
    )
    def test_available(self, disconnected_since: datetime | None, expected: bool) -> None:
        device = _make_device(disconnected_since=disconnected_since)
        sensor = AndersenEvChargerStatusSensor(_make_coordinator([device]), device)

        assert sensor.available is expected


class TestConnectorSensorInit:
    """Tests for AndersenEvConnectorSensor.__init__()."""

    def test_default_translation_key(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_translation_key == "connector"


class TestConnectorSensorUpdateDeviceInfoFromStatus:
    """Tests for AndersenEvConnectorSensor._update_device_info_from_status()."""

    def test_known_product_id_maps_to_the_marketed_model_name(self):
        device = _make_device(live_status={"sysProductName": "Thurlestone", "sysProductId": 30})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_device_info["model"] == "A2"

    def test_integer_product_id_rendered_as_string(self):
        device = _make_device(live_status={"sysProductId": 99})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_device_info["model"] == "99"

    def test_hw_fw_and_serial_populated(self):
        device = _make_device(live_status={"sysHwVersion": "4", "sysFwVersion": "314", "konnectSerial": "1234567890"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_device_info["hw_version"] == "4"
        assert sensor._attr_device_info["sw_version"] == "314"
        assert sensor._attr_device_info["serial_number"] == "1234567890"

    def test_no_status_keeps_default_device_info(self):
        device = _make_device(live_status=None)
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert "model" not in sensor._attr_device_info
        assert "hw_version" not in sensor._attr_device_info
        assert "sw_version" not in sensor._attr_device_info
        assert sensor._attr_device_info["serial_number"] == device.device_id


class TestConnectorSensorAvailable:
    """Tests for AndersenEvConnectorSensor.available."""

    def test_available_when_device_found_and_connected(self):
        device = _make_device()
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.available is True

    def test_unavailable_when_disconnected_too_long(self):
        device = _make_device(disconnected_since=DISCONNECTED_LONG_AGO)
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.available is False

    def test_unavailable_when_device_not_found(self):
        device = _make_device(device_id="device_1")
        coordinator = _make_coordinator([], last_update_success=True)
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.available is False


class TestConnectorSensorNativeValue:
    """Tests for AndersenEvConnectorSensor.native_value."""

    @pytest.mark.parametrize(
        ("evse_state", "expected"),
        [
            ("1", "Ready"),
            (1, "Ready"),
            ("2", "Connected"),
            (2, "Connected"),
            ("3", "Charging"),
            (3, "Charging"),
            ("4", "Error"),
            (4, "Error"),
            ("254", "Sleeping"),
            (254, "Sleeping"),
            ("255", "Disabled"),
            (255, "Disabled"),
            ("99", "unknown"),
            (99, "unknown"),
        ],
    )
    def test_maps_evse_state(self, evse_state, expected):
        device = _make_device(live_status={"evseState": evse_state})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.native_value == expected

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            pytest.param({"evseState": 255, "sysPlugged": False}, "Unplugged", id="disabled_unplugged"),
            pytest.param({"evseState": 254, "sysPlugged": False}, "Unplugged", id="sleeping_unplugged"),
            pytest.param({"evseState": 255, "sysPlugged": True}, "Disabled", id="disabled_plugged"),
            pytest.param({"evseState": 3, "sysPlugged": False}, "Charging", id="charging_ignores_plug"),
        ],
    )
    def test_plug_state(self, status: dict, expected: str) -> None:
        device = _make_device(live_status=status)
        sensor = AndersenEvConnectorSensor(_make_coordinator([device]), device)

        assert sensor.native_value == expected

    def test_no_status_returns_default_unknown(self):
        device = _make_device(live_status=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.native_value == "unknown"

    def test_no_evse_state_key_keeps_previous_state(self):
        device = _make_device(live_status={"other": True})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.native_value == "unknown"

    def test_updates_device_reference_from_coordinator_data(self):
        original_device = _make_device(device_id="device_1", live_status=None)
        updated_device = _make_device(device_id="device_1", live_status={"evseState": "3"})
        coordinator = _make_coordinator([updated_device])
        sensor = AndersenEvConnectorSensor(coordinator, original_device)

        assert sensor.native_value == "Charging"
        assert sensor._device is updated_device

    def test_repeated_same_state_does_not_relog(self):
        device = _make_device(live_status={"evseState": "3"})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        first = sensor.native_value
        second = sensor.native_value

        assert first == second == "Charging"


class TestChargeStatusSensorInit:
    """Tests for AndersenEvChargeStatusSensor.__init__()."""

    def test_sets_optional_attributes_when_provided(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "charge_power",
            "chargePower",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfPower.WATT,
        )

        assert sensor._attr_device_class == SensorDeviceClass.POWER
        assert sensor._attr_state_class == SensorStateClass.MEASUREMENT
        assert sensor._attr_native_unit_of_measurement == UnitOfPower.WATT

    def test_optional_attributes_omitted_when_absent(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor._attr_unique_id == f"{device.device_id}_charge_power"


class TestChargeStatusSensorUpdateDeviceInfoFromStatus:
    """Tests for AndersenEvChargeStatusSensor._update_device_info_from_status()."""

    def test_known_product_id_maps_to_the_marketed_model_name(self):
        device = _make_device(live_status={"sysProductName": "Thurlestone", "sysProductId": 30})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor._attr_device_info["model"] == "A2"

    def test_integer_product_id_rendered_as_string(self):
        device = _make_device(live_status={"sysProductId": 99})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor._attr_device_info["model"] == "99"

    def test_hw_fw_and_serial_populated(self):
        device = _make_device(live_status={"sysHwVersion": "4", "sysFwVersion": "314", "konnectSerial": "1234567890"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor._attr_device_info["hw_version"] == "4"
        assert sensor._attr_device_info["sw_version"] == "314"
        assert sensor._attr_device_info["serial_number"] == "1234567890"

    def test_no_status_keeps_default_device_info(self):
        device = _make_device(live_status=None)
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert "model" not in sensor._attr_device_info
        assert "hw_version" not in sensor._attr_device_info
        assert "sw_version" not in sensor._attr_device_info
        assert sensor._attr_device_info["serial_number"] == device.device_id


class TestChargeStatusSensorAvailable:
    """Tests for ChargeStatusSensor.available."""

    @pytest.mark.parametrize(
        "live_status",
        [
            pytest.param({"chargeStatus": {"chargePower": 1000}, "sysGridPower": 1.2}, id="with_value"),
            pytest.param({"other": True}, id="value_missing"),
            pytest.param(None, id="before_first_payload"),
        ],
    )
    def test_available_while_connected(self, live_status):
        """The value may be unknown, but the entity stays available while the subscription is up."""
        device = _make_device(live_status=live_status)
        sensor = AndersenEvChargeStatusSensor(_make_coordinator([device]), device, "charge_power", "chargePower")

        assert sensor.available is True

    @pytest.mark.parametrize(
        ("disconnected_for", "available"),
        [
            pytest.param(timedelta(minutes=4), True, id="under_five_minutes"),
            pytest.param(timedelta(minutes=6), False, id="over_five_minutes"),
        ],
    )
    def test_unavailable_after_five_minutes_disconnected(self, disconnected_for, available):
        device = _make_device(live_status={"sysGridPower": 1.2}, disconnected_since=dt_util.utcnow() - disconnected_for)
        sensor = AndersenEvChargeStatusSensor(_make_coordinator([device]), device, "charge_power", "chargePower")

        assert sensor.available is available

    def test_unavailable_when_device_not_found(self):
        device = _make_device(device_id="device_1")
        coordinator = _make_coordinator([])
        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor.available is False


class TestChargeStatusSensorNativeValue:
    """Tests for AndersenEvChargeStatusSensor.native_value."""

    def test_returns_value_from_charge_status(self):
        device = _make_device(live_status={"chargeStatus": {"chargePower": 2500}})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "charge_power",
            "chargePower",
            device_class=SensorDeviceClass.POWER,
        )

        assert sensor.native_value == 2500

    def test_returns_none_when_data_key_missing(self):
        device = _make_device(live_status={"chargeStatus": {}})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "charge_power",
            "chargePower",
            device_class=SensorDeviceClass.POWER,
        )

        assert sensor.native_value is None

    def test_returns_none_when_charge_status_missing(self):
        device = _make_device(live_status={"other": True})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "charge_power",
            "chargePower",
            device_class=SensorDeviceClass.POWER,
        )

        assert sensor.native_value is None

    def test_returns_none_when_no_status(self):
        device = _make_device(live_status=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "charge_power",
            "chargePower",
            device_class=SensorDeviceClass.POWER,
        )

        assert sensor.native_value is None

    def test_parses_valid_timestamp(self):
        device = _make_device(live_status={"chargeStatus": {"start": "2024-02-19T10:30:00Z"}})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "session_start",
            "start",
            device_class=SensorDeviceClass.TIMESTAMP,
        )

        value = sensor.native_value

        assert value.year == 2024
        assert value.month == 2
        assert value.day == 19

    def test_invalid_timestamp_returns_none(self):
        device = _make_device(live_status={"chargeStatus": {"start": "not-a-real-timestamp!!"}})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "session_start",
            "start",
            device_class=SensorDeviceClass.TIMESTAMP,
        )

        assert sensor.native_value is None

    def test_updates_device_reference_from_coordinator_data(self):
        original_device = _make_device(device_id="device_1", live_status=None)
        updated_device = _make_device(device_id="device_1", live_status={"chargeStatus": {"chargePower": 500}})
        coordinator = _make_coordinator([updated_device])
        sensor = AndersenEvChargeStatusSensor(
            coordinator,
            original_device,
            "charge_power",
            "chargePower",
            device_class=SensorDeviceClass.POWER,
        )

        assert sensor.native_value == 500
        assert sensor._device is updated_device


REALTIME_ENERGY_SENSORS = [
    ("current_charge_energy", "chargeEnergyTotal"),
    ("current_solar_energy", "solarEnergyTotal"),
    ("current_grid_energy", "gridEnergyTotal"),
]


def _realtime_energy_sensor(device, sensor_type="current_charge_energy", data_key="chargeEnergyTotal"):
    """Build a realtime energy sensor the way async_setup_entry declares it."""
    return AndersenEvChargeStatusSensor(
        _make_coordinator([device]),
        device,
        sensor_type,
        data_key,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL,
        UnitOfEnergy.KILO_WATT_HOUR,
    )


class TestChargeStatusSensorLastReset:
    """Tests for AndersenEvChargeStatusSensor.last_reset.

    Without a last_reset, Home Assistant cannot tell a new session from a drop in the value, so it
    subtracts the finished session from long-term statistics when the next one starts at zero.
    """

    @pytest.mark.parametrize(("sensor_type", "data_key"), REALTIME_ENERGY_SENSORS)
    def test_energy_total_sensors_report_the_session_start(self, sensor_type, data_key):
        device = _make_device(live_status={"chargeStatus": {"start": "2026-10-02T07:12:41+01:00", data_key: 4.08}})
        sensor = _realtime_energy_sensor(device, sensor_type, data_key)

        assert sensor.last_reset == datetime(2026, 10, 2, 6, 12, 41, tzinfo=UTC)
        assert sensor.last_reset.tzinfo is not None

    def test_a_new_session_changes_last_reset(self):
        device = _make_device(live_status={"chargeStatus": {"start": "2026-10-02T07:12:41+01:00"}})
        sensor = _realtime_energy_sensor(device)
        first = sensor.last_reset

        device.live_status = {"chargeStatus": {"start": "2026-10-03T00:30:05+01:00"}}

        assert sensor.last_reset != first
        assert sensor.last_reset == datetime(2026, 10, 2, 23, 30, 5, tzinfo=UTC)

    def test_same_session_keeps_last_reset_stable(self):
        device = _make_device(
            live_status={"chargeStatus": {"start": "2026-10-02T07:12:41+01:00", "chargeEnergyTotal": 1}}
        )
        sensor = _realtime_energy_sensor(device)
        first = sensor.last_reset
        assert first is not None

        device.live_status = {"chargeStatus": {"start": "2026-10-02T07:12:41+01:00", "chargeEnergyTotal": 2}}

        assert sensor.last_reset == first

    def test_uninitialised_clock_after_a_restart_is_a_normal_new_session(self):
        """A restarted charger reports a 2000-01-01 start with its counters back at zero.

        That is a genuine new cycle, so it passes through like any other start time.
        """
        device = _make_device(live_status={"chargeStatus": {"start": "2000-01-01T01:00:21+01:00"}})
        sensor = _realtime_energy_sensor(device)

        assert sensor.last_reset == datetime(2000, 1, 1, 0, 0, 21, tzinfo=UTC)

    def test_start_without_an_offset_is_read_as_local_time(self):
        original_tz = dt_util.DEFAULT_TIME_ZONE
        dt_util.set_default_time_zone(dt_util.get_time_zone("America/New_York"))
        try:
            device = _make_device(live_status={"chargeStatus": {"start": "2026-10-02T07:12:41"}})
            sensor = _realtime_energy_sensor(device)

            last_reset = sensor.last_reset

            assert last_reset is not None
            assert last_reset.utcoffset() == timedelta(hours=-4)
            assert (last_reset.hour, last_reset.minute) == (7, 12)
        finally:
            dt_util.set_default_time_zone(original_tz)

    def test_power_sensor_has_no_last_reset(self):
        """Home Assistant rejects last_reset on anything but an energy total."""
        device = _make_device(live_status={"chargeStatus": {"start": "2026-10-02T07:12:41+01:00", "chargePower": 7}})
        sensor = AndersenEvChargeStatusSensor(
            _make_coordinator([device]),
            device,
            "charge_power",
            "chargePower",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            UnitOfPower.WATT,
        )

        assert sensor.last_reset is None

    def test_session_start_sensor_has_no_last_reset(self):
        device = _make_device(live_status={"chargeStatus": {"start": "2026-10-02T07:12:41+01:00"}})
        sensor = AndersenEvChargeStatusSensor(
            _make_coordinator([device]), device, "session_start", "start", SensorDeviceClass.TIMESTAMP
        )

        assert sensor.last_reset is None

    @pytest.mark.parametrize(
        "last_status",
        [
            None,
            {"other": True},
            {"chargeStatus": {}},
            {"chargeStatus": {"start": None}},
            {"chargeStatus": {"start": ""}},
            {"chargeStatus": {"start": "not-a-date"}},
            {"chargeStatus": {"start": 12345}},
        ],
    )
    def test_returns_none_when_start_is_missing_or_unusable(self, last_status):
        device = _make_device(live_status=last_status)
        sensor = _realtime_energy_sensor(device)

        assert sensor.last_reset is None

    def test_follows_the_coordinators_current_device(self):
        """The coordinator replaces device objects between refreshes, so a stale reference must not stick."""
        stale = _make_device(device_id="device_1", live_status=None)
        fresh = _make_device(device_id="device_1", live_status={"chargeStatus": {"start": "2026-10-02T07:12:41+01:00"}})
        sensor = AndersenEvChargeStatusSensor(
            _make_coordinator([fresh]),
            stale,
            "current_charge_energy",
            "chargeEnergyTotal",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL,
            UnitOfEnergy.KILO_WATT_HOUR,
        )

        assert sensor.last_reset == datetime(2026, 10, 2, 6, 12, 41, tzinfo=UTC)


class TestLiveSensorUpdateDeviceInfoFromStatus:
    """Tests for AndersenEvLiveSensor._update_device_info_from_status()."""

    def test_known_product_id_maps_to_the_marketed_model_name(self):
        device = _make_device(live_status={"sysProductName": "Thurlestone", "sysProductId": 30})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor._attr_device_info["model"] == "A2"

    def test_integer_product_id_rendered_as_string(self):
        device = _make_device(live_status={"sysProductId": 99})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor._attr_device_info["model"] == "99"

    def test_hw_fw_and_serial_populated(self):
        device = _make_device(live_status={"sysHwVersion": "4", "sysFwVersion": "314", "konnectSerial": "1234567890"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor._attr_device_info["hw_version"] == "4"
        assert sensor._attr_device_info["sw_version"] == "314"
        assert sensor._attr_device_info["serial_number"] == "1234567890"

    def test_no_status_keeps_default_device_info(self):
        device = _make_device(live_status=None)
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert "model" not in sensor._attr_device_info
        assert "hw_version" not in sensor._attr_device_info
        assert "sw_version" not in sensor._attr_device_info
        assert sensor._attr_device_info["serial_number"] == device.device_id


class TestLiveSensorInit:
    """Tests for AndersenEvLiveSensor.__init__()."""

    def test_sets_optional_attributes_when_provided(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(
            coordinator,
            device,
            "sys_grid_power",
            "sysGridPower",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfPower.KILO_WATT,
        )

        assert sensor._attr_device_class == SensorDeviceClass.POWER
        assert sensor._attr_state_class == SensorStateClass.MEASUREMENT
        assert sensor._attr_native_unit_of_measurement == UnitOfPower.KILO_WATT

    def test_entity_category_set_when_provided(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(
            coordinator,
            device,
            "sys_fault_code",
            "sysFaultCode",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

        assert sensor.entity_category == EntityCategory.DIAGNOSTIC

    def test_entity_category_none_by_default(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.entity_category is None

    def test_enabled_default_true_by_default(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.entity_registry_enabled_default is True

    def test_enabled_default_false_when_provided(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(
            coordinator,
            device,
            "sys_grid_energy_delta",
            "sysGridEnergyDelta",
            enabled_default=False,
        )

        assert sensor.entity_registry_enabled_default is False


class TestLiveSensorAvailable:
    """Tests for LiveSensor.available."""

    @pytest.mark.parametrize(
        "live_status",
        [
            pytest.param({"chargeStatus": {"chargePower": 1000}, "sysGridPower": 1.2}, id="with_value"),
            pytest.param({"other": True}, id="value_missing"),
            pytest.param(None, id="before_first_payload"),
        ],
    )
    def test_available_while_connected(self, live_status):
        """The value may be unknown, but the entity stays available while the subscription is up."""
        device = _make_device(live_status=live_status)
        sensor = AndersenEvLiveSensor(_make_coordinator([device]), device, "sys_grid_power", "sysGridPower")

        assert sensor.available is True

    @pytest.mark.parametrize(
        ("disconnected_for", "available"),
        [
            pytest.param(timedelta(minutes=4), True, id="under_five_minutes"),
            pytest.param(timedelta(minutes=6), False, id="over_five_minutes"),
        ],
    )
    def test_unavailable_after_five_minutes_disconnected(self, disconnected_for, available):
        device = _make_device(live_status={"sysGridPower": 1.2}, disconnected_since=dt_util.utcnow() - disconnected_for)
        sensor = AndersenEvLiveSensor(_make_coordinator([device]), device, "sys_grid_power", "sysGridPower")

        assert sensor.available is available

    def test_unavailable_when_device_not_found(self):
        device = _make_device(device_id="device_1")
        coordinator = _make_coordinator([])
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.available is False


class TestLiveSensorNativeValue:
    """Tests for AndersenEvLiveSensor.native_value."""

    def test_returns_value_from_live_status(self):
        device = _make_device(live_status={"sysGridPower": 1.5})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.native_value == 1.5

    def test_returns_none_when_data_key_missing(self):
        device = _make_device(live_status={"other": True})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.native_value is None

    def test_returns_none_when_no_status(self):
        device = _make_device(live_status=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.native_value is None

    def test_parses_valid_timestamp(self):
        device = _make_device(live_status={"lastSeen": "2024-02-19T10:30:00Z"})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(
            coordinator, device, "last_seen", "lastSeen", device_class=SensorDeviceClass.TIMESTAMP
        )

        value = sensor.native_value

        assert value.year == 2024

    def test_invalid_timestamp_returns_none(self):
        device = _make_device(live_status={"lastSeen": "definitely-not-a-timestamp!!"})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(
            coordinator, device, "last_seen", "lastSeen", device_class=SensorDeviceClass.TIMESTAMP
        )

        assert sensor.native_value is None

    def test_updates_device_reference_from_coordinator_data(self):
        original_device = _make_device(device_id="device_1", live_status=None)
        updated_device = _make_device(device_id="device_1", live_status={"sysGridPower": 3.3})
        coordinator = _make_coordinator([updated_device])
        sensor = AndersenEvLiveSensor(coordinator, original_device, "sys_grid_power", "sysGridPower")

        assert sensor.native_value == 3.3
        assert sensor._device is updated_device


class TestBuildEntitiesForDeviceEntityCategories:
    """Tests locking in per-instance entity_category/enabled_default overrides."""

    def _entities_by_unique_id_suffix(self, device):
        coordinator = _make_coordinator([device])
        entities = _build_entities_for_device(coordinator, device)
        return {entity._attr_unique_id.removeprefix(f"{device.device_id}_"): entity for entity in entities}

    def test_fault_code_sensor_is_diagnostic(self):
        device = _make_device()
        entities = self._entities_by_unique_id_suffix(device)

        sensor = entities["sys_fault_code"]

        assert sensor.entity_category == EntityCategory.DIAGNOSTIC
        assert sensor.entity_registry_enabled_default is True

    def test_grid_energy_delta_sensor_is_diagnostic_and_disabled_by_default(self):
        device = _make_device()
        entities = self._entities_by_unique_id_suffix(device)

        sensor = entities["sys_grid_energy_delta"]

        assert sensor.entity_category == EntityCategory.DIAGNOSTIC
        assert sensor.entity_registry_enabled_default is False

    def test_unrelated_live_sensor_is_uncategorized_and_enabled(self):
        device = _make_device()
        entities = self._entities_by_unique_id_suffix(device)

        sensor = entities["sys_grid_power"]

        assert sensor.entity_category is None
        assert sensor.entity_registry_enabled_default is True

    def test_unrelated_energy_sensor_is_uncategorized_and_enabled(self):
        device = _make_device()
        entities = self._entities_by_unique_id_suffix(device)

        sensor = entities["energy"]

        assert sensor.entity_category is None
        assert sensor.entity_registry_enabled_default is True


class TestEnergySensorUnitConstants:
    """Sanity checks that energy sensors report the expected static attributes."""

    def test_native_unit_is_kwh(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
        assert sensor._attr_device_class == SensorDeviceClass.ENERGY
        assert sensor._attr_state_class == SensorStateClass.TOTAL


class TestSolarPowerSensor:
    """Tests for AndersenEvSolarPowerSensor."""

    @pytest.mark.parametrize(
        ("live_status", "expected"),
        [
            pytest.param({"sysSolarCT": True, "sysSolarPower": 1.234}, 1234.0, id="solar_ct_fitted"),
            pytest.param({"sysSolarCT": False, "sysSolarPower": 0.005465}, None, id="no_solar_ct_stale_value"),
            pytest.param({"sysSolarCT": True, "sysSolarPower": None}, None, id="no_reading"),
            pytest.param({}, None, id="before_first_payload"),
        ],
    )
    def test_native_value_in_watts(self, live_status, expected):
        device = _make_device(live_status=live_status)
        sensor = AndersenEvSolarPowerSensor(_make_coordinator([device]), device)

        assert sensor.native_value == expected
        assert sensor.native_unit_of_measurement == UnitOfPower.WATT
        assert sensor.unique_id == "device_1_solar_power"

    @pytest.mark.parametrize(
        ("live_status", "enabled"),
        [
            pytest.param({"sysSolarCT": True}, True, id="solar_ct_fitted"),
            pytest.param({"sysSolarCT": False}, False, id="no_solar_ct"),
            pytest.param({}, False, id="unknown"),
        ],
    )
    def test_enabled_by_default_only_with_solar_ct(self, live_status, enabled):
        device = _make_device(live_status=live_status)
        sensor = AndersenEvSolarPowerSensor(_make_coordinator([device]), device)

        assert sensor.entity_registry_enabled_default is enabled


TODAY = date(2026, 11, 11)
YESTERDAY = date(2026, 11, 10)


def _make_rest_energy_device(lifetime_wh: object = None) -> MagicMock:
    """Build a mock device whose REST info reports lifetime_wh, or nothing when None."""
    device = _make_device()
    device.last_rest_info = {} if lifetime_wh is None else {"lifetimeEnergyDelivered": lifetime_wh}
    return device


def _make_daily_sensor(device: MagicMock) -> AndersenEvDailyEnergySensor:
    return AndersenEvDailyEnergySensor(_make_coordinator([device]), device)


@pytest.fixture
def write_ha_state():
    """Stub out state writes, as the sensor isn't added to a running Home Assistant."""
    with patch.object(AndersenEvDailyEnergySensor, "async_write_ha_state") as mock_write:
        yield mock_write


@pytest.fixture
def local_today():
    """Pin the local date the daily sensor counts against."""
    with patch("andersen_ev.sensor.dt_util.now", return_value=datetime(2026, 11, 11, 12, 0, tzinfo=UTC)) as mock_now:
        yield mock_now


class TestLifetimeEnergySensor:
    """Tests for AndersenEvLifetimeEnergySensor."""

    def test_entity_description(self) -> None:
        sensor = AndersenEvLifetimeEnergySensor(_make_coordinator(), _make_rest_energy_device())

        assert sensor.unique_id == "device_1_lifetime_energy"
        assert sensor.translation_key == "lifetime_energy"
        assert sensor.device_class == SensorDeviceClass.ENERGY
        assert sensor.state_class == SensorStateClass.TOTAL_INCREASING
        assert sensor.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR

    @pytest.mark.parametrize(
        ("lifetime_wh", "expected"),
        [
            pytest.param(3015612.602, 3015.6, id="float_wh_to_kwh"),
            pytest.param(1500, 1.5, id="int_wh_to_kwh"),
            pytest.param(None, None, id="missing"),
            pytest.param("3015612.602", None, id="not_a_number"),
            pytest.param(True, None, id="bool"),
        ],
    )
    def test_native_value(self, lifetime_wh: object, expected: float | None) -> None:
        sensor = AndersenEvLifetimeEnergySensor(_make_coordinator(), _make_rest_energy_device(lifetime_wh))

        assert sensor.native_value == expected

    @pytest.mark.parametrize(
        ("lifetime_wh", "last_update_success", "expected"),
        [
            pytest.param(1000.0, True, True, id="value_present"),
            pytest.param(None, True, False, id="value_missing"),
            pytest.param(1000.0, False, False, id="update_failed"),
        ],
    )
    def test_available(self, lifetime_wh: float | None, last_update_success: bool, expected: bool) -> None:
        device = _make_rest_energy_device(lifetime_wh)
        sensor = AndersenEvLifetimeEnergySensor(_make_coordinator([device], last_update_success), device)

        assert sensor.available is expected


@pytest.mark.usefixtures("local_today", "write_ha_state")
class TestDailyEnergySensor:
    """Tests for AndersenEvDailyEnergySensor."""

    def test_entity_description(self) -> None:
        sensor = _make_daily_sensor(_make_rest_energy_device())

        assert sensor.unique_id == "device_1_daily_energy"
        assert sensor.translation_key == "daily_energy"
        assert sensor.device_class == SensorDeviceClass.ENERGY
        assert sensor.state_class == SensorStateClass.TOTAL_INCREASING
        assert sensor.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR

    def test_unknown_until_first_reading(self) -> None:
        device = _make_rest_energy_device()
        sensor = _make_daily_sensor(device)
        sensor._handle_coordinator_update()

        assert sensor.native_value is None
        assert sensor.available is False

    def test_counts_from_first_reading_of_the_day(self) -> None:
        device = _make_rest_energy_device(3_000_000.0)
        sensor = _make_daily_sensor(device)
        sensor._handle_coordinator_update()
        assert sensor.native_value == 0.0

        device.last_rest_info = {"lifetimeEnergyDelivered": 3_007_250.7}
        sensor._handle_coordinator_update()

        assert sensor.native_value == 7.3
        assert sensor.extra_restore_state_data == DailyEnergyExtraStoredData(TODAY, 3_000_000.0)

    def test_resets_at_midnight(self, local_today: MagicMock, write_ha_state: MagicMock) -> None:
        device = _make_rest_energy_device(3_000_000.0)
        sensor = _make_daily_sensor(device)
        sensor._handle_coordinator_update()
        device.last_rest_info = {"lifetimeEnergyDelivered": 3_010_000.0}
        sensor._handle_coordinator_update()
        assert sensor.native_value == 10.0

        local_today.return_value = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)
        sensor._async_midnight(local_today.return_value)

        assert sensor.native_value == 0.0
        assert sensor.extra_restore_state_data == DailyEnergyExtraStoredData(date(2026, 11, 12), 3_010_000.0)
        write_ha_state.assert_called()

    def test_poll_on_a_new_day_restarts_the_count(self, local_today: MagicMock) -> None:
        """Covers a midnight missed because the REST data was unavailable then."""
        device = _make_rest_energy_device(3_000_000.0)
        sensor = _make_daily_sensor(device)
        sensor._handle_coordinator_update()

        local_today.return_value = datetime(2026, 11, 12, 0, 1, tzinfo=UTC)
        device.last_rest_info = {"lifetimeEnergyDelivered": 3_010_000.0}
        sensor._handle_coordinator_update()

        assert sensor.native_value == 0.0

    def test_lifetime_drop_restarts_the_count(self) -> None:
        """Andersen can recalculate the lifetime total, which may lower it."""
        device = _make_rest_energy_device(3_000_000.0)
        sensor = _make_daily_sensor(device)
        sensor._handle_coordinator_update()

        device.last_rest_info = {"lifetimeEnergyDelivered": 2_900_000.0}
        sensor._handle_coordinator_update()

        assert sensor.native_value == 0.0
        assert sensor.extra_restore_state_data == DailyEnergyExtraStoredData(TODAY, 2_900_000.0)

    @pytest.mark.parametrize(
        ("restored", "expected"),
        [
            pytest.param({"day": "2026-11-11", "start_wh": 3_000_000.0}, 5.0, id="same_day_continues"),
            pytest.param({"day": "2026-11-10", "start_wh": 3_000_000.0}, 0.0, id="previous_day_restarts"),
            pytest.param(None, 0.0, id="nothing_stored"),
        ],
    )
    async def test_restore_after_restart(self, restored: dict | None, expected: float) -> None:
        device = _make_rest_energy_device(3_005_000.0)
        sensor = _make_daily_sensor(device)
        extra = None if restored is None else DailyEnergyExtraStoredData.from_dict(restored)

        with (
            patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()),
            patch.object(sensor, "async_get_last_extra_data", AsyncMock(return_value=extra)),
            patch("andersen_ev.sensor.async_track_time_change") as mock_track,
        ):
            sensor.hass = MagicMock()
            await sensor.async_added_to_hass()

        assert sensor.native_value == expected
        assert mock_track.call_args.kwargs == {"hour": 0, "minute": 0, "second": 0}


class TestDailyEnergyExtraStoredData:
    """Tests for DailyEnergyExtraStoredData."""

    def test_round_trip(self) -> None:
        data = DailyEnergyExtraStoredData(YESTERDAY, 1234.5)

        assert DailyEnergyExtraStoredData.from_dict(data.as_dict()) == data

    @pytest.mark.parametrize(
        "stored",
        [
            pytest.param({}, id="empty"),
            pytest.param({"day": "not a date", "start_wh": 1.0}, id="bad_day"),
            pytest.param({"day": "2026-11-11", "start_wh": "x"}, id="bad_start"),
        ],
    )
    def test_from_dict_rejects_unreadable_data(self, stored: dict) -> None:
        assert DailyEnergyExtraStoredData.from_dict(stored) is None
