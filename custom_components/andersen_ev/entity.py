"""Shared entity helpers for Andersen EV."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo

from .konnect.device import KonnectDevice


class AndersenEvDeviceInfoMixin:
    """Mixin providing shared device-info update logic for Andersen EV entities.

    Entities using this mixin must set ``self._device`` (a ``KonnectDevice``) and
    ``self._attr_device_info`` (a ``DeviceInfo``) before calling
    ``_update_device_info_from_status``.
    """

    _device: KonnectDevice
    _attr_device_info: DeviceInfo | None

    @staticmethod
    def _text(value: object) -> str | None:
        """Render a status value as a non-empty string, or None if there isn't one.

        Every one of these fields is nullable in the GraphQL schema, so the key is
        present with a null value far more often than it is absent.
        """
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _update_device_info_from_status(self) -> None:
        """Update model, hardware/firmware version and serial number from device status."""
        assert self._attr_device_info is not None, "_attr_device_info must be set before this call"
        status = self._device.last_status
        if not status:
            return
        model = self._text(status.get("sysProductName")) or self._text(status.get("sysProductId"))
        if model:
            self._attr_device_info["model"] = model
        if hw_version := self._text(status.get("sysHwVersion")):
            self._attr_device_info["hw_version"] = hw_version
        if sw_version := self._text(status.get("sysFwVersion")):
            self._attr_device_info["sw_version"] = sw_version
        if serial_number := self._text(status.get("konnectSerial")):
            self._attr_device_info["serial_number"] = serial_number
