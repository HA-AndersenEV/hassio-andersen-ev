"""Tests for the Andersen EV button platform."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from andersen_ev.button import BUTTONS, AndersenEvButton, async_setup_entry

CHARGE_NOW, STOP_CHARGING = BUTTONS


def _make_device(device_id="device_1", friendly_name="Device", status_available=True):
    """Build a mock KonnectDevice."""
    device = MagicMock()
    device.device_id = device_id
    device.friendly_name = friendly_name
    device.status_available = status_available
    device.last_status = None
    device.charge_now = AsyncMock(return_value=True)
    device.stop_charging = AsyncMock(return_value=True)
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
    async def test_creates_buttons_per_device(self):
        coordinator = _make_coordinator([_make_device("device_1"), _make_device("device_2")])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        entities = async_add_entities.call_args.args[0]
        assert sorted(entity.unique_id for entity in entities) == [
            "device_1_charge_now",
            "device_1_stop_charging",
            "device_2_charge_now",
            "device_2_stop_charging",
        ]

    @pytest.mark.asyncio
    async def test_new_device_added_without_reload(self):
        coordinator = _make_coordinator([_make_device("device_1")])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)
        listener = coordinator.async_add_listener.call_args.args[0]

        coordinator.data = [coordinator.data[0], _make_device("device_2")]
        listener()

        new_entities = async_add_entities.call_args.args[0]
        assert sorted(entity.unique_id for entity in new_entities) == [
            "device_2_charge_now",
            "device_2_stop_charging",
        ]


def test_device_name_excludes_device_id():
    """Home Assistant adds the device name to the entity name, so it is kept short."""
    device = _make_device(friendly_name="My Charger")

    button = AndersenEvButton(_make_coordinator([device]), device, CHARGE_NOW)

    assert button.device_info["name"] == "My Charger"


class TestAvailability:
    """Tests for AndersenEvButton.available."""

    @pytest.mark.parametrize(
        ("last_update_success", "status_available", "expected"),
        [
            pytest.param(True, True, True, id="available"),
            pytest.param(False, True, False, id="update_failed"),
            pytest.param(True, False, False, id="status_unavailable"),
        ],
    )
    def test_available(self, last_update_success: bool, status_available: bool, expected: bool):
        device = _make_device(status_available=status_available)
        coordinator = _make_coordinator([device], last_update_success=last_update_success)

        assert AndersenEvButton(coordinator, device, CHARGE_NOW).available is expected

    def test_unavailable_when_device_removed(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        button = AndersenEvButton(coordinator, device, CHARGE_NOW)

        coordinator.data = []

        assert button.available is False


class TestPress:
    """Tests for AndersenEvButton.async_press()."""

    @pytest.mark.parametrize(
        ("description", "method"),
        [
            pytest.param(CHARGE_NOW, "charge_now", id="charge_now"),
            pytest.param(STOP_CHARGING, "stop_charging", id="stop_charging"),
        ],
    )
    @pytest.mark.asyncio
    async def test_press_sends_command_and_refreshes(self, description, method: str):
        device = _make_device()
        coordinator = _make_coordinator([device])
        button = AndersenEvButton(coordinator, device, description)

        await button.async_press()

        getattr(device, method).assert_awaited_once()
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.parametrize(
        ("description", "method", "translation_key"),
        [
            pytest.param(CHARGE_NOW, "charge_now", "charge_now_failed", id="charge_now"),
            pytest.param(STOP_CHARGING, "stop_charging", "stop_charging_failed", id="stop_charging"),
        ],
    )
    @pytest.mark.asyncio
    async def test_press_failure_raises(self, description, method: str, translation_key: str):
        device = _make_device()
        getattr(device, method).return_value = False
        coordinator = _make_coordinator([device])
        button = AndersenEvButton(coordinator, device, description)

        with pytest.raises(HomeAssistantError) as exc_info:
            await button.async_press()

        assert exc_info.value.translation_key == translation_key
        coordinator.async_request_refresh.assert_not_awaited()
