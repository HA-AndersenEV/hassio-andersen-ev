"""Tests for the change-description helpers used in debug logging."""

from typing import Any

import pytest

from andersen_ev.konnect.changes import find_changes, format_changes, merge_changes


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        pytest.param({"a": 1}, {"a": 1}, {}, id="unchanged"),
        pytest.param({"a": 1}, {"a": 2}, {"a": (1, 2)}, id="changed"),
        pytest.param({}, {"a": None}, {"a": (None, None)}, id="new_field"),
        pytest.param({"a": 1}, {}, {"a": (1, None)}, id="removed_field"),
        pytest.param(
            {"status": {"x": 1, "y": 2}}, {"status": {"x": 1, "y": 3}}, {"status.y": (2, 3)}, id="nested_dotted"
        ),
        pytest.param({"list": [1]}, {"list": [1, 2]}, {"list": ([1], [1, 2])}, id="lists_compared_whole"),
    ],
)
def test_find_changes(old: dict[str, Any], new: dict[str, Any], expected: dict[str, tuple[Any, Any]]) -> None:
    assert find_changes(old, new) == expected


@pytest.mark.parametrize(
    ("cache", "update", "merged", "expected"),
    [
        pytest.param({"a": 1}, {"a": 1}, {"a": 1}, {}, id="unchanged"),
        pytest.param({"a": 1, "b": 2}, {"a": 3}, {"a": 3, "b": 2}, {"a": (1, 3)}, id="missing_field_kept"),
        pytest.param({}, {"a": None}, {"a": None}, {"a": (None, None)}, id="new_field"),
        pytest.param(
            {"status": {"x": 1, "y": 2}},
            {"status": {"y": 3}},
            {"status": {"x": 1, "y": 3}},
            {"status.y": (2, 3)},
            id="nested_merged_key_by_key",
        ),
        pytest.param(
            {"status": {"x": 1}}, {"status": None}, {"status": None}, {"status": ({"x": 1}, None)}, id="nulled"
        ),
    ],
)
def test_merge_changes(
    cache: dict[str, Any], update: dict[str, Any], merged: dict[str, Any], expected: dict[str, tuple[Any, Any]]
) -> None:
    assert merge_changes(cache, update) == expected
    assert cache == merged


def test_merge_changes_copies_new_nested_objects() -> None:
    """Later changes to the update must not leak into the cache."""
    update = {"status": {"x": 1}}
    cache: dict[str, Any] = {}

    merge_changes(cache, update)
    update["status"]["x"] = 2

    assert cache == {"status": {"x": 1}}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param({"evseState": (2, 3)}, "evseState 2 -> 3", id="value"),
        pytest.param(
            {"sysTime": ("2026-10-24T12:00:00+01:00", "2026-10-24T12:01:00+01:00")},
            "sysTime -> '2026-10-24T12:01:00+01:00'",
            id="timestamp_shows_only_new",
        ),
        pytest.param(
            {"lastEvent": ("2026-10-24T11:00:00.000Z", "2026-10-24T11:01:00.123Z")},
            "lastEvent -> '2026-10-24T11:01:00.123Z'",
            id="utc_timestamp",
        ),
        pytest.param({"sysFwVersion": ("629", "630")}, "sysFwVersion '629' -> '630'", id="short_string_not_timestamp"),
        pytest.param(
            {"sysSSID": ("OldNetwork1", "HomeNetwork1"), "sysPlugged": (False, True)},
            "sysSSID 'OldNetwork1' -> 'HomeNetwork1', sysPlugged False -> True",
            id="long_string_not_timestamp",
        ),
    ],
)
def test_format_changes(changes: dict[str, tuple[Any, Any]], expected: str) -> None:
    assert format_changes(changes) == expected


def test_format_changes_without_old_values() -> None:
    """REST responses log only the new values."""
    changes = {"sysPlugged": (False, True), "sysTime": ("2026-10-24T12:00:00+01:00", "2026-10-24T12:01:00+01:00")}

    assert format_changes(changes, show_old=False) == "sysPlugged -> True, sysTime -> '2026-10-24T12:01:00+01:00'"
