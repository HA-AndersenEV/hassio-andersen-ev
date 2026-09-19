"""Tests for KonnectDevice GraphQL calls."""
# pylint: disable=protected-access

from unittest.mock import AsyncMock

import pytest


class TestDeviceGraphQLCalls:
    """Test GraphQL calls from KonnectDevice."""

    @pytest.mark.asyncio
    async def test_get_detailed_device_status_success(self, mock_device, graphql_device_status_response):
        """Test successful get_detailed_device_status call."""
        # Mock the GraphQLClient.execute_query method
        mock_device.graphql_client.execute_query = AsyncMock(return_value=graphql_device_status_response)

        # Call the method
        status = await mock_device.get_detailed_device_status()

        # Assertions
        assert status is not None
        assert status["online"] is True
        assert status["evseState"] == "3"
        assert status["chargeStatus"]["chargePower"] == 2500

        # Verify the method was called correctly
        mock_device.graphql_client.execute_query.assert_called_once()
        call_args = mock_device.graphql_client.execute_query.call_args
        assert call_args[1]["operation_name"] == "getDeviceStatus"
        assert call_args[1]["variables"]["id"] == "test_device_123"

    @pytest.mark.asyncio
    async def test_get_detailed_device_status_error_response(self, mock_device):
        """Test get_detailed_device_status with invalid response format."""
        # Return response missing deviceStatus
        invalid_response = {"getDevice": {"name": "Test"}}
        mock_device.graphql_client.execute_query = AsyncMock(return_value=invalid_response)

        status = await mock_device.get_detailed_device_status()
        assert status is None

    @pytest.mark.asyncio
    async def test_get_detailed_device_status_graphql_error(self, mock_device):
        """Test get_detailed_device_status when GraphQL returns None (error)."""
        # GraphQLClient returns None when there are errors
        mock_device.graphql_client.execute_query = AsyncMock(return_value=None)

        status = await mock_device.get_detailed_device_status()
        assert status is None

    @pytest.mark.asyncio
    async def test_get_last_charge_success(self, mock_device, graphql_charge_logs_response):
        """Test successful get_last_charge call."""
        mock_device.graphql_client.execute_query = AsyncMock(return_value=graphql_charge_logs_response)

        charge_log = await mock_device.get_last_charge()

        assert charge_log is not None
        assert charge_log["chargeEnergyTotal"] == 15.5
        assert charge_log["chargeCostTotal"] == 4.50
        assert mock_device.last_charge == charge_log

    @pytest.mark.asyncio
    async def test_get_last_charge_empty_logs(self, mock_device):
        """Test get_last_charge with empty logs."""
        # Empty logs response
        empty_response = {"getDevice": {"deviceCalculatedChargeLogs": []}}
        mock_device.graphql_client.execute_query = AsyncMock(return_value=empty_response)

        charge_log = await mock_device.get_last_charge()
        assert charge_log is None

    @pytest.mark.asyncio
    async def test_get_last_charge_failure_keeps_previous_cached_value(self, mock_device, graphql_charge_logs_response):
        """A later failed fetch must not clobber the last known-good charge data."""
        mock_device.graphql_client.execute_query = AsyncMock(return_value=graphql_charge_logs_response)
        await mock_device.get_last_charge()
        assert mock_device.last_charge is not None

        mock_device.graphql_client.execute_query = AsyncMock(return_value=None)
        result = await mock_device.get_last_charge()

        assert result is None
        assert mock_device.last_charge is not None
        assert mock_device.last_charge["chargeEnergyTotal"] == 15.5

    @pytest.mark.asyncio
    async def test_get_device_info_success(self, mock_device, graphql_device_info_response):
        """Test successful get_device_info call."""
        mock_device.graphql_client.execute_query = AsyncMock(return_value=graphql_device_info_response)

        device_info = await mock_device.get_device_info()

        assert device_info is not None
        assert device_info["name"] == "Andersen A2"
        assert device_info["id"] == "device_123"
        assert mock_device.currency == "GBP"

    @pytest.mark.asyncio
    async def test_get_device_info_missing_currency_leaves_it_unset(self, mock_device, graphql_device_info_response):
        """Test get_device_info doesn't overwrite currency when deviceInfo lacks it."""
        del graphql_device_info_response["getDevice"]["deviceInfo"]["currency"]
        mock_device.graphql_client.execute_query = AsyncMock(return_value=graphql_device_info_response)

        await mock_device.get_device_info()

        assert mock_device.currency is None

    @pytest.mark.asyncio
    async def test_enable_charging(self, mock_device, graphql_command_success_response):
        """Test enable charging."""
        mock_device.graphql_client.execute_mutation = AsyncMock(return_value=graphql_command_success_response)

        result = await mock_device.enable()

        assert result is True

    @pytest.mark.asyncio
    async def test_disable_charging(self, mock_device, graphql_command_success_response):
        """Test disable charging."""
        mock_device.graphql_client.execute_mutation = AsyncMock(return_value=graphql_command_success_response)

        result = await mock_device.disable()

        assert result is True

    @pytest.mark.asyncio
    async def test_disable_all_schedules_success(self, mock_device, graphql_command_success_response):
        """Test successful disable_all_schedules."""
        mock_device.graphql_client.execute_mutation = AsyncMock(return_value=graphql_command_success_response)

        result = await mock_device.disable_all_schedules()

        assert result is True
        mock_device.graphql_client.execute_mutation.assert_called_once()

    @pytest.mark.asyncio
    async def test_disable_all_schedules_failure(self, mock_device):
        """Test disable_all_schedules with error."""
        mock_device.graphql_client.execute_mutation = AsyncMock(return_value=None)

        result = await mock_device.disable_all_schedules()

        assert result is False

    @pytest.mark.asyncio
    async def test_request_bearer_auth_header(self, mock_device):
        """Test that Bearer token is properly passed to the GraphQL client."""
        # The GraphQLClient is initialized with the API token
        assert mock_device.graphql_client.token == mock_device.api.token

    @pytest.mark.asyncio
    async def test_graphql_url_used(self, mock_device, graphql_device_status_response):
        """Test that GraphQL client is used for requests."""
        mock_device.graphql_client.execute_query = AsyncMock(return_value=graphql_device_status_response)

        await mock_device.get_detailed_device_status()

        # Verify execute_query was called
        mock_device.graphql_client.execute_query.assert_called_once()

    # -- solar tests -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_solar_success(self, mock_device):
        """Test get_solar re-fetches status and extracts solar fields."""
        mock_device.graphql_client.execute_query = AsyncMock(
            return_value={
                "getDevice": {
                    "deviceStatus": {
                        "solarOverride": False,
                        "solarChargeAlways": True,
                        "solarMaxGridChargePercent": 50,
                        "online": True,
                    }
                }
            }
        )

        solar = await mock_device.get_solar()

        assert solar is not None
        assert solar["solarOverride"] is False
        assert solar["solarChargeAlways"] is True
        assert solar["solarMaxGridChargePercent"] == 50
        mock_device.graphql_client.execute_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_solar_no_status(self, mock_device):
        """Test get_solar returns None when status fetch fails."""
        mock_device.graphql_client.execute_query = AsyncMock(return_value=None)

        solar = await mock_device.get_solar()
        assert solar is None

    @pytest.mark.asyncio
    async def test_get_solar_missing_fields(self, mock_device):
        """Test get_solar returns None values for missing solar fields."""
        mock_device.graphql_client.execute_query = AsyncMock(
            return_value={"getDevice": {"deviceStatus": {"online": True}}}
        )

        solar = await mock_device.get_solar()
        assert solar is not None
        assert solar["solarOverride"] is None
        assert solar["solarChargeAlways"] is None
        assert solar["solarMaxGridChargePercent"] is None

    @pytest.mark.asyncio
    async def test_set_solar_all_params(self, mock_device, graphql_set_solar_response):
        """Test set_solar with all optional parameters."""
        mock_device.graphql_client.set_solar = AsyncMock(return_value=True)

        result = await mock_device.set_solar(
            override=True,
            charge_always=False,
            max_grid_charge_percent=75,
            charge_outside_schedules=True,
        )

        assert result is True
        mock_device.graphql_client.set_solar.assert_called_once_with(
            "test_device_123",
            {
                "override": True,
                "chargeAlways": False,
                "maxGridChargePercent": 75,
                "chargeOutsideSchedules": True,
            },
        )

    @pytest.mark.asyncio
    async def test_set_solar_single_param(self, mock_device, graphql_set_solar_response):
        """Test set_solar with only one optional parameter."""
        mock_device.graphql_client.set_solar = AsyncMock(return_value=True)

        result = await mock_device.set_solar(override=True)

        assert result is True
        mock_device.graphql_client.set_solar.assert_called_once_with(
            "test_device_123",
            {"override": True},
        )

    @pytest.mark.asyncio
    async def test_set_solar_failure(self, mock_device):
        """Test set_solar when API returns None."""
        mock_device.graphql_client.set_solar = AsyncMock(return_value=False)

        result = await mock_device.set_solar(override=True)
        assert result is False

    # -- misc / lifecycle tests ---------------------------------------------

    def test_last_status_property(self, mock_device):
        """Test that last_status exposes the private _last_status field."""
        assert mock_device.last_status is None

        mock_device._last_status = {"evseState": "3"}
        assert mock_device.last_status == {"evseState": "3"}

    @pytest.mark.asyncio
    async def test_refresh_graphql_token(self, mock_device, mock_api):
        """Test _refresh_graphql_token refreshes the API client and returns its token/expiry."""
        mock_api.token = "new-token"
        mock_api.tokenExpiryTime = 12345
        mock_api.refresh_token = AsyncMock()

        token, expiry = await mock_device._refresh_graphql_token()

        mock_api.refresh_token.assert_awaited_once()
        assert token == "new-token"
        assert expiry == 12345

    @pytest.mark.asyncio
    async def test_close_noop_when_graphql_client_not_created(self, mock_device):
        """Test close() is a no-op if the GraphQL client was never lazily created."""
        assert mock_device._graphql_client is None
        await mock_device.close()  # should not raise
        assert mock_device._graphql_client is None

    @pytest.mark.asyncio
    async def test_close_closes_graphql_client(self, mock_device):
        """Test close() delegates to the GraphQL client's close() once created."""
        mock_device.graphql_client
        mock_device._graphql_client.close = AsyncMock()

        await mock_device.close()

        mock_device._graphql_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reset_rcm_success(self, mock_device, graphql_command_success_response):
        """Test reset_rcm returns True on success."""
        mock_device.graphql_client.execute_mutation = AsyncMock(return_value=graphql_command_success_response)

        result = await mock_device.reset_rcm()

        assert result is True

    @pytest.mark.asyncio
    async def test_reset_rcm_failure(self, mock_device):
        """Test reset_rcm returns False when the mutation fails."""
        mock_device.graphql_client.execute_mutation = AsyncMock(return_value=None)

        result = await mock_device.reset_rcm()

        assert result is False

    @pytest.mark.asyncio
    async def test_enable_failure_leaves_user_lock_unchanged(self, mock_device):
        """Test enable() does not flip user_lock when the mutation fails."""
        mock_device.user_lock = False
        mock_device.graphql_client.execute_mutation = AsyncMock(return_value=None)

        result = await mock_device.enable()

        assert result is False
        assert mock_device.user_lock is False

    @pytest.mark.asyncio
    async def test_disable_failure_leaves_user_lock_unchanged(self, mock_device):
        """Test disable() does not flip user_lock when the mutation fails."""
        mock_device.user_lock = True
        mock_device.graphql_client.execute_mutation = AsyncMock(return_value=None)

        result = await mock_device.disable()

        assert result is False
        assert mock_device.user_lock is True

    # -- status change logging ----------------------------------------------

    @pytest.mark.asyncio
    async def test_log_status_changes_detects_evse_and_online_changes(self, mock_device, caplog):
        """Test _log_status_changes logs when evseState or online flips between polls."""
        first_status = {"evseState": "1", "online": True}
        second_status = {"evseState": "3", "online": False}

        mock_device.graphql_client.execute_query = AsyncMock(
            side_effect=[
                {"getDevice": {"deviceStatus": first_status}},
                {"getDevice": {"deviceStatus": second_status}},
            ]
        )

        await mock_device.get_detailed_device_status()
        with caplog.at_level("INFO"):
            await mock_device.get_detailed_device_status()

        assert "EVSE state changed from 1 to 3" in caplog.text
        assert "Online state changed from True to False" in caplog.text

    @pytest.mark.asyncio
    async def test_log_status_changes_no_change_does_not_log(self, mock_device, caplog):
        """Test _log_status_changes stays quiet when nothing tracked has changed."""
        status = {"evseState": "1", "online": True}

        mock_device.graphql_client.execute_query = AsyncMock(
            side_effect=[
                {"getDevice": {"deviceStatus": dict(status)}},
                {"getDevice": {"deviceStatus": dict(status)}},
            ]
        )

        await mock_device.get_detailed_device_status()
        with caplog.at_level("INFO"):
            await mock_device.get_detailed_device_status()

        assert "EVSE state changed" not in caplog.text
        assert "Online state changed" not in caplog.text

    # -- get_last_charge() error branches ------------------------------------

    @pytest.mark.asyncio
    async def test_get_last_charge_graphql_error(self, mock_device):
        """Test get_last_charge returns None when the GraphQL call itself fails."""
        mock_device.graphql_client.execute_query = AsyncMock(return_value=None)

        result = await mock_device.get_last_charge()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_last_charge_invalid_response_format(self, mock_device):
        """Test get_last_charge returns None when the response is missing expected keys."""
        invalid_response = {"unexpected": "shape"}
        mock_device.graphql_client.execute_query = AsyncMock(return_value=invalid_response)

        result = await mock_device.get_last_charge()
        assert result is None

    # -- get_device_info() error branches ------------------------------------

    @pytest.mark.asyncio
    async def test_get_device_info_graphql_error(self, mock_device):
        """Test get_device_info returns None when the GraphQL call itself fails."""
        mock_device.graphql_client.execute_query = AsyncMock(return_value=None)

        result = await mock_device.get_device_info()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_device_info_invalid_response_format(self, mock_device):
        """Test get_device_info returns None when the response is missing 'getDevice'."""
        mock_device.graphql_client.execute_query = AsyncMock(return_value={"unexpected": "shape"})

        result = await mock_device.get_device_info()
        assert result is None
