"""Konnect device interface for Andersen EV chargers."""

import logging
from typing import TYPE_CHECKING, Any

from . import const
from .graphql_client import GraphQLClient

if TYPE_CHECKING:
    from .client import KonnectClient

_LOGGER = logging.getLogger(__name__)


class KonnectDevice:
    """Represents an Andersen EV charger and its GraphQL operations."""

    api: KonnectClient
    device_id: str
    friendly_name: str
    user_lock: bool = False
    _last_status: dict[str, Any] | None = None
    _last_charge: dict[str, Any] | None = None
    _last_solar_status: dict[str, Any] | None = None
    # Restored when grid supplement is turned back on, since turning it off zeroes the percent.
    last_grid_charge_percent: int | None = None
    model_name: str | None = None
    currency: str | None = None
    _graphql_client: GraphQLClient | None = None  # GraphQL client instance

    def __init__(self, api, device_id, friendly_name, user_lock):
        """Initialize the device."""
        self.api = api
        self.device_id = device_id
        self.friendly_name = friendly_name
        self.user_lock = user_lock
        self._last_status = None
        self._last_charge = None
        self._last_solar_status = None
        self.last_grid_charge_percent = None
        self.model_name = None
        self.currency = None
        self._graphql_client = None
        self.status_available = True

    @property
    def last_status(self):
        """Return the last known device status."""
        return self._last_status

    @property
    def last_solar_status(self):
        """Return the last known solar status."""
        return self._last_solar_status or {}

    def update_solar_status_cache(self, changes: dict[str, Any]) -> None:
        """Merge locally applied solar changes into the cached solar status."""
        if self._last_solar_status is None:
            self._last_solar_status = {}
        self._last_solar_status.update(changes)

    @property
    def last_charge(self):
        """Return the last known charge session data."""
        return self._last_charge

    @property
    def graphql_client(self) -> GraphQLClient:
        """Lazily create the GraphQL client on first use."""
        if self._graphql_client is None:
            assert self.api.token is not None, "graphql_client accessed before authentication"
            self._graphql_client = GraphQLClient(
                token=self.api.token,
                token_refresh=self._refresh_graphql_token,
                token_expiry_time=getattr(self.api, "token_expiry_time", None),
            )
        return self._graphql_client

    async def _refresh_graphql_token(self):
        """Refresh authentication and return new token with expiry time."""
        await self.api.refresh_token()
        return self.api.token, getattr(self.api, "token_expiry_time", None)

    async def close(self):
        """Clean up the GraphQL client resources."""
        if self._graphql_client is not None:
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
                "Unlock command sent for device %s (%s), awaiting confirmation from the charger",
                self.device_id,
                self.friendly_name,
            )
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
                "Lock command sent for device %s (%s), awaiting confirmation from the charger",
                self.device_id,
                self.friendly_name,
            )
        else:
            _LOGGER.warning(
                "Failed to disable charging for device %s (%s)",
                self.device_id,
                self.friendly_name,
            )
        return success

    async def charge_now(self) -> bool:
        """Start charging immediately, as the app's Charge now button does."""
        _LOGGER.debug(
            "Attempting to start charging now for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )
        success = await self._run_command("chargeNow")
        if not success:
            _LOGGER.warning(
                "Failed to start charging for device %s (%s)",
                self.device_id,
                self.friendly_name,
            )
        return success

    async def stop_charging(self) -> bool:
        """Stop charging, as the app's Stop charging button does."""
        _LOGGER.debug(
            "Attempting to stop charging for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )
        # Matches the app, which sends userForceStop and then stopChargeNow.
        success = await self._run_command("userForceStop") and await self._run_command("stopChargeNow")
        if not success:
            _LOGGER.warning(
                "Failed to stop charging for device %s (%s)",
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

        _LOGGER.debug("Sending API command to disable all schedules for device %s", self.device_id)

        result = await self.graphql_client.execute_mutation(
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
        _LOGGER.debug("Sending API command: %s for device %s", function, self.device_id)

        result = await self.graphql_client.execute_mutation(
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

    async def get_detailed_device_status(self):
        """Get the detailed status of the device."""
        _LOGGER.debug(
            "Fetching detailed status for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )

        result = await self.graphql_client.execute_query(
            operation_name="getDeviceStatus",
            query=const.GRAPHQL_DEVICE_STATUS_DETAILED_QUERY,
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
            _LOGGER.debug("Model name for device %s: %s", self.friendly_name, self.model_name)

        status = result["getDevice"]["deviceStatus"]
        self._log_status_changes(status)
        self._last_status = status
        return status

    def _log_status_changes(self, status: dict) -> None:
        """Log changes to important status values compared to last known status."""
        if not self._last_status:
            return

        log_changes = False
        if "evseState" in status and "evseState" in self._last_status:
            if status["evseState"] != self._last_status["evseState"]:
                _LOGGER.info(
                    "Device %s: EVSE state changed from %s to %s",
                    self.friendly_name,
                    self._last_status["evseState"],
                    status["evseState"],
                )
                log_changes = True

        if "online" in status and "online" in self._last_status:
            if status["online"] != self._last_status["online"]:
                _LOGGER.info(
                    "Device %s: Online state changed from %s to %s",
                    self.friendly_name,
                    self._last_status["online"],
                    status["online"],
                )
                log_changes = True

        if "solarChargeAlways" in status and "solarChargeAlways" in self._last_status:
            if status["solarChargeAlways"] != self._last_status["solarChargeAlways"]:
                _LOGGER.info(
                    "Device %s: Solar charge always changed from %s to %s",
                    self.friendly_name,
                    self._last_status["solarChargeAlways"],
                    status["solarChargeAlways"],
                )
                log_changes = True

        if "solarOverride" in status and "solarOverride" in self._last_status:
            if status["solarOverride"] != self._last_status["solarOverride"]:
                _LOGGER.info(
                    "Device %s: Boost changed from %s to %s",
                    self.friendly_name,
                    self._last_status["solarOverride"],
                    status["solarOverride"],
                )
                log_changes = True

        if "sysScheduleLock" in status and "sysScheduleLock" in self._last_status:
            if status["sysScheduleLock"] != self._last_status["sysScheduleLock"]:
                _LOGGER.info(
                    "Device %s: System schedule lock changed from %s to %s",
                    self.friendly_name,
                    self._last_status["sysScheduleLock"],
                    status["sysScheduleLock"],
                )
                log_changes = True

        if "solarMaxGridChargePercent" in status and "solarMaxGridChargePercent" in self._last_status:
            if status["solarMaxGridChargePercent"] != self._last_status["solarMaxGridChargePercent"]:
                _LOGGER.info(
                    "Device %s: Solar max grid charge percent changed from %s to %s",
                    self.friendly_name,
                    self._last_status["solarMaxGridChargePercent"],
                    status["solarMaxGridChargePercent"],
                )
                log_changes = True

        if log_changes:
            _LOGGER.debug("Full status for %s: %s", self.friendly_name, status)

    async def get_last_charge(self):
        """Get the last charge session data."""
        result = await self.graphql_client.execute_query(
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

        if "getDevice" not in result or "deviceCalculatedChargeLogs" not in result["getDevice"]:
            _LOGGER.warning("Invalid response format from last charge request")
            return None

        device_logs = result["getDevice"]["deviceCalculatedChargeLogs"]
        if len(device_logs) == 0:
            _LOGGER.debug("No charge logs available for device %s", self.friendly_name)
            return None

        latest_log = device_logs[0]
        self._last_charge = {
            "duration": latest_log["duration"],
            "chargeCostTotal": latest_log["chargeCostTotal"],
            "chargeEnergyTotal": latest_log["chargeEnergyTotal"],
            "gridCostTotal": latest_log["gridCostTotal"],
            "gridEnergyTotal": latest_log["gridEnergyTotal"],
            "solarEnergyTotal": latest_log["solarEnergyTotal"],
            "solarCostTotal": latest_log["solarCostTotal"],
            "surplusUsedCostTotal": latest_log["surplusUsedCostTotal"],
            "surplusUsedEnergyTotal": latest_log["surplusUsedEnergyTotal"],
            "startDateTimeLocal": latest_log.get("startDateTimeLocal"),
        }
        return self._last_charge

    async def get_solar(self) -> dict | None:
        """Get the current solar charging settings from the device.

        The API's ``getSolar`` query is non-functional, so instead we
        re-fetch the full device status and extract the solar fields.
        """
        status = await self.get_detailed_device_status()
        if not status:
            _LOGGER.debug(
                "Could not fetch status for device %s (%s), cannot read solar settings",
                self.device_id,
                self.friendly_name,
            )
            return None

        return self.update_solar_status_from_device_status(status)

    def update_solar_status_from_device_status(self, status: dict[str, Any]) -> dict[str, Any]:
        """Populate the solar settings cache from an already-fetched device status."""
        # The API only exposes this as sysScheduleLock, which is the inverse of "charge outside schedule".
        schedule_lock = status.get("sysScheduleLock")
        self._last_solar_status = {
            "boost": status.get("solarOverride"),
            "solarChargeAlways": status.get("solarChargeAlways"),
            "solarMaxGridChargePercent": status.get("solarMaxGridChargePercent"),
            "solarChargeOutsideSchedules": None if schedule_lock is None else not schedule_lock,
        }
        self._remember_grid_charge_percent(status.get("solarMaxGridChargePercent"))
        return self._last_solar_status

    def _remember_grid_charge_percent(self, percent: int | None) -> None:
        if percent:
            self.last_grid_charge_percent = percent

    async def set_solar(
        self,
        *,
        boost: bool | None = None,
        charge_always: bool | None = None,
        max_grid_charge_percent: int | None = None,
        charge_outside_schedules: bool | None = None,
    ) -> bool:
        """Update solar charging settings on the device.

        Only the provided (non-None) parameters are sent to the API.
        """
        _LOGGER.debug(
            "Setting solar options for device %s (%s): boost=%s, charge_always=%s, max_grid_charge_percent=%s",
            self.device_id,
            self.friendly_name,
            boost,
            charge_always,
            max_grid_charge_percent,
        )

        fields: dict[str, object] = {}
        if boost is not None:
            fields["override"] = boost
        if charge_always is not None:
            fields["chargeAlways"] = charge_always
        if max_grid_charge_percent is not None:
            fields["maxGridChargePercent"] = max_grid_charge_percent
        if charge_outside_schedules is not None:
            fields["chargeOutsideSchedules"] = charge_outside_schedules

        success = await self.graphql_client.set_solar(self.device_id, fields)
        if success:
            self._remember_grid_charge_percent(max_grid_charge_percent)
        return success

    async def get_device_info(self):
        """Get the detailed device information."""
        _LOGGER.debug(
            "Fetching detailed info for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )

        result = await self.graphql_client.execute_query(
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
        currency = (device_info.get("deviceInfo") or {}).get("currency")
        if currency:
            self.currency = currency
        _LOGGER.debug("Successfully retrieved device info for %s", self.friendly_name)
        return device_info
