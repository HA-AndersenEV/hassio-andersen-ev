"""Switch platform for Andersen EV charging schedules."""

from __future__ import annotations

import copy
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .const import DOMAIN
from .entity import AndersenEvDeviceInfoMixin
from .konnect import const

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def _async_build_switches_for_device(
    coordinator: AndersenEvCoordinator, device
) -> list[AndersenEvScheduleSwitch]:
    """Build schedule switches for a single device (fetches device info)."""
    # Get device info including schedule names and schedule slots
    device_info = await device.get_device_info()
    if not device_info or "deviceInfo" not in device_info:
        _LOGGER.warning("Could not retrieve device info for %s", device.friendly_name)
        return []

    # Get schedule slots from device_info
    if "deviceStatus" not in device_info or "scheduleSlotsArray" not in device_info["deviceStatus"]:
        _LOGGER.warning("Could not retrieve schedule slots for %s", device.friendly_name)
        return []

    schedule_slots = device_info["deviceStatus"]["scheduleSlotsArray"]
    device_info_data = device_info["deviceInfo"]

    # Create switches for each schedule
    entities = []
    for idx, _slot in enumerate(schedule_slots):
        # Get the schedule name from deviceInfo if available
        schedule_name_key = f"schedule{idx}Name"
        if device_info_data.get(schedule_name_key):
            schedule_name = device_info_data[schedule_name_key]
        else:
            schedule_name = f"Schedule {idx + 1}"

        entities.append(AndersenEvScheduleSwitch(coordinator, device, idx, schedule_name))

    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AndersenEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Andersen EV schedule switches."""
    coordinator = entry.runtime_data
    known_device_ids: set[str] = set()

    async def _async_add_switches_for_devices(devices) -> None:
        """Fetch device info and create switches for the given devices.

        A device whose switches fail to build (e.g. a transient GraphQL error) is
        unmarked so it's retried on the next coordinator update instead of being
        permanently skipped.
        """
        entities = []
        for device in devices:
            built = await _async_build_switches_for_device(coordinator, device)
            if built:
                entities.extend(built)
            else:
                known_device_ids.discard(device.device_id)
        async_add_entities(entities)

    def _handle_coordinator_update() -> None:
        """Schedule switch creation for any device not seen before."""
        known_device_ids.intersection_update(device.device_id for device in coordinator.data)
        new_devices = [device for device in coordinator.data if device.device_id not in known_device_ids]
        if not new_devices:
            return
        for device in new_devices:
            known_device_ids.add(device.device_id)
        hass.async_create_task(_async_add_switches_for_devices(new_devices))

    initial_devices = list(coordinator.data)
    for device in initial_devices:
        known_device_ids.add(device.device_id)
    await _async_add_switches_for_devices(initial_devices)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class AndersenEvScheduleSwitch(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], SwitchEntity):  # pylint: disable=abstract-method
    """Representation of an Andersen EV charging schedule switch."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        index: int,
        schedule_name: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._schedule_index = index
        self._schedule_name = schedule_name
        self._attr_translation_key = "schedule"
        self._attr_translation_placeholders = {"index": str(index + 1)}
        self._attr_unique_id = f"{device.device_id}_schedule_{index}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=f"{device.friendly_name} ({device.device_id})",
            manufacturer="Andersen EV",
            serial_number=f"{device.device_id}",
        )
        self._update_device_info_from_status()

    @property
    def extra_state_attributes(self):
        """Return additional attributes for the entity."""
        return {
            "schedule_name": self._schedule_name,
            "schedule_index": self._schedule_index,
        }

    @property
    def available(self) -> bool:
        """Return if the switch is available."""
        # Check for the device in the coordinator data
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return self.coordinator.last_update_success and self._device.status_available
        return False

    @property
    def is_on(self) -> bool:
        """Return true if the schedule is enabled."""
        # Check if device exists in coordinator data and update reference
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        # Try to get the latest scheduleSlotsArray from the device's last status
        # This ensures we pick up changes made in the mobile app
        if self._device.last_status:
            status = self._device.last_status
            if "scheduleSlotsArray" in status and len(status["scheduleSlotsArray"]) > self._schedule_index:
                schedule_slot = status["scheduleSlotsArray"][self._schedule_index]
                return schedule_slot["enabled"]

        # If we can't get the state from the last status, return False as a safe default
        _LOGGER.debug(
            "Could not determine state for schedule %s of %s", self._schedule_index, self._device.friendly_name
        )
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the schedule."""
        await self._set_schedule_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the schedule."""
        await self._set_schedule_enabled(False)

    async def _set_schedule_enabled(self, enabled: bool) -> None:
        """Set the enabled state of the schedule."""
        try:
            # Get the current schedule slots from the device's last status
            if not self._device.last_status or "scheduleSlotsArray" not in self._device.last_status:
                # If we don't have the data in the coordinator, fetch it
                device_info = await self._device.get_device_info()
                if (
                    not device_info
                    or "deviceStatus" not in device_info
                    or "scheduleSlotsArray" not in device_info["deviceStatus"]
                ):
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="schedule_data_unavailable",
                        translation_placeholders={"device_name": self._device.friendly_name},
                    )
                schedule_slots = copy.deepcopy(device_info["deviceStatus"]["scheduleSlotsArray"])
            else:
                # Use the data from the coordinator
                schedule_slots = copy.deepcopy(self._device.last_status["scheduleSlotsArray"])

            # Modify the enabled state of the specified schedule
            if len(schedule_slots) > self._schedule_index:
                # Update the enabled state
                schedule_slots[self._schedule_index]["enabled"] = enabled

                # Create the proper format for the API with sch0, sch1, etc. keys
                schedule_to_update = schedule_slots[self._schedule_index]
                formatted_slots = {f"sch{self._schedule_index}": schedule_to_update}

                # Send the properly formatted schedule slots to the API
                success = await self._send_set_schedules_mutation(formatted_slots, enabled)

                if success:
                    # Update the local state immediately
                    if self._device.last_status:
                        if "scheduleSlotsArray" not in self._device.last_status:
                            self._device.last_status["scheduleSlotsArray"] = schedule_slots
                        elif len(self._device.last_status["scheduleSlotsArray"]) <= self._schedule_index:
                            # Extend the array if needed
                            while len(self._device.last_status["scheduleSlotsArray"]) <= self._schedule_index:
                                self._device.last_status["scheduleSlotsArray"].append({})

                        # Update the enabled state
                        self._device.last_status["scheduleSlotsArray"][self._schedule_index]["enabled"] = enabled

                    # Force the entity to update its state immediately
                    self.async_write_ha_state()

                    # Request a refresh of the coordinator data to update all entities
                    await self.coordinator.async_request_refresh()
                else:
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="schedule_update_failed",
                        translation_placeholders={"device_name": self._device.friendly_name},
                    )
            else:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="schedule_index_out_of_range",
                    translation_placeholders={
                        "index": str(self._schedule_index),
                        "device_name": self._device.friendly_name,
                    },
                )

        except Exception as err:  # pylint: disable=broad-exception-caught
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_update_error",
                translation_placeholders={"device_name": self._device.friendly_name, "error": str(err)},
            ) from err

    async def _send_set_schedules_mutation(self, schedule_slots, enabled=None) -> bool:
        """Send the setSchedules mutation to the Andersen EV API."""
        _LOGGER.debug("Sending schedule update for device %s, payload: %s", self._device.friendly_name, schedule_slots)

        result = await self._device.graphql_client.execute_mutation(
            operation_name="setSchedules",
            mutation=const.GRAPHQL_SET_SCHEDULES_MUTATION,
            variables={
                "deviceId": self._device.device_id,
                "scheduleSlots": schedule_slots,
            },
        )

        if result is None:
            _LOGGER.warning("Failed to update schedule for %s", self._device.friendly_name)
            return False

        state_text = "enabled" if enabled else "disabled" if enabled is not None else "updated"
        _LOGGER.info("Schedule %s for %s %s", self._schedule_name, self._device.friendly_name, state_text)
        return True
