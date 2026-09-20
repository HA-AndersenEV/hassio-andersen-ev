"""The Andersen EV integration."""

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ATTR_DEVICE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_DISABLE_ALL_SCHEDULES,
    SERVICE_GET_DEVICE_INFO,
    SERVICE_GET_DEVICE_STATUS,
    SERVICE_RCM_RESET,
)
from .konnect.client import KonnectClient
from .konnect.device import KonnectDevice
from .konnect.exceptions import AndersenApiError, AndersenAuthError, AndersenError

PLATFORMS = [Platform.LOCK, Platform.SENSOR, Platform.SWITCH]

type AndersenEvConfigEntry = ConfigEntry[AndersenEvCoordinator]

_LOGGER = logging.getLogger(__name__)


def _make_stale_device_listener(hass: HomeAssistant, coordinator: "AndersenEvCoordinator"):
    """Build a coordinator listener that removes devices no longer returned by the API.

    Compares the device_id set on each coordinator refresh against the previous one; any
    device_id that drops out gets its device-registry entry removed. Runs once (not per
    platform) since it acts on the registry, not entities.
    """
    device_registry = dr.async_get(hass)
    known_device_ids = {device.device_id for device in coordinator.data}

    def _handle_stale_devices() -> None:
        current_device_ids = {device.device_id for device in coordinator.data}
        for device_id in known_device_ids - current_device_ids:
            if device_entry := device_registry.async_get_device(identifiers={(DOMAIN, device_id)}):
                device_registry.async_remove_device(device_entry.id)
        known_device_ids.clear()
        known_device_ids.update(current_device_ids)

    return _handle_stale_devices


async def async_setup_entry(hass: HomeAssistant, entry: AndersenEvConfigEntry) -> bool:
    """Set up Andersen EV from a config entry."""
    email = entry.data["email"]
    password = entry.data["password"]

    client = KonnectClient(email, password)

    coordinator = AndersenEvCoordinator(hass, entry, client)

    # Fetch initial data so we have data when entities subscribe
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    entry.async_on_unload(coordinator.async_add_listener(_make_stale_device_listener(hass, coordinator)))

    # Register services
    async def disable_all_schedules(call: ServiceCall) -> None:
        """Disable all schedules for a device."""
        device_id = call.data.get(ATTR_DEVICE_ID)
        devices = coordinator.data

        for device in devices:
            if device.device_id == device_id:
                await device.disable_all_schedules()
                await coordinator.async_request_refresh()
                break
        else:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"device_id": str(device_id)},
            )

    async def get_device_info(call: ServiceCall) -> dict:
        """Get detailed information for a device and return it to the UI."""
        device_id = call.data.get(ATTR_DEVICE_ID)
        devices = coordinator.data

        for device in devices:
            if device.device_id == device_id:
                device_info = await device.get_device_info()
                if device_info:
                    # Return the device info as a response that will be shown in the UI
                    return device_info
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="get_device_info_failed",
                    translation_placeholders={"device_id": str(device_id)},
                )

        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": str(device_id)},
        )

    async def get_device_status(call: ServiceCall) -> dict:
        """Get detailed status for a device and return it to the UI."""
        device_id = call.data.get(ATTR_DEVICE_ID)
        devices = coordinator.data

        for device in devices:
            if device.device_id == device_id:
                device_status = await device.get_detailed_device_status()
                if device_status:
                    return device_status
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="get_device_status_failed",
                    translation_placeholders={"device_id": str(device_id)},
                )

        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": str(device_id)},
        )

    async def reset_rcm(call: ServiceCall) -> None:
        """Reset RCM fault for a device."""
        device_id = call.data.get(ATTR_DEVICE_ID)
        devices = coordinator.data

        for device in devices:
            if device.device_id == device_id:
                await device.reset_rcm()
                await coordinator.async_request_refresh()
                break
        else:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"device_id": str(device_id)},
            )

    # Register services using simpler schema
    service_schema = vol.Schema({vol.Required(ATTR_DEVICE_ID): str})

    hass.services.async_register(DOMAIN, SERVICE_DISABLE_ALL_SCHEDULES, disable_all_schedules, schema=service_schema)

    # Register the get_device_info service with response support
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DEVICE_INFO,
        get_device_info,
        schema=service_schema,
        supports_response=SupportsResponse.OPTIONAL,
    )

    # Register the get_device_status service with response support
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DEVICE_STATUS,
        get_device_status,
        schema=service_schema,
        supports_response=SupportsResponse.OPTIONAL,
    )

    # Register the reset_rcm service
    hass.services.async_register(DOMAIN, SERVICE_RCM_RESET, reset_rcm, schema=service_schema)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AndersenEvConfigEntry) -> bool:
    """Unload a config entry."""
    await asyncio.gather(*(device.close() for device in entry.runtime_data.devices), return_exceptions=True)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


class AndersenEvCoordinator(DataUpdateCoordinator[list[KonnectDevice]]):
    """Data update coordinator for Andersen EV."""

    def __init__(self, hass: HomeAssistant, entry: AndersenEvConfigEntry, client: KonnectClient) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.devices: list[KonnectDevice] = []
        self._device_availability: dict[str, bool] = {}

    async def async_request_refresh(self) -> None:
        """Request a data refresh after a short delay.

        The Andersen API needs a moment to apply changes before the
        updated state is available, so we wait briefly before fetching.
        """
        await asyncio.sleep(1.5)
        await super().async_request_refresh()

    async def _async_update_data(self) -> list[KonnectDevice]:
        """Fetch data from API endpoint."""
        try:
            devices = await self.client.getDevices()
        except AndersenAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed - please re-enter credentials") from err
        except AndersenApiError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "api_shape_changed",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="api_shape_changed",
            )
            if self.devices:
                _LOGGER.warning("API error, using cached device data: %s", err)
                return self.devices
            raise UpdateFailed(f"Error communicating with Andersen EV API: {err}") from err
        except AndersenError as err:
            if self.devices:
                _LOGGER.warning("API error, using cached device data: %s", err)
                return self.devices
            raise UpdateFailed(f"Error communicating with Andersen EV API: {err}") from err

        ir.async_delete_issue(self.hass, DOMAIN, "api_shape_changed")

        if not devices:
            if self.devices:
                _LOGGER.debug("No devices returned, using cached data")
                return self.devices
            _LOGGER.warning("No devices found")
            return []

        # Reuse existing device objects to preserve persistent GraphQL
        # sessions.  Most users have a single device that rarely changes.
        existing = {d.device_id: d for d in self.devices}
        refreshed = []
        for new_dev in devices:
            if old := existing.get(new_dev.device_id):
                old.friendly_name = new_dev.friendly_name
                old.user_lock = new_dev.user_lock
                refreshed.append(old)
            else:
                refreshed.append(new_dev)
        self.devices = refreshed

        # Fetch status for each device
        for device in self.devices:
            _LOGGER.debug(
                "Device ID: %s, Name: %s, User Lock: %s", device.device_id, device.friendly_name, device.user_lock
            )
            try:
                status = await device.get_detailed_device_status()
            except Exception as err:  # noqa: BLE001
                self._mark_device_unavailable(device, err)
                continue

            if status is None:
                self._mark_device_unavailable(device, "no status returned")
                continue

            device.status_available = True
            if self._device_availability.get(device.device_id) is False:
                _LOGGER.info("Device %s is back online", device.friendly_name)
            self._device_availability[device.device_id] = True

            try:
                await device.get_last_charge()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed to fetch last charge for %s: %s", device.friendly_name, err)

        return self.devices

    def _mark_device_unavailable(self, device, error) -> None:
        """Mark a device unavailable and log once per transition."""
        device.status_available = False
        if self._device_availability.get(device.device_id, True):
            _LOGGER.warning("Device %s is unavailable: %s", device.friendly_name, error)
        else:
            _LOGGER.debug("Device %s still unavailable: %s", device.friendly_name, error)
        self._device_availability[device.device_id] = False
