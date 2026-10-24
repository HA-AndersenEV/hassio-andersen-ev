"""The Andersen EV integration."""

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DEVICE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_DISABLE_ALL_SCHEDULES,
    SERVICE_GET_DEVICE_INFO,
    SERVICE_GET_DEVICE_STATUS,
    SERVICE_RCM_RESET,
    SUBSCRIPTION_RETRY_MAX,
    SUBSCRIPTION_RETRY_MIN,
)
from .konnect.client import KonnectClient
from .konnect.device import KonnectDevice
from .konnect.exceptions import AndersenApiError, AndersenAuthError, AndersenError

PLATFORMS = [Platform.BUTTON, Platform.LOCK, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

type AndersenEvConfigEntry = ConfigEntry[AndersenEvCoordinator]

_LOGGER = logging.getLogger(__name__)


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

    coordinator.async_start_subscriptions()
    # Picks up devices added to, or removed from, the account after setup.
    entry.async_on_unload(coordinator.async_add_listener(coordinator.async_start_subscriptions))

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

    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_ALL_SCHEDULES,
        disable_all_schedules,
        schema=service_schema,
    )

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
    # Stop the subscription loops first so closing the devices isn't seen as a dropped connection.
    await entry.runtime_data.async_stop_subscriptions()
    await asyncio.gather(
        *(device.close() for device in entry.runtime_data.devices),
        return_exceptions=True,
    )
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
        self._rest_failing: set[str] = set()
        self._subscription_tasks: dict[str, asyncio.Task[None]] = {}
        self._subscription_failing: set[str] = set()

    async def async_request_refresh(self) -> None:
        """Request a data refresh after a short delay.

        The Andersen API needs a moment to apply changes before the
        updated state is available, so we wait briefly before fetching.
        """
        await asyncio.sleep(3)
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
                "Device ID: %s, Name: %s, User Lock: %s",
                device.device_id,
                device.friendly_name,
                device.user_lock,
            )
            try:
                status = await device.get_detailed_device_status()
            except Exception as err:  # noqa: BLE001
                self._mark_device_unavailable(device, err)
                continue

            if status is None:
                self._mark_device_unavailable(device, "no status returned")
                continue

            device.update_solar_status_from_device_status(status)
            device.status_available = True
            if self._device_availability.get(device.device_id) is False:
                _LOGGER.info("Device %s is back online", device.friendly_name)
            self._device_availability[device.device_id] = True

            await self._update_user_device(device)

            try:
                await device.get_last_charge()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed to fetch last charge for %s: %s", device.friendly_name, err)

        return self.devices

    @callback
    def async_start_subscriptions(self) -> None:
        """Run a live status subscription for each device, and stop those of removed devices."""
        assert self.config_entry is not None
        current = {device.device_id: device for device in self.devices}
        for device_id in self._subscription_tasks.keys() - current.keys():
            self._subscription_tasks.pop(device_id).cancel()
        for device_id, device in current.items():
            if device_id not in self._subscription_tasks:
                self._subscription_tasks[device_id] = self.config_entry.async_create_background_task(
                    self.hass,
                    self._async_run_subscription(device),
                    name=f"{DOMAIN} subscription {device_id}",
                )

    async def async_stop_subscriptions(self) -> None:
        """Cancel every subscription loop and wait for them to finish."""
        tasks = list(self._subscription_tasks.values())
        self._subscription_tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _async_run_subscription(self, device: KonnectDevice) -> None:
        """Keep a device's live status subscription open, resubscribing with backoff after failures."""
        retry_delay = SUBSCRIPTION_RETRY_MIN
        device.subscription_disconnected_since = dt_util.utcnow()
        try:
            while True:
                reason: object = "the server ended the subscription"
                try:
                    async for payload in device.subscription.updates():
                        self._handle_subscription_payload(device, payload)
                        retry_delay = SUBSCRIPTION_RETRY_MIN
                except AndersenError as err:
                    reason = err
                except Exception as err:  # pylint: disable=broad-exception-caught
                    # Without this, one unexpected payload would end live updates until a restart.
                    if device.device_id not in self._subscription_failing:
                        _LOGGER.exception("Unexpected error in live status subscription for %s", device.friendly_name)
                    reason = err
                self._mark_subscription_lost(device, reason, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, SUBSCRIPTION_RETRY_MAX)
        finally:
            device.subscription_connected = False
            await device.subscription.close()

    @callback
    def _handle_subscription_payload(self, device: KonnectDevice, payload: dict) -> None:
        """Store a subscription payload, log it and notify listeners if anything changed."""
        initial = device.last_subscription_status is None
        changes = device.apply_subscription_update(payload)
        device.last_subscription_update = dt_util.utcnow()
        if not device.subscription_connected:
            device.subscription_connected = True
            device.subscription_disconnected_since = None
            if device.device_id in self._subscription_failing:
                self._subscription_failing.discard(device.device_id)
                _LOGGER.info("Live status subscription for %s restored", device.friendly_name)
        if initial:
            _LOGGER.debug("Initial live status for %s: %s", device.friendly_name, payload)
        elif changes:
            _LOGGER.debug(
                "Live status update for %s: %s",
                device.friendly_name,
                ", ".join(f"{field} {old!r} -> {new!r}" for field, (old, new) in changes.items()),
            )
        if changes:
            self.async_update_listeners()

    def _mark_subscription_lost(self, device: KonnectDevice, reason: object, retry_delay: int) -> None:
        """Record a dropped subscription, warning once until it is restored."""
        device.subscription_connected = False
        if device.subscription_disconnected_since is None:
            device.subscription_disconnected_since = dt_util.utcnow()
        if device.device_id in self._subscription_failing:
            _LOGGER.debug(
                "Live status subscription for %s still unavailable (%s), retrying in %s s",
                device.friendly_name,
                reason,
                retry_delay,
            )
            return
        self._subscription_failing.add(device.device_id)
        _LOGGER.warning(
            "Live status subscription for %s lost (%s), retrying in %s s",
            device.friendly_name,
            reason,
            retry_delay,
        )

    async def _update_user_device(self, device: KonnectDevice) -> None:
        """Refresh the device's REST data, keeping the cached copy if the call fails."""
        try:
            await device.get_user_device()
        except AndersenError as err:
            if device.device_id in self._rest_failing:
                _LOGGER.debug("REST device data for %s still unavailable: %s", device.friendly_name, err)
            else:
                _LOGGER.warning("Failed to fetch REST device data for %s: %s", device.friendly_name, err)
                self._rest_failing.add(device.device_id)
            return
        if device.device_id in self._rest_failing:
            _LOGGER.info("REST device data for %s is available again", device.friendly_name)
            self._rest_failing.discard(device.device_id)

    def _mark_device_unavailable(self, device, error) -> None:
        """Mark a device unavailable and log once per transition."""
        device.status_available = False
        if self._device_availability.get(device.device_id, True):
            _LOGGER.warning("Device %s is unavailable: %s", device.friendly_name, error)
        else:
            _LOGGER.debug("Device %s still unavailable: %s", device.friendly_name, error)
        self._device_availability[device.device_id] = False


def _make_stale_device_listener(hass: HomeAssistant, coordinator: AndersenEvCoordinator):
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
