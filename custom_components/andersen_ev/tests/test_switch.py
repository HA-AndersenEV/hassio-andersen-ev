"""Tests for the Andersen EV switch platform."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from andersen_ev.konnect.exceptions import AndersenAuthError
from andersen_ev.switch import (
    AndersenEvGovernmentScheduleSwitch,
    AndersenEvScheduleSwitch,
    async_setup_entry,
    schedule_slot_count,
)

# Disconnected for longer than LIVE_STATUS_TIMEOUT, so live entities are unavailable.
DISCONNECTED_LONG_AGO = dt_util.utcnow() - timedelta(minutes=6)


SLOT_NAMES = {
    "schedule0Name": "Government default",
    "schedule1Name": "Government default",
    "schedule2Name": "Overnight",
}


def _make_device(
    device_id="device_1",
    friendly_name="Device",
    status_available=True,
    live_status=None,
):
    """Build a mock KonnectDevice."""
    device = MagicMock()
    device.device_id = device_id
    device.friendly_name = friendly_name
    device.subscription_disconnected_since = None if status_available else DISCONNECTED_LONG_AGO
    device.live_status = live_status or {}
    # Schedule updates fetch the current slot names before sending.
    device.get_device_info = AsyncMock(return_value={"deviceInfo": SLOT_NAMES})
    return device


def _make_coordinator(devices=None, last_update_success=True):
    """Build a mock AndersenEvCoordinator."""
    coordinator = MagicMock()
    coordinator.data = devices or []
    coordinator.last_update_success = last_update_success
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _make_switch(coordinator, device, index=0, schedule_name="Schedule 1"):
    switch = AndersenEvScheduleSwitch(coordinator, device, index, schedule_name)
    switch.async_write_ha_state = MagicMock()
    return switch


class TestAsyncSetupEntry:
    """Tests for async_setup_entry()."""

    @pytest.mark.asyncio
    async def test_creates_government_switch_and_one_switch_per_user_slot(self):
        """Slots 0 and 1 share one Government default switch; later slots get their own."""
        device = _make_device()
        device.get_device_info = AsyncMock(
            return_value={
                "deviceInfo": {
                    "schedule0Name": "Government default",
                    "schedule1Name": "Government default",
                    "schedule2Name": "Overnight",
                },
                "deviceStatus": {"scheduleSlotsArray": [{"enabled": False}] * 4},
            }
        )
        coordinator = _make_coordinator([device])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        entities = async_add_entities.call_args.args[0]
        schedule_switches = [e for e in entities if isinstance(e, AndersenEvScheduleSwitch)]
        assert len(entities) == 6
        assert isinstance(schedule_switches[0], AndersenEvGovernmentScheduleSwitch)
        assert [e.unique_id for e in schedule_switches] == [
            "device_1_schedule_0",
            "device_1_schedule_2",
            "device_1_schedule_3",
        ]
        assert [e.translation_key for e in schedule_switches] == ["government_schedule", "schedule", "schedule"]
        assert [e._schedule_name for e in schedule_switches] == ["Government Schedule", "Overnight", "Schedule 4"]
        assert [e._slot_indices for e in schedule_switches] == [(0, 1), (2,), (3,)]

    @pytest.mark.asyncio
    async def test_missing_device_info_skips_device(self):
        device = _make_device()
        device.get_device_info = AsyncMock(return_value=None)
        coordinator = _make_coordinator([device])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        assert async_add_entities.call_args.args[0] == []

    @pytest.mark.asyncio
    async def test_missing_device_info_key_skips_device(self):
        device = _make_device()
        device.get_device_info = AsyncMock(return_value={"other": True})
        coordinator = _make_coordinator([device])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        assert async_add_entities.call_args.args[0] == []

    @pytest.mark.asyncio
    async def test_missing_device_status_skips_device(self):
        device = _make_device()
        device.get_device_info = AsyncMock(return_value={"deviceInfo": {}})
        coordinator = _make_coordinator([device])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        assert async_add_entities.call_args.args[0] == []

    @pytest.mark.asyncio
    async def test_missing_schedule_slots_array_skips_device(self):
        device = _make_device()
        device.get_device_info = AsyncMock(return_value={"deviceInfo": {}, "deviceStatus": {}})
        coordinator = _make_coordinator([device])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, async_add_entities)

        assert async_add_entities.call_args.args[0] == []


class TestDynamicDevices:
    """Tests for the dynamic-devices coordinator listener registered in async_setup_entry()."""

    @pytest.mark.asyncio
    async def test_new_device_scheduled_for_addition(self):
        device_a = _make_device(device_id="device_1")
        device_a.get_device_info = AsyncMock(
            return_value={
                "deviceInfo": {},
                "deviceStatus": {"scheduleSlotsArray": [{"enabled": True}]},
            }
        )
        coordinator = _make_coordinator([device_a])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()
        hass = MagicMock()

        await async_setup_entry(hass, entry, async_add_entities)

        assert async_add_entities.call_count == 1
        entry.async_on_unload.assert_called_once()
        listener = coordinator.async_add_listener.call_args.args[0]

        device_b = _make_device(device_id="device_2")
        device_b.get_device_info = AsyncMock(
            return_value={
                "deviceInfo": {},
                "deviceStatus": {"scheduleSlotsArray": [{"enabled": False}, {"enabled": True}]},
            }
        )
        coordinator.data = [device_a, device_b]

        listener()

        hass.async_create_task.assert_called_once()
        scheduled_coro = hass.async_create_task.call_args.args[0]
        await scheduled_coro

        assert async_add_entities.call_count == 2
        new_entities = async_add_entities.call_args.args[0]
        assert len(new_entities) == 4
        assert all(entity._device.device_id == "device_2" for entity in new_entities)

    @pytest.mark.asyncio
    async def test_no_new_devices_does_not_schedule_a_task(self):
        device_a = _make_device(device_id="device_1")
        device_a.get_device_info = AsyncMock(
            return_value={
                "deviceInfo": {},
                "deviceStatus": {"scheduleSlotsArray": [{"enabled": True}]},
            }
        )
        coordinator = _make_coordinator([device_a])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()
        hass = MagicMock()

        await async_setup_entry(hass, entry, async_add_entities)
        listener = coordinator.async_add_listener.call_args.args[0]

        listener()

        hass.async_create_task.assert_not_called()
        assert async_add_entities.call_count == 1

    @pytest.mark.asyncio
    async def test_device_readded_after_disappearing_is_scheduled_again(self):
        """A device_id that drops out and reappears must not be treated as already known."""
        device_a = _make_device(device_id="device_1")
        device_a.get_device_info = AsyncMock(
            return_value={
                "deviceInfo": {},
                "deviceStatus": {"scheduleSlotsArray": [{"enabled": True}]},
            }
        )
        coordinator = _make_coordinator([device_a])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()
        hass = MagicMock()

        await async_setup_entry(hass, entry, async_add_entities)
        listener = coordinator.async_add_listener.call_args.args[0]

        # device_a drops out of the coordinator's data for a cycle.
        coordinator.data = []
        listener()
        hass.async_create_task.assert_not_called()

        # ...then reappears with the same device_id.
        coordinator.data = [device_a]
        listener()

        hass.async_create_task.assert_called_once()
        await hass.async_create_task.call_args.args[0]

        assert async_add_entities.call_count == 2
        new_entities = async_add_entities.call_args.args[0]
        assert len(new_entities) == 4
        assert all(entity._device.device_id == "device_1" for entity in new_entities)

    @pytest.mark.asyncio
    async def test_failed_switch_build_is_retried_on_next_update(self):
        """If get_device_info() fails transiently, the device must not be stuck as known."""
        device_a = _make_device(device_id="device_1")
        coordinator = _make_coordinator([])
        entry = MagicMock()
        entry.runtime_data = coordinator
        async_add_entities = MagicMock()
        hass = MagicMock()

        await async_setup_entry(hass, entry, async_add_entities)
        listener = coordinator.async_add_listener.call_args.args[0]

        # First attempt fails to retrieve device info (e.g. transient GraphQL error).
        device_a.get_device_info = AsyncMock(return_value=None)
        coordinator.data = [device_a]
        listener()
        hass.async_create_task.assert_called_once()
        await hass.async_create_task.call_args.args[0]
        assert async_add_entities.call_args.args[0] == []

        # A later coordinator update must retry the same still-present device, not skip it.
        device_a.get_device_info = AsyncMock(
            return_value={
                "deviceInfo": {},
                "deviceStatus": {"scheduleSlotsArray": [{"enabled": True}]},
            }
        )
        listener()

        assert hass.async_create_task.call_count == 2
        await hass.async_create_task.call_args.args[0]
        new_entities = async_add_entities.call_args.args[0]
        assert len(new_entities) == 4
        assert all(entity._device.device_id == "device_1" for entity in new_entities)


class TestInit:
    """Tests for AndersenEvScheduleSwitch.__init__()."""

    def test_sets_unique_id_translation_key_and_device_info(self):
        device = _make_device(device_id="device_1", friendly_name="My Charger")
        coordinator = _make_coordinator([device])

        switch = _make_switch(coordinator, device, index=2, schedule_name="Weekend")

        assert switch._attr_unique_id == "device_1_schedule_2"
        assert switch._attr_translation_key == "schedule"
        assert switch._attr_translation_placeholders == {"index": "3"}
        assert switch._attr_device_info["name"] == "My Charger"
        assert switch._attr_has_entity_name is True

    def test_entity_category_is_config(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        switch = _make_switch(coordinator, device)

        assert switch.entity_category == EntityCategory.CONFIG

    def test_extra_state_attributes(self):
        device = _make_device()
        coordinator = _make_coordinator([device])

        switch = _make_switch(coordinator, device, index=1, schedule_name="Weekend")

        assert switch.extra_state_attributes == {
            "schedule_name": "Weekend",
            "schedule_index": 1,
        }


class TestUpdateDeviceInfoFromStatus:
    """Tests for AndersenEvScheduleSwitch._update_device_info_from_status()."""

    def test_known_product_id_maps_to_the_marketed_model_name(self):
        device = _make_device(live_status={"sysProductName": "Thurlestone", "sysProductId": 30})
        coordinator = _make_coordinator([device])

        switch = _make_switch(coordinator, device)

        assert switch._attr_device_info["model"] == "A2"

    def test_integer_product_id_rendered_as_string(self):
        device = _make_device(live_status={"sysProductId": 99})
        coordinator = _make_coordinator([device])

        switch = _make_switch(coordinator, device)

        assert switch._attr_device_info["model"] == "99"

    def test_hw_fw_and_serial_populated(self):
        device = _make_device(live_status={"sysHwVersion": "4", "sysFwVersion": "314", "konnectSerial": "1234567890"})
        coordinator = _make_coordinator([device])

        switch = _make_switch(coordinator, device)

        assert switch._attr_device_info["hw_version"] == "4"
        assert switch._attr_device_info["sw_version"] == "314"
        assert switch._attr_device_info["serial_number"] == "1234567890"

    def test_no_status_keeps_default_device_info(self):
        device = _make_device(live_status=None)
        coordinator = _make_coordinator([device])

        switch = _make_switch(coordinator, device)

        assert "model" not in switch._attr_device_info
        assert "hw_version" not in switch._attr_device_info
        assert "sw_version" not in switch._attr_device_info
        assert switch._attr_device_info["serial_number"] == device.device_id


class TestAvailable:
    """Tests for AndersenEvScheduleSwitch.available."""

    def test_available_when_device_found_and_status_available(self):
        device = _make_device(status_available=True)
        coordinator = _make_coordinator([device], last_update_success=True)
        switch = _make_switch(coordinator, device)

        assert switch.available is True

    def test_unavailable_when_status_unavailable(self):
        device = _make_device(status_available=False)
        coordinator = _make_coordinator([device], last_update_success=True)
        switch = _make_switch(coordinator, device)

        assert switch.available is False

    def test_unavailable_when_device_not_found(self):
        device = _make_device(device_id="device_1")
        coordinator = _make_coordinator([], last_update_success=True)
        switch = _make_switch(coordinator, device)

        assert switch.available is False


class TestIsOn:
    """Tests for AndersenEvScheduleSwitch.is_on."""

    def test_returns_enabled_from_schedule_slots_array(self):
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": True}]})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=0)

        assert switch.is_on is True

    def test_returns_disabled_from_schedule_slots_array(self):
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": False}]})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=0)

        assert switch.is_on is False

    def test_unknown_before_first_payload(self):
        device = _make_device(live_status=None)
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=0)

        assert switch.is_on is None

    def test_index_out_of_range_is_unknown(self):
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": True}]})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=5)

        assert switch.is_on is None


class TestAsyncTurnOnOff:
    """Tests for AndersenEvScheduleSwitch.async_turn_on() / async_turn_off()."""

    @pytest.mark.asyncio
    async def test_turn_on_calls_set_schedule_enabled_true(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device)
        switch._set_schedule_enabled = AsyncMock()

        await switch.async_turn_on()

        switch._set_schedule_enabled.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_turn_off_calls_set_schedule_enabled_false(self):
        device = _make_device()
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device)
        switch._set_schedule_enabled = AsyncMock()

        await switch.async_turn_off()

        switch._set_schedule_enabled.assert_awaited_once_with(False)


class TestSetScheduleEnabled:
    """Tests for AndersenEvScheduleSwitch._set_schedule_enabled()."""

    @pytest.mark.asyncio
    async def test_success_sends_slot_and_waits_for_live_status(self):
        """The switch changes only when the subscription reports the new slot, so nothing is updated locally."""
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": False}]})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=0)
        switch._send_set_schedules_mutation = AsyncMock(return_value=True)

        await switch._set_schedule_enabled(True)

        switch._send_set_schedules_mutation.assert_awaited_once()
        formatted_slots = switch._send_set_schedules_mutation.call_args.args[0]
        assert formatted_slots == {"sch0": {"enabled": True, "name": "Government default", "readOnly": True}}
        assert device.live_status["scheduleSlotsArray"][0]["enabled"] is False
        switch.async_write_ha_state.assert_not_called()
        coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sends_every_slot_with_current_names(self) -> None:
        """The server reads every slot's name and readOnly, so all slots are sent as the app does."""
        slots = [{"startHour": hour, "enabled": False} for hour in (22, 11, 2, 7)]
        device = _make_device(live_status={"scheduleSlotsArray": slots})
        switch = _make_switch(_make_coordinator([device]), device, index=2)
        switch._send_set_schedules_mutation = AsyncMock(return_value=True)

        await switch._set_schedule_enabled(True)

        assert switch._send_set_schedules_mutation.call_args.args[0] == {
            "sch0": {"startHour": 22, "enabled": False, "name": "Government default", "readOnly": True},
            "sch1": {"startHour": 11, "enabled": False, "name": "Government default", "readOnly": True},
            "sch2": {"startHour": 2, "enabled": True, "name": "Overnight", "readOnly": False},
            "sch3": {"startHour": 7, "enabled": False, "name": "", "readOnly": False},
        }

    @pytest.mark.parametrize(
        "device_info",
        [
            pytest.param(None, id="fetch_failed"),
            pytest.param({"deviceStatus": {"scheduleSlotsArray": [{"enabled": False}]}}, id="no_device_info"),
        ],
    )
    @pytest.mark.asyncio
    async def test_missing_names_raises_without_sending(self, device_info: dict | None) -> None:
        """Without the current names, sending would blank them on the charger."""
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": False}]})
        device.get_device_info = AsyncMock(return_value=device_info)
        switch = _make_switch(_make_coordinator([device]), device, index=0)
        switch._send_set_schedules_mutation = AsyncMock(return_value=True)

        with pytest.raises(HomeAssistantError):
            await switch._set_schedule_enabled(True)

        switch._send_set_schedules_mutation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_existing_status_fetches_from_api(self):
        device = _make_device(live_status=None)
        device.get_device_info = AsyncMock(
            return_value={"deviceInfo": SLOT_NAMES, "deviceStatus": {"scheduleSlotsArray": [{"enabled": False}]}}
        )
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=0)
        switch._send_set_schedules_mutation = AsyncMock(return_value=True)

        await switch._set_schedule_enabled(True)

        device.get_device_info.assert_awaited_once()
        assert switch._send_set_schedules_mutation.call_args.args[0]["sch0"]["enabled"] is True
        switch._send_set_schedules_mutation.assert_awaited_once()
        coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_fetch_missing_schedule_data_raises(self):
        device = _make_device(live_status=None)
        device.get_device_info = AsyncMock(return_value={"deviceInfo": SLOT_NAMES, "deviceStatus": {}})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=0)

        with pytest.raises(HomeAssistantError):
            await switch._set_schedule_enabled(True)

    @pytest.mark.asyncio
    async def test_index_out_of_range_raises(self):
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": False}]})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=5)

        with pytest.raises(HomeAssistantError):
            await switch._set_schedule_enabled(True)

        coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mutation_failure_raises(self):
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": False}]})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=0)
        switch._send_set_schedules_mutation = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await switch._set_schedule_enabled(True)

        coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_error_raises_update_error(self):
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": False}]})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=0)
        switch._send_set_schedules_mutation = AsyncMock(side_effect=AndersenAuthError("expired"))

        with pytest.raises(HomeAssistantError) as exc_info:
            await switch._set_schedule_enabled(True)

        assert exc_info.value.translation_key == "schedule_update_error"
        coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_formatted_slots_are_deep_copied_from_source(self):
        captured = {}

        async def capture_mutation(slots, *_args, **_kwargs):
            captured["slots"] = slots
            return True

        original_slots = [{"enabled": False, "start": "08:00"}]
        device = _make_device(live_status={"scheduleSlotsArray": original_slots})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device, index=0)
        switch._send_set_schedules_mutation = AsyncMock(side_effect=capture_mutation)

        await switch._set_schedule_enabled(True)

        captured["slots"]["sch0"]["start"] = "99:99"
        assert original_slots[0]["start"] == "08:00"


class TestGovernmentScheduleSwitch:
    """Tests for AndersenEvGovernmentScheduleSwitch, which covers slots 0 and 1."""

    @staticmethod
    def _make_government_switch(device):
        switch = AndersenEvGovernmentScheduleSwitch(_make_coordinator([device]), device)
        switch.async_write_ha_state = MagicMock()
        return switch

    @pytest.mark.parametrize(
        ("slot_0", "slot_1", "expected"),
        [
            pytest.param(True, True, True, id="both_on"),
            pytest.param(True, False, False, id="only_slot_0_on"),
            pytest.param(False, False, False, id="both_off"),
        ],
    )
    def test_is_on_only_when_both_slots_are_on(self, slot_0: bool, slot_1: bool, expected: bool) -> None:
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": slot_0}, {"enabled": slot_1}]})

        assert self._make_government_switch(device).is_on is expected

    def test_is_on_unknown_without_both_slots(self) -> None:
        device = _make_device(live_status={"scheduleSlotsArray": [{"enabled": True}]})

        assert self._make_government_switch(device).is_on is None

    def test_keeps_slot_0_unique_id(self) -> None:
        """Existing slot 0 entities keep their entity ID and history."""
        switch = self._make_government_switch(_make_device())

        assert switch.unique_id == "device_1_schedule_0"
        assert switch.translation_key == "government_schedule"

    @pytest.mark.asyncio
    async def test_toggles_both_slots_in_one_update(self) -> None:
        device = _make_device(
            live_status={
                "scheduleSlotsArray": [
                    {"startHour": 22, "enabled": False},
                    {"startHour": 11, "enabled": False},
                    {"startHour": 2, "enabled": True},
                ]
            }
        )
        switch = self._make_government_switch(device)
        switch._send_set_schedules_mutation = AsyncMock(return_value=True)

        await switch._set_schedule_enabled(True)

        switch._send_set_schedules_mutation.assert_awaited_once()
        assert switch._send_set_schedules_mutation.call_args.args[0] == {
            "sch0": {"startHour": 22, "enabled": True, "name": "Government default", "readOnly": True},
            "sch1": {"startHour": 11, "enabled": True, "name": "Government default", "readOnly": True},
            "sch2": {"startHour": 2, "enabled": True, "name": "Overnight", "readOnly": False},
        }


class TestSendSetSchedulesMutation:
    """Tests for AndersenEvScheduleSwitch._send_set_schedules_mutation()."""

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        device = _make_device()
        device.graphql_client.execute_mutation = AsyncMock(return_value={"return_value": True})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device)

        result = await switch._send_set_schedules_mutation({"sch0": {"enabled": True}}, enabled=True)

        assert result is True
        device.graphql_client.execute_mutation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disabled_state_text(self):
        device = _make_device()
        device.graphql_client.execute_mutation = AsyncMock(return_value={"return_value": True})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device)

        result = await switch._send_set_schedules_mutation({"sch0": {"enabled": False}}, enabled=False)

        assert result is True

    @pytest.mark.asyncio
    async def test_updated_state_text_when_enabled_is_none(self):
        device = _make_device()
        device.graphql_client.execute_mutation = AsyncMock(return_value={"return_value": True})
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device)

        result = await switch._send_set_schedules_mutation({"sch0": {}}, enabled=None)

        assert result is True

    @pytest.mark.asyncio
    async def test_none_result_returns_false(self):
        device = _make_device()
        device.graphql_client.execute_mutation = AsyncMock(return_value=None)
        coordinator = _make_coordinator([device])
        switch = _make_switch(coordinator, device)

        result = await switch._send_set_schedules_mutation({"sch0": {"enabled": True}}, enabled=True)

        assert result is False


TEN_SLOTS = [{"startHour": hour, "enabled": False} for hour in range(10)]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param({"sysFwVersion": 630}, 10, id="new_firmware"),
        pytest.param({"sysFwVersion": "630"}, 10, id="new_firmware_as_text"),
        pytest.param({"sysFwVersion": 500}, 10, id="first_new_firmware"),
        pytest.param({"sysFwVersion": 499}, 5, id="last_old_firmware"),
        pytest.param({"sysFwVersion": "314"}, 5, id="old_firmware_as_text"),
        pytest.param({"sysFwVersion": None}, 5, id="null_counts_as_old"),
        pytest.param({}, 5, id="missing_counts_as_old"),
        pytest.param({"sysFwVersion": "unknown"}, 5, id="unreadable_counts_as_old"),
    ],
)
def test_schedule_slot_count(status: dict, expected: int) -> None:
    """As in the app, firmware below 500 uses 5 schedule slots and newer firmware 10."""
    assert schedule_slot_count(status) == expected


@pytest.mark.parametrize(
    ("fw_version", "expected_slots"),
    [
        pytest.param("314", [2, 3, 4], id="old_firmware"),
        pytest.param("630", [2, 3, 4, 5, 6, 7, 8, 9], id="new_firmware"),
    ],
)
@pytest.mark.asyncio
async def test_setup_creates_switches_for_the_slots_the_firmware_uses(
    fw_version: str, expected_slots: list[int]
) -> None:
    """Old firmware only gets switches for the 5 slots the app shows."""
    device = _make_device()
    device.get_device_info = AsyncMock(
        return_value={
            "deviceInfo": SLOT_NAMES,
            "deviceStatus": {"sysFwVersion": fw_version, "scheduleSlotsArray": TEN_SLOTS},
        }
    )
    entry = MagicMock()
    entry.runtime_data = _make_coordinator([device])
    async_add_entities = MagicMock()

    await async_setup_entry(MagicMock(), entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    schedule_switches = [e for e in entities if isinstance(e, AndersenEvScheduleSwitch)]
    assert isinstance(schedule_switches[0], AndersenEvGovernmentScheduleSwitch)
    assert [e._schedule_index for e in schedule_switches[1:]] == expected_slots


@pytest.mark.parametrize(
    ("fw_version", "expected_keys"),
    [
        pytest.param("314", [f"sch{idx}" for idx in range(5)], id="old_firmware"),
        pytest.param("630", [f"sch{idx}" for idx in range(10)], id="new_firmware"),
    ],
)
@pytest.mark.asyncio
async def test_update_sends_the_slots_the_firmware_uses(fw_version: str, expected_keys: list[str]) -> None:
    """As in the app, old firmware is sent 5 slots and newer firmware 10."""
    device = _make_device(live_status={"sysFwVersion": fw_version, "scheduleSlotsArray": TEN_SLOTS})
    switch = _make_switch(_make_coordinator([device]), device, index=2)
    switch._send_set_schedules_mutation = AsyncMock(return_value=True)

    await switch._set_schedule_enabled(True)

    assert list(switch._send_set_schedules_mutation.call_args.args[0]) == expected_keys


@pytest.mark.asyncio
async def test_update_of_slot_old_firmware_doesnt_use_raises() -> None:
    """A switch left over for slot 5 or above raises on old firmware instead of sending."""
    device = _make_device(live_status={"sysFwVersion": "314", "scheduleSlotsArray": TEN_SLOTS})
    switch = _make_switch(_make_coordinator([device]), device, index=7)
    switch._send_set_schedules_mutation = AsyncMock(return_value=True)

    with pytest.raises(HomeAssistantError):
        await switch._set_schedule_enabled(True)

    switch._send_set_schedules_mutation.assert_not_awaited()
