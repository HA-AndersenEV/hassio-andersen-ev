"""Sensor platform for Andersen EV."""

from __future__ import annotations

import logging

import dateutil.parser
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .const import DOMAIN
from .entity import AndersenEvDeviceInfoMixin

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


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

        # Live status sensors
        # sysGridPower/sysSolarPower are declared KILO_WATT; chargeStatus.gridPower
        # below is declared WATT for the same physical quantity but only reports
        # during a charge session. Unit verified via live load test (~2.9 kW kettle).
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_grid_power",
                "System Grid Power",
                "sysGridPower",
                SensorDeviceClass.POWER,
                SensorStateClass.MEASUREMENT,
                UnitOfPower.KILO_WATT,
                "mdi:transmission-tower",
            )
        )
        # --- Solar live sensors -------------------------------------------------
        # sysSolarPower/sysSolarEnergyDelta/sysChargePower are already fetched in
        # the GraphQL query (konnect/const.py) but were never exposed as entities.
        # They live in deviceStatus, not chargeStatus, so they report continuously
        # - not just during a charge session - making this a whole-house solar
        # monitor for free.
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_solar_power",
                "System Solar Power",
                "sysSolarPower",
                SensorDeviceClass.POWER,
                SensorStateClass.MEASUREMENT,
                UnitOfPower.KILO_WATT,
                "mdi:solar-power",
            )
        )
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_charge_power",
                "System Charge Power",
                "sysChargePower",
                SensorDeviceClass.POWER,
                SensorStateClass.MEASUREMENT,
                UnitOfPower.KILO_WATT,
                "mdi:ev-station",
            )
        )
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_solar_energy_delta",
                "System Solar Energy Delta",
                "sysSolarEnergyDelta",
                SensorDeviceClass.ENERGY,
                SensorStateClass.TOTAL,
                UnitOfEnergy.KILO_WATT_HOUR,
                "mdi:solar-power",
            )
        )
        # --- CT clamp diagnostics ------------------------------------------------
        # Raw CT readings and configuration - not for the Energy dashboard. These
        # exist to diagnose mis-clamped or mis-configured CTs, a common cause of
        # solar generation reading suspiciously low.
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_solar_ct",
                "Solar CT",
                "sysSolarCT",
                None,
                None,
                None,
                "mdi:gauge",
            )
        )
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_grid_ct",
                "Grid CT",
                "sysGridCT",
                None,
                None,
                None,
                "mdi:gauge",
            )
        )
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "cfg_ct_config",
                "CT Configuration",
                "cfgCTConfig",
                None,
                None,
                None,
                "mdi:cog-outline",
            )
        )
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_temperature",
                "System Temperature",
                "sysTemperature",
                SensorDeviceClass.TEMPERATURE,
                SensorStateClass.MEASUREMENT,
                UnitOfTemperature.CELSIUS,
                "mdi:temperature-celsius",
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
    entities.append(
        AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "solar_power",
            "solarPower",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            UnitOfPower.WATT,
        )
    )
    entities.append(
        AndersenEvChargeStatusSensor(
            coordinator,
            device,
            "grid_power",
            "gridPower",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            UnitOfPower.WATT,
        )
    )

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
            name=f"{device.friendly_name} ({device.device_id})",
            manufacturer="Andersen EV",
            model="A2",
            serial_number=f"{device.device_id}",
        )
        self._last_charge = None
        self._update_model_from_device_status()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Update last charge data
        await self._update_last_charge()

    async def _update_last_charge(self):
        """Get the last charge data for the device."""
        self._last_charge = await self._device.get_last_charge()

        # Try to update the model with the latest device status
        if self._device.last_status:
            self._update_model_from_device_status()

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        await super().async_update()
        await self._update_last_charge()

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        # We need to override this because the last charge might be None
        return self.coordinator.last_update_success and self._last_charge is not None


class AndersenEvEnergySensor(AndersenEvBaseSensor):
    """Sensor for Andersen EV energy values."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: AndersenEvCoordinator, device, sensor_type, data_key) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, sensor_type, data_key)

    @property
    def native_value(self) -> float | None:
        """Return the energy value."""
        if self._last_charge and self._data_key in self._last_charge:
            return self._last_charge[self._data_key]
        return None


class AndersenEvCostSensor(AndersenEvBaseSensor):
    """Sensor for Andersen EV cost values."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    # Assuming GBP - you could make this configurable
    _attr_native_unit_of_measurement = "GBP"

    def __init__(self, coordinator: AndersenEvCoordinator, device, sensor_type, data_key) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, sensor_type, data_key)

    @property
    def native_value(self) -> float | None:
        """Return the cost value."""
        if self._last_charge and self._data_key in self._last_charge:
            return self._last_charge[self._data_key]
        return None


class AndersenEvConnectorSensor(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], SensorEntity):
    """Sensor for Andersen EV connector state."""

    _attr_device_class = SensorDeviceClass.ENUM
    # Not ClassVar: SensorEntity itself declares _attr_options as an instance
    # variable, so a ClassVar override here would conflict with mypy's override check.
    _attr_options: list[str] = [  # noqa: RUF012
        "Ready",
        "Connected",
        "Charging",
        "Error",
        "Sleeping",
        "Disabled",
        "unknown",
    ]

    _attr_has_entity_name = True
    _attr_translation_key = "connector"

    def __init__(self, coordinator: AndersenEvCoordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_connector"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=f"{device.friendly_name} ({device.device_id})",
            manufacturer="Andersen EV",
            model="A2",
            serial_number=f"{device.device_id}",
        )
        self._update_model_from_device_status()
        self._connector_state = "unknown"
        self._last_evse_state = None

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        # Always available if the coordinator and device are available
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return self.coordinator.last_update_success and self._device.status_available
        return False

    @property
    def native_value(self) -> str:
        """Return the connector state based on evseState."""
        # Check if device exists in coordinator data and update reference
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        # Check if the device has status information
        if self._device.last_status:
            status = self._device.last_status
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
                if evse_state == "1" or evse_state == 1:
                    self._connector_state = "Ready"
                elif evse_state == "2" or evse_state == 2:
                    self._connector_state = "Connected"
                elif evse_state == "3" or evse_state == 3:
                    self._connector_state = "Charging"
                elif evse_state == "4" or evse_state == 4:
                    self._connector_state = "Error"
                elif evse_state == "254" or evse_state == 254:
                    self._connector_state = "Sleeping"
                elif evse_state == "255" or evse_state == 255:
                    self._connector_state = "Disabled"
                else:
                    # Log unknown states for debugging
                    _LOGGER.debug("Unknown EVSE state: %s for %s", evse_state, self._device.friendly_name)
                    self._connector_state = "unknown"

        return self._connector_state

    async def async_update(self) -> None:
        """Update the entity with latest status from coordinator."""
        await super().async_update()

        # Force refresh of device status to get the latest evseState
        try:
            # Update model if device status is available
            self._update_model_from_device_status()

            # This will make the connector sensor more responsive
            # by getting the most up-to-date status directly from the API
            status = await self._device.get_detailed_device_status()
            if status and "evseState" in status:
                evse_state = status["evseState"]
                if self._last_evse_state != evse_state:
                    _LOGGER.debug(
                        "Direct API call: EVSE state changed to %s for %s", evse_state, self._device.friendly_name
                    )
                    self._last_evse_state = evse_state
        except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            _LOGGER.debug("Error updating connector state: %s", err)


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
            name=f"{device.friendly_name} ({device.device_id})",
            manufacturer="Andersen EV",
            model="A2",
            serial_number=f"{device.device_id}",
        )
        if device_class:
            self._attr_device_class = device_class
        if state_class:
            self._attr_state_class = state_class
        if unit:
            self._attr_native_unit_of_measurement = unit
        self._update_model_from_device_status()

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        # Always available if the coordinator and device are available
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                # Check if chargeStatus exists in last_status
                if self._device.last_status and "chargeStatus" in self._device.last_status:
                    return self.coordinator.last_update_success and self._device.status_available
        return False

    @property
    def native_value(self) -> float | int | str | None:
        """Return the sensor value."""
        # Check if device exists in coordinator data and update reference
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        # Check if the device has charge status information
        if (
            self._device.last_status
            and "chargeStatus" in self._device.last_status
            and self._data_key in self._device.last_status["chargeStatus"]
        ):
            value = self._device.last_status["chargeStatus"][self._data_key]
            if self._attr_device_class == SensorDeviceClass.TIMESTAMP and isinstance(value, str):
                try:
                    return dateutil.parser.isoparse(value)
                except ValueError:
                    _LOGGER.debug("Error parsing timestamp: %s", value)
                    return None
            return value
        return None

    async def async_update(self) -> None:
        """Update the entity with latest status from coordinator."""
        await super().async_update()

        # Force refresh of device status to get the latest data
        try:
            # Update model if device status is available
            self._update_model_from_device_status()

            # This will make the sensors more responsive
            # by getting the most up-to-date status directly from the API
            await self._device.get_detailed_device_status()
        except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            _LOGGER.debug("Error updating charge status sensor: %s", err)


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
            name=f"{device.friendly_name} ({device.device_id})",
            manufacturer="Andersen EV",
            model="A2",
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
        self._update_model_from_device_status()

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        # Always available if the coordinator and device are available
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                if self._device.last_status and self._data_key in self._device.last_status:
                    _LOGGER.debug("Live available for %s is %s", self._data_key, self.coordinator.last_update_success)
                    return self.coordinator.last_update_success and self._device.status_available
        return False

    @property
    def native_value(self) -> float | int | str | None:
        """Return the sensor value."""
        # Check if device exists in coordinator data and update reference
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        # Check if the device has charge status information
        if self._device.last_status and self._data_key in self._device.last_status:
            value = self._device.last_status[self._data_key]
            _LOGGER.debug("Live value for %s is %s", self._data_key, value)
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

    async def async_update(self) -> None:
        """Update the entity with latest status from coordinator."""
        await super().async_update()

        # Force refresh of device status to get the latest data
        try:
            # Update model if device status is available
            self._update_model_from_device_status()

            # This will make the sensors more responsive
            # by getting the most up-to-date status directly from the API
            await self._device.get_detailed_device_status()
        except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            _LOGGER.debug("Error updating live detailed status sensor: %s", err)
