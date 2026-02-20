"""The Andersen EV integration."""

import asyncio
import json
import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ATTR_DEVICE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_DISABLE_ALL_SCHEDULES,
    SERVICE_GET_DEVICE_INFO,
    SERVICE_GET_DEVICE_STATUS,
    SERVICE_RCM_RESET,
    STORAGE_KEY,
    STORAGE_VERSION,
)

# Import the konnect module from the local directory
from .konnect.client import KonnectClient

PLATFORMS = [Platform.LOCK, Platform.SENSOR, Platform.SWITCH]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Andersen EV component."""
    hass.data[DOMAIN] = {}
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Andersen EV from a config entry."""
    email = entry.data["email"]
    password = entry.data["password"]

    # Create a storage manager for tokens
    storage = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    token_data = await storage.async_load() or {}

    # Create the client with stored tokens if available
    client = KonnectClient(email, password)

    # If we have stored tokens, set them in the client
    if entry.entry_id in token_data:
        stored_tokens = token_data[entry.entry_id]
        _LOGGER.debug("Found stored tokens for %s", email)
        client.token = stored_tokens.get("token")
        client.tokenType = stored_tokens.get("tokenType")
        client.tokenExpiresIn = stored_tokens.get("tokenExpiresIn")
        client.tokenExpiryTime = stored_tokens.get("tokenExpiryTime")
        client.refreshToken = stored_tokens.get("refreshToken")

    coordinator = AndersenEvCoordinator(hass, client, storage, entry.entry_id)

    # Fetch initial data so we have data when entities subscribe
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

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
                return {"error": "Failed to retrieve device information"}

        return {"error": f"Device with ID {device_id} not found"}

    async def get_device_status(call: ServiceCall) -> dict:
        """Get detailed status for a device and return it to the UI."""
        device_id = call.data.get(ATTR_DEVICE_ID)
        devices = coordinator.data

        for device in devices:
            if device.device_id == device_id:
                device_status = await device.get_detailed_device_status()
                if device_status:
                    # Return the device status as a response that will be shown in the UI
                    return device_status
                return {"error": "Failed to retrieve device status"}

        return {"error": f"Device with ID {device_id} not found"}

    async def reset_rcm(call: ServiceCall) -> None:
        """Reset RCM fault for a device."""
        device_id = call.data.get(ATTR_DEVICE_ID)
        devices = coordinator.data

        for device in devices:
            if device.device_id == device_id:
                await device.reset_rcm()
                await coordinator.async_request_refresh()
                break

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
        supports_response=True,
    )

    # Register the get_device_status service with response support
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DEVICE_STATUS,
        get_device_status,
        schema=service_schema,
        supports_response=True,
    )

    # Register the reset_rcm service
    hass.services.async_register(
        DOMAIN, SERVICE_RCM_RESET, reset_rcm, schema=service_schema
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class AndersenEvCoordinator(DataUpdateCoordinator):
    """Data update coordinator for Andersen EV."""

    def __init__(
        self, hass: HomeAssistant, client: KonnectClient, storage: Store, entry_id: str
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.devices = []
        self.auth_failures = 0
        self.max_auth_failures = 3
        self.storage = storage
        self.entry_id = entry_id

    async def _async_update_data(self):
        """Fetch data from API endpoint with automatic token refresh."""
        try:
            # Reset auth failures counter on successful updates
            if self.devices:
                self.auth_failures = 0

            # Get devices
            devices = await self.client.getDevices()

            # Save tokens after successful API call
            await self._save_tokens()

            if not devices:
                _LOGGER.warning("No devices found")

                # Increment auth failures counter
                self.auth_failures += 1

                # If we exceed the max failures, raise an auth exception
                # This will trigger a config entry reload
                if self.auth_failures >= self.max_auth_failures:
                    _LOGGER.error(
                        "Multiple authentication failures, requesting re-authentication"
                    )
                    self.auth_failures = 0
                    raise ConfigEntryAuthFailed("Persistent authentication failures")

                # If we still have existing devices from previous update, return those
                if self.devices:
                    _LOGGER.info("Using cached device data")
                    return self.devices

            # Cache the devices for potential future use
            self.devices = devices

            # For each device, fetch the current status
            for device in devices:
                _LOGGER.debug(
                    f"Device ID: {device.device_id}, Name: {device.friendly_name}, User Lock: {device.user_lock}"
                )

                # Fetch and log device status
                try:
                    device_status = await device.get_detailed_device_status()
                    if device_status:
                        _LOGGER.debug(
                            f"Device Status for {device.friendly_name}: evseState={device_status.get('evseState')}, online={device_status.get('online')}, charging={device_status.get('sysChargingEnabled')}, locked={device_status.get('sysUserLock')}"
                        )
                except Exception as status_err:
                    _LOGGER.debug(
                        f"Error getting device status for {device.friendly_name}: {status_err}"
                    )

            return devices
        except ConfigEntryAuthFailed as auth_err:
            # Pass this through to trigger re-authentication
            raise auth_err
        except Exception as err:
            # Check if this is an authentication error
            if (
                "Failed to sign in" in str(err)
                or "Authentication failed" in str(err)
                or "Unauthorized" in str(err)
                or "401" in str(err)
            ):
                _LOGGER.error("Authentication error: %s", str(err))

                # Increment auth failures counter
                self.auth_failures += 1

                # If we exceed the max failures, raise an auth exception
                if self.auth_failures >= self.max_auth_failures:
                    _LOGGER.error(
                        "Multiple authentication failures, requesting re-authentication"
                    )
                    self.auth_failures = 0
                    raise ConfigEntryAuthFailed("Authentication failed") from err

                # Try a full re-authentication
                try:
                    await self.client.authenticate_user()
                    _LOGGER.info("Re-authentication successful")

                    # Save new tokens after successful re-authentication
                    await self._save_tokens()

                    # Try again with the new token
                    return await self._async_update_data()
                except Exception as auth_err:
                    _LOGGER.error("Re-authentication failed: %s", str(auth_err))

                # If we still have existing devices from previous update, return those
                if self.devices:
                    _LOGGER.info("Using cached device data")
                    return self.devices

            raise UpdateFailed(f"Error communicating with Andersen EV API: {err}")

    async def _save_tokens(self):
        """Save authentication tokens to persistent storage."""
        try:
            # Load existing data
            token_data = await self.storage.async_load() or {}

            # Update with current client tokens
            token_data[self.entry_id] = {
                "token": self.client.token,
                "tokenType": self.client.tokenType,
                "tokenExpiresIn": self.client.tokenExpiresIn,
                "tokenExpiryTime": self.client.tokenExpiryTime,
                "refreshToken": self.client.refreshToken,
            }

            # Save back to storage
            await self.storage.async_save(token_data)
            _LOGGER.debug("Auth tokens saved to persistent storage")
        except Exception as err:
            _LOGGER.warning("Failed to save auth tokens: %s", str(err))
