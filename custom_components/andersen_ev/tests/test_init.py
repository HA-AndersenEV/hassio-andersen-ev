"""Tests for the Andersen EV integration setup, coordinator, and services."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from andersen_ev import (
    PLATFORMS,
    AndersenEvCoordinator,
    _make_stale_device_listener,
    async_migrate_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from andersen_ev.const import (
    ATTR_DEVICE_ID,
    DOMAIN,
    LIVE_STATUS_SETUP_TIMEOUT,
    SERVICE_DISABLE_ALL_SCHEDULES,
    SERVICE_GET_DEVICE_INFO,
    SERVICE_GET_DEVICE_STATUS,
    SERVICE_RCM_RESET,
)
from andersen_ev.konnect.exceptions import AndersenApiError, AndersenAuthError, AndersenError


def _make_coordinator(client=None, hass=None):
    """Build an AndersenEvCoordinator without running DataUpdateCoordinator.__init__."""
    coordinator = AndersenEvCoordinator.__new__(AndersenEvCoordinator)
    coordinator.client = client or MagicMock()
    coordinator.devices = []
    coordinator._rest_failing = set()
    coordinator._subscription_tasks = {}
    coordinator._subscription_failing = set()
    coordinator._live_status_received = {}
    coordinator._live_timeout_unsubs = {}
    coordinator.hass = hass if hass is not None else MagicMock()
    coordinator.config_entry = MagicMock()
    setattr(coordinator, "async_start_subscriptions", MagicMock())
    return coordinator


def _make_device(device_id="device_1", friendly_name="Device", user_lock=False):
    """Build a MagicMock standing in for a KonnectDevice."""
    device = MagicMock()
    device.device_id = device_id
    device.friendly_name = friendly_name
    device.user_lock = user_lock
    device.get_detailed_device_status = AsyncMock(return_value={"online": True})
    device.get_last_charge = AsyncMock(return_value={"chargeEnergyTotal": 1.0})
    device.get_user_device = AsyncMock(return_value={"status": {"sysUserMaxChargeAmpSet": 16}})
    device.get_solar = AsyncMock()
    device.disable_all_schedules = AsyncMock()
    device.reset_rcm = AsyncMock()
    device.get_device_info = AsyncMock(return_value={"info": "data"})
    return device


def _make_hass():
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return hass


def _make_entry(email="test@example.com", password="testpass"):
    entry = MagicMock()
    entry.data = {"email": email, "password": password}
    entry.runtime_data = None
    return entry


async def _setup_and_get_handlers():
    """Run async_setup and return its service handlers and the coordinator of a loaded entry."""
    hass = _make_hass()
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.data = []
    hass.config_entries.async_loaded_entries.return_value = [MagicMock(runtime_data=coordinator)]

    await async_setup(hass, {})

    hass.config_entries.async_loaded_entries.assert_not_called()
    handlers = {c.args[1]: c.args[2] for c in hass.services.async_register.call_args_list}

    def _call(service: str, device_id: str):
        call = MagicMock(hass=hass, data={ATTR_DEVICE_ID: device_id})
        return handlers[service](call)

    return _call, coordinator


class TestAsyncUpdateData:
    """Tests for AndersenEvCoordinator._async_update_data()."""

    @pytest.mark.asyncio
    async def test_happy_path_polls_only_what_the_subscription_lacks(self):
        device = _make_device()
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result == [device]
        coordinator.async_start_subscriptions.assert_called_once_with()
        # Live status, including the solar cache, comes from the subscription.
        device.get_detailed_device_status.assert_not_awaited()
        device.update_solar_status_from_device_status.assert_not_called()
        device.get_solar.assert_not_awaited()
        device.get_last_charge.assert_awaited_once()
        device.get_user_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_successful_update_clears_api_shape_repair_issue(self):
        device = _make_device()
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)

        with patch("andersen_ev.ir.async_delete_issue") as mock_delete_issue:
            await coordinator._async_update_data()

        mock_delete_issue.assert_called_once_with(coordinator.hass, DOMAIN, "api_shape_changed")

    @pytest.mark.asyncio
    async def test_auth_error_raises_config_entry_auth_failed(self):
        client = MagicMock()
        client.getDevices = AsyncMock(side_effect=AndersenAuthError("bad credentials"))
        coordinator = _make_coordinator(client)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_connection_error_with_cache_returns_cached_devices(self):
        cached_device = _make_device()
        client = MagicMock()
        client.getDevices = AsyncMock(side_effect=AndersenError("network unreachable"))
        coordinator = _make_coordinator(client)
        coordinator.devices = [cached_device]

        result = await coordinator._async_update_data()

        assert result == [cached_device]

    @pytest.mark.asyncio
    async def test_connection_error_without_cache_raises_update_failed(self):
        client = MagicMock()
        client.getDevices = AsyncMock(side_effect=AndersenError("network unreachable"))
        coordinator = _make_coordinator(client)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_api_shape_error_creates_repair_issue_and_raises_update_failed(self):
        client = MagicMock()
        client.getDevices = AsyncMock(side_effect=AndersenApiError("missing 'devices' key"))
        coordinator = _make_coordinator(client)

        with (
            patch("andersen_ev.ir.async_create_issue") as mock_create_issue,
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()

        mock_create_issue.assert_called_once()
        args, kwargs = mock_create_issue.call_args
        assert args[0] is coordinator.hass
        assert args[1] == DOMAIN
        assert args[2] == "api_shape_changed"
        assert kwargs["translation_key"] == "api_shape_changed"
        assert kwargs["is_fixable"] is False
        assert kwargs["severity"] == ir.IssueSeverity.ERROR

    @pytest.mark.asyncio
    async def test_api_shape_error_with_cache_creates_issue_and_returns_cached_devices(self):
        cached_device = _make_device()
        client = MagicMock()
        client.getDevices = AsyncMock(side_effect=AndersenApiError("missing 'devices' key"))
        coordinator = _make_coordinator(client)
        coordinator.devices = [cached_device]

        with patch("andersen_ev.ir.async_create_issue") as mock_create_issue:
            result = await coordinator._async_update_data()

        assert result == [cached_device]
        mock_create_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_devices_with_cache_returns_cached_devices(self):
        cached_device = _make_device()
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[])
        coordinator = _make_coordinator(client)
        coordinator.devices = [cached_device]

        result = await coordinator._async_update_data()

        assert result == [cached_device]

    @pytest.mark.asyncio
    async def test_empty_devices_without_cache_returns_empty_list(self):
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[])
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result == []

    @pytest.mark.asyncio
    async def test_reuses_existing_device_object_for_matching_id(self):
        existing_device = _make_device(device_id="device_1", friendly_name="Old Name", user_lock=False)
        new_device = _make_device(device_id="device_1", friendly_name="New Name", user_lock=True)
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[new_device])
        coordinator = _make_coordinator(client)
        coordinator.devices = [existing_device]

        result = await coordinator._async_update_data()

        assert result == [existing_device]
        assert result[0] is existing_device
        assert existing_device.friendly_name == "New Name"
        assert existing_device.user_lock is True

    @pytest.mark.asyncio
    async def test_new_device_added_to_existing_set(self):
        existing_device = _make_device(device_id="device_1")
        new_device = _make_device(device_id="device_2")
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[existing_device, new_device])
        coordinator = _make_coordinator(client)
        coordinator.devices = [existing_device]

        result = await coordinator._async_update_data()

        assert existing_device in result
        assert new_device in result
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_last_charge_fetch_exception_is_not_fatal(self):
        """A failure fetching charge logs doesn't fail the refresh."""
        device = _make_device()
        device.get_last_charge = AsyncMock(side_effect=Exception("boom"))
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result == [device]

    @pytest.mark.asyncio
    async def test_rest_failure_keeps_device_available_and_warns_once(self, caplog: pytest.LogCaptureFixture) -> None:
        """A failed REST getDevice leaves the device available and only warns on the first failure."""
        device = _make_device()
        device.get_user_device = AsyncMock(side_effect=AndersenError("rest down"))
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)

        await coordinator._async_update_data()
        await coordinator._async_update_data()

        device.get_last_charge.assert_awaited()
        warnings = [r for r in caplog.records if "Failed to fetch REST device data" in r.getMessage()]
        assert len(warnings) == 1
        assert device.device_id in coordinator._rest_failing

    @pytest.mark.asyncio
    async def test_rest_recovery_clears_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        """A successful REST getDevice after a failure is logged, and warns again on the next failure."""
        device = _make_device()
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)
        coordinator._rest_failing.add(device.device_id)

        with caplog.at_level(logging.INFO):
            await coordinator._async_update_data()

        assert device.device_id not in coordinator._rest_failing
        assert "REST device data for Device is available again" in caplog.text


class TestMakeStaleDeviceListener:
    """Tests for _make_stale_device_listener() (the stale-devices rule)."""

    def test_removed_device_is_removed_from_registry(self):
        device_a = _make_device(device_id="device_1")
        device_b = _make_device(device_id="device_2")
        coordinator = _make_coordinator()
        coordinator.data = [device_a, device_b]
        hass = MagicMock()
        mock_registry = MagicMock()
        mock_registry_entry = MagicMock()
        mock_registry_entry.id = "registry_entry_1"
        mock_registry.async_get_device.return_value = mock_registry_entry

        with patch("andersen_ev.dr.async_get", return_value=mock_registry):
            listener = _make_stale_device_listener(hass, coordinator)
            coordinator.data = [device_a]
            listener()

        mock_registry.async_get_device.assert_called_once_with(identifiers={(DOMAIN, "device_2")})
        mock_registry.async_remove_device.assert_called_once_with("registry_entry_1")

    def test_no_change_removes_nothing(self):
        device_a = _make_device(device_id="device_1")
        coordinator = _make_coordinator()
        coordinator.data = [device_a]
        hass = MagicMock()
        mock_registry = MagicMock()

        with patch("andersen_ev.dr.async_get", return_value=mock_registry):
            listener = _make_stale_device_listener(hass, coordinator)
            listener()

        mock_registry.async_remove_device.assert_not_called()

    def test_device_missing_from_registry_skips_removal(self):
        device_a = _make_device(device_id="device_1")
        coordinator = _make_coordinator()
        coordinator.data = [device_a]
        hass = MagicMock()
        mock_registry = MagicMock()
        mock_registry.async_get_device.return_value = None

        with patch("andersen_ev.dr.async_get", return_value=mock_registry):
            listener = _make_stale_device_listener(hass, coordinator)
            coordinator.data = []
            listener()

        mock_registry.async_remove_device.assert_not_called()

    def test_new_device_appearing_is_tracked_without_removal(self):
        device_a = _make_device(device_id="device_1")
        device_b = _make_device(device_id="device_2")
        coordinator = _make_coordinator()
        coordinator.data = [device_a]
        hass = MagicMock()
        mock_registry = MagicMock()

        with patch("andersen_ev.dr.async_get", return_value=mock_registry):
            listener = _make_stale_device_listener(hass, coordinator)
            coordinator.data = [device_a, device_b]
            listener()

            # A subsequent refresh with no further changes should still remove nothing.
            listener()

        mock_registry.async_remove_device.assert_not_called()


class TestAsyncSetupEntry:
    """Tests for async_setup_entry()."""

    @pytest.mark.asyncio
    async def test_registers_stale_device_cleanup_listener(self):
        hass = _make_hass()
        entry = _make_entry()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_wait_for_live_status = AsyncMock()
        mock_coordinator.data = []

        with (
            patch("andersen_ev.KonnectClient"),
            patch("andersen_ev.AndersenEvCoordinator", return_value=mock_coordinator),
        ):
            await async_setup_entry(hass, entry)

        mock_coordinator.async_add_listener.assert_called_once()
        entry.async_on_unload.assert_called_once()

    @pytest.mark.asyncio
    async def test_waits_for_live_status_before_creating_entities(self):
        hass = _make_hass()
        entry = _make_entry()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        order = MagicMock()
        mock_coordinator.async_wait_for_live_status = AsyncMock(side_effect=lambda _timeout: order("wait"))
        mock_coordinator.async_update_device_registry.side_effect = lambda: order("registry")
        hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=lambda *_args: order("platforms"))

        with (
            patch("andersen_ev.KonnectClient"),
            patch("andersen_ev.AndersenEvCoordinator", return_value=mock_coordinator),
        ):
            await async_setup_entry(hass, entry)

        mock_coordinator.async_wait_for_live_status.assert_awaited_once_with(LIVE_STATUS_SETUP_TIMEOUT)
        assert [c.args[0] for c in order.call_args_list] == ["wait", "platforms", "registry"]

    @pytest.mark.asyncio
    async def test_creates_client_and_coordinator_and_stores_runtime_data(self):
        hass = _make_hass()
        entry = _make_entry()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_wait_for_live_status = AsyncMock()

        with (
            patch("andersen_ev.KonnectClient") as mock_client_cls,
            patch("andersen_ev.AndersenEvCoordinator", return_value=mock_coordinator) as mock_coordinator_cls,
        ):
            result = await async_setup_entry(hass, entry)

        mock_client_cls.assert_called_once_with("test@example.com", "testpass")
        mock_coordinator_cls.assert_called_once_with(hass, entry, mock_client_cls.return_value)
        mock_coordinator.async_config_entry_first_refresh.assert_awaited_once()
        assert entry.runtime_data is mock_coordinator
        assert result is True

    @pytest.mark.asyncio
    async def test_does_not_register_services(self) -> None:
        """Services are registered once in async_setup, not per config entry."""
        hass = _make_hass()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_wait_for_live_status = AsyncMock()

        with (
            patch("andersen_ev.KonnectClient"),
            patch("andersen_ev.AndersenEvCoordinator", return_value=mock_coordinator),
        ):
            await async_setup_entry(hass, _make_entry())

        hass.services.async_register.assert_not_called()


class TestAsyncSetup:
    """Tests for async_setup()."""

    @pytest.mark.asyncio
    async def test_registers_all_four_services(self) -> None:
        hass = _make_hass()

        assert await async_setup(hass, {}) is True

        registered = {c.args[1]: c for c in hass.services.async_register.call_args_list}
        assert set(registered) == {
            SERVICE_DISABLE_ALL_SCHEDULES,
            SERVICE_GET_DEVICE_INFO,
            SERVICE_GET_DEVICE_STATUS,
            SERVICE_RCM_RESET,
        }
        for registered_call in hass.services.async_register.call_args_list:
            assert registered_call.args[0] == DOMAIN
        assert registered[SERVICE_GET_DEVICE_INFO].kwargs["supports_response"] is SupportsResponse.OPTIONAL
        assert registered[SERVICE_GET_DEVICE_STATUS].kwargs["supports_response"] is SupportsResponse.OPTIONAL

    @pytest.mark.asyncio
    async def test_forwards_platform_setup(self):
        hass = _make_hass()
        entry = _make_entry()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.async_wait_for_live_status = AsyncMock()

        with (
            patch("andersen_ev.KonnectClient"),
            patch("andersen_ev.AndersenEvCoordinator", return_value=mock_coordinator),
        ):
            await async_setup_entry(hass, entry)

        hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        call_args = hass.config_entries.async_forward_entry_setups.call_args.args
        assert call_args[0] is entry
        assert list(call_args[1]) == [
            Platform.BUTTON,
            Platform.LOCK,
            Platform.NUMBER,
            Platform.SENSOR,
            Platform.SWITCH,
        ]


class TestAsyncMigrateEntry:
    """Tests for async_migrate_entry()."""

    @staticmethod
    def _entry(version: int = 1, minor_version: int = 1) -> MagicMock:
        return MagicMock(version=version, minor_version=minor_version, entry_id="entry_1")

    @pytest.mark.asyncio
    async def test_removes_retired_grid_power_entity(self) -> None:
        hass = _make_hass()
        entry = self._entry()
        devices = [
            MagicMock(identifiers={(DOMAIN, "device_1")}),
            MagicMock(identifiers={(DOMAIN, "device_2"), ("other", "x")}),
        ]
        registry = MagicMock()
        registry.async_get_entity_id.side_effect = lambda _domain, _platform, unique_id: (
            "sensor.device_grid_power" if unique_id == "device_1_grid_power" else None
        )

        with (
            patch("andersen_ev.er.async_get", return_value=registry),
            patch("andersen_ev.dr.async_get"),
            patch("andersen_ev.dr.async_entries_for_config_entry", return_value=devices),
        ):
            assert await async_migrate_entry(hass, entry) is True

        registry.async_get_entity_id.assert_any_call("sensor", DOMAIN, "device_2_grid_power")
        assert registry.async_get_entity_id.call_count == 2
        registry.async_remove.assert_called_once_with("sensor.device_grid_power")
        hass.config_entries.async_update_entry.assert_called_once_with(entry, minor_version=2)

    @pytest.mark.asyncio
    async def test_current_version_is_left_alone(self) -> None:
        hass = _make_hass()

        with patch("andersen_ev.er.async_get") as mock_er:
            assert await async_migrate_entry(hass, self._entry(minor_version=2)) is True

        mock_er.assert_not_called()
        hass.config_entries.async_update_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_future_version_is_refused(self) -> None:
        """A downgrade from a newer major version can't be migrated."""
        hass = _make_hass()

        assert await async_migrate_entry(hass, self._entry(version=2)) is False

        hass.config_entries.async_update_entry.assert_not_called()


class TestAsyncUnloadEntry:
    """Tests for async_unload_entry()."""

    @pytest.mark.asyncio
    async def test_closes_all_devices_and_unloads_platforms(self):
        hass = _make_hass()
        device_a = MagicMock()
        device_a.close = AsyncMock()
        device_b = MagicMock()
        device_b.close = AsyncMock()
        entry = _make_entry()
        entry.runtime_data = MagicMock(devices=[device_a, device_b])
        order = MagicMock()
        entry.runtime_data.async_stop_subscriptions = AsyncMock(side_effect=lambda: order("stop"))
        device_a.close.side_effect = lambda: order("close")

        result = await async_unload_entry(hass, entry)

        assert [c.args[0] for c in order.call_args_list] == ["stop", "close"]
        device_a.close.assert_awaited_once()
        device_b.close.assert_awaited_once()
        hass.config_entries.async_unload_platforms.assert_awaited_once_with(entry, PLATFORMS)
        assert result is True


class TestDisableAllSchedulesService:
    """Tests for the disable_all_schedules service handler."""

    @pytest.mark.asyncio
    async def test_calls_device_method_without_refresh(self):
        call_service, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        coordinator.data = [device]

        await call_service(SERVICE_DISABLE_ALL_SCHEDULES, "device_1")

        device.disable_all_schedules.assert_awaited_once()
        coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_device_raises_service_validation_error(self):
        call_service, coordinator = await _setup_and_get_handlers()
        coordinator.data = []

        with pytest.raises(ServiceValidationError):
            await call_service(SERVICE_DISABLE_ALL_SCHEDULES, "missing")


class TestGetDeviceInfoService:
    """Tests for the get_device_info service handler."""

    @pytest.mark.asyncio
    async def test_returns_device_info_when_found(self):
        call_service, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        device.get_device_info = AsyncMock(return_value={"name": "Andersen A2"})
        coordinator.data = [device]

        result = await call_service(SERVICE_GET_DEVICE_INFO, "device_1")

        assert result == {"name": "Andersen A2"}

    @pytest.mark.asyncio
    async def test_raises_when_device_info_is_none(self):
        call_service, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        device.get_device_info = AsyncMock(return_value=None)
        coordinator.data = [device]

        with pytest.raises(HomeAssistantError):
            await call_service(SERVICE_GET_DEVICE_INFO, "device_1")

    @pytest.mark.asyncio
    async def test_unknown_device_raises_service_validation_error(self):
        call_service, coordinator = await _setup_and_get_handlers()
        coordinator.data = []

        with pytest.raises(ServiceValidationError):
            await call_service(SERVICE_GET_DEVICE_INFO, "missing")


class TestGetDeviceStatusService:
    """Tests for the get_device_status service handler."""

    @pytest.mark.asyncio
    async def test_returns_device_status_when_found(self):
        call_service, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        device.get_detailed_device_status = AsyncMock(return_value={"online": True})
        coordinator.data = [device]

        result = await call_service(SERVICE_GET_DEVICE_STATUS, "device_1")

        assert result == {"online": True}

    @pytest.mark.asyncio
    async def test_raises_when_device_status_is_none(self):
        call_service, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        device.get_detailed_device_status = AsyncMock(return_value=None)
        coordinator.data = [device]

        with pytest.raises(HomeAssistantError):
            await call_service(SERVICE_GET_DEVICE_STATUS, "device_1")

    @pytest.mark.asyncio
    async def test_unknown_device_raises_service_validation_error(self):
        call_service, coordinator = await _setup_and_get_handlers()
        coordinator.data = []

        with pytest.raises(ServiceValidationError):
            await call_service(SERVICE_GET_DEVICE_STATUS, "missing")


class TestResetRcmService:
    """Tests for the reset_rcm service handler."""

    @pytest.mark.asyncio
    async def test_calls_device_method_without_refresh(self):
        call_service, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        coordinator.data = [device]

        await call_service(SERVICE_RCM_RESET, "device_1")

        device.reset_rcm.assert_awaited_once()
        coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_device_raises_service_validation_error(self):
        call_service, coordinator = await _setup_and_get_handlers()
        coordinator.data = []

        with pytest.raises(ServiceValidationError):
            await call_service(SERVICE_RCM_RESET, "missing")
