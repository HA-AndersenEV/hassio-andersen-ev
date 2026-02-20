"""Sensor platform for Andersen EV."""

from __future__ import annotations

import logging

import dateutil.parser
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Andersen EV sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device in coordinator.data:
        # Create energy sensors from historical data
        entities.append(
            AndersenEvEnergySensor(
                coordinator,
                device,
                "energy",
                "Total Energy",
                "chargeEnergyTotal",
                "mdi:lightning-bolt-circle",
            )
        )
        entities.append(
            AndersenEvEnergySensor(
                coordinator,
                device,
                "grid_energy",
                "Grid Energy",
                "gridEnergyTotal",
                "mdi:transmission-tower",
            )
        )
        entities.append(
            AndersenEvEnergySensor(
                coordinator,
                device,
                "solar_energy",
                "Solar Energy",
                "solarEnergyTotal",
                "mdi:solar-power",
            )
        )
        entities.append(
            AndersenEvEnergySensor(
                coordinator,
                device,
                "surplus_energy",
                "Surplus Energy",
                "surplusUsedEnergyTotal",
                "mdi:battery-plus",
            )
        )

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
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_voltage",
                "System Voltage",
                "sysVoltageC",
                SensorDeviceClass.VOLTAGE,
                SensorStateClass.MEASUREMENT,
                UnitOfElectricPotential.VOLT,
                "mdi:transmission-tower",
            )
        )
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_fault_code",
                "Fault Code",
                "sysFaultCode",
                None,
                None,
                None,
                "mdi:exclamation",
            )
        )
        entities.append(
            AndersenEvLiveSensor(
                coordinator,
                device,
                "sys_grid_energy_delta",
                "System Grid Energy Delta",
                "sysGridEnergyDelta",
                SensorDeviceClass.POWER,
                SensorStateClass.MEASUREMENT,
                UnitOfEnergy.KILO_WATT_HOUR,
                "mdi:transmission-tower",
            )
        )

        # Create cost sensors from historical data
        entities.append(
            AndersenEvCostSensor(
                coordinator,
                device,
                "cost",
                "Total Cost",
                "chargeCostTotal",
                "mdi:currency-gbp",
            )
        )
        entities.append(
            AndersenEvCostSensor(
                coordinator,
                device,
                "grid_cost",
                "Grid Cost",
                "gridCostTotal",
                "mdi:cash-multiple",
            )
        )
        entities.append(
            AndersenEvCostSensor(
                coordinator,
                device,
                "solar_cost",
                "Solar Cost",
                "solarCostTotal",
                "mdi:solar-power-variant",
            )
        )
        entities.append(
            AndersenEvCostSensor(
                coordinator,
                device,
                "surplus_cost",
                "Surplus Cost",
                "surplusUsedCostTotal",
                "mdi:cash-plus",
            )
        )

        # Create connector state sensor
        entities.append(AndersenEvConnectorSensor(coordinator, device))

        # Create realtime charge status sensors
        # Power sensors
        entities.append(
            AndersenEvChargeStatusSensor(
                coordinator,
                device,
                "charge_power",
                "Charge Power",
                "chargePower",
                SensorDeviceClass.POWER,
                SensorStateClass.MEASUREMENT,
                UnitOfPower.WATT,
                "mdi:ev-station",
            )
        )
        entities.append(
            AndersenEvChargeStatusSensor(
                coordinator,
                device,
                "charge_power_max",
                "Max Charge Power",
                "chargePowerMax",
                SensorDeviceClass.POWER,
                SensorStateClass.MEASUREMENT,
                UnitOfPower.KILO_WATT,
                "mdi:speedometer",
            )
        )
        entities.append(
            AndersenEvChargeStatusSensor(
                coordinator,
                device,
                "solar_power",
                "Solar Power",
                "solarPower",
                SensorDeviceClass.POWER,
                SensorStateClass.MEASUREMENT,
                UnitOfPower.WATT,
                "mdi:solar-power",
            )
        )
        entities.append(
            AndersenEvChargeStatusSensor(
                coordinator,
                device,
                "grid_power",
                "Grid Power",
                "gridPower",
                SensorDeviceClass.POWER,
                SensorStateClass.MEASUREMENT,
                UnitOfPower.WATT,
                "mdi:transmission-tower",
            )
        )

        # Energy sensors from realtime status
        entities.append(
            AndersenEvChargeStatusSensor(
                coordinator,
                device,
                "current_charge_energy",
                "Current Session Energy",
                "chargeEnergyTotal",
                SensorDeviceClass.ENERGY,
                SensorStateClass.TOTAL,
                UnitOfEnergy.KILO_WATT_HOUR,
                "mdi:car-electric",
            )
        )
        entities.append(
            AndersenEvChargeStatusSensor(
                coordinator,
                device,
                "current_solar_energy",
                "Current Session Solar Energy",
                "solarEnergyTotal",
                SensorDeviceClass.ENERGY,
                SensorStateClass.TOTAL,
                UnitOfEnergy.KILO_WATT_HOUR,
                "mdi:solar-power-variant",
            )
        )
        entities.append(
            AndersenEvChargeStatusSensor(
                coordinator,
                device,
                "current_grid_energy",
                "Current Session Grid Energy",
                "gridEnergyTotal",
                SensorDeviceClass.ENERGY,
                SensorStateClass.TOTAL,
                UnitOfEnergy.KILO_WATT_HOUR,
                "mdi:power-plug",
            )
        )

        # Start time sensor
        entities.append(
            AndersenEvChargeStatusSensor(
                coordinator,
                device,
                "session_start",
                "Session Start Time",
                "start",
                SensorDeviceClass.TIMESTAMP,
                None,
                None,
                "mdi:clock-start",
            )
        )

    async_add_entities(entities)


class AndersenEvBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Andersen EV sensors."""

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        sensor_type,
        name_suffix,
        data_key=None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._sensor_type = sensor_type
        self._data_key = data_key
        self._attr_name = f"{device.friendly_name} {name_suffix}"
        self._attr_unique_id = f"{device.device_id}_{sensor_type}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": f"{device.friendly_name} ({device.device_id})",
            "manufacturer": "Andersen EV",
            "model": "A2",  # Default model, will be updated if available from device status
        }
        self._last_charge = None
        self._update_model_from_device_status()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Update last charge data
        await self._update_last_charge()

    def _update_model_from_device_status(self):
        """Update model information from device status if available."""
        # First try to use the model name from the API if available
        if hasattr(self._device, "model_name") and self._device.model_name:
            self._attr_device_info["model"] = self._device.model_name
        # Fall back to the information from device status
        elif hasattr(self._device, "_last_status") and self._device._last_status:
            status = self._device._last_status
            if "sysProductName" in status:
                self._attr_device_info["model"] = status["sysProductName"]
            elif "sysProductId" in status:
                self._attr_device_info["model"] = status["sysProductId"]
            elif "sysHwVersion" in status:
                self._attr_device_info["model"] = f"A2 (HW: {status['sysHwVersion']})"

    async def _update_last_charge(self):
        """Get the last charge data for the device."""
        self._last_charge = await self._device.get_last_charge()

        # Try to update the model with the latest device status
        if hasattr(self._device, "_last_status"):
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

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        sensor_type,
        name_suffix,
        data_key,
        icon=None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, sensor_type, name_suffix, data_key)
        if icon:
            self._attr_icon = icon

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

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        sensor_type,
        name_suffix,
        data_key,
        icon=None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, sensor_type, name_suffix, data_key)
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self) -> float | None:
        """Return the cost value."""
        if self._last_charge and self._data_key in self._last_charge:
            return self._last_charge[self._data_key]
        return None


class AndersenEvConnectorSensor(CoordinatorEntity, SensorEntity):
    """Sensor for Andersen EV connector state."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "Ready",
        "Connected",
        "Charging",
        "Error",
        "Sleeping",
        "Disabled",
        "unknown",
    ]

    def __init__(self, coordinator: AndersenEvCoordinator, device, icon=None) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_name = f"{device.friendly_name} Connector"
        self._attr_unique_id = f"{device.device_id}_connector"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": f"{device.friendly_name} ({device.device_id})",
            "manufacturer": "Andersen EV",
            "model": "A2",  # Default model, will be updated if available from device status
        }
        if icon:
            self._attr_icon = icon
        else:
            self._attr_icon = "mdi:ev-plug-type2"
        self._update_model_from_device_status()
        self._connector_state = "unknown"
        self._last_evse_state = None

    def _update_model_from_device_status(self):
        """Update model information from device status if available."""
        # First try to use the model name from the API if available
        if hasattr(self._device, "model_name") and self._device.model_name:
            self._attr_device_info["model"] = self._device.model_name
        # Fall back to the information from device status
        elif hasattr(self._device, "_last_status") and self._device._last_status:
            status = self._device._last_status
            if "sysProductName" in status:
                self._attr_device_info["model"] = status["sysProductName"]
            elif "sysProductId" in status:
                self._attr_device_info["model"] = status["sysProductId"]
            elif "sysHwVersion" in status:
                self._attr_device_info["model"] = f"A2 (HW: {status['sysHwVersion']})"

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        # Always available if the coordinator and device are available
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return self.coordinator.last_update_success
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
        if hasattr(self._device, "_last_status") and self._device._last_status:
            status = self._device._last_status
            if "evseState" in status:
                evse_state = status["evseState"]

                # Log if evse_state changes to help debugging
                if self._last_evse_state != evse_state:
                    _LOGGER.debug(
                        f"EVSE state changed from {self._last_evse_state} to {evse_state} for {self._device.friendly_name}"
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
                    _LOGGER.debug(
                        f"Unknown EVSE state: {evse_state} for {self._device.friendly_name}"
                    )
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
                        f"Direct API call: EVSE state changed to {evse_state} for {self._device.friendly_name}"
                    )
                    self._last_evse_state = evse_state
        except Exception as err:
            _LOGGER.debug(f"Error updating connector state: {err}")


class AndersenEvChargeStatusSensor(CoordinatorEntity, SensorEntity):
    """Sensor for Andersen EV charge status values."""

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        sensor_type,
        name_suffix,
        data_key,
        device_class=None,
        state_class=None,
        unit=None,
        icon=None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._sensor_type = sensor_type
        self._data_key = data_key
        self._attr_name = f"{device.friendly_name} {name_suffix}"
        self._attr_unique_id = f"{device.device_id}_{sensor_type}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": f"{device.friendly_name} ({device.device_id})",
            "manufacturer": "Andersen EV",
            "model": "A2",  # Default model, will be updated if available from device status
        }
        if device_class:
            self._attr_device_class = device_class
        if state_class:
            self._attr_state_class = state_class
        if unit:
            self._attr_native_unit_of_measurement = unit
        if icon:
            self._attr_icon = icon
        self._update_model_from_device_status()

    def _update_model_from_device_status(self):
        """Update model information from device status if available."""
        # First try to use the model name from the API if available
        if hasattr(self._device, "model_name") and self._device.model_name:
            self._attr_device_info["model"] = self._device.model_name
        # Fall back to the information from device status
        elif hasattr(self._device, "_last_status") and self._device._last_status:
            status = self._device._last_status
            if "sysProductName" in status:
                self._attr_device_info["model"] = status["sysProductName"]
            elif "sysProductId" in status:
                self._attr_device_info["model"] = status["sysProductId"]
            elif "sysHwVersion" in status:
                self._attr_device_info["model"] = f"A2 (HW: {status['sysHwVersion']})"

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        # Always available if the coordinator and device are available
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                # Check if chargeStatus exists in last_status
                if (
                    hasattr(self._device, "_last_status")
                    and self._device._last_status
                    and "chargeStatus" in self._device._last_status
                ):
                    return self.coordinator.last_update_success
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
            hasattr(self._device, "_last_status")
            and self._device._last_status
            and "chargeStatus" in self._device._last_status
            and self._data_key in self._device._last_status["chargeStatus"]
        ):
            value = self._device._last_status["chargeStatus"][self._data_key]
            if self._attr_device_class == SensorDeviceClass.TIMESTAMP and isinstance(
                value, str
            ):
                try:
                    return dateutil.parser.isoparse(value)
                except ValueError:
                    _LOGGER.debug(f"Error parsing timestamp: {value}")
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
        except Exception as err:
            _LOGGER.debug(f"Error updating charge status sensor: {err}")


class AndersenEvLiveSensor(CoordinatorEntity, SensorEntity):
    """Sensor for Andersen EV live status values."""

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        sensor_type,
        name_suffix,
        data_key,
        device_class=None,
        state_class=None,
        unit=None,
        icon=None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._sensor_type = sensor_type
        self._data_key = data_key
        self._attr_name = f"{device.friendly_name} {name_suffix}"
        self._attr_unique_id = f"{device.device_id}_{sensor_type}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": f"{device.friendly_name} ({device.device_id})",
            "manufacturer": "Andersen EV",
            "model": "A2",  # Default model, will be updated if available from device status
        }
        if device_class:
            self._attr_device_class = device_class
        if state_class:
            self._attr_state_class = state_class
        if unit:
            self._attr_native_unit_of_measurement = unit
        if icon:
            self._attr_icon = icon
        self._update_model_from_device_status()

    def _update_model_from_device_status(self):
        """Update model information from device status if available."""
        # First try to use the model name from the API if available
        if hasattr(self._device, "model_name") and self._device.model_name:
            self._attr_device_info["model"] = self._device.model_name
        # Fall back to the information from device status
        elif hasattr(self._device, "_last_status") and self._device._last_status:
            status = self._device._last_status
            if "sysProductName" in status:
                self._attr_device_info["model"] = status["sysProductName"]
            elif "sysProductId" in status:
                self._attr_device_info["model"] = status["sysProductId"]
            elif "sysHwVersion" in status:
                self._attr_device_info["model"] = f"A2 (HW: {status['sysHwVersion']})"

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        # Always available if the coordinator and device are available
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                if (
                    hasattr(self._device, "_last_status")
                    and self._device._last_status
                    and self._data_key in self._device._last_status
                ):
                    _LOGGER.debug(
                        f"Live available for {self._data_key} is {self.coordinator.last_update_success}"
                    )
                    return self.coordinator.last_update_success
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
            hasattr(self._device, "_last_status")
            and self._device._last_status
            and self._data_key in self._device._last_status
        ):
            value = self._device._last_status[self._data_key]
            _LOGGER.debug(f"(Live value for {self._data_key} is {value}")
            if (
                hasattr(self, "_attr_device_class")
                and self._attr_device_class == SensorDeviceClass.TIMESTAMP
                and isinstance(value, str)
            ):
                try:
                    return dateutil.parser.isoparse(value)
                except ValueError:
                    _LOGGER.debug(f"Error parsing timestamp: {value}")
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
        except Exception as err:
            _LOGGER.debug(f"Error updating live detailed status sensor: {err}")
