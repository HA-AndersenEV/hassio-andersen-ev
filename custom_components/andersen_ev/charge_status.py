"""The charger status badge shown on the Konnect+ app's home screen.

The charger doesn't report this. The app works out a charge state in getChargeStatus, then picks the
badge text from it in DeviceConnectionStatus. Both are replicated here; see engineering.md section 10.
"""

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Charge states returned by the app's getChargeStatus.
CHARGING = "CHARGING"
CONNECTED = "CONNECTED"
DISABLED = "DISABLED"
FAULT = "FAULT"
LOCKED = "LOCKED"
OFFLINE = "OFFLINE"
PAUSED = "PAUSED"
SCHEDULED = "SCHEDULED"
STANDBY = "STANDBY"
STARTING = "STARTING"
WONT_CHARGE = "WONT_CHARGE"

# Badge values, in the order the app's badge can show them.
CHARGE_STATUS_OPTIONS = [
    "charging",
    "connected",
    "unplugged",
    "stopped",
    "locked",
    "scheduled",
    "waiting_for_solar",
    "error",
    "offline",
]

# States with their own badge; the rest show stopped, connected or unplugged.
STATE_BADGES = {
    OFFLINE: "offline",
    CONNECTED: "connected",
    CHARGING: "charging",
    LOCKED: "locked",
    SCHEDULED: "scheduled",
    PAUSED: "waiting_for_solar",
    FAULT: "error",
}

EVSE_STATES = {0: STARTING, 1: STANDBY, 2: CONNECTED, 3: CHARGING}
EVSE_STATE_DISABLED = 255

FAULT_CODE_RCM_TRIP = 1
FAULT_CODE_EARTH_CT_OP = 32
FAULT_CODE_EARTH_CT_ERROR = 64
FAULT_OVERTEMP = 16384
FAULT_CODE_SUPPLY_VOLTS_FAULT = 131072
FAULT_SOLAR_CHARGE_CURRENT_LOW = 262144
FAULT_CODE_PEN_INHIBIT = 1048576
FAULT_CODE_OCP_TRIP = 2097152
FAULT_CODE_CHARGE_CURRENT_TOO_LOW_AF = 4194304
FAULT_CODE_STUCK_CONTACT = 67108864
FAULT_CODE_OPEN_CONTACT = 134217728

HARD_FAULTS = FAULT_CODE_STUCK_CONTACT | FAULT_CODE_OPEN_CONTACT | FAULT_OVERTEMP
DISABLED_FAULTS = (
    FAULT_CODE_RCM_TRIP
    | FAULT_CODE_OCP_TRIP
    | FAULT_CODE_SUPPLY_VOLTS_FAULT
    | FAULT_CODE_PEN_INHIBIT
    | FAULT_CODE_EARTH_CT_OP
    | FAULT_CODE_EARTH_CT_ERROR
)
WAITING_FAULTS = FAULT_SOLAR_CHARGE_CURRENT_LOW | FAULT_CODE_CHARGE_CURRENT_TOO_LOW_AF

OFFLINE_SECONDS = 300
# Firmware these ranges cover gets longer before it counts as offline.
OFFLINE_SECONDS_LONG = 900
LONG_OFFLINE_FW_RANGES = (range(516, 600), range(611, 700), range(706, 900))

# Weekday codes in sysKaluzaSch strings, Monday first.
KALUZA_DAY_CODES = ("02", "04", "08", "10", "20", "40", "01")
KALUZA_UNSET = "000000000000"
KALUZA_SCHEDULE_FIELDS = [f"sysKaluzaSch{index}" for index in range(10)]
SCHEDULE_FIELDS = [f"sysSch{index}" for index in range(10)]


def _int(value: Any) -> int:
    """Read a numeric status field, which may arrive as a string, treating anything else as 0."""
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


def _last_event_age(status: Mapping[str, Any], now: datetime) -> float | None:
    """Return seconds since the charger's last event, or None if lastEvent can't be read."""
    if not isinstance(last_event := status.get("lastEvent"), str):
        return None
    if (parsed := dt_util.parse_datetime(last_event)) is None:
        return None
    # Like the app's new Date(), a time without an offset is read as local time.
    return (now - dt_util.as_utc(parsed)).total_seconds()


def _is_offline(status: Mapping[str, Any], info: Mapping[str, Any], now: datetime) -> bool:
    fw_version = _int(status.get("sysFwVersion"))
    long_threshold = any(fw_version in fw_range for fw_range in LONG_OFFLINE_FW_RANGES)
    # The app only looks at the offline notification on firmware with the long threshold.
    if long_threshold and info.get("NotifiedDeviceOfflineAt"):
        return True
    age = _last_event_age(status, now)
    if age is not None and age > (OFFLINE_SECONDS_LONG if long_threshold else OFFLINE_SECONDS):
        return True
    return not status.get("online")


def _schedule_active(status: Mapping[str, Any]) -> bool:
    """Return True if a schedule is blocking charging, i.e. locked with an enabled slot."""
    if status.get("sysScheduleLock") is not True:
        return False
    for field in SCHEDULE_FIELDS:
        schedule = (status.get(field) or "").replace("\\", "").replace('"', "")
        if schedule.endswith("1"):
            return True
    return False


def _smart_tariff_state(status: Mapping[str, Any], info: Mapping[str, Any], now: datetime) -> str | None:
    """Return SCHEDULED or WONT_CHARGE for a Kaluza or Octopus smart tariff, or None if neither applies."""
    kaluza_schedules = [status.get(field) for field in KALUZA_SCHEDULE_FIELDS]
    first = kaluza_schedules[0]
    today_code = KALUZA_DAY_CODES[now.weekday()]
    yesterday_code = KALUZA_DAY_CODES[(now - timedelta(days=1)).weekday()]
    tomorrow_code = KALUZA_DAY_CODES[(now + timedelta(days=1)).weekday()]

    if first and first != KALUZA_UNSET and first[8:10] == yesterday_code and first[0:4] == "1759":
        return WONT_CHARGE

    # Octopus checked yesterday and nothing is planned for later today or tomorrow.
    last_checked = info.get("LastCheckedOctopus")
    if isinstance(last_checked, str) and (checked := dt_util.parse_datetime(last_checked)) is not None:
        if dt_util.as_local(checked).date() == (now - timedelta(days=1)).date():
            upcoming = False
            for schedule in kaluza_schedules:
                if not schedule:
                    continue
                day_code = schedule[8:10]
                if day_code == tomorrow_code or (day_code == today_code and schedule[0:4] > now.strftime("%H%M")):
                    upcoming = True
            if not upcoming:
                return WONT_CHARGE

    raw = info.get("KaluzaSchedules")
    if not raw or not any(kaluza_schedules):
        return None
    try:
        schedules = json.loads(raw) if isinstance(raw, str) else raw
        scheduled = False
        # As in the app, the last schedule decides.
        for value in (schedules or {}).values():
            if not info.get("KaluzaLinked"):
                scheduled = True
                continue
            schedule = json.loads(value) if isinstance(value, str) else value
            scheduled = (schedule or {}).get("deviceMode") != "idle"
    except ValueError, AttributeError, TypeError:
        _LOGGER.debug("Invalid format for KaluzaSchedules: %s", raw)
        return None
    return SCHEDULED if scheduled else None


def calculate_charge_state(status: Mapping[str, Any], info: Mapping[str, Any], now: datetime) -> str:
    """Return the app's charge state from the live status and the REST info.

    now must be timezone aware; it gives the local day for smart tariff schedules.
    """
    fault_code = _int(status.get("sysFaultCode"))
    if _is_offline(status, info, now):
        return OFFLINE
    if status.get("sysUserLock") is True:
        return LOCKED
    if fault_code & HARD_FAULTS:
        return FAULT
    evse_state = _int(status.get("evseState"))
    if evse_state == 3 and fault_code & FAULT_CODE_RCM_TRIP:
        return FAULT
    if evse_state in EVSE_STATES:
        return EVSE_STATES[evse_state]
    if evse_state != EVSE_STATE_DISABLED:
        return FAULT
    if fault_code & DISABLED_FAULTS:
        return FAULT
    if _schedule_active(status):
        return SCHEDULED
    if smart_state := _smart_tariff_state(status, info, now):
        return smart_state
    if fault_code & WAITING_FAULTS:
        return PAUSED
    return DISABLED


def charge_status_badge(charge_state: str, status: Mapping[str, Any]) -> str:
    """Return the badge the app shows for a charge state, as one of CHARGE_STATUS_OPTIONS."""
    if badge := STATE_BADGES.get(charge_state):
        return badge
    if status.get("sysForceStop"):
        return "stopped"
    return "connected" if status.get("sysPlugged") else "unplugged"
