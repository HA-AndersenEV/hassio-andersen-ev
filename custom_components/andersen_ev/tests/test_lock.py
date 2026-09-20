"""Tests for the Andersen EV lock platform."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from andersen_ev.lock import AndersenEvLock, async_setup_entry


def _make_device(
    device_id="device_1",
    friendly_name="Device",
    user_lock=False,
    status_available=True,
    last_status=None,
):
    """Build a mock KonnectDevice."""
    device = MagicMock()
    device.device_id = device_id
    device.friendly_name = friendly_name
    device.user_lock = user_lock
    device.status_available = status_available
    device.last_status = last_status
    device.disable = AsyncMock(return_value=True)
    device.enable = AsyncMock(return_value=True)
    return device


def _make_coordinator(devices=None, last_update_success=True):
    """Build a mock AndersenEvCoordinator."""
    coordinator = MagicMock()
    coordinator.data = devices or []
    coordinator.last_update_success = last_update_success
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


class TestAsyncSetupEntry:
    """Tests for async_setup_entry()."""

    @pytest.mark.asyncio
    async def test_creates_lock_entity_per_device(self):
        device_a = _make_device(device_id="device_1")
        device_b = _make_device(device_id="device_2")
        coordinator = _make_coordinator([device_a, device_b])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args.args[0]
        assert len(entities) == 2
        assert all(isinstance(entity, AndersenEvLock) for entity in entities)

    @pytest.mark.asyncio
    async def test_no_devices_creates_no_entities(self):
        coordinator = _make_coordinator([])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        entities = async_add_entities.call_args.args[0]
        assert entities == []


class TestDynamicDevices:
    """Tests for the dynamic-devices coordinator listener registered in async_setup_entry()."""

    @pytest.mark.asyncio
    async def test_new_device_added_without_reload(self):
        device_a = _make_device(device_id="device_1")
        coordinator = _make_coordinator([device_a])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        assert async_add_entities.call_count == 1
        entry.async_on_unload.assert_called_once()
        listener = coordinator.async_add_listener.call_args.args[0]

        device_b = _make_device(device_id="device_2")
        coordinator.data = [device_a, device_b]
        listener()

        assert async_add_entities.call_count == 2
        new_entities = async_add_entities.call_args.args[0]
        assert len(new_entities) == 1
        assert new_entities[0]._device.device_id == "device_2"

    @pytest.mark.asyncio
    async def test_no_new_devices_does_not_call_add_entities_again(self):
        device_a = _make_device(device_id="device_1")
        coordinator = _make_coordinator([device_a])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)
        listener = coordinator.async_add_listener.call_args.args[0]

        listener()

        assert async_add_entities.call_count == 1

    @pytest.mark.asyncio
    async def test_device_readded_after_disappearing_gets_new_entity(self):
        """A device_id that drops out and reappears must not be treated as already known."""
        device_a = _make_device(device_id="device_1")
        coordinator = _make_coordinator([device_a])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)
        listener = coordinator.async_add_listener.call_args.args[0]

        # device_a drops out of the coordinator's data for a cycle.
        coordinator.data = []
        listener()
        assert async_add_entities.call_count == 1

        # ...then reappears with the same device_id.
        coordinator.data = [device_a]
        listener()

        assert async_add_entities.call_count == 2
        new_entities = async_add_entities.call_args.args[0]
        assert len(new_entities) == 1
        assert new_entities[0]._device.device_id == "device_1"


class TestInit:
    """Tests for AndersenEvLock.__init__()."""

    def test_sets_unique_id_name_and_device_info(self):
        device = _make_device(device_id="device_1", friendly_name="My Charger")
        coordinator = _make_coordinator([device])

        lock = AndersenEvLock(coordinator, device)

        assert lock._attr_unique_id == "device_1_lock"
        assert lock._attr_translation_key == "lock"
        assert lock._attr_has_entity_name is True
        assert lock._attr_device_info["name"] == "My Charger (device_1)"
        assert lock._attr_device_info["manufacturer"] == "Andersen EV"
        assert lock._attr_device_info["serial_number"] == "device_1"


class TestUpdateDeviceInfoFromStatus:
    """Tests for AndersenEvLock._update_device_info_from_status()."""

    def test_prefers_product_name_over_product_id(self):
        device = _make_device(last_status={"sysProductName": "Thurlestone", "sysProductId": 30})
        coordinator = _make_coordinator([device])

        lock = AndersenEvLock(coordinator, device)

        assert lock._attr_device_info["model"] == "Thurlestone"

    def test_integer_product_id_rendered_as_string(self):
        device = _make_device(last_status={"sysProductId": 30})
        coordinator = _make_coordinator([device])

        lock = AndersenEvLock(coordinator, device)

        assert lock._attr_device_info["model"] == "30"

    def test_hw_fw_and_serial_populated(self):
        device = _make_device(last_status={"sysHwVersion": "4", "sysFwVersion": "314", "konnectSerial": "1234567890"})
        coordinator = _make_coordinator([device])

        lock = AndersenEvLock(coordinator, device)

        assert lock._attr_device_info["hw_version"] == "4"
        assert lock._attr_device_info["sw_version"] == "314"
        assert lock._attr_device_info["serial_number"] == "1234567890"

    def test_no_status_keeps_default_device_info(self):
        device = _make_device(last_status=None)
        coordinator = _make_coordinator([device])

        lock = AndersenEvLock(coordinator, device)

        assert "model" not in lock._attr_device_info
        assert "hw_version" not in lock._attr_device_info
        assert "sw_version" not in lock._attr_device_info
        assert lock._attr_device_info["serial_number"] == device.device_id


class TestAvailable:
    """Tests for AndersenEvLock.available."""

    def test_available_when_device_found_and_status_available(self):
        device = _make_device(status_available=True)
        coordinator = _make_coordinator([device], last_update_success=True)
        lock = AndersenEvLock(coordinator, device)

        assert lock.available is True

    def test_unavailable_when_status_unavailable(self):
        device = _make_device(status_available=False)
        coordinator = _make_coordinator([device], last_update_success=True)
        lock = AndersenEvLock(coordinator, device)

        assert lock.available is False

    def test_unavailable_when_coordinator_update_failed(self):
        device = _make_device(status_available=True)
        coordinator = _make_coordinator([device], last_update_success=False)
        lock = AndersenEvLock(coordinator, device)

        assert lock.available is False

    def test_unavailable_when_device_not_found(self):
        device = _make_device(device_id="device_1")
        coordinator = _make_coordinator([], last_update_success=True)
        lock = AndersenEvLock(coordinator, device)

        assert lock.available is False


class TestIsLocked:
    """Tests for AndersenEvLock.is_locked."""

    def test_returns_sys_user_lock_true(self):
        device = _make_device(last_status={"sysUserLock": True})
        coordinator = _make_coordinator([device])
        lock = AndersenEvLock(coordinator, device)

        assert lock.is_locked is True

    def test_returns_sys_user_lock_false(self):
        device = _make_device(last_status={"sysUserLock": False})
        coordinator = _make_coordinator([device])
        lock = AndersenEvLock(coordinator, device)

        assert lock.is_locked is False

    def test_falls_back_to_user_lock_when_no_status(self):
        device = _make_device(last_status=None, user_lock=False)
        coordinator = _make_coordinator([device])
        lock = AndersenEvLock(coordinator, device)

        assert lock.is_locked is True

    def test_falls_back_to_user_lock_when_key_missing(self):
        device = _make_device(last_status={"other": 1}, user_lock=True)
        coordinator = _make_coordinator([device])
        lock = AndersenEvLock(coordinator, device)

        assert lock.is_locked is False

    def test_device_not_found_returns_false(self):
        device = _make_device(device_id="device_1")
        coordinator = _make_coordinator([])
        lock = AndersenEvLock(coordinator, device)

        assert lock.is_locked is False

    def test_exception_reading_status_falls_back(self):
        class RaisingStatus:
            def __contains__(self, item):
                raise RuntimeError("boom")

            def __bool__(self):
                return True

        device = _make_device(last_status=None, user_lock=False)
        coordinator = _make_coordinator([device])
        lock = AndersenEvLock(coordinator, device)
        lock._device.last_status = RaisingStatus()

        assert lock.is_locked is True


class TestAsyncLock:
    """Tests for AndersenEvLock.async_lock()."""

    @pytest.mark.asyncio
    async def test_success_requests_refresh(self):
        device = _make_device()
        device.disable = AsyncMock(return_value=True)
        coordinator = _make_coordinator([device])
        lock = AndersenEvLock(coordinator, device)

        await lock.async_lock()

        device.disable.assert_awaited_once()
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_raises_home_assistant_error(self):
        device = _make_device()
        device.disable = AsyncMock(return_value=False)
        coordinator = _make_coordinator([device])
        lock = AndersenEvLock(coordinator, device)

        with pytest.raises(HomeAssistantError):
            await lock.async_lock()

        coordinator.async_request_refresh.assert_not_awaited()


class TestAsyncUnlock:
    """Tests for AndersenEvLock.async_unlock()."""

    @pytest.mark.asyncio
    async def test_success_requests_refresh(self):
        device = _make_device()
        device.enable = AsyncMock(return_value=True)
        coordinator = _make_coordinator([device])
        lock = AndersenEvLock(coordinator, device)

        await lock.async_unlock()

        device.enable.assert_awaited_once()
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_raises_home_assistant_error(self):
        device = _make_device()
        device.enable = AsyncMock(return_value=False)
        coordinator = _make_coordinator([device])
        lock = AndersenEvLock(coordinator, device)

        with pytest.raises(HomeAssistantError):
            await lock.async_unlock()

        coordinator.async_request_refresh.assert_not_awaited()
