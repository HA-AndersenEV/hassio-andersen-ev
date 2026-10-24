"""Tests for the Andersen EV diagnostics platform."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from andersen_ev.diagnostics import async_get_config_entry_diagnostics
from andersen_ev.konnect.device import KonnectDevice


def _make_entry(devices):
    """Build a fake AndersenEvConfigEntry with runtime_data.devices set."""
    entry = MagicMock()
    entry.data = {"email": "test@example.com", "password": "supersecret"}
    entry.runtime_data = MagicMock(devices=devices)
    return entry


class TestAsyncGetConfigEntryDiagnostics:
    """Tests for async_get_config_entry_diagnostics()."""

    @pytest.mark.asyncio
    async def test_returns_expected_top_level_keys(self, mock_device):
        entry = _make_entry([mock_device])

        result = await async_get_config_entry_diagnostics(MagicMock(), entry)

        assert set(result) == {"entry_data", "client", "devices"}
        assert len(result["devices"]) == 1

    @pytest.mark.asyncio
    async def test_sensitive_fields_are_redacted(self, mock_device, mock_api):
        mock_api.email = "secret@example.com"
        mock_api.refresh_token_value = "refresh-secret"
        mock_api.device_key = "device-key-secret"
        entry = _make_entry([mock_device])

        result = await async_get_config_entry_diagnostics(MagicMock(), entry)

        assert result["entry_data"]["email"] == "**REDACTED**"
        assert result["entry_data"]["password"] == "**REDACTED**"
        assert result["client"]["email"] == "**REDACTED**"
        assert result["client"]["token"] == "**REDACTED**"
        assert result["client"]["refreshToken"] == "**REDACTED**"
        assert result["client"]["deviceKey"] == "**REDACTED**"

    @pytest.mark.asyncio
    async def test_non_sensitive_fields_pass_through_unredacted(self, mock_device):
        mock_device.model_name = "Andersen A2"
        mock_device.apply_subscription_update({"online": True})
        entry = _make_entry([mock_device])

        result = await async_get_config_entry_diagnostics(MagicMock(), entry)

        device_diag = result["devices"][0]
        assert device_diag["device_id"] == "test_device_123"
        assert device_diag["friendly_name"] == "Test Device"
        assert device_diag["model_name"] == "Andersen A2"
        assert device_diag["subscription"]["last_status"] == {"online": True}
        assert "last_status" not in device_diag
        assert result["client"]["tokenType"] == "Bearer"

    @pytest.mark.asyncio
    async def test_only_rest_status_is_included(self, mock_device: KonnectDevice) -> None:
        """The REST device and info objects hold account emails, so only the status is included."""
        mock_device._last_user_device = {
            "device": {"customer_id": "owner@example.com"},
            "info": {"ChargeOutsideSchedules": True},
            "status": {"sysUserMaxChargeAmpSet": 16},
        }
        entry = _make_entry([mock_device])

        result = await async_get_config_entry_diagnostics(MagicMock(), entry)

        device_diag = result["devices"][0]
        assert device_diag["last_rest_status"] == {"sysUserMaxChargeAmpSet": 16}
        assert "owner@example.com" not in str(result)

    @pytest.mark.asyncio
    async def test_no_devices_returns_empty_list_and_no_client_block(self):
        entry = _make_entry([])

        result = await async_get_config_entry_diagnostics(MagicMock(), entry)

        assert result["devices"] == []
        assert result["client"] is None

    @pytest.mark.asyncio
    async def test_client_block_not_duplicated_per_device(self, mock_api):
        from andersen_ev.konnect.device import KonnectDevice

        device_a = KonnectDevice(api=mock_api, device_id="a", friendly_name="A", user_lock=False)
        device_b = KonnectDevice(api=mock_api, device_id="b", friendly_name="B", user_lock=False)
        entry = _make_entry([device_a, device_b])

        result = await async_get_config_entry_diagnostics(MagicMock(), entry)

        assert len(result["devices"]) == 2
        assert isinstance(result["client"], dict)

    @pytest.mark.asyncio
    async def test_includes_subscription_state(self, mock_device: KonnectDevice) -> None:
        """The subscription's connection state and merged status are included."""
        mock_device.apply_subscription_update({"evseState": 3})
        mock_device.subscription_connected = True
        mock_device.last_subscription_update = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
        entry = _make_entry([mock_device])

        result = await async_get_config_entry_diagnostics(MagicMock(), entry)

        assert result["devices"][0]["subscription"] == {
            "connected": True,
            "disconnected_since": None,
            "last_update": "2026-10-24T12:00:00+00:00",
            "last_status": {"evseState": 3},
        }

    @pytest.mark.asyncio
    async def test_device_identifiers_are_redacted(self, mock_device: KonnectDevice) -> None:
        """The Wi-Fi network, charger serial and LoRa EUIs identify the user's home."""
        mock_device.apply_subscription_update(
            {"sysSSID": "HomeNetwork", "konnectSerial": "1234567890", "cfgLora0": {"eui": "00-11-22", "on": True}}
        )
        entry = _make_entry([mock_device])

        result = await async_get_config_entry_diagnostics(MagicMock(), entry)

        assert result["devices"][0]["subscription"]["last_status"] == {
            "sysSSID": "**REDACTED**",
            "konnectSerial": "**REDACTED**",
            "cfgLora0": {"eui": "**REDACTED**", "on": True},
        }
