"""Service actions for the Andersen EV integration."""

from typing import TYPE_CHECKING, cast

import probatio as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import (
    ATTR_DEVICE_ID,
    DOMAIN,
    SERVICE_DISABLE_ALL_SCHEDULES,
    SERVICE_GET_DEVICE_INFO,
    SERVICE_GET_DEVICE_STATUS,
    SERVICE_RCM_RESET,
)
from .konnect.device import KonnectDevice

if TYPE_CHECKING:
    from . import AndersenEvConfigEntry

SERVICE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): str})


def _get_device(hass: HomeAssistant, call: ServiceCall) -> KonnectDevice:
    """Return the charger named in the call from any loaded config entry."""
    device_id = call.data[ATTR_DEVICE_ID]
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        for device in cast("AndersenEvConfigEntry", entry).runtime_data.data:
            if device.device_id == device_id:
                return device
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_found",
        translation_placeholders={"device_id": device_id},
    )


async def _disable_all_schedules(call: ServiceCall) -> None:
    """Disable all schedules for a device."""
    await _get_device(call.hass, call).disable_all_schedules()


async def _get_device_info(call: ServiceCall) -> dict:
    """Get detailed information for a device and return it to the UI."""
    if device_info := await _get_device(call.hass, call).get_device_info():
        return device_info
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="get_device_info_failed",
        translation_placeholders={"device_id": call.data[ATTR_DEVICE_ID]},
    )


async def _get_device_status(call: ServiceCall) -> dict:
    """Get detailed status for a device and return it to the UI."""
    if device_status := await _get_device(call.hass, call).get_detailed_device_status():
        return device_status
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="get_device_status_failed",
        translation_placeholders={"device_id": call.data[ATTR_DEVICE_ID]},
    )


async def _reset_rcm(call: ServiceCall) -> None:
    """Reset RCM fault for a device."""
    await _get_device(call.hass, call).reset_rcm()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's service actions."""
    hass.services.async_register(DOMAIN, SERVICE_DISABLE_ALL_SCHEDULES, _disable_all_schedules, schema=SERVICE_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DEVICE_INFO,
        _get_device_info,
        schema=SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DEVICE_STATUS,
        _get_device_status,
        schema=SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_RCM_RESET, _reset_rcm, schema=SERVICE_SCHEMA)
