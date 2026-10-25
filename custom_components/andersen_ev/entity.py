"""Shared entity helpers for Andersen EV."""

from __future__ import annotations

from typing import Any, TypedDict, cast

from homeassistant.helpers.entity import ChildDeviceInfo, DeviceInfo
from homeassistant.util import dt as dt_util

from .const import LIVE_STATUS_TIMEOUT, PRODUCT_NAMES
from .konnect.device import KonnectDevice


def live_status_available(device: KonnectDevice) -> bool:
    """Return False once the device's subscription has been disconnected for LIVE_STATUS_TIMEOUT."""
    since = device.subscription_disconnected_since
    return since is None or dt_util.utcnow() - since < LIVE_STATUS_TIMEOUT


# Status fields that feed the device registry entry.
DEVICE_INFO_FIELDS = frozenset({"sysProductId", "sysProductName", "sysHwVersion", "sysFwVersion", "konnectSerial"})


def _text(value: object) -> str | None:
    """Render a status value as a non-empty string, or None if there isn't one.

    Every one of these fields is nullable in the GraphQL schema, so the key is
    present with a null value far more often than it is absent.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class StatusDeviceInfo(TypedDict, total=False):
    """Device registry fields that come from the live status (typed to match DeviceInfo)."""

    model: str | None
    model_id: str | None
    hw_version: str | None
    sw_version: str | None
    serial_number: str | None


def device_info_from_status(status: dict[str, Any]) -> StatusDeviceInfo:
    """Return the model, model id, hardware/firmware versions and serial number found in a status.

    The keys match the device registry's, so the result can be passed to async_update_device.
    """
    info: StatusDeviceInfo = {}
    product_id = _text(status.get("sysProductId"))
    # Prefer the name the charger is sold under, then the API's internal product name,
    # then the bare id, so an unrecognised charger still shows something identifiable.
    model = (PRODUCT_NAMES.get(product_id) if product_id else None) or _text(status.get("sysProductName")) or product_id
    if model:
        info["model"] = model
    if product_id:
        info["model_id"] = product_id
    if hw_version := _text(status.get("sysHwVersion")):
        info["hw_version"] = hw_version
    if sw_version := _text(status.get("sysFwVersion")):
        info["sw_version"] = sw_version
    if serial_number := _text(status.get("konnectSerial")):
        info["serial_number"] = serial_number
    return info


class AndersenEvDeviceInfoMixin:
    """Mixin filling an entity's device info from the device's live status.

    Entities using this mixin must set ``self._device`` (a ``KonnectDevice``) and
    ``self._attr_device_info`` (a ``DeviceInfo``) before calling
    ``_update_device_info_from_status``. This only applies when the device is first
    registered; later changes are written to the registry by the coordinator.
    """

    _device: KonnectDevice
    _attr_device_info: DeviceInfo | ChildDeviceInfo | None

    def _update_device_info_from_status(self) -> None:
        """Update model, hardware/firmware version and serial number from device status."""
        assert self._attr_device_info is not None, "_attr_device_info must be set before this call"
        device_info = cast(DeviceInfo, self._attr_device_info)
        device_info.update(device_info_from_status(self._device.live_status))
