"""Test setup process."""

from andersen_ev import (
    AndersenEvCoordinator,
    async_setup_entry,
    async_unload_entry,
)
from andersen_ev.const import DOMAIN


def test_functions_exist() -> None:
    """Test that required functions are defined."""
    assert callable(async_setup_entry)
    assert callable(async_unload_entry)
    assert AndersenEvCoordinator is not None


def test_domain_constant() -> None:
    """Test that domain constant is defined."""
    assert DOMAIN == "andersen_ev"
