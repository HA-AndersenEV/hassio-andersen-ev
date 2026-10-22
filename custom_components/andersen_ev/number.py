"""Number platform for Andersen EV charge rate and solar controls."""

import logging
from collections.abc import Callable

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import UnitOfElectricCurrent, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .const import ALLOWED_USER_MAX_AMPS, DOMAIN
from .entity import AndersenEvDeviceInfoMixin
from .grid_source import (
    MIN_GRID_SOURCE_POWER_KW,
    amps_to_kw,
    device_max_amps,
    effective_max_amps,
    grid_source_kw_to_percent,
    grid_source_percent_to_kw,
)
from .konnect.device import KonnectDevice
from .konnect.exceptions import AndersenError
from .user_max_current import (
    allowed_user_max_amps,
    nearest_allowed_amps,
    nearest_allowed_amps_for_kw,
    user_max_amps,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AndersenEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Andersen EV number entities."""
    coordinator = entry.runtime_data
    known_device_ids: set[str] = set()

    def _entities_for_new_devices() -> list[NumberEntity]:
        """Build number entities for any device not seen before."""
        known_device_ids.intersection_update(device.device_id for device in coordinator.data)
        new_devices = [device for device in coordinator.data if device.device_id not in known_device_ids]
        known_device_ids.update(device.device_id for device in new_devices)
        entities: list[NumberEntity] = []
        for device in new_devices:
            entities.extend(
                (
                    AndersenEvGridSourcePowerNumber(coordinator, device),
                    AndersenEvUserMaxCurrentNumber(coordinator, device),
                    AndersenEvUserMaxPowerNumber(coordinator, device),
                )
            )
        return entities

    def _handle_coordinator_update() -> None:
        if new_entities := _entities_for_new_devices():
            async_add_entities(new_entities)

    async_add_entities(_entities_for_new_devices())
    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class AndersenEvGridSourcePowerNumber(CoordinatorEntity[AndersenEvCoordinator], NumberEntity):  # pylint: disable=abstract-method
    """Grid source power in kW, stored by the API as maxGridChargePercent."""

    _attr_has_entity_name = True
    _attr_translation_key = "grid_source_power"

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_grid_source_power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": device.friendly_name,
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
                return self.coordinator.last_update_success and self._device.status_available
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
        if (max_amps := effective_max_amps(self._device)) is None:
            return 0.0
        return amps_to_kw(max_amps)

    @property
    def native_value(self) -> float | None:
        """Return the current grid source power in kW."""
        self._refresh_device()
        percent = self._device.last_solar_status.get("solarMaxGridChargePercent")
        max_amps = effective_max_amps(self._device)
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
        if (max_amps := effective_max_amps(self._device)) is None:
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
        # The API only honours chargeAlways on with a non-zero percent, and off with 0.
        charge_always = percent > 0
        try:
            success = await self._device.set_solar(charge_always=charge_always, max_grid_charge_percent=percent)
        except AndersenError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="solar_update_error",
                translation_placeholders={
                    "setting": "Grid source power",
                    "device_name": self._device.friendly_name,
                    "error": str(err),
                },
            ) from err
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="solar_update_failed",
                translation_placeholders={
                    "setting": "Grid source power",
                    "device_name": self._device.friendly_name,
                },
            )

        self._device.update_solar_status_cache(
            {"solarMaxGridChargePercent": percent, "solarChargeAlways": charge_always}
        )
        self._attr_native_value = grid_source_percent_to_kw(percent, max_amps)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class AndersenEvUserMaxCharge(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], NumberEntity):  # pylint: disable=abstract-method
    """Base for the user max charge current, set in the app's power settings.

    The current and power entities show the same charger setting (sysUserMaxChargeAmpSet).
    """

    _attr_has_entity_name = True
    _key: str

    def __init__(self, coordinator: AndersenEvCoordinator, device: KonnectDevice) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._device = device
        self._attr_translation_key = self._key
        self._attr_unique_id = f"{device.device_id}_{self._key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=device.friendly_name,
            manufacturer="Andersen EV",
            serial_number=f"{device.device_id}",
        )
        self._update_device_info_from_status()

    def _refresh_device(self) -> None:
        """Pick up the latest device object from the coordinator."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return

    @property
    def available(self) -> bool:
        """Return if the number entity is available."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return self.coordinator.last_update_success and self._device.status_available
        return False

    def _allowed_amps(self) -> list[int]:
        """Return the allowed currents, or all of them if the installer limit is unknown."""
        if (max_amps := device_max_amps(self._device)) is None:
            return list(ALLOWED_USER_MAX_AMPS)
        return allowed_user_max_amps(max_amps) or [ALLOWED_USER_MAX_AMPS[0]]

    async def _async_set_user_max_amps(self, requested: float, pick: Callable[[float, list[int]], int]) -> None:
        """Send the allowed current that pick chooses for the requested value."""
        self._refresh_device()
        device_name = self._device.friendly_name
        if (max_amps := device_max_amps(self._device)) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="user_max_current_limit_unknown",
                translation_placeholders={"device_name": device_name},
            )
        if not (allowed := allowed_user_max_amps(max_amps)):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="user_max_current_limit_too_low",
                translation_placeholders={
                    "device_name": device_name,
                    "max_amps": f"{max_amps:g}",
                    "min_amps": str(ALLOWED_USER_MAX_AMPS[0]),
                },
            )
        amps = pick(requested, allowed)
        _LOGGER.debug("Requested %s for %s, setting user max charge current to %s A", requested, device_name, amps)
        try:
            success = await self._device.set_user_max_amps(amps)
        except AndersenError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="user_max_current_error",
                translation_placeholders={"device_name": device_name, "error": str(err)},
            ) from err
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="user_max_current_failed",
                translation_placeholders={"device_name": device_name},
            )

        self._device.update_rest_status_cache({"sysUserMaxChargeAmpSet": amps})
        # Updates the linked current/power entity too, not just this one.
        self.coordinator.async_update_listeners()
        await self.coordinator.async_request_refresh()


class AndersenEvUserMaxCurrentNumber(AndersenEvUserMaxCharge):  # pylint: disable=abstract-method
    """User max charge current in amps."""

    _key = "user_max_current"
    # Disabled by default as it duplicates user max power, which matches the app.
    _attr_entity_registry_enabled_default = False
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_min_value = float(ALLOWED_USER_MAX_AMPS[0])
    _attr_native_step = 1

    @property
    def native_max_value(self) -> float:
        """Return the highest allowed current within the installer limit."""
        self._refresh_device()
        return float(max(self._allowed_amps()))

    @property
    def native_value(self) -> float | None:
        """Return the user max charge current."""
        self._refresh_device()
        return user_max_amps(self._device)

    async def async_set_native_value(self, value: float) -> None:
        """Set the user max charge current, rounded to the nearest allowed value."""
        await self._async_set_user_max_amps(value, nearest_allowed_amps)


class AndersenEvUserMaxPowerNumber(AndersenEvUserMaxCharge):  # pylint: disable=abstract-method
    """User max charge power in kW, converted to and from the user max charge current."""

    _key = "user_max_power"
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_native_min_value = amps_to_kw(ALLOWED_USER_MAX_AMPS[0])
    _attr_native_step = 0.1

    @property
    def native_max_value(self) -> float:
        """Return the power of the highest allowed current within the installer limit."""
        self._refresh_device()
        return amps_to_kw(max(self._allowed_amps()))

    @property
    def native_value(self) -> float | None:
        """Return the user max charge power."""
        self._refresh_device()
        if (amps := user_max_amps(self._device)) is None:
            return None
        return amps_to_kw(amps)

    async def async_set_native_value(self, value: float) -> None:
        """Set the user max charge power, rounded to the nearest allowed value."""
        await self._async_set_user_max_amps(value, nearest_allowed_amps_for_kw)
