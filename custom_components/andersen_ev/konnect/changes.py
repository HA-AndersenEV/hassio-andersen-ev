"""Find and describe changes between two API responses, for concise debug logging."""

from datetime import datetime
from typing import Any


def find_changes(old: dict[str, Any], new: dict[str, Any], prefix: str = "") -> dict[str, tuple[Any, Any]]:
    """Return the fields that differ between old and new as {name: (old, new)}.

    Nested objects are compared key by key and named with dots, e.g. ``status.sysUserLock``.
    A field missing from new is reported with a new value of None.
    """
    changes: dict[str, tuple[Any, Any]] = {}
    for key in new.keys() | old.keys():
        name = f"{prefix}{key}"
        old_value, new_value = old.get(key), new.get(key)
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            changes.update(find_changes(old_value, new_value, f"{name}."))
        elif key not in old or old_value != new_value:
            changes[name] = (old_value, new_value)
    return dict(sorted(changes.items()))


def format_changes(changes: dict[str, tuple[Any, Any]], *, show_old: bool = True) -> str:
    """Describe changes as ``field old -> new``, or ``field -> new`` for timestamps or without show_old."""
    return ", ".join(
        f"{name} {old!r} -> {new!r}" if show_old and not _is_timestamp(new) else f"{name} -> {new!r}"
        for name, (old, new) in changes.items()
    )


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str) or len(value) < 10:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True
