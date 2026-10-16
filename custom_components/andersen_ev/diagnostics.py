"""Diagnostics support for Andersen EV."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import AndersenEvConfigEntry
from .konnect.device import KonnectDevice

TO_REDACT = {"email", "password", "token", "refreshToken", "deviceKey"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: AndersenEvConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    devices = coordinator.devices

    client_diagnostics = _client_diagnostics(devices[0].api) if devices else None

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "client": client_diagnostics,
        "devices": [_device_diagnostics(device) for device in devices],
    }


def _device_diagnostics(device: KonnectDevice) -> dict[str, Any]:
    """Build a redacted diagnostics dict for a single device."""
    data = {
        "device_id": device.device_id,
        "friendly_name": device.friendly_name,
        "user_lock": device.user_lock,
        "status_available": device.status_available,
        "model_name": device.model_name,
        "last_status": device.last_status,
    }
    return async_redact_data(data, TO_REDACT)


def _client_diagnostics(api) -> dict[str, Any]:
    """Build a redacted diagnostics dict for the shared Konnect auth client."""
    data = {
        "email": api.email,
        "token": api.token,
        "tokenType": api.token_type,
        "tokenExpiresIn": api.token_expires_in,
        "tokenExpiryTime": api.token_expiry_time,
        "refreshToken": api.refresh_token_value,
        "deviceKey": api.device_key,
    }
    return async_redact_data(data, TO_REDACT)
