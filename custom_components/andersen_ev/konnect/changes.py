"""Find, merge and describe changes between API responses, for concise debug logging."""

from datetime import datetime
from typing import Any


def find_changes(
    old: dict[str, Any], new: dict[str, Any], prefix: str = "", *, partial: bool = False
) -> dict[str, tuple[Any, Any]]:
    """Return the fields that differ between old and new as {name: (old, new)}.

    Nested objects are compared key by key and named with dots, e.g. ``status.sysUserLock``.
    A field missing from new is reported with a new value of None, unless new is a partial
    update, in which case only the fields it holds are compared.
    """
    changes: dict[str, tuple[Any, Any]] = {}
    for key in new.keys() if partial else new.keys() | old.keys():
        name = f"{prefix}{key}"
        old_value, new_value = old.get(key), new.get(key)
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            changes.update(find_changes(old_value, new_value, f"{name}.", partial=partial))
        elif key not in old or old_value != new_value:
            changes[name] = (old_value, new_value)
    return dict(sorted(changes.items()))


def merge_changes(cache: dict[str, Any], update: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Merge a partial update into cache in place and return what changed, as find_changes does."""
    changes = find_changes(cache, update, partial=True)
    _deep_merge(cache, update)
    return changes


def _deep_merge(cache: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(cache.get(key), dict):
            _deep_merge(cache[key], value)
        else:
            cache[key] = dict(value) if isinstance(value, dict) else value


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
