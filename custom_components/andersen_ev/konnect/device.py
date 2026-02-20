"""Konnect device interface for Andersen EV chargers."""

import logging

from . import const
from .graphql_client import GraphQLClient

_LOGGER = logging.getLogger(__name__)


class KonnectDevice:
    """Represents an Andersen EV charger and its GraphQL operations."""

    api = None
    device_id = None
    friendly_name = None
    user_lock = False
    _last_status = None
    model_name = None
    _graphql_client = None  # GraphQL client instance

    def __init__(self, api, device_id, friendly_name, user_lock):
        """Initialize the device."""
        self.api = api
        self.device_id = device_id
        self.friendly_name = friendly_name
        self.user_lock = user_lock
        self._last_status = None
        self.model_name = None
        self._graphql_client = GraphQLClient(
            token=api.token,
            token_refresh=self._refresh_graphql_token,
            token_expiry_time=getattr(api, "tokenExpiryTime", None),
        )

    async def _refresh_graphql_token(self):
        """Refresh authentication and return new token with expiry time."""
        await self.api.refresh_token()
        return self.api.token, getattr(self.api, "tokenExpiryTime", None)

    async def close(self):
        """Clean up the GraphQL client resources."""
        await self._graphql_client.close()

    async def reset_rcm(self):
        """Reset RCM fault on the device."""
        _LOGGER.debug(
            "Attempting to reset RCM for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )
        success = await self._run_command("rcmReset")
        if success:
            _LOGGER.debug(
                "Successfully reset RCM for device %s (%s)",
                self.device_id,
                self.friendly_name,
            )
        else:
            _LOGGER.warning(
                "Failed to reset RCM for device %s (%s)",
                self.device_id,
                self.friendly_name,
            )
        return success

    async def enable(self):
        """Enable charging by unlocking user lock."""
        _LOGGER.debug(
            "Attempting to enable charging for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )
        success = await self._run_command("userUnlock")
        if success:
            _LOGGER.debug(
                "Successfully enabled charging for device %s (%s)",
                self.device_id,
                self.friendly_name,
            )
            self.user_lock = True
        else:
            _LOGGER.warning(
                "Failed to enable charging for device %s (%s)",
                self.device_id,
                self.friendly_name,
            )
        return success

    async def disable(self):
        """Disable charging by locking user lock."""
        _LOGGER.debug(
            "Attempting to disable charging for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )
        success = await self._run_command("userLock")
        if success:
            _LOGGER.debug(
                "Successfully disabled charging for device %s (%s)",
                self.device_id,
                self.friendly_name,
            )
            self.user_lock = False
        else:
            _LOGGER.warning(
                "Failed to disable charging for device %s (%s)",
                self.device_id,
                self.friendly_name,
            )
        return success

    async def disable_all_schedules(self):
        """Disable all charging schedules for the device."""
        _LOGGER.debug(
            "Attempting to disable all schedules for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )

        mutation = (
            "mutation setAllSchedulesDisabled($deviceId: ID!)"
            " { setAllSchedulesDisabled(deviceId: $deviceId)"
            " { id name return_value } }"
        )

        _LOGGER.debug(
            "Sending API command to disable all schedules for device %s",
            self.device_id,
        )

        result = await self._graphql_client.execute_mutation(
            operation_name="setAllSchedulesDisabled",
            mutation=mutation,
            variables={"deviceId": self.device_id},
        )

        if result is None:
            return False

        _LOGGER.debug("API disable all schedules response: %s", result)
        return True

    async def _run_command(self, function):
        """Run a command on the device with automatic token refresh."""
        _LOGGER.debug(
            "Sending API command: %s for device %s",
            function,
            self.device_id,
        )

        result = await self._graphql_client.execute_mutation(
            operation_name="runAEVCommand",
            mutation=const.GRAPHQL_RUN_COMMAND_QUERY,
            variables={
                "deviceId": self.device_id,
                "functionName": function,
            },
        )

        if result is None:
            return False

        _LOGGER.debug("API command response: %s", result)
        return True

    async def get_device_status(self):
        """Get the real-time status of the device."""
        result = await self._graphql_client.execute_query(
            operation_name="getDeviceStatusSimple",
            query=const.GRAPHQL_DEVICE_STATUS_QUERY,
            variables={"id": self.device_id},
        )

        if result is None:
            return None

        if "getDevice" not in result or "deviceStatus" not in result["getDevice"]:
            _LOGGER.warning("Invalid response format from device status request")
            return None

        # Store the model name if available
        if "name" in result["getDevice"]:
            self.model_name = result["getDevice"]["name"]
            _LOGGER.debug(
                "Model name for device %s: %s",
                self.friendly_name,
                self.model_name,
            )

        # Store the last status for reference
        status = result["getDevice"]["deviceStatus"]

        # Log changes to important status values
        log_changes = False
        if (
            self._last_status
            and "evseState" in status
            and "evseState" in self._last_status
        ):
            if status["evseState"] != self._last_status["evseState"]:
                _LOGGER.info(
                    "Device %s: EVSE state changed from %s to %s",
                    self.friendly_name,
                    self._last_status["evseState"],
                    status["evseState"],
                )
                log_changes = True

        if self._last_status and "online" in status and "online" in self._last_status:
            if status["online"] != self._last_status["online"]:
                _LOGGER.info(
                    "Device %s: Online state changed from %s to %s",
                    self.friendly_name,
                    self._last_status["online"],
                    status["online"],
                )
                log_changes = True

        if log_changes:
            _LOGGER.debug(
                "Full status for %s: %s",
                self.friendly_name,
                status,
            )

        self._last_status = status
        return status

    async def get_last_charge(self):
        """Get the last charge session data."""
        result = await self._graphql_client.execute_query(
            operation_name="getDeviceCalculatedChargeLogs",
            query=const.GRAPHQL_DEVICE_CHARGE_LOGS_QUERY,
            variables={
                "id": self.device_id,
                "offset": 0,
                "limit": 1,
                "minEnergy": 0.5,
            },
        )

        if result is None:
            return None

        if (
            "getDevice" not in result
            or "deviceCalculatedChargeLogs" not in result["getDevice"]
        ):
            _LOGGER.warning("Invalid response format from last charge request")
            return None

        device_logs = result["getDevice"]["deviceCalculatedChargeLogs"]
        if len(device_logs) == 0:
            _LOGGER.debug(
                "No charge logs available for device %s",
                self.friendly_name,
            )
            return None

        latest_log = device_logs[0]
        return {
            "duration": latest_log["duration"],
            "chargeCostTotal": latest_log["chargeCostTotal"],
            "chargeEnergyTotal": latest_log["chargeEnergyTotal"],
            "gridCostTotal": latest_log["gridCostTotal"],
            "gridEnergyTotal": latest_log["gridEnergyTotal"],
            "solarEnergyTotal": latest_log["solarEnergyTotal"],
            "solarCostTotal": latest_log["solarCostTotal"],
            "surplusUsedCostTotal": latest_log["surplusUsedCostTotal"],
            "surplusUsedEnergyTotal": latest_log["surplusUsedEnergyTotal"],
        }

    async def get_device_info(self):
        """Get the detailed device information."""
        _LOGGER.debug(
            "Fetching detailed info for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )

        result = await self._graphql_client.execute_query(
            operation_name="getDevice",
            query=const.GRAPHQL_DEVICE_INFO_QUERY,
            variables={"id": self.device_id},
        )

        if result is None:
            return None

        if "getDevice" not in result:
            _LOGGER.warning("Invalid response format from device info request")
            return None

        device_info = result["getDevice"]
        _LOGGER.debug(
            "Successfully retrieved device info for %s",
            self.friendly_name,
        )
        return device_info

    async def get_detailed_device_status(self):
        """Get the detailed status of the device."""
        _LOGGER.debug(
            "Fetching detailed status for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )

        result = await self._graphql_client.execute_query(
            operation_name="getDeviceStatus",
            query=const.GRAPHQL_DEVICE_STATUS_DETAILED_QUERY,
            variables={"id": self.device_id},
        )

        if result is None:
            return None

        if "getDevice" not in result or "deviceStatus" not in result["getDevice"]:
            _LOGGER.warning(
                "Invalid response format from detailed device status request"
            )
            return None

        # Store the model name if available
        if "name" in result["getDevice"]:
            self.model_name = result["getDevice"]["name"]
            _LOGGER.debug(
                "Model name for device %s: %s",
                self.friendly_name,
                self.model_name,
            )

        # Store the last status for reference in the lock entity
        status = result["getDevice"]["deviceStatus"]

        # Log changes to important status values
        log_changes = False
        if (
            self._last_status
            and "evseState" in status
            and "evseState" in self._last_status
        ):
            if status["evseState"] != self._last_status["evseState"]:
                _LOGGER.info(
                    "Device %s: EVSE state changed from %s to %s",
                    self.friendly_name,
                    self._last_status["evseState"],
                    status["evseState"],
                )
                log_changes = True

        if self._last_status and "online" in status and "online" in self._last_status:
            if status["online"] != self._last_status["online"]:
                _LOGGER.info(
                    "Device %s: Online state changed from %s to %s",
                    self.friendly_name,
                    self._last_status["online"],
                    status["online"],
                )
                log_changes = True

        if log_changes:
            _LOGGER.debug(
                "Full detailed status for %s: %s",
                self.friendly_name,
                status,
            )

        self._last_status = status
        return status
