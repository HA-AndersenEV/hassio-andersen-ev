"""Conversion between grid source power (kW) and the API's maxGridChargePercent."""

from .konnect.device import KonnectDevice

KW_PER_AMP = 0.225
# The charger can't supplement with less than this, so anything between 0 and this is rejected.
MIN_GRID_SOURCE_POWER_KW = 1.4


def max_grid_source_power_kw(max_amps: float) -> float:
    """Return the charger's max power in kW for its max current."""
    return round(max_amps * KW_PER_AMP, 1)


def grid_source_percent_to_kw(percent: float, max_amps: float) -> float:
    """Convert the API's maxGridChargePercent to kW."""
    return round(percent / 100 * max_grid_source_power_kw(max_amps), 1)


def grid_source_kw_to_percent(power_kw: float, max_amps: float) -> int:
    """Convert kW to the API's maxGridChargePercent."""
    percent = round(power_kw / max_grid_source_power_kw(max_amps) * 100)
    return max(0, min(100, percent))


def device_max_amps(device: KonnectDevice) -> float | None:
    """Return the charger's configured max current (cfgChargeAmpMax)."""
    status = device.last_status or {}
    max_amps = status.get("cfgChargeAmpMax")
    if not max_amps:
        return None
    return float(max_amps)


def grid_supplement_on_percent(device: KonnectDevice, max_amps: float) -> int:
    """Return the percent to send when grid supplement is turned on.

    Restores the last non-zero percent, raised to the 1.4 kW minimum if it was below it.
    """
    min_percent = grid_source_kw_to_percent(MIN_GRID_SOURCE_POWER_KW, max_amps)
    return max(device.last_grid_charge_percent or 0, min_percent)
