"""Sensor platform for Andersen EV."""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Self

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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .charge_status import CHARGE_STATUS_OPTIONS, calculate_charge_state, charge_status_badge
from .const import DOMAIN
from .entity import AndersenEvDeviceInfoMixin, live_status_available
from .konnect.device import KonnectDevice

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

CONNECTOR_STATE_OPTIONS = [
    "Ready",
    "Connected",
    "Charging",
    "Error",
    "Sleeping",
    "Disabled",
    "Unplugged",
    "unknown",
]

# REST getDevice info field, in Wh.
LIFETIME_ENERGY_FIELD = "lifetimeEnergyDelivered"


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
    entities.append(AndersenEvChargerStatusSensor(coordinator, device))

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

    # Energy totals from the REST getDevice poll
    entities.append(AndersenEvLifetimeEnergySensor(coordinator, device))
    entities.append(AndersenEvDailyEnergySensor(coordinator, device))

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

                # Disabled and sleeping don't say whether a car is plugged in, so check sysPlugged.
                if self._connector_state in ("Disabled", "Sleeping") and status.get("sysPlugged") is False:
                    self._connector_state = "Unplugged"

        return self._connector_state


class AndersenEvChargerStatusSensor(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], SensorEntity):
    """The charger status badge from the Konnect+ app's home screen."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CHARGE_STATUS_OPTIONS
    _attr_has_entity_name = True
    _attr_translation_key = "charge_status"

    def __init__(self, coordinator: AndersenEvCoordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_charge_status"
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
        return live_status_available(self._device)

    @property
    def native_value(self) -> str | None:
        """Return the badge, worked out as the app does from the live status and REST info."""
        if not (status := self._device.live_status):
            return None
        charge_state = calculate_charge_state(status, self._device.last_rest_info, dt_util.now())
        return charge_status_badge(charge_state, status)


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
            # Only decided when the entity is first registered, and disabled if the live status hasn't
            # arrived by then; users can still enable it, and existing users keep their setting.
            enabled_default=bool(device.live_status.get("sysSolarCT")),
        )

    @property
    def native_value(self) -> float | None:
        """Return the solar power, or None when no solar CT is fitted (the reading is then stale)."""
        status = self._device.live_status
        if not status.get("sysSolarCT") or (kilowatts := status.get("sysSolarPower")) is None:
            return None
        return round(kilowatts * 1000, 1)


def _lifetime_energy_wh(device: KonnectDevice) -> float | None:
    """Return the lifetime energy delivered from the last REST getDevice poll, in Wh."""
    value = device.last_rest_info.get(LIFETIME_ENERGY_FIELD)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _wh_to_kwh(value: float) -> float:
    return round(value / 1000, 1)


class AndersenEvRestEnergySensor(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], SensorEntity):
    """Base for energy sensors read from the polled REST lifetime energy total."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: AndersenEvCoordinator, device: KonnectDevice, sensor_type: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
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
        return (
            self.coordinator.last_update_success
            and self.coordinator.rest_available(self._device.device_id)
            and _lifetime_energy_wh(self._device) is not None
        )


class AndersenEvLifetimeEnergySensor(AndersenEvRestEnergySensor):
    """Total energy the charger has delivered over its lifetime."""

    def __init__(self, coordinator: AndersenEvCoordinator, device: KonnectDevice) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, "lifetime_energy")

    @property
    def native_value(self) -> float | None:
        """Return the lifetime energy in kWh."""
        if (lifetime := _lifetime_energy_wh(self._device)) is None:
            return None
        return _wh_to_kwh(lifetime)


@dataclass
class DailyEnergyExtraStoredData(ExtraStoredData):
    """The day being counted and the lifetime reading it started from, kept across restarts."""

    day: date | None
    start_wh: float | None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the data."""
        return {"day": self.day.isoformat() if self.day else None, "start_wh": self.start_wh}

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> Self | None:
        """Build the data from a stored dict, or None if it can't be read."""
        try:
            day = date.fromisoformat(restored["day"]) if restored["day"] else None
            start_wh = float(restored["start_wh"]) if restored["start_wh"] is not None else None
        except KeyError, TypeError, ValueError:
            return None
        return cls(day, start_wh)


class AndersenEvDailyEnergySensor(AndersenEvRestEnergySensor, RestoreEntity):
    """Energy delivered since local midnight, worked out from the lifetime total."""

    def __init__(self, coordinator: AndersenEvCoordinator, device: KonnectDevice) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, "daily_energy")
        self._day: date | None = None
        self._start_wh: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the day's starting reading and reset the count at each local midnight."""
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) and (
            restored := DailyEnergyExtraStoredData.from_dict(extra.as_dict())
        ):
            self._day = restored.day
            self._start_wh = restored.start_wh
        self._roll_over()
        self.async_on_remove(async_track_time_change(self.hass, self._async_midnight, hour=0, minute=0, second=0))

    @property
    def extra_restore_state_data(self) -> DailyEnergyExtraStoredData:
        """Return the data needed to carry on the day's count after a restart."""
        return DailyEnergyExtraStoredData(self._day, self._start_wh)

    def _roll_over(self) -> None:
        """Start counting from the latest lifetime reading if the day has changed.

        Energy delivered between the last poll and midnight counts towards the new day. If Home
        Assistant was stopped over midnight, or the REST data was failing at midnight, counting
        starts from the first fresh reading after that. A lifetime total that drops (Andersen can
        recalculate it) also restarts the count.
        """
        today = dt_util.now().date()
        if not self.coordinator.rest_available(self._device.device_id):
            # The cached reading may be hours old, so using it would count yesterday's energy today.
            if self._day != today:
                self._day = today
                self._start_wh = None
            return
        if (lifetime := _lifetime_energy_wh(self._device)) is None:
            return
        if self._day != today or self._start_wh is None or lifetime < self._start_wh:
            self._day = today
            self._start_wh = lifetime

    @callback
    def _async_midnight(self, _now: datetime) -> None:
        self._roll_over()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._roll_over()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        """Return the energy delivered today in kWh."""
        if (lifetime := _lifetime_energy_wh(self._device)) is None or self._start_wh is None:
            return None
        return _wh_to_kwh(lifetime - self._start_wh)
