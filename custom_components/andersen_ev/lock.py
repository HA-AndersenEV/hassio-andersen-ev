"""Support for Andersen EV locks."""

from __future__ import annotations

import logging
from time import monotonic
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

# How long a sent command may show as locking/unlocking before it is treated as unconfirmed.
# The charger reports in about every 90s and the coordinator polls every 60s, and on the real
# charger a command has taken anywhere from about 1 to 2.5 minutes to be confirmed, so this leaves
# generous headroom: expiring early would show the old state while the command is still on its way.
COMMAND_CONFIRM_TIMEOUT = 300  # seconds

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
        self._pending_locked: bool | None = None
        self._pending_deadline = 0.0
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
    def is_locked(self) -> bool | None:
        """Return true if the lock is locked (charging disabled), or None if the charger has not said."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                try:
                    if device.last_status and "sysUserLock" in device.last_status:
                        return device.last_status["sysUserLock"]
                except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                    _LOGGER.error("Error getting lock state: %s", err)
                # The getDevices userLock flag is not used as a fallback: it can disagree with the
                # charger's own status, so an unknown state is more honest than a guess.
                return None

        # Device no longer exists
        return False

    def _pending_target(self) -> bool | None:
        """Return the lock state a sent command is still waiting for, or None if nothing is pending."""
        if self._pending_locked is None:
            return None
        if self.is_locked == self._pending_locked:
            self._pending_locked = None
        elif monotonic() >= self._pending_deadline:
            _LOGGER.warning(
                "%s command for %s was not confirmed by the charger within %s seconds",
                "Lock" if self._pending_locked else "Unlock",
                self._device.friendly_name,
                COMMAND_CONFIRM_TIMEOUT,
            )
            self._pending_locked = None
        return self._pending_locked

    @property
    def is_locking(self) -> bool:
        """Return true while a lock command has been sent but not yet confirmed."""
        return self._pending_target() is True

    @property
    def is_unlocking(self) -> bool:
        """Return true while an unlock command has been sent but not yet confirmed."""
        return self._pending_target() is False

    async def _send_command(self, locked: bool) -> None:
        """Send a lock or unlock command and show it as pending until the charger confirms it."""
        if self._pending_target() is locked:
            return  # this command is already on its way, so a repeat press adds nothing

        if locked:
            sent = await self._device.disable()
            translation_key = "lock_failed"
        else:
            sent = await self._device.enable()
            translation_key = "unlock_failed"
        if not sent:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders={"device_name": self._device.friendly_name},
            )

        _LOGGER.debug("%s command sent for %s", "Lock" if locked else "Unlock", self._device.friendly_name)
        self._pending_locked = locked
        self._pending_deadline = monotonic() + COMMAND_CONFIRM_TIMEOUT
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the charging station (disable charging)."""
        await self._send_command(locked=True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the charging station (enable charging)."""
        await self._send_command(locked=False)
