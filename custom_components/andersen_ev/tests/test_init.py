"""Tests for the Andersen EV integration setup, coordinator, and services."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from andersen_ev import (
    PLATFORMS,
    AndersenEvCoordinator,
    _make_stale_device_listener,
    async_setup_entry,
    async_unload_entry,
)
from andersen_ev.const import (
    ATTR_DEVICE_ID,
    DOMAIN,
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
    coordinator._device_availability = {}
    coordinator.hass = hass if hass is not None else MagicMock()
    return coordinator


def _make_device(device_id="device_1", friendly_name="Device", user_lock=False):
    """Build a MagicMock standing in for a KonnectDevice."""
    device = MagicMock()
    device.device_id = device_id
    device.friendly_name = friendly_name
    device.user_lock = user_lock
    device.status_available = None
    device.get_detailed_device_status = AsyncMock(return_value={"online": True})
    device.get_last_charge = AsyncMock(return_value={"chargeEnergyTotal": 1.0})
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


def _make_call(device_id="device_1"):
    call = MagicMock()
    call.data = {ATTR_DEVICE_ID: device_id}
    return call


async def _setup_and_get_handlers():
    """Run async_setup_entry with a stubbed coordinator and return its registered service handlers."""
    hass = _make_hass()
    entry = _make_entry()
    mock_coordinator = MagicMock()
    mock_coordinator.async_config_entry_first_refresh = AsyncMock()
    mock_coordinator.async_request_refresh = AsyncMock()
    mock_coordinator.data = []

    with (
        patch("andersen_ev.KonnectClient"),
        patch("andersen_ev.AndersenEvCoordinator", return_value=mock_coordinator),
    ):
        await async_setup_entry(hass, entry)

    handlers = {c.args[1]: c.args[2] for c in hass.services.async_register.call_args_list}
    return handlers, mock_coordinator


class TestAsyncUpdateData:
    """Tests for AndersenEvCoordinator._async_update_data()."""

    @pytest.mark.asyncio
    async def test_happy_path_fetches_status_and_marks_available(self):
        device = _make_device()
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result == [device]
        device.get_detailed_device_status.assert_awaited_once()
        device.get_last_charge.assert_awaited_once()
        assert device.status_available is True
        assert coordinator._device_availability[device.device_id] is True

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
    async def test_status_fetch_exception_marks_device_unavailable(self):
        device = _make_device()
        device.get_detailed_device_status = AsyncMock(side_effect=Exception("boom"))
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)

        await coordinator._async_update_data()

        assert device.status_available is False
        assert coordinator._device_availability[device.device_id] is False
        device.get_last_charge.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_none_marks_device_unavailable(self):
        device = _make_device()
        device.get_detailed_device_status = AsyncMock(return_value=None)
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)

        await coordinator._async_update_data()

        assert device.status_available is False
        assert coordinator._device_availability[device.device_id] is False
        device.get_last_charge.assert_not_called()

    @pytest.mark.asyncio
    async def test_last_charge_fetch_exception_does_not_mark_device_unavailable(self):
        """A failure fetching charge logs is independent of the primary status fetch."""
        device = _make_device()
        device.get_last_charge = AsyncMock(side_effect=Exception("boom"))
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)

        result = await coordinator._async_update_data()

        assert result == [device]
        assert device.status_available is True
        assert coordinator._device_availability[device.device_id] is True

    @pytest.mark.asyncio
    async def test_device_recovery_logs_info(self):
        device = _make_device()
        client = MagicMock()
        client.getDevices = AsyncMock(return_value=[device])
        coordinator = _make_coordinator(client)
        coordinator._device_availability[device.device_id] = False

        with patch("andersen_ev._LOGGER") as mock_logger:
            await coordinator._async_update_data()

        assert device.status_available is True
        assert coordinator._device_availability[device.device_id] is True
        mock_logger.info.assert_called_once()


class TestMarkDeviceUnavailable:
    """Tests for AndersenEvCoordinator._mark_device_unavailable()."""

    def test_first_unavailable_transition_logs_warning(self):
        coordinator = _make_coordinator()
        device = _make_device()

        with patch("andersen_ev._LOGGER") as mock_logger:
            coordinator._mark_device_unavailable(device, "boom")

        assert device.status_available is False
        assert coordinator._device_availability[device.device_id] is False
        mock_logger.warning.assert_called_once()
        mock_logger.debug.assert_not_called()

    def test_already_unavailable_logs_debug_not_warning(self):
        coordinator = _make_coordinator()
        device = _make_device()
        coordinator._device_availability[device.device_id] = False

        with patch("andersen_ev._LOGGER") as mock_logger:
            coordinator._mark_device_unavailable(device, "still broken")

        mock_logger.debug.assert_called_once()
        mock_logger.warning.assert_not_called()


class TestAsyncRequestRefresh:
    """Tests for AndersenEvCoordinator.async_request_refresh()."""

    @pytest.mark.asyncio
    async def test_sleeps_before_delegating_to_parent(self):
        coordinator = _make_coordinator()

        with (
            patch("andersen_ev.asyncio.sleep", new=AsyncMock()) as mock_sleep,
            patch.object(DataUpdateCoordinator, "async_request_refresh", new=AsyncMock()) as mock_parent_refresh,
        ):
            await coordinator.async_request_refresh()

        mock_sleep.assert_awaited_once_with(1.5)
        mock_parent_refresh.assert_awaited_once()


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
        mock_coordinator.data = []

        with (
            patch("andersen_ev.KonnectClient"),
            patch("andersen_ev.AndersenEvCoordinator", return_value=mock_coordinator),
        ):
            await async_setup_entry(hass, entry)

        mock_coordinator.async_add_listener.assert_called_once()
        entry.async_on_unload.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_client_and_coordinator_and_stores_runtime_data(self):
        hass = _make_hass()
        entry = _make_entry()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()

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
    async def test_registers_all_four_services(self):
        hass = _make_hass()
        entry = _make_entry()
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()

        with (
            patch("andersen_ev.KonnectClient"),
            patch("andersen_ev.AndersenEvCoordinator", return_value=mock_coordinator),
        ):
            await async_setup_entry(hass, entry)

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

        with (
            patch("andersen_ev.KonnectClient"),
            patch("andersen_ev.AndersenEvCoordinator", return_value=mock_coordinator),
        ):
            await async_setup_entry(hass, entry)

        hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        call_args = hass.config_entries.async_forward_entry_setups.call_args.args
        assert call_args[0] is entry
        assert list(call_args[1]) == [Platform.LOCK, Platform.SENSOR, Platform.SWITCH]


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

        result = await async_unload_entry(hass, entry)

        device_a.close.assert_awaited_once()
        device_b.close.assert_awaited_once()
        hass.config_entries.async_unload_platforms.assert_awaited_once_with(entry, PLATFORMS)
        assert result is True


class TestDisableAllSchedulesService:
    """Tests for the disable_all_schedules service handler."""

    @pytest.mark.asyncio
    async def test_calls_device_method_and_requests_refresh(self):
        handlers, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        coordinator.data = [device]

        await handlers[SERVICE_DISABLE_ALL_SCHEDULES](_make_call("device_1"))

        device.disable_all_schedules.assert_awaited_once()
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_device_raises_home_assistant_error(self):
        handlers, coordinator = await _setup_and_get_handlers()
        coordinator.data = []

        with pytest.raises(HomeAssistantError):
            await handlers[SERVICE_DISABLE_ALL_SCHEDULES](_make_call("missing"))


class TestGetDeviceInfoService:
    """Tests for the get_device_info service handler."""

    @pytest.mark.asyncio
    async def test_returns_device_info_when_found(self):
        handlers, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        device.get_device_info = AsyncMock(return_value={"name": "Andersen A2"})
        coordinator.data = [device]

        result = await handlers[SERVICE_GET_DEVICE_INFO](_make_call("device_1"))

        assert result == {"name": "Andersen A2"}

    @pytest.mark.asyncio
    async def test_raises_when_device_info_is_none(self):
        handlers, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        device.get_device_info = AsyncMock(return_value=None)
        coordinator.data = [device]

        with pytest.raises(HomeAssistantError):
            await handlers[SERVICE_GET_DEVICE_INFO](_make_call("device_1"))

    @pytest.mark.asyncio
    async def test_unknown_device_raises_home_assistant_error(self):
        handlers, coordinator = await _setup_and_get_handlers()
        coordinator.data = []

        with pytest.raises(HomeAssistantError):
            await handlers[SERVICE_GET_DEVICE_INFO](_make_call("missing"))


class TestGetDeviceStatusService:
    """Tests for the get_device_status service handler."""

    @pytest.mark.asyncio
    async def test_returns_device_status_when_found(self):
        handlers, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        device.get_detailed_device_status = AsyncMock(return_value={"online": True})
        coordinator.data = [device]

        result = await handlers[SERVICE_GET_DEVICE_STATUS](_make_call("device_1"))

        assert result == {"online": True}

    @pytest.mark.asyncio
    async def test_raises_when_device_status_is_none(self):
        handlers, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        device.get_detailed_device_status = AsyncMock(return_value=None)
        coordinator.data = [device]

        with pytest.raises(HomeAssistantError):
            await handlers[SERVICE_GET_DEVICE_STATUS](_make_call("device_1"))

    @pytest.mark.asyncio
    async def test_unknown_device_raises_home_assistant_error(self):
        handlers, coordinator = await _setup_and_get_handlers()
        coordinator.data = []

        with pytest.raises(HomeAssistantError):
            await handlers[SERVICE_GET_DEVICE_STATUS](_make_call("missing"))


class TestResetRcmService:
    """Tests for the reset_rcm service handler."""

    @pytest.mark.asyncio
    async def test_calls_device_method_and_requests_refresh(self):
        handlers, coordinator = await _setup_and_get_handlers()
        device = _make_device(device_id="device_1")
        coordinator.data = [device]

        await handlers[SERVICE_RCM_RESET](_make_call("device_1"))

        device.reset_rcm.assert_awaited_once()
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_device_raises_home_assistant_error(self):
        handlers, coordinator = await _setup_and_get_handlers()
        coordinator.data = []

        with pytest.raises(HomeAssistantError):
            await handlers[SERVICE_RCM_RESET](_make_call("missing"))
