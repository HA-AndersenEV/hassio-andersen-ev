"""Button platform for Andersen EV charge now and stop charging."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvConfigEntry, AndersenEvCoordinator
from .const import DOMAIN
from .entity import AndersenEvDeviceInfoMixin
from .konnect.device import KonnectDevice

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AndersenEvButtonEntityDescription(ButtonEntityDescription):
    """Describes an Andersen EV button."""

    press_fn: Callable[[KonnectDevice], Awaitable[bool]]


BUTTONS: tuple[AndersenEvButtonEntityDescription, ...] = (
    AndersenEvButtonEntityDescription(
        key="charge_now",
        translation_key="charge_now",
        press_fn=lambda device: device.charge_now(),
    ),
    AndersenEvButtonEntityDescription(
        key="stop_charging",
        translation_key="stop_charging",
        press_fn=lambda device: device.stop_charging(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: AndersenEvConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Andersen EV button platform."""
    coordinator = entry.runtime_data
    known_device_ids: set[str] = set()

    def _entities_for_new_devices() -> list[AndersenEvButton]:
        """Build button entities for any device not seen before."""
        known_device_ids.intersection_update(device.device_id for device in coordinator.data)
        new_devices = [device for device in coordinator.data if device.device_id not in known_device_ids]
        entities: list[AndersenEvButton] = []
        for device in new_devices:
            known_device_ids.add(device.device_id)
            entities.extend(AndersenEvButton(coordinator, device, description) for description in BUTTONS)
        return entities

    def _handle_coordinator_update() -> None:
        if new_entities := _entities_for_new_devices():
            async_add_entities(new_entities)

    async_add_entities(_entities_for_new_devices())
    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class AndersenEvButton(AndersenEvDeviceInfoMixin, CoordinatorEntity[AndersenEvCoordinator], ButtonEntity):  # pylint: disable=abstract-method
    """Button that sends a charging command to an Andersen EV charger."""

    _attr_has_entity_name = True
    entity_description: AndersenEvButtonEntityDescription

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device: KonnectDevice,
        description: AndersenEvButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device = device
        self._attr_unique_id = f"{device.device_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            name=f"{device.friendly_name} ({device.device_id})",
            manufacturer="Andersen EV",
            serial_number=f"{device.device_id}",
        )
        self._update_device_info_from_status()

    @property
    def available(self) -> bool:
        """Return if the button is available."""
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return self.coordinator.last_update_success and self._device.status_available
        return False

    async def async_press(self) -> None:
        """Send the command to the charger."""
        if not await self.entity_description.press_fn(self._device):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=f"{self.entity_description.key}_failed",
                translation_placeholders={"device_name": self._device.friendly_name},
            )
        await self.coordinator.async_request_refresh()
