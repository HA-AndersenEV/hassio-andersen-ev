"""Number platform for Andersen EV solar controls."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AndersenEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Andersen EV solar number entities."""
    coordinator = entry.runtime_data

    entities = []
    for device in coordinator.data:
        # Create solar max grid charge percent number entity
        entities.append(AndersenEvSolarMaxGridChargeNumber(coordinator, device))

    async_add_entities(entities)


class AndersenEvSolarMaxGridChargeNumber(CoordinatorEntity, NumberEntity):  # pylint: disable=abstract-method
    """Representation of solar max grid charge percent number entity."""

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_name = f"{device.friendly_name} Solar max grid charge percent"
        self._attr_unique_id = f"{device.device_id}_solar_max_grid_charge_percent"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": f"{device.friendly_name} ({device.device_id})",
            "manufacturer": "Andersen EV",
            "model": "A2",
        }
        self._attr_icon = "mdi:solar-power-box"
        self._attr_native_min_value = 0.0
        self._attr_native_max_value = 100.0
        self._attr_native_step = 1.0
        self._attr_native_unit_of_measurement = "%"
        self._attr_native_value = None

    def _update_model_from_device_status(self):
        """Update model information from device status if available."""
        if hasattr(self._device, "model_name") and self._device.model_name:
            self._attr_device_info["model"] = self._device.model_name
        elif self._device.last_status:
            status = self._device.last_status
            if "sysProductName" in status:
                self._attr_device_info["model"] = status["sysProductName"]
            elif "sysProductId" in status:
                self._attr_device_info["model"] = status["sysProductId"]
            elif "sysHwVersion" in status:
                self._attr_device_info["model"] = f"A2 (HW: {status['sysHwVersion']})"

    @property
    def available(self) -> bool:
        """Return if the number entity is available."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return self.coordinator.last_update_success
        return False

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        solar_status = self._device.last_solar_status
        if "solarMaxGridChargePercent" in solar_status:
            value = solar_status["solarMaxGridChargePercent"]
            if value is not None:
                return float(value)

        _LOGGER.debug(
            "Could not determine state for solar max grid charge percent of %s",
            self._device.friendly_name,
        )
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the solar max grid charge percent value."""
        try:
            # Call set_solar with the max_grid_charge_percent parameter
            success = await self._device.set_solar(max_grid_charge_percent=int(value))

            if success:
                # Update local solar status immediately
                if self._device._last_solar_status is None:
                    self._device._last_solar_status = {}
                self._device._last_solar_status["solarMaxGridChargePercent"] = int(
                    value
                )

                # Force the entity to update its state immediately
                self._attr_native_value = float(value)
                self.async_write_ha_state()

                # Request a refresh of the coordinator data to verify
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.warning(
                    "Failed to update solar max grid charge percent for %s",
                    self._device.friendly_name,
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Error setting solar max grid charge percent: %s", err)
