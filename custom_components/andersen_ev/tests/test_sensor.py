"""Tests for the Andersen EV sensor platform."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.helpers.entity import EntityCategory

from andersen_ev.sensor import (
    AndersenEvChargeStatusSensor,
    AndersenEvConnectorSensor,
    AndersenEvCostSensor,
    AndersenEvEnergySensor,
    AndersenEvLiveSensor,
    _build_entities_for_device,
    async_setup_entry,
)


def _make_device(
    device_id="device_1",
    friendly_name="Device",
    status_available=True,
    last_status=None,
    model_name=None,
    last_charge=None,
):
    """Build a mock KonnectDevice."""
    device = MagicMock()
    device.device_id = device_id
    device.friendly_name = friendly_name
    device.status_available = status_available
    device.last_status = last_status
    device.model_name = model_name
    device.get_last_charge = AsyncMock(return_value=last_charge)
    device.get_detailed_device_status = AsyncMock(return_value=last_status)
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
        assert len(entities) == 56
        assert sum(isinstance(e, AndersenEvEnergySensor) for e in entities) == 8
        assert sum(isinstance(e, AndersenEvCostSensor) for e in entities) == 8
        assert sum(isinstance(e, AndersenEvConnectorSensor) for e in entities) == 2
        assert sum(isinstance(e, AndersenEvChargeStatusSensor) for e in entities) == 16
        assert sum(isinstance(e, AndersenEvLiveSensor) for e in entities) == 22

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
        assert len(new_entities) == 22
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
        assert len(new_entities) == 22
        assert all(entity._device.device_id == "device_1" for entity in new_entities)


class TestBaseSensorInit:
    """Tests for AndersenEvBaseSensor.__init__() via AndersenEvEnergySensor."""

    def test_sets_attributes(self):
        device = _make_device(device_id="device_1", friendly_name="My Charger")
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_unique_id == "device_1_energy"
        assert sensor._attr_translation_key == "energy"
        assert sensor._attr_device_info["name"] == "My Charger (device_1)"
        assert sensor._attr_has_entity_name is True


class TestBaseSensorUpdateModelFromDeviceStatus:
    """Tests for AndersenEvBaseSensor._update_model_from_device_status()."""

    def test_uses_model_name_when_present(self):
        device = _make_device(model_name="Andersen A3")
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_device_info["model"] == "Andersen A3"

    def test_falls_back_to_sys_product_name(self):
        device = _make_device(last_status={"sysProductName": "Andersen A2 Pro"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_device_info["model"] == "Andersen A2 Pro"

    def test_falls_back_to_sys_product_id(self):
        device = _make_device(last_status={"sysProductId": "A2"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_device_info["model"] == "A2"

    def test_falls_back_to_hw_version(self):
        device = _make_device(last_status={"sysHwVersion": "1.5"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_device_info["model"] == "A2 (HW: 1.5)"

    def test_no_status_keeps_default_model(self):
        device = _make_device(last_status=None)
        coordinator = _make_coordinator([device])

        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        assert sensor._attr_device_info["model"] == "A2"


class TestBaseSensorAvailable:
    """Tests for AndersenEvBaseSensor.available."""

    def test_available_when_update_success_and_last_charge_present(self):
        device = _make_device()
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")
        sensor._last_charge = {"chargeEnergyTotal": 1.0}

        assert sensor.available is True

    def test_unavailable_when_last_charge_none(self):
        device = _make_device()
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")
        sensor._last_charge = None

        assert sensor.available is False

    def test_unavailable_when_coordinator_update_failed(self):
        device = _make_device()
        coordinator = _make_coordinator([device], last_update_success=False)
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")
        sensor._last_charge = {"chargeEnergyTotal": 1.0}

        assert sensor.available is False


class TestBaseSensorAsyncAddedToHass:
    """Tests for AndersenEvBaseSensor.async_added_to_hass()."""

    @pytest.mark.asyncio
    async def test_updates_last_charge(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")
        sensor._update_last_charge = AsyncMock()

        await sensor.async_added_to_hass()

        sensor._update_last_charge.assert_awaited_once()


class TestBaseSensorAsyncUpdate:
    """Tests for AndersenEvBaseSensor.async_update()."""

    @pytest.mark.asyncio
    async def test_updates_last_charge(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")
        sensor._update_last_charge = AsyncMock()

        await sensor.async_update()

        sensor._update_last_charge.assert_awaited_once()


class TestUpdateLastCharge:
    """Tests for AndersenEvBaseSensor._update_last_charge()."""

    @pytest.mark.asyncio
    async def test_fetches_last_charge_and_updates_model(self):
        device = _make_device(last_status={"sysProductName": "Andersen A2 Pro"}, last_charge={"chargeEnergyTotal": 5})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        await sensor._update_last_charge()

        assert sensor._last_charge == {"chargeEnergyTotal": 5}
        assert sensor._attr_device_info["model"] == "Andersen A2 Pro"

    @pytest.mark.asyncio
    async def test_no_status_skips_model_update(self):
        device = _make_device(last_status=None, last_charge={"chargeEnergyTotal": 5})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")

        await sensor._update_last_charge()

        assert sensor._last_charge == {"chargeEnergyTotal": 5}


class TestEnergySensorNativeValue:
    """Tests for AndersenEvEnergySensor.native_value."""

    def test_returns_value_from_last_charge(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")
        sensor._last_charge = {"chargeEnergyTotal": 15.5}

        assert sensor.native_value == 15.5

    def test_returns_none_when_key_missing(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")
        sensor._last_charge = {"other": 1}

        assert sensor.native_value is None

    def test_returns_none_when_no_last_charge(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvEnergySensor(coordinator, device, "energy", "chargeEnergyTotal")
        sensor._last_charge = None

        assert sensor.native_value is None


class TestCostSensorNativeValue:
    """Tests for AndersenEvCostSensor.native_value."""

    def test_returns_value_from_last_charge(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")
        sensor._last_charge = {"chargeCostTotal": 4.5}

        assert sensor.native_value == 4.5

    def test_returns_none_when_key_missing(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")
        sensor._last_charge = {"other": 1}

        assert sensor.native_value is None

    def test_returns_none_when_no_last_charge(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvCostSensor(coordinator, device, "cost", "chargeCostTotal")
        sensor._last_charge = None

        assert sensor.native_value is None


class TestConnectorSensorInit:
    """Tests for AndersenEvConnectorSensor.__init__()."""

    def test_default_translation_key(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_translation_key == "connector"


class TestConnectorSensorUpdateModelFromDeviceStatus:
    """Tests for AndersenEvConnectorSensor._update_model_from_device_status()."""

    def test_uses_model_name_when_present(self):
        device = _make_device(model_name="Andersen A3")
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_device_info["model"] == "Andersen A3"

    def test_falls_back_to_sys_product_name(self):
        device = _make_device(last_status={"sysProductName": "Andersen A2 Pro"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_device_info["model"] == "Andersen A2 Pro"

    def test_falls_back_to_sys_product_id(self):
        device = _make_device(last_status={"sysProductId": "A2"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_device_info["model"] == "A2"

    def test_falls_back_to_hw_version(self):
        device = _make_device(last_status={"sysHwVersion": "1.5"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_device_info["model"] == "A2 (HW: 1.5)"

    def test_no_status_keeps_default_model(self):
        device = _make_device(last_status=None)
        coordinator = _make_coordinator([device])

        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor._attr_device_info["model"] == "A2"


class TestConnectorSensorAvailable:
    """Tests for AndersenEvConnectorSensor.available."""

    def test_available_when_device_found_and_status_available(self):
        device = _make_device(status_available=True)
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.available is True

    def test_unavailable_when_status_unavailable(self):
        device = _make_device(status_available=False)
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
        device = _make_device(last_status={"evseState": evse_state})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.native_value == expected

    def test_no_status_returns_default_unknown(self):
        device = _make_device(last_status=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.native_value == "unknown"

    def test_no_evse_state_key_keeps_previous_state(self):
        device = _make_device(last_status={"other": True})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        assert sensor.native_value == "unknown"

    def test_updates_device_reference_from_coordinator_data(self):
        original_device = _make_device(device_id="device_1", last_status=None)
        updated_device = _make_device(device_id="device_1", last_status={"evseState": "3"})
        coordinator = _make_coordinator([updated_device])
        sensor = AndersenEvConnectorSensor(coordinator, original_device)

        assert sensor.native_value == "Charging"
        assert sensor._device is updated_device

    def test_repeated_same_state_does_not_relog(self):
        device = _make_device(last_status={"evseState": "3"})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        first = sensor.native_value
        second = sensor.native_value

        assert first == second == "Charging"


class TestConnectorSensorAsyncUpdate:
    """Tests for AndersenEvConnectorSensor.async_update()."""

    @pytest.mark.asyncio
    async def test_refreshes_from_api_and_updates_evse_state(self):
        device = _make_device()
        device.get_detailed_device_status = AsyncMock(return_value={"evseState": "2"})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        await sensor.async_update()

        device.get_detailed_device_status.assert_awaited_once()
        assert sensor._last_evse_state == "2"

    @pytest.mark.asyncio
    async def test_no_evse_state_in_status_leaves_state_unchanged(self):
        device = _make_device()
        device.get_detailed_device_status = AsyncMock(return_value={"other": True})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        await sensor.async_update()

        assert sensor._last_evse_state is None

    @pytest.mark.asyncio
    async def test_exception_during_refresh_is_swallowed(self):
        device = _make_device()
        device.get_detailed_device_status = AsyncMock(side_effect=RuntimeError("boom"))
        coordinator = _make_coordinator([device])
        sensor = AndersenEvConnectorSensor(coordinator, device)

        await sensor.async_update()


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


class TestChargeStatusSensorUpdateModelFromDeviceStatus:
    """Tests for AndersenEvChargeStatusSensor._update_model_from_device_status()."""

    def test_uses_model_name_when_present(self):
        device = _make_device(model_name="Andersen A3")
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor._attr_device_info["model"] == "Andersen A3"

    def test_falls_back_to_sys_product_name(self):
        device = _make_device(last_status={"sysProductName": "Andersen A2 Pro"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor._attr_device_info["model"] == "Andersen A2 Pro"

    def test_falls_back_to_sys_product_id(self):
        device = _make_device(last_status={"sysProductId": "A2"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor._attr_device_info["model"] == "A2"

    def test_falls_back_to_hw_version(self):
        device = _make_device(last_status={"sysHwVersion": "1.5"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor._attr_device_info["model"] == "A2 (HW: 1.5)"

    def test_no_status_keeps_default_model(self):
        device = _make_device(last_status=None)
        coordinator = _make_coordinator([device])

        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor._attr_device_info["model"] == "A2"


class TestChargeStatusSensorAvailable:
    """Tests for AndersenEvChargeStatusSensor.available."""

    def test_available_when_charge_status_present(self):
        device = _make_device(last_status={"chargeStatus": {"chargePower": 1000}}, status_available=True)
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor.available is True

    def test_unavailable_when_charge_status_missing(self):
        device = _make_device(last_status={"other": True}, status_available=True)
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor.available is False

    def test_unavailable_when_no_status(self):
        device = _make_device(last_status=None, status_available=True)
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor.available is False

    def test_unavailable_when_device_not_found(self):
        device = _make_device(device_id="device_1")
        coordinator = _make_coordinator([], last_update_success=True)
        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        assert sensor.available is False


class TestChargeStatusSensorNativeValue:
    """Tests for AndersenEvChargeStatusSensor.native_value."""

    def test_returns_value_from_charge_status(self):
        device = _make_device(last_status={"chargeStatus": {"chargePower": 2500}})
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
        device = _make_device(last_status={"chargeStatus": {}})
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
        device = _make_device(last_status={"other": True})
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
        device = _make_device(last_status=None)
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
        device = _make_device(last_status={"chargeStatus": {"start": "2024-02-19T10:30:00Z"}})
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
        device = _make_device(last_status={"chargeStatus": {"start": "not-a-real-timestamp!!"}})
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
        original_device = _make_device(device_id="device_1", last_status=None)
        updated_device = _make_device(device_id="device_1", last_status={"chargeStatus": {"chargePower": 500}})
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


class TestChargeStatusSensorAsyncUpdate:
    """Tests for AndersenEvChargeStatusSensor.async_update()."""

    @pytest.mark.asyncio
    async def test_refreshes_from_api(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        await sensor.async_update()

        device.get_detailed_device_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_during_refresh_is_swallowed(self):
        device = _make_device()
        device.get_detailed_device_status = AsyncMock(side_effect=RuntimeError("boom"))
        coordinator = _make_coordinator([device])
        sensor = AndersenEvChargeStatusSensor(coordinator, device, "charge_power", "chargePower")

        await sensor.async_update()


class TestLiveSensorUpdateModelFromDeviceStatus:
    """Tests for AndersenEvLiveSensor._update_model_from_device_status()."""

    def test_uses_model_name_when_present(self):
        device = _make_device(model_name="Andersen A3")
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor._attr_device_info["model"] == "Andersen A3"

    def test_falls_back_to_sys_product_name(self):
        device = _make_device(last_status={"sysProductName": "Andersen A2 Pro"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor._attr_device_info["model"] == "Andersen A2 Pro"

    def test_falls_back_to_sys_product_id(self):
        device = _make_device(last_status={"sysProductId": "A2"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor._attr_device_info["model"] == "A2"

    def test_falls_back_to_hw_version(self):
        device = _make_device(last_status={"sysHwVersion": "1.5"})
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor._attr_device_info["model"] == "A2 (HW: 1.5)"

    def test_no_status_keeps_default_model(self):
        device = _make_device(last_status=None)
        coordinator = _make_coordinator([device])

        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor._attr_device_info["model"] == "A2"


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
    """Tests for AndersenEvLiveSensor.available."""

    def test_available_when_data_key_present(self):
        device = _make_device(last_status={"sysGridPower": 1.2}, status_available=True)
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.available is True

    def test_unavailable_when_data_key_missing(self):
        device = _make_device(last_status={"other": True}, status_available=True)
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.available is False

    def test_unavailable_when_no_status(self):
        device = _make_device(last_status=None, status_available=True)
        coordinator = _make_coordinator([device], last_update_success=True)
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.available is False

    def test_unavailable_when_device_not_found(self):
        device = _make_device(device_id="device_1")
        coordinator = _make_coordinator([], last_update_success=True)
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.available is False


class TestLiveSensorNativeValue:
    """Tests for AndersenEvLiveSensor.native_value."""

    def test_returns_value_from_last_status(self):
        device = _make_device(last_status={"sysGridPower": 1.5})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.native_value == 1.5

    def test_returns_none_when_data_key_missing(self):
        device = _make_device(last_status={"other": True})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.native_value is None

    def test_returns_none_when_no_status(self):
        device = _make_device(last_status=None)
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        assert sensor.native_value is None

    def test_parses_valid_timestamp(self):
        device = _make_device(last_status={"lastSeen": "2024-02-19T10:30:00Z"})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(
            coordinator, device, "last_seen", "lastSeen", device_class=SensorDeviceClass.TIMESTAMP
        )

        value = sensor.native_value

        assert value.year == 2024

    def test_invalid_timestamp_returns_none(self):
        device = _make_device(last_status={"lastSeen": "definitely-not-a-timestamp!!"})
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(
            coordinator, device, "last_seen", "lastSeen", device_class=SensorDeviceClass.TIMESTAMP
        )

        assert sensor.native_value is None

    def test_updates_device_reference_from_coordinator_data(self):
        original_device = _make_device(device_id="device_1", last_status=None)
        updated_device = _make_device(device_id="device_1", last_status={"sysGridPower": 3.3})
        coordinator = _make_coordinator([updated_device])
        sensor = AndersenEvLiveSensor(coordinator, original_device, "sys_grid_power", "sysGridPower")

        assert sensor.native_value == 3.3
        assert sensor._device is updated_device


class TestLiveSensorAsyncUpdate:
    """Tests for AndersenEvLiveSensor.async_update()."""

    @pytest.mark.asyncio
    async def test_refreshes_from_api(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        await sensor.async_update()

        device.get_detailed_device_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_during_refresh_is_swallowed(self):
        device = _make_device()
        device.get_detailed_device_status = AsyncMock(side_effect=RuntimeError("boom"))
        coordinator = _make_coordinator([device])
        sensor = AndersenEvLiveSensor(coordinator, device, "sys_grid_power", "sysGridPower")

        await sensor.async_update()


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
        assert sensor._attr_state_class == SensorStateClass.TOTAL_INCREASING
