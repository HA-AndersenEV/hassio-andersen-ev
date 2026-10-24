"""Sensor platform for Andersen EV."""

import logging
from datetime import datetime
from typing import Any

import dateutil.parser
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .const import DOMAIN
from .entity import AndersenEvDeviceInfoMixin, live_status_available

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

CONNECTOR_STATE_OPTIONS = [
    "Ready",
    "Connected",
    "Charging",
    "Error",
    "Sleeping",
    "Disabled",
    "unknown",
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AndersenEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Andersen EV sensor platform."""
    coordinator = entry.runtime_data
    known_device_ids: set[str] = set()

    def _entities_for_new_devices() -> list[SensorEntity]:
        """Build sensor entities for any device not seen before."""
        known_device_ids.intersection_update(device.device_id for device in coordinator.data)
        new_devices = [device for device in coordinator.data if device.device_id not in known_device_ids]
        entities: list[SensorEntity] = []
        for device in new_devices:
            known_device_ids.add(device.device_id)
            entities.extend(_build_entities_for_device(coordinator, device))
        return entities

    def _handle_coordinator_update() -> None:
        if new_entities := _entities_for_new_devices():
            async_add_entities(new_entities)

    async_add_entities(_entities_for_new_devices())
    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


def _build_entities_for_device(coordinator: AndersenEvCoordinator, device) -> list[SensorEntity]:
    """Build all sensor entities for a single device."""
    entities: list[SensorEntity] = []
    # Energy sensors from historical data
    entities.append(
        AndersenEvEnergySensor(
            coordinator,
            device,
            "energy",
            "chargeEnergyTotal",
        )
    )
    entities.append(
        AndersenEvEnergySensor(
            coordinator,
            device,
            "grid_energy",
            "gridEnergyTotal",
        )
    )
    entities.append(
        AndersenEvEnergySensor(
            coordinator,
            device,
            "solar_energy",
            "solarEnergyTotal",
        )
    )
    entities.append(
        AndersenEvEnergySensor(
            coordinator,
            device,
            "surplus_energy",
            "surplusUsedEnergyTotal",
        )
    )

    # Live status sensors
    entities.append(
        AndersenEvLiveSensor(
            coordinator,
            device,
            "sys_grid_power",
            "sysGridPower",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            UnitOfPower.KILO_WATT,
        )
    )
    entities.append(
        AndersenEvLiveSensor(
            coordinator,
            device,
            "sys_temperature",
            "sysTemperature",
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
            UnitOfTemperature.CELSIUS,
        )
    )
    entities.append(
        AndersenEvLiveSensor(
            coordinator,
            device,
            "sys_voltage",
            "sysVoltageC",
            SensorDeviceClass.VOLTAGE,
            SensorStateClass.MEASUREMENT,
            UnitOfElectricPotential.VOLT,
        )
    )
    entities.append(
        AndersenEvLiveSensor(
            coordinator,
            device,
            "sys_fault_code",
            "sysFaultCode",
            None,
            None,
            None,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
    )
    entities.append(
        AndersenEvLiveSensor(
            coordinator,
            device,
            "sys_grid_energy_delta",
            "sysGridEnergyDelta",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL,
            UnitOfEnergy.KILO_WATT_HOUR,
            entity_category=EntityCategory.DIAGNOSTIC,
            enabled_default=False,
        )
    )
    entities.append(
        AndersenEvLiveSensor(
            coordinator,
            device,
            "max_system_charge_current",
            "cfgChargeAmpMax",
            SensorDeviceClass.CURRENT,
            None,
            UnitOfElectricCurrent.AMPERE,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
    )

    # Cost sensors from historical data
    entities.append(
        AndersenEvCostSensor(
            coordinator,
            device,
            "cost",
            "chargeCostTotal",
        )
    )
    entities.append(
        AndersenEvCostSensor(
            coordinator,
            device,
            "grid_cost",
            "gridCostTotal",
        )
    )
    entities.append(
        AndersenEvCostSensor(
            coordinator,
            device,
            "solar_cost",
            "solarCostTotal",
        )
    )
    entities.append(
        AndersenEvCostSensor(
            coordinator,
            device,
            "surplus_cost",
            "surplusUsedCostTotal",
        )
    )

    # Connector state sensor
    entities.append(AndersenEvConnectorSensor(coordinator, device))

    # Realtime charge status sensors - power
    entities.append(
        AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "charge_power",
            "chargePower",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            UnitOfPower.WATT,
        )
    )
    entities.append(
        AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "charge_power_max",
            "chargePowerMax",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            UnitOfPower.KILO_WATT,
        )
    )
    entities.append(AndersenEvSolarPowerSensor(coordinator, device))

    # Realtime charge status sensors - energy
    entities.append(
        AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "current_charge_energy",
            "chargeEnergyTotal",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL,
            UnitOfEnergy.KILO_WATT_HOUR,
        )
    )
    entities.append(
        AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "current_solar_energy",
            "solarEnergyTotal",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL,
            UnitOfEnergy.KILO_WATT_HOUR,
        )
    )
    entities.append(
        AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "current_grid_energy",
            "gridEnergyTotal",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL,
            UnitOfEnergy.KILO_WATT_HOUR,
        )
    )

    # Start time sensor
    entities.append(
        AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "session_start",
            "start",
            SensorDeviceClass.TIMESTAMP,
            None,
            None,
        )
    )

    return entities


def _session_start_as_local(start: Any) -> datetime | None:
    """Parse a charge session start time into an aware local datetime, or None if unusable."""
    if not start:
        return None
    try:
        parsed = dt_util.parse_datetime(start)
    except TypeError, ValueError:
        return None
    if parsed is None:
        return None
    return dt_util.as_local(parsed)


class AndersenEvBaseSensor(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], SensorEntity):
    """Base class for Andersen EV sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AndersenEvCoordinator, device, sensor_type, data_key=None) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._sensor_type = sensor_type
        self._data_key = data_key
        self._attr_translation_key = sensor_type
        self._attr_unique_id = f"{device.device_id}_{sensor_type}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.friendly_name,
            manufacturer="Andersen EV",
            serial_number=f"{device.device_id}",
        )
        self._update_device_info_from_status()

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        # We need to override this because the last charge might be None
        return self.coordinator.last_update_success and self._device.last_charge is not None

    @property
    def last_reset(self) -> datetime | None:
        """Return the start of the last charge session, as an aware datetime.

        This is the metering-cycle boundary for TOTAL state class sensors: HA sums a new
        value into long-term statistics whenever last_reset changes, so each charge session
        is counted as its own cycle rather than diffed against the previous one.
        """
        last_charge = self._device.last_charge
        if not last_charge:
            return None
        return _session_start_as_local(last_charge.get("startDateTimeLocal"))


class AndersenEvEnergySensor(AndersenEvBaseSensor):
    """Sensor for Andersen EV energy values."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: AndersenEvCoordinator, device, sensor_type, data_key) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, sensor_type, data_key)

    @property
    def native_value(self) -> float | None:
        """Return the energy value."""
        last_charge = self._device.last_charge
        if last_charge and self._data_key in last_charge:
            return last_charge[self._data_key]
        return None


class AndersenEvCostSensor(AndersenEvBaseSensor):
    """Sensor for Andersen EV cost values."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator: AndersenEvCoordinator, device, sensor_type, data_key) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, sensor_type, data_key)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Fetch the account's currency once; it isn't part of the regular status poll.
        if self._device.currency is None:
            await self._device.get_device_info()

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the currency, falling back to GBP until it's known."""
        return self._device.currency or "GBP"

    @property
    def native_value(self) -> float | None:
        """Return the cost value."""
        last_charge = self._device.last_charge
        if last_charge and self._data_key in last_charge:
            return last_charge[self._data_key]
        return None


class AndersenEvConnectorSensor(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], SensorEntity):
    """Sensor for Andersen EV connector state."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CONNECTOR_STATE_OPTIONS

    _attr_has_entity_name = True
    _attr_translation_key = "connector"

    def __init__(self, coordinator: AndersenEvCoordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_connector"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.friendly_name,
            manufacturer="Andersen EV",
            serial_number=f"{device.device_id}",
        )
        self._update_device_info_from_status()
        self._connector_state = "unknown"
        self._last_evse_state = None

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return live_status_available(device)
        return False

    @property
    def native_value(self) -> str:
        """Return the connector state based on evseState."""
        # Check if device exists in coordinator data and update reference
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        status = self._device.live_status
        if status:
            if "evseState" in status:
                evse_state = status["evseState"]

                # Log if evse_state changes to help debugging
                if self._last_evse_state != evse_state:
                    _LOGGER.debug(
                        "EVSE state changed from %s to %s for %s",
                        self._last_evse_state,
                        evse_state,
                        self._device.friendly_name,
                    )
                    self._last_evse_state = evse_state

                # Map evseState values to connector states
                if evse_state in ("1", 1):
                    self._connector_state = "Ready"
                elif evse_state in ("2", 2):
                    self._connector_state = "Connected"
                elif evse_state in ("3", 3):
                    self._connector_state = "Charging"
                elif evse_state in ("4", 4):
                    self._connector_state = "Error"
                elif evse_state in ("254", 254):
                    self._connector_state = "Sleeping"
                elif evse_state in ("255", 255):
                    self._connector_state = "Disabled"
                else:
                    # Log unknown states for debugging
                    _LOGGER.debug("Unknown EVSE state: %s for %s", evse_state, self._device.friendly_name)
                    self._connector_state = "unknown"

        return self._connector_state


class AndersenEvChargeStatusSensor(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], SensorEntity):
    """Sensor for Andersen EV charge status values."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        sensor_type,
        data_key,
        device_class=None,
        state_class=None,
        unit=None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._sensor_type = sensor_type
        self._data_key = data_key
        self._attr_translation_key = sensor_type
        self._attr_unique_id = f"{device.device_id}_{sensor_type}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.friendly_name,
            manufacturer="Andersen EV",
            serial_number=f"{device.device_id}",
        )
        if device_class:
            self._attr_device_class = device_class
        if state_class:
            self._attr_state_class = state_class
        if unit:
            self._attr_native_unit_of_measurement = unit
        self._update_device_info_from_status()

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return live_status_available(device)
        return False

    @property
    def native_value(self) -> float | int | str | None:
        """Return the sensor value."""
        # Check if device exists in coordinator data and update reference
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        charge_status = self._device.live_status.get("chargeStatus") or {}
        if self._data_key in charge_status:
            value = charge_status[self._data_key]
            if self._attr_device_class == SensorDeviceClass.TIMESTAMP and isinstance(value, str):
                try:
                    return dateutil.parser.isoparse(value)
                except ValueError:
                    _LOGGER.debug("Error parsing timestamp: %s", value)
                    return None
            return value
        return None

    @property
    def last_reset(self) -> datetime | None:
        """Return the start of the live charge session, for energy total sensors only.

        These sensors count up through a session and start again from zero in the next one. Home
        Assistant can only tell that apart from a drop in the value if last_reset changes with each
        session; without it, the finished session is subtracted from long-term statistics. Home
        Assistant rejects last_reset on any other kind of sensor, so the rest return None.
        """
        if self.state_class != SensorStateClass.TOTAL:
            return None
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break
        return _session_start_as_local((self._device.live_status.get("chargeStatus") or {}).get("start"))


class AndersenEvLiveSensor(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], SensorEntity):
    """Sensor for Andersen EV live status values."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        sensor_type,
        data_key,
        device_class=None,
        state_class=None,
        unit=None,
        entity_category: EntityCategory | None = None,
        enabled_default: bool = True,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._sensor_type = sensor_type
        self._data_key = data_key
        self._attr_translation_key = sensor_type
        self._attr_unique_id = f"{device.device_id}_{sensor_type}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.friendly_name,
            manufacturer="Andersen EV",
            serial_number=f"{device.device_id}",
        )
        if device_class:
            self._attr_device_class = device_class
        if state_class:
            self._attr_state_class = state_class
        if unit:
            self._attr_native_unit_of_measurement = unit
        if entity_category is not None:
            self._attr_entity_category = entity_category
        self._attr_entity_registry_enabled_default = enabled_default
        self._update_device_info_from_status()

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return live_status_available(device)
        return False

    @property
    def native_value(self) -> float | int | str | None:
        """Return the sensor value."""
        # Check if device exists in coordinator data and update reference
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        status = self._device.live_status
        if self._data_key in status:
            value = status[self._data_key]
            if (
                hasattr(self, "_attr_device_class")
                and self._attr_device_class == SensorDeviceClass.TIMESTAMP
                and isinstance(value, str)
            ):
                try:
                    return dateutil.parser.isoparse(value)
                except ValueError:
                    _LOGGER.debug("Error parsing timestamp: %s", value)
                    return None
            return value
        return None


class AndersenEvSolarPowerSensor(AndersenEvLiveSensor):
    """Solar power from the charger's solar CT clamp, in W."""

    def __init__(self, coordinator: AndersenEvCoordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            device,
            "solar_power",
            "sysSolarPower",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            UnitOfPower.WATT,
            # Only decided when the entity is first registered; existing users keep their setting.
            enabled_default=bool(device.live_status.get("sysSolarCT")),
        )

    @property
    def native_value(self) -> float | None:
        """Return the solar power, or None when no solar CT is fitted (the reading is then stale)."""
        status = self._device.live_status
        if not status.get("sysSolarCT") or (kilowatts := status.get("sysSolarPower")) is None:
            return None
        return round(kilowatts * 1000, 1)
