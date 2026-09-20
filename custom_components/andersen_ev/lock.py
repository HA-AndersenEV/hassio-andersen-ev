"""Support for Andersen EV locks."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .const import DOMAIN
from .entity import AndersenEvDeviceInfoMixin

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: AndersenEvConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Andersen EV lock platform."""
    coordinator = entry.runtime_data
    known_device_ids: set[str] = set()

    def _entities_for_new_devices() -> list[AndersenEvLock]:
        """Build lock entities for any device not seen before."""
        known_device_ids.intersection_update(device.device_id for device in coordinator.data)
        new_devices = [device for device in coordinator.data if device.device_id not in known_device_ids]
        entities = []
        for device in new_devices:
            known_device_ids.add(device.device_id)
            entities.append(AndersenEvLock(coordinator, device))
        return entities

    def _handle_coordinator_update() -> None:
        if new_entities := _entities_for_new_devices():
            async_add_entities(new_entities)

    async_add_entities(_entities_for_new_devices())
    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class AndersenEvLock(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], LockEntity):  # pylint: disable=abstract-method
    """Representation of an Andersen EV charging lock."""

    _attr_has_entity_name = True
    _attr_translation_key = "lock"

    def __init__(self, coordinator: AndersenEvCoordinator, device) -> None:
        """Initialize the lock."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=f"{device.friendly_name} ({device.device_id})",
            manufacturer="Andersen EV",
            serial_number=f"{device.device_id}",
        )
        # Update model if device status is already available
        self._update_device_info_from_status()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                # Try to update model info if we have device status
                self._update_device_info_from_status()
                return self.coordinator.last_update_success and self._device.status_available

        # Device no longer exists
        return False

    @property
    def is_locked(self) -> bool:
        """Return true if the lock is locked (charging disabled)."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device

                # Get most recent device status from coordinator data
                for device in self.coordinator.data:
                    if device.device_id == self._device.device_id:
                        try:
                            if device.last_status:
                                if "sysUserLock" in device.last_status:
                                    _LOGGER.debug(
                                        "Device %s sysUserLock state: %s",
                                        device.friendly_name,
                                        device.last_status["sysUserLock"],
                                    )
                                    return device.last_status["sysUserLock"]
                        except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                            _LOGGER.error("Error getting lock state: %s", err)

                # Inverted because enabled=unlocked, disabled=locked
                return not device.user_lock

        # Device no longer exists
        return False

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the charging station (disable charging)."""
        if not await self._device.disable():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="lock_failed",
                translation_placeholders={"device_name": self._device.friendly_name},
            )
        _LOGGER.debug("Locking device %s (disabling charging)", self._device.friendly_name)
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the charging station (enable charging)."""
        if not await self._device.enable():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unlock_failed",
                translation_placeholders={"device_name": self._device.friendly_name},
            )
        _LOGGER.debug("Unlocking device %s (enabling charging)", self._device.friendly_name)
        await self.coordinator.async_request_refresh()
