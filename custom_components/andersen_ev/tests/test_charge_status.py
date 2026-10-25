"""Tests for the charger status badge logic."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from andersen_ev.charge_status import (
    CHARGE_STATUS_OPTIONS,
    CHARGING,
    CONNECTED,
    DISABLED,
    FAULT,
    LOCKED,
    OFFLINE,
    PAUSED,
    SCHEDULED,
    STANDBY,
    STARTING,
    WONT_CHARGE,
    calculate_charge_state,
    charge_status_badge,
)

# A Wednesday; Kaluza day codes are Tuesday 04, Wednesday 08 and Thursday 10.
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
RECENT_EVENT = "2026-10-21T11:59:00.000Z"
BASE_STATUS: dict[str, Any] = {
    "online": True,
    "lastEvent": RECENT_EVENT,
    "sysFwVersion": 504,
    "evseState": 255,
    "sysFaultCode": 0,
    "sysUserLock": False,
    "sysScheduleLock": False,
}


def _state(info: dict[str, Any] | None = None, **status: Any) -> str:
    return calculate_charge_state({**BASE_STATUS, **status}, info or {}, NOW)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param({"evseState": 0}, STARTING, id="starting"),
        pytest.param({"evseState": 1}, STANDBY, id="standby"),
        pytest.param({"evseState": "2"}, CONNECTED, id="connected_as_string"),
        pytest.param({"evseState": 3}, CHARGING, id="charging"),
        pytest.param({"evseState": 3, "sysFaultCode": 1}, FAULT, id="charging_with_rcm_trip"),
        pytest.param({"evseState": 14}, FAULT, id="open_relay"),
        pytest.param({"evseState": 99}, FAULT, id="unknown_evse_state"),
        pytest.param({}, DISABLED, id="disabled"),
        pytest.param({"sysFaultCode": 2097152}, FAULT, id="disabled_with_ocp_trip"),
        pytest.param({"sysFaultCode": 262144}, PAUSED, id="solar_current_low"),
        pytest.param({"sysFaultCode": 4194304}, PAUSED, id="adaptive_fuse_current_low"),
        pytest.param({"sysFaultCode": 8192}, DISABLED, id="fault_bit_not_checked"),
        pytest.param({"evseState": 3, "sysFaultCode": 16384}, FAULT, id="overtemp_beats_evse_state"),
        pytest.param({"evseState": 3, "sysUserLock": True}, LOCKED, id="locked"),
        pytest.param({"evseState": 3, "sysUserLock": True, "online": False}, OFFLINE, id="offline_beats_locked"),
        pytest.param({"online": None}, OFFLINE, id="online_missing"),
        pytest.param({"lastEvent": "2026-10-21T11:54:00.000Z"}, OFFLINE, id="event_6_min_old"),
        pytest.param(
            {"lastEvent": "2026-10-21T11:54:00.000Z", "sysFwVersion": "520"}, DISABLED, id="long_threshold_fw"
        ),
        pytest.param({"lastEvent": "2026-10-21T11:44:00.000Z", "sysFwVersion": 520}, OFFLINE, id="event_16_min_old"),
        pytest.param({"lastEvent": None}, DISABLED, id="no_last_event"),
        pytest.param({"lastEvent": "not a date"}, DISABLED, id="unreadable_last_event"),
        pytest.param({"lastEvent": "2026-10-21T11:59:00"}, DISABLED, id="last_event_without_offset"),
        pytest.param({"sysScheduleLock": True, "sysSch3": '\\"00000000001\\"'}, SCHEDULED, id="schedule_lock"),
        pytest.param({"sysScheduleLock": True, "sysSch3": "00000000000"}, DISABLED, id="schedule_lock_no_slot"),
        pytest.param({"sysScheduleLock": False, "sysSch3": "00000000001"}, DISABLED, id="slot_without_lock"),
    ],
)
def test_charge_state(status: dict[str, Any], expected: str) -> None:
    assert _state(**status) == expected


@pytest.mark.parametrize(
    ("fw_version", "expected"),
    [
        pytest.param(520, OFFLINE, id="long_threshold_fw"),
        pytest.param(504, DISABLED, id="other_fw"),
    ],
)
def test_offline_notification_only_on_long_threshold_firmware(fw_version: int, expected: str) -> None:
    info = {"NotifiedDeviceOfflineAt": "2026-10-21T10:00:00.000Z"}
    assert _state(info, sysFwVersion=fw_version) == expected


@pytest.mark.parametrize(
    ("info", "status", "expected"),
    [
        pytest.param({}, {"sysKaluzaSch0": "1759000004"}, WONT_CHARGE, id="kaluza_1759_yesterday"),
        pytest.param({}, {"sysKaluzaSch0": "1759000008"}, DISABLED, id="kaluza_1759_today"),
        pytest.param(
            {"LastCheckedOctopus": "2026-10-20T18:00:00.000Z"},
            {"sysKaluzaSch0": "0100050008"},
            WONT_CHARGE,
            id="octopus_checked_yesterday_nothing_ahead",
        ),
        pytest.param(
            {"LastCheckedOctopus": "2026-10-20T18:00:00.000Z"},
            {"sysKaluzaSch0": "2300050008"},
            DISABLED,
            id="octopus_checked_yesterday_later_today",
        ),
        pytest.param(
            {"LastCheckedOctopus": "2026-10-20T18:00:00.000Z"},
            {"sysKaluzaSch0": "0100050010"},
            DISABLED,
            id="octopus_checked_yesterday_tomorrow",
        ),
        pytest.param(
            {"LastCheckedOctopus": "2026-10-21T08:00:00.000Z"},
            {"sysKaluzaSch0": "0100050008"},
            DISABLED,
            id="octopus_checked_today",
        ),
        pytest.param(
            {"KaluzaLinked": True, "KaluzaSchedules": json.dumps({"a": json.dumps({"deviceMode": "smart"})})},
            {"sysKaluzaSch0": "2300050008"},
            SCHEDULED,
            id="kaluza_smart",
        ),
        pytest.param(
            {"KaluzaLinked": True, "KaluzaSchedules": json.dumps({"a": {"deviceMode": "idle"}})},
            {"sysKaluzaSch0": "2300050008"},
            DISABLED,
            id="kaluza_idle",
        ),
        pytest.param(
            {"KaluzaLinked": False, "KaluzaSchedules": json.dumps({"a": {}})},
            {"sysKaluzaSch0": "2300050008"},
            SCHEDULED,
            id="schedules_without_kaluza",
        ),
        pytest.param(
            {"KaluzaLinked": True, "KaluzaSchedules": json.dumps({"a": {"deviceMode": "smart"}})},
            {},
            DISABLED,
            id="no_kaluza_slots",
        ),
        pytest.param(
            {"KaluzaLinked": True, "KaluzaSchedules": "not json"},
            {"sysKaluzaSch0": "2300050008"},
            DISABLED,
            id="invalid_kaluza_schedules",
        ),
        pytest.param(
            {"KaluzaLinked": True, "KaluzaSchedules": json.dumps({"a": {"deviceMode": "idle"}})},
            {"sysKaluzaSch0": "2300050008", "sysFaultCode": 262144},
            PAUSED,
            id="falls_through_to_paused",
        ),
    ],
)
def test_smart_tariff(info: dict[str, Any], status: dict[str, Any], expected: str) -> None:
    assert _state(info, **status) == expected


@pytest.mark.parametrize(
    ("charge_state", "status", "expected"),
    [
        pytest.param(CHARGING, {"sysForceStop": True}, "charging", id="charging"),
        pytest.param(CONNECTED, {"sysForceStop": True}, "connected", id="connected_ignores_force_stop"),
        pytest.param(LOCKED, {}, "locked", id="locked"),
        pytest.param(SCHEDULED, {}, "scheduled", id="scheduled"),
        pytest.param(PAUSED, {}, "waiting_for_solar", id="paused"),
        pytest.param(FAULT, {}, "error", id="fault"),
        pytest.param(OFFLINE, {}, "offline", id="offline"),
        pytest.param(DISABLED, {"sysPlugged": True, "sysForceStop": True}, "stopped", id="stopped"),
        pytest.param(DISABLED, {"sysPlugged": True}, "connected", id="disabled_plugged"),
        pytest.param(WONT_CHARGE, {"sysPlugged": None}, "unplugged", id="wont_charge_unplugged"),
        pytest.param(STANDBY, {}, "unplugged", id="standby"),
        pytest.param(STARTING, {"sysPlugged": True}, "connected", id="starting_plugged"),
    ],
)
def test_badge(charge_state: str, status: dict[str, Any], expected: str) -> None:
    badge = charge_status_badge(charge_state, status)
    assert badge == expected
    assert badge in CHARGE_STATUS_OPTIONS
