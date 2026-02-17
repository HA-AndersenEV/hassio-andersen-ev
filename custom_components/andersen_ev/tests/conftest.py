"""Shared fixtures for tests."""

from unittest.mock import patch

import pytest

from andersen_ev.const import CONF_EMAIL, CONF_PASSWORD

MOCK_CONFIG = {
    CONF_EMAIL: "test@example.com",
    CONF_PASSWORD: "testpassword",
}


@pytest.fixture
def bypass_get_data():
    """Skip calls to get data from API."""
    with patch(
        "andersen_ev.AndersenEvCoordinator._async_update_data",
        return_value={},
    ):
        yield


@pytest.fixture
def error_on_get_data():
    """Simulate error on get data from API."""
    with patch(
        "andersen_ev.AndersenEvCoordinator._async_update_data",
        side_effect=Exception("API Error"),
    ):
        yield
