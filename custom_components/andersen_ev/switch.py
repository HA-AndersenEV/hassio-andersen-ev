"""Switch platform for Andersen EV charging schedules."""

import copy
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .const import DOMAIN
from .entity import AndersenEvDeviceInfoMixin, live_status_available
from .grid_source import effective_max_amps, grid_supplement_on_percent
from .konnect import const
from .konnect.exceptions import AndersenError

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)

SOLAR_FIELD_TO_PARAM = {
    "boost": "boost",
    "solarChargeAlways": "charge_always",
    "solarChargeOutsideSchedules": "charge_outside_schedules",
    "solarMaxGridChargePercent": "max_grid_charge_percent",
}


async def _async_build_switches_for_device(coordinator: AndersenEvCoordinator, device) -> list[SwitchEntity]:
    """Build schedule and solar switches for a single device (fetches device info)."""
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
    entities: list[SwitchEntity] = [
        AndersenEvBoostSwitch(coordinator, device),
        AndersenEvSolarChargeAlwaysSwitch(coordinator, device),
        AndersenEvSolarChargeOutsideScheduleSwitch(coordinator, device),
    ]
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
            name=device.friendly_name,
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
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return live_status_available(device)
        return False

    @property
    def is_on(self) -> bool | None:
        """Return true if the schedule is enabled, or None before the live status has it."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        slots = self._device.live_status.get("scheduleSlotsArray") or []
        if len(slots) > self._schedule_index:
            return slots[self._schedule_index]["enabled"]
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the schedule."""
        await self._set_schedule_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the schedule."""
        await self._set_schedule_enabled(False)

    async def _set_schedule_enabled(self, enabled: bool) -> None:
        """Set the enabled state of the schedule."""
        if "scheduleSlotsArray" in self._device.live_status:
            schedule_slots = copy.deepcopy(self._device.live_status["scheduleSlotsArray"])
        else:
            # Not in the coordinator data, so fetch it directly
            try:
                device_info = await self._device.get_device_info()
            except AndersenError as err:
                raise self._schedule_update_error(err) from err
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

        if len(schedule_slots) <= self._schedule_index:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_index_out_of_range",
                translation_placeholders={
                    "index": str(self._schedule_index),
                    "device_name": self._device.friendly_name,
                },
            )

        schedule_slots[self._schedule_index]["enabled"] = enabled
        # The API expects slots keyed as sch0, sch1, etc.
        formatted_slots = {f"sch{self._schedule_index}": schedule_slots[self._schedule_index]}

        try:
            success = await self._send_set_schedules_mutation(formatted_slots, enabled)
        except AndersenError as err:
            raise self._schedule_update_error(err) from err
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_update_failed",
                translation_placeholders={"device_name": self._device.friendly_name},
            )

    def _schedule_update_error(self, err: AndersenError) -> HomeAssistantError:
        """Build the error raised when the API call itself fails."""
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="schedule_update_error",
            translation_placeholders={
                "device_name": self._device.friendly_name,
                "error": str(err),
            },
        )

    async def _send_set_schedules_mutation(self, schedule_slots, enabled=None) -> bool:
        """Send the setSchedules mutation to the Andersen EV API."""
        _LOGGER.debug(
            "Sending schedule update for device %s, payload: %s",
            self._device.friendly_name,
            schedule_slots,
        )

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
        _LOGGER.info(
            "Schedule %s for %s %s",
            self._schedule_name,
            self._device.friendly_name,
            state_text,
        )
        return True


class AndersenEvSolarSwitch(CoordinatorEntity[AndersenEvCoordinator], SwitchEntity):  # pylint: disable=abstract-method
    """Base class for Andersen EV solar control switches."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        solar_field: str,
        translation_key: str,
        name_suffix: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._solar_field = solar_field
        self._setting_name = name_suffix
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{device.device_id}_solar_{solar_field}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": device.friendly_name,
            "manufacturer": "Andersen EV",
            "model": "A2",
        }
        self._attr_icon = "mdi:solar-power"

    @property
    def available(self) -> bool:
        """Return if the switch is available."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return live_status_available(device)
        return False

    @property
    def is_on(self) -> bool | None:
        """Return true if the solar setting is enabled."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        solar_status = self._device.last_solar_status
        if solar_status.get(self._solar_field) is None:
            return None
        return bool(solar_status[self._solar_field])

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the solar setting."""
        await self._set_solar_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the solar setting."""
        await self._set_solar_value(False)

    def _solar_changes(self, value: bool) -> dict[str, Any] | None:
        """Return the solar status fields to set, or None if they can't be worked out."""
        return {self._solar_field: value}

    async def _set_solar_value(self, value: bool) -> None:
        """Set the solar value."""
        if (changes := self._solar_changes(value)) is None:
            return
        try:
            success = await self._device.set_solar(
                **{SOLAR_FIELD_TO_PARAM[field]: field_value for field, field_value in changes.items()}
            )
        except AndersenError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="solar_update_error",
                translation_placeholders={
                    "setting": self._setting_name,
                    "device_name": self._device.friendly_name,
                    "error": str(err),
                },
            ) from err
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="solar_update_failed",
                translation_placeholders={
                    "setting": self._setting_name,
                    "device_name": self._device.friendly_name,
                },
            )


class AndersenEvBoostSwitch(AndersenEvSolarSwitch):
    """Switch for boost (charge at max rate, ignoring solar settings)."""

    def __init__(self, coordinator: AndersenEvCoordinator, device) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device, "boost", "boost", "Boost")


class AndersenEvSolarChargeAlwaysSwitch(AndersenEvSolarSwitch, RestoreEntity):
    """Switch for solar charge always control."""

    def __init__(self, coordinator: AndersenEvCoordinator, device) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device, "solarChargeAlways", "solar_charge_always", "Supplement with grid power")

    async def async_added_to_hass(self) -> None:
        """Restore the percent to use when grid supplement is next turned on."""
        await super().async_added_to_hass()
        # A percent read from the charger on startup is newer than the stored one.
        if self._device.last_grid_charge_percent is not None:
            return
        if (last_extra_data := await self.async_get_last_extra_data()) is None:
            return
        self._device.last_grid_charge_percent = last_extra_data.as_dict().get("last_grid_charge_percent")

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """Keep the percent to restore across restarts, as the charger reports 0 while off."""
        return RestoredExtraData({"last_grid_charge_percent": self._device.last_grid_charge_percent})

    def _solar_changes(self, value: bool) -> dict[str, Any] | None:
        """Pair chargeAlways with a percent, as the API ignores it otherwise."""
        if not value:
            return {"solarChargeAlways": False, "solarMaxGridChargePercent": 0}
        if (max_amps := effective_max_amps(self._device)) is None:
            _LOGGER.warning(
                "Cannot turn on grid supplement for %s: max charge current is unknown",
                self._device.friendly_name,
            )
            return None
        return {
            "solarChargeAlways": True,
            "solarMaxGridChargePercent": grid_supplement_on_percent(self._device, max_amps),
        }


class AndersenEvSolarChargeOutsideScheduleSwitch(AndersenEvSolarSwitch):
    """Switch for charging outside of schedules."""

    def __init__(self, coordinator: AndersenEvCoordinator, device) -> None:
        """Initialize the switch."""
        super().__init__(
            coordinator,
            device,
            "solarChargeOutsideSchedules",
            "solar_charge_outside_schedules",
            "Charge outside schedule",
        )
