"""Conversion and rounding for the user max charge current."""

from decimal import Decimal

from .const import ALLOWED_USER_MAX_AMPS
from .grid_source import amps_to_kw, effective_max_amps
from .konnect.device import KonnectDevice


def allowed_user_max_amps(max_amps: float) -> list[int]:
    """Return the allowed currents that don't exceed the installer limit (cfgChargeAmpMax)."""
    return [amps for amps in ALLOWED_USER_MAX_AMPS if amps <= max_amps]


def nearest_allowed_amps(amps: float, allowed: list[int]) -> int:
    """Return the allowed current nearest to amps, taking the lower one on a tie."""
    target = Decimal(str(amps))
    return min(allowed, key=lambda option: (abs(option - target), option))


def nearest_allowed_amps_for_kw(power_kw: float, allowed: list[int]) -> int:
    """Return the allowed current whose kW is nearest to power_kw, taking the lower one on a tie."""
    target = Decimal(str(power_kw))
    return min(allowed, key=lambda option: (abs(Decimal(str(amps_to_kw(option))) - target), option))


def user_max_amps(device: KonnectDevice) -> float | None:
    """Return the user max charge current from the live status.

    Falls back to the installer limit when it isn't set, as the app does.
    """
    if not device.live_status:
        return None
    return effective_max_amps(device)
