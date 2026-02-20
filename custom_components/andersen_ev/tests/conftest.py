"""Pytest configuration and fixtures for Andersen EV tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def event_loop():
    """Create an event loop for tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_api():
    """Create a mock API client."""
    api = MagicMock()
    api.token = "test_token_12345"
    api.tokenType = "Bearer"
    api.tokenExpiryTime = None
    api.ensure_valid_auth = AsyncMock()
    api.refresh_token = AsyncMock()
    return api


@pytest.fixture
def mock_device(mock_api):  # pylint: disable=redefined-outer-name
    """Create a mock KonnectDevice."""
    from andersen_ev.konnect.device import (
        KonnectDevice,  # pylint: disable=import-outside-toplevel
    )

    return KonnectDevice(
        api=mock_api,
        device_id="test_device_123",
        friendly_name="Test Device",
        user_lock=False,
    )


@pytest.fixture
def graphql_device_status_response():
    """Sample GraphQL response for device status (unwrapped by GraphQLClient)."""
    return {
        "getDevice": {
            "name": "Andersen A2",
            "deviceStatus": {
                "id": "device_123",
                "online": True,
                "evseState": "3",
                "sysChargingEnabled": True,
                "sysUserLock": False,
                "sysScheduleLock": False,
                "sysProductName": "Andersen A2",
                "sysProductId": "A2",
                "sysHwVersion": "1.0",
                "evseHwVersion": "2.0",
                "chargeStatus": {
                    "start": "2024-02-19T10:30:00Z",
                    "chargeEnergyTotal": 15.5,
                    "solarEnergyTotal": 5.2,
                    "gridEnergyTotal": 10.3,
                    "chargePower": 2500,
                    "chargePowerMax": 7400,
                    "solarPower": 1200,
                    "gridPower": 1300,
                    "duration": 120,
                },
                "scheduleSlotsArray": [],
            },
        }
    }


@pytest.fixture
def graphql_charge_logs_response():
    """Sample GraphQL response for charge logs (unwrapped by GraphQLClient)."""
    return {
        "getDevice": {
            "deviceCalculatedChargeLogs": [
                {
                    "chargeCostTotal": 4.50,
                    "chargeEnergyTotal": 15.5,
                    "deviceId": "device_123",
                    "duration": 120,
                    "gridCostTotal": 3.20,
                    "gridEnergyTotal": 10.3,
                    "particleFwVersion": "1.2.3",
                    "solarEnergyTotal": 5.2,
                    "solarCostTotal": 0.0,
                    "startDateTimeLocal": "2024-02-19T10:30:00",
                    "surplusUsedCostTotal": 1.30,
                    "surplusUsedEnergyTotal": 0.0,
                    "uuid": "charge_log_uuid",
                }
            ]
        }
    }


@pytest.fixture
def graphql_device_info_response():
    """Sample GraphQL response for device info (unwrapped by GraphQLClient)."""
    return {
        "getDevice": {
            "id": "device_123",
            "name": "Andersen A2",
            "last_ip_address": "192.168.1.100",
            "deviceStatus": {
                "id": "device_status_123",
                "evseState": "3",
                "sysFwVersion": "1.2.3",
                "sysSchEnabled": True,
                "sysUserLock": False,
                "sysScheduleLock": False,
                "sysRssi": -65,
                "sysSSID": "MyWiFi",
                "sysLan": True,
                "sysTemperature": 25,
                "sysFreeMemory": 512000,
                "sysRuntime": 864000,
                "sysHwVersion": "1.0",
                "evseFwVersion": "2.0",
                "evseHwVersion": "2.1",
            },
        }
    }


@pytest.fixture
def graphql_command_success_response():
    """Sample GraphQL response for command execution (unwrapped by GraphQLClient)."""
    return {
        "runAEVCommand": {
            "return_value": True,
            "__typename": "CommandResult",
        }
    }


@pytest.fixture
def graphql_error_response():
    """Sample GraphQL error response."""
    return {
        "errors": [
            {
                "message": "Authentication required",
                "extensions": {"code": "UNAUTHENTICATED"},
            }
        ]
    }
