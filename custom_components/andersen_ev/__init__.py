"""The Andersen EV integration."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LIVE_STATUS_SETUP_TIMEOUT,
    LIVE_STATUS_TIMEOUT,
    SUBSCRIPTION_RETRY_MAX,
    SUBSCRIPTION_RETRY_MIN,
)
from .entity import DEVICE_INFO_FIELDS, device_info_from_status
from .konnect.changes import format_changes
from .konnect.client import KonnectClient
from .konnect.device import KonnectDevice
from .konnect.exceptions import AndersenApiError, AndersenAuthError, AndersenError
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.BUTTON, Platform.LOCK, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

type AndersenEvConfigEntry = ConfigEntry[AndersenEvCoordinator]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Andersen EV integration."""
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: AndersenEvConfigEntry) -> bool:
    """Migrate an old config entry."""
    if entry.version > 1:
        # Downgraded from a future version.
        return False
    if entry.minor_version < 2:
        # grid_power duplicated system_grid_power and its source field is always null.
        entity_registry = er.async_get(hass)
        device_registry = dr.async_get(hass)
        for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
            for domain, device_id in device_entry.identifiers:
                if domain == DOMAIN and (
                    entity_id := entity_registry.async_get_entity_id("sensor", DOMAIN, f"{device_id}_grid_power")
                ):
                    entity_registry.async_remove(entity_id)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    if entry.minor_version < 3:
        _async_merge_government_schedule_switches(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=3)
    return True


def _async_merge_government_schedule_switches(hass: HomeAssistant, entry: AndersenEvConfigEntry) -> None:
    """Remove the slot 1 schedule switch, now part of the Government Schedule switch on slot 0."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    removed: list[str] = []
    kept: list[str] = []
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        for domain, device_id in device_entry.identifiers:
            if domain != DOMAIN:
                continue
            if entity_id := entity_registry.async_get_entity_id("switch", DOMAIN, f"{device_id}_schedule_1"):
                entity_registry.async_remove(entity_id)
                removed.append(entity_id)
            if entity_id := entity_registry.async_get_entity_id("switch", DOMAIN, f"{device_id}_schedule_0"):
                kept.append(entity_id)
    if removed:
        # Automations and scripts that used the removed switch need pointing at the merged one.
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"government_schedule_merged_{entry.entry_id}",
            is_fixable=False,
            # The migration runs once, so the notice must survive restarts until dismissed.
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="government_schedule_merged",
            translation_placeholders={
                "removed_entities": ", ".join(removed),
                "government_entities": ", ".join(kept) or "the Government Schedule switch",
            },
        )


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

    # Entities read the subscription, and some (e.g. whether solar power is enabled) are decided from
    # its first payload, so give it a moment to arrive before creating them.
    await coordinator.async_wait_for_live_status(LIVE_STATUS_SETUP_TIMEOUT)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Covers a payload that arrived after the wait but before the devices were registered.
    coordinator.async_update_device_registry()
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
        self._rest_failing: set[str] = set()
        self._subscription_tasks: dict[str, asyncio.Task[None]] = {}
        self._subscription_failing: set[str] = set()
        self._live_status_received: dict[str, asyncio.Event] = {}
        self._live_timeout_unsubs: dict[str, CALLBACK_TYPE] = {}

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

        # Live status comes from the subscription; the poll only covers what it doesn't carry.
        for device in self.devices:
            _LOGGER.debug(
                "Device ID: %s, Name: %s, User Lock: %s",
                device.device_id,
                device.friendly_name,
                device.user_lock,
            )
            await self._update_user_device(device)

            try:
                await device.get_last_charge()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed to fetch last charge for %s: %s", device.friendly_name, err)

        self.async_start_subscriptions()
        return self.devices

    @callback
    def async_start_subscriptions(self) -> None:
        """Run a live status subscription for each device, and stop those of removed devices."""
        assert self.config_entry is not None
        current = {device.device_id: device for device in self.devices}
        for device_id in self._subscription_tasks.keys() - current.keys():
            self._forget_device(device_id)
        for device_id, device in current.items():
            if device_id not in self._subscription_tasks:
                self._subscription_tasks[device_id] = self.config_entry.async_create_background_task(
                    self.hass,
                    self._async_run_subscription(device),
                    name=f"{DOMAIN} subscription {device_id}",
                )

    def _forget_device(self, device_id: str) -> None:
        """Stop a removed device's subscription and drop its live status state."""
        self._subscription_tasks.pop(device_id).cancel()
        if unsub := self._live_timeout_unsubs.pop(device_id, None):
            unsub()
        self._subscription_failing.discard(device_id)
        self._live_status_received.pop(device_id, None)

    async def async_stop_subscriptions(self) -> None:
        """Cancel every subscription loop and wait for them to finish."""
        tasks = list(self._subscription_tasks.values())
        self._subscription_tasks.clear()
        for task in tasks:
            task.cancel()
        for unsub in self._live_timeout_unsubs.values():
            unsub()
        self._live_timeout_unsubs.clear()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def async_wait_for_live_status(self, timeout: float) -> None:
        """Wait until every device has had a subscription payload, or the timeout passes."""
        events = [self._live_status_event(device.device_id) for device in self.devices]
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*(event.wait() for event in events))
        except TimeoutError:
            _LOGGER.debug("No live status for every device within %s s, continuing setup", timeout)

    def _live_status_event(self, device_id: str) -> asyncio.Event:
        return self._live_status_received.setdefault(device_id, asyncio.Event())

    async def _async_run_subscription(self, device: KonnectDevice) -> None:
        """Keep a device's live status subscription open, resubscribing with backoff after failures."""
        retry_delay = SUBSCRIPTION_RETRY_MIN
        device.subscription_disconnected_since = dt_util.utcnow()
        self._schedule_live_timeout(device)
        try:
            while True:
                reason: object = "the server ended the subscription"
                try:
                    async for payload in device.subscription.updates():
                        self._handle_subscription_payload(device, payload)
                        retry_delay = SUBSCRIPTION_RETRY_MIN
                except AndersenAuthError as err:
                    # Retrying with credentials that are refused would only repeat the failure.
                    self._mark_disconnected(device)
                    _LOGGER.warning(
                        "Live status subscription for %s was refused the login (%s), starting re-authentication",
                        device.friendly_name,
                        err,
                    )
                    assert self.config_entry is not None
                    self.config_entry.async_start_reauth(self.hass)
                    return
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
            if unsub := self._live_timeout_unsubs.pop(device.device_id, None):
                unsub()
            if device.device_id in self._subscription_failing:
                self._subscription_failing.discard(device.device_id)
                _LOGGER.info("Live status subscription for %s restored", device.friendly_name)
        if initial:
            _LOGGER.debug("deviceStatusUpdated for %s (initial): %s", device.friendly_name, payload)
        elif changes:
            _LOGGER.debug("deviceStatusUpdated for %s: %s", device.friendly_name, format_changes(changes))
        self._live_status_event(device.device_id).set()
        if not changes:
            return
        device.update_solar_status_from_device_status(device.live_status)
        if not changes.keys().isdisjoint(DEVICE_INFO_FIELDS):
            self._update_device_registry_entry(device)
        self.async_update_listeners()

    @callback
    def async_update_device_registry(self) -> None:
        """Write each device's model, versions and serial number from its live status to the registry."""
        for device in self.devices:
            self._update_device_registry_entry(device)

    def _update_device_registry_entry(self, device: KonnectDevice) -> None:
        # Before the platforms are set up the device isn't registered yet; its entities fill it in then.
        device_registry = dr.async_get(self.hass)
        if (device_entry := device_registry.async_get_device(identifiers={(DOMAIN, device.device_id)})) and (
            info := device_info_from_status(device.live_status)
        ):
            device_registry.async_update_device(device_entry.id, **info)

    def _mark_disconnected(self, device: KonnectDevice) -> None:
        """Record that the subscription is down, starting the countdown to unavailable."""
        device.subscription_connected = False
        if device.subscription_disconnected_since is None:
            device.subscription_disconnected_since = dt_util.utcnow()
            self._schedule_live_timeout(device)

    def _mark_subscription_lost(self, device: KonnectDevice, reason: object, retry_delay: int) -> None:
        """Record a dropped subscription, warning once until it is restored."""
        self._mark_disconnected(device)
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

    def _schedule_live_timeout(self, device: KonnectDevice) -> None:
        """Refresh entities once the device has been disconnected for LIVE_STATUS_TIMEOUT, so they go unavailable."""
        if unsub := self._live_timeout_unsubs.pop(device.device_id, None):
            unsub()

        @callback
        def _timed_out(_now: object) -> None:
            self._live_timeout_unsubs.pop(device.device_id, None)
            self.async_update_listeners()

        self._live_timeout_unsubs[device.device_id] = async_call_later(
            self.hass, LIVE_STATUS_TIMEOUT.total_seconds(), _timed_out
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
