"""Number platform for Andersen EV solar controls."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .const import DOMAIN
from .grid_source import (
    MIN_GRID_SOURCE_POWER_KW,
    device_max_amps,
    grid_source_kw_to_percent,
    grid_source_percent_to_kw,
    max_grid_source_power_kw,
)

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
        entities.append(AndersenEvGridSourcePowerNumber(coordinator, device))

    async_add_entities(entities)


class AndersenEvGridSourcePowerNumber(CoordinatorEntity, NumberEntity):  # pylint: disable=abstract-method
    """Grid source power in kW, stored by the API as maxGridChargePercent."""

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_name = f"{device.friendly_name} Grid source power"
        self._attr_unique_id = f"{device.device_id}_grid_source_power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": f"{device.friendly_name} ({device.device_id})",
            "manufacturer": "Andersen EV",
            "model": "A2",
        }
        self._attr_icon = "mdi:transmission-tower"
        self._attr_device_class = NumberDeviceClass.POWER
        self._attr_native_min_value = 0.0
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
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

    def _refresh_device(self) -> None:
        """Pick up the latest device object from the coordinator."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return

    @property
    def native_max_value(self) -> float:
        """Return the charger's max power in kW."""
        self._refresh_device()
        if (max_amps := device_max_amps(self._device)) is None:
            return 0.0
        return max_grid_source_power_kw(max_amps)

    @property
    def native_value(self) -> float | None:
        """Return the current grid source power in kW."""
        self._refresh_device()
        percent = self._device.last_solar_status.get("solarMaxGridChargePercent")
        max_amps = device_max_amps(self._device)
        if percent is None or max_amps is None:
            _LOGGER.debug(
                "Could not determine grid source power of %s (percent=%s, max amps=%s)",
                self._device.friendly_name,
                percent,
                max_amps,
            )
            return None
        return grid_source_percent_to_kw(float(percent), max_amps)

    async def async_set_native_value(self, value: float) -> None:
        """Set the grid source power in kW."""
        self._refresh_device()
        if (max_amps := device_max_amps(self._device)) is None:
            _LOGGER.warning(
                "Cannot set grid source power for %s: max charge current is unknown",
                self._device.friendly_name,
            )
            return
        if 0 < value < MIN_GRID_SOURCE_POWER_KW:
            _LOGGER.warning(
                "Invalid grid source power %s kW for %s: must be 0 or at least %s kW, using 0",
                value,
                self._device.friendly_name,
                MIN_GRID_SOURCE_POWER_KW,
            )
            value = 0.0
        percent = grid_source_kw_to_percent(value, max_amps)
        try:
            # The API only honours chargeAlways on with a non-zero percent, and off with 0.
            charge_always = percent > 0
            success = await self._device.set_solar(charge_always=charge_always, max_grid_charge_percent=percent)

            if success:
                if self._device._last_solar_status is None:
                    self._device._last_solar_status = {}
                self._device._last_solar_status["solarMaxGridChargePercent"] = percent
                self._device._last_solar_status["solarChargeAlways"] = charge_always

                self._attr_native_value = grid_source_percent_to_kw(percent, max_amps)
                self.async_write_ha_state()

                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.warning(
                    "Failed to update grid source power for %s",
                    self._device.friendly_name,
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Error setting grid source power: %s", err)
