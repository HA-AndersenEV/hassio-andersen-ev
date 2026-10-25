"""Conversion between grid source power (kW) and the API's maxGridChargePercent."""

from decimal import ROUND_HALF_UP, Decimal

from .const import KW_PER_AMP
from .konnect.device import SYS_USER_MAX_CHARGE_AMP_SET, USER_SET_CHARGE_AMP_MAX, KonnectDevice

# The charger can't supplement with less than this, so anything between 0 and this is rejected.
MIN_GRID_SOURCE_POWER_KW = 1.4


def amps_to_kw(amps: float) -> float:
    """Return the power in kW for a current, rounded half up to 0.1 kW as the app does."""
    power = Decimal(str(amps)) * Decimal(str(KW_PER_AMP))
    return float(power.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def grid_source_percent_to_kw(percent: float, max_amps: float) -> float:
    """Convert the API's maxGridChargePercent to kW."""
    return round(percent / 100 * amps_to_kw(max_amps), 1)


def grid_source_kw_to_percent(power_kw: float, max_amps: float) -> int:
    """Convert kW to the API's maxGridChargePercent."""
    percent = round(power_kw / amps_to_kw(max_amps) * 100)
    return max(0, min(100, percent))


def device_max_amps(device: KonnectDevice) -> float | None:
    """Return the charger's configured max current (cfgChargeAmpMax)."""
    max_amps = device.live_status.get("cfgChargeAmpMax")
    if not max_amps:
        return None
    return float(max_amps)


def effective_max_amps(device: KonnectDevice) -> float | None:
    """Return the user max charge current, or the installer limit if it isn't set.

    Read from the polled userSetChargeAmpMax or the live sysUserMaxChargeAmpSet, whichever last
    changed. The app works out grid source power from this, so percentages follow a lowered user max.
    """
    if device.user_max_amps_field == USER_SET_CHARGE_AMP_MAX:
        amps = device.last_rest_status.get(USER_SET_CHARGE_AMP_MAX)
    else:
        amps = device.live_status.get(SYS_USER_MAX_CHARGE_AMP_SET)
    if amps:
        return float(amps)
    return device_max_amps(device)


def grid_supplement_on_percent(device: KonnectDevice, max_amps: float) -> int:
    """Return the percent to send when grid supplement is turned on.

    Restores the last non-zero percent, raised to the 1.4 kW minimum if it was below it.
    """
    min_percent = grid_source_kw_to_percent(MIN_GRID_SOURCE_POWER_KW, max_amps)
    return max(device.last_grid_charge_percent or 0, min_percent)
