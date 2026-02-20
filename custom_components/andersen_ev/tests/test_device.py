"""Tests for KonnectDevice GraphQL calls."""
# pylint: disable=protected-access

from unittest.mock import AsyncMock

import pytest


class TestDeviceGraphQLCalls:
    """Test GraphQL calls from KonnectDevice."""

    @pytest.mark.asyncio
    async def test_get_device_status_success(
        self, mock_device, graphql_device_status_response
    ):
        """Test successful get_device_status call."""
        # Mock the GraphQLClient.execute_query method
        mock_device._graphql_client.execute_query = AsyncMock(
            return_value=graphql_device_status_response
        )

        # Call the method
        status = await mock_device.get_device_status()

        # Assertions
        assert status is not None
        assert status["online"] is True
        assert status["evseState"] == "3"
        assert status["chargeStatus"]["chargePower"] == 2500

        # Verify the method was called correctly
        mock_device._graphql_client.execute_query.assert_called_once()
        call_args = mock_device._graphql_client.execute_query.call_args
        assert call_args[1]["operation_name"] == "getDeviceStatusSimple"
        assert call_args[1]["variables"]["id"] == "test_device_123"

    @pytest.mark.asyncio
    async def test_get_device_status_error_response(self, mock_device):
        """Test get_device_status with invalid response format."""
        # Return response missing deviceStatus
        invalid_response = {"getDevice": {"name": "Test"}}
        mock_device._graphql_client.execute_query = AsyncMock(
            return_value=invalid_response
        )

        status = await mock_device.get_device_status()
        assert status is None

    @pytest.mark.asyncio
    async def test_get_device_status_graphql_error(self, mock_device):
        """Test get_device_status when GraphQL returns None (error)."""
        # GraphQLClient returns None when there are errors
        mock_device._graphql_client.execute_query = AsyncMock(return_value=None)

        status = await mock_device.get_device_status()
        assert status is None

    @pytest.mark.asyncio
    async def test_get_last_charge_success(
        self, mock_device, graphql_charge_logs_response
    ):
        """Test successful get_last_charge call."""
        mock_device._graphql_client.execute_query = AsyncMock(
            return_value=graphql_charge_logs_response
        )

        charge_log = await mock_device.get_last_charge()

        assert charge_log is not None
        assert charge_log["chargeEnergyTotal"] == 15.5
        assert charge_log["chargeCostTotal"] == 4.50

    @pytest.mark.asyncio
    async def test_get_last_charge_empty_logs(self, mock_device):
        """Test get_last_charge with empty logs."""
        # Empty logs response
        empty_response = {"getDevice": {"deviceCalculatedChargeLogs": []}}
        mock_device._graphql_client.execute_query = AsyncMock(
            return_value=empty_response
        )

        charge_log = await mock_device.get_last_charge()
        assert charge_log is None

    @pytest.mark.asyncio
    async def test_get_device_info_success(
        self, mock_device, graphql_device_info_response
    ):
        """Test successful get_device_info call."""
        mock_device._graphql_client.execute_query = AsyncMock(
            return_value=graphql_device_info_response
        )

        device_info = await mock_device.get_device_info()

        assert device_info is not None
        assert device_info["name"] == "Andersen A2"
        assert device_info["id"] == "device_123"

    @pytest.mark.asyncio
    async def test_enable_charging(self, mock_device, graphql_command_success_response):
        """Test enable charging."""
        mock_device._graphql_client.execute_mutation = AsyncMock(
            return_value=graphql_command_success_response
        )

        result = await mock_device.enable()

        assert result is True

    @pytest.mark.asyncio
    async def test_disable_charging(
        self, mock_device, graphql_command_success_response
    ):
        """Test disable charging."""
        mock_device._graphql_client.execute_mutation = AsyncMock(
            return_value=graphql_command_success_response
        )

        result = await mock_device.disable()

        assert result is True

    @pytest.mark.asyncio
    async def test_disable_all_schedules_success(
        self, mock_device, graphql_command_success_response
    ):
        """Test successful disable_all_schedules."""
        mock_device._graphql_client.execute_mutation = AsyncMock(
            return_value=graphql_command_success_response
        )

        result = await mock_device.disable_all_schedules()

        assert result is True
        mock_device._graphql_client.execute_mutation.assert_called_once()

    @pytest.mark.asyncio
    async def test_disable_all_schedules_failure(self, mock_device):
        """Test disable_all_schedules with error."""
        mock_device._graphql_client.execute_mutation = AsyncMock(return_value=None)

        result = await mock_device.disable_all_schedules()

        assert result is False

    @pytest.mark.asyncio
    async def test_request_bearer_auth_header(self, mock_device):
        """Test that Bearer token is properly passed to the GraphQL client."""
        # The GraphQLClient is initialized with the API token
        assert mock_device._graphql_client.token == mock_device.api.token

    @pytest.mark.asyncio
    async def test_graphql_url_used(self, mock_device, graphql_device_status_response):
        """Test that GraphQL client is used for requests."""
        mock_device._graphql_client.execute_query = AsyncMock(
            return_value=graphql_device_status_response
        )

        await mock_device.get_device_status()

        # Verify execute_query was called
        mock_device._graphql_client.execute_query.assert_called_once()
