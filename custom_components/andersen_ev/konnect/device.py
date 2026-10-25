"""Konnect device interface for Andersen EV chargers."""

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import const
from .changes import merge_changes
from .graphql_client import GraphQLClient
from .subscription import DeviceStatusSubscription

if TYPE_CHECKING:
    from .client import KonnectClient

_LOGGER = logging.getLogger(__name__)

# Older firmware keeps sysUserMaxChargeAmpSet fixed and only the REST status userSetChargeAmpMax follows the setting.
USER_SET_CHARGE_AMP_MAX = "userSetChargeAmpMax"
SYS_USER_MAX_CHARGE_AMP_SET = "sysUserMaxChargeAmpSet"


class KonnectDevice:
    """Represents an Andersen EV charger and its GraphQL operations."""

    api: KonnectClient
    device_id: str
    friendly_name: str
    user_lock: bool = False
    _last_status: dict[str, Any] | None = None
    _last_charge: dict[str, Any] | None = None
    _last_solar_status: dict[str, Any] | None = None
    # The REST getDevice userDevice object, which has fields GraphQL doesn't expose.
    _last_user_device: dict[str, Any] | None = None
    # Restored when grid supplement is turned back on, since turning it off zeroes the percent.
    last_grid_charge_percent: int | None = None
    model_name: str | None = None
    currency: str | None = None
    _graphql_client: GraphQLClient | None = None  # GraphQL client instance
    _subscription: DeviceStatusSubscription | None = None
    # Merged deviceStatusUpdated payloads, kept apart from the polled status so a poll can't wipe
    # subscription-only fields.
    _last_subscription_status: dict[str, Any] | None = None
    # Set by the integration's subscription loop.
    subscription_connected: bool = False
    subscription_disconnected_since: datetime | None = None
    last_subscription_update: datetime | None = None
    # The user max current field last seen to appear or change; see effective_max_amps.
    user_max_amps_field: str | None = None
    _user_max_amps_seen: dict[str, Any]

    def __init__(self, api, device_id, friendly_name, user_lock):
        """Initialize the device."""
        self.api = api
        self.device_id = device_id
        self.friendly_name = friendly_name
        self.user_lock = user_lock
        self._last_status = None
        self._last_charge = None
        self._last_solar_status = None
        self._last_user_device = None
        self.last_grid_charge_percent = None
        self.model_name = None
        self.currency = None
        self._graphql_client = None
        self._subscription = None
        self._last_subscription_status = None
        self.subscription_connected = False
        self.subscription_disconnected_since = None
        self.last_subscription_update = None
        self.user_max_amps_field = None
        self._user_max_amps_seen = {}

    @property
    def last_status(self):
        """Return the last known device status."""
        return self._last_status

    @property
    def last_solar_status(self):
        """Return the last known solar status."""
        return self._last_solar_status or {}

    @property
    def last_rest_status(self) -> dict[str, Any]:
        """Return the status object from the last REST getDevice call."""
        return (self._last_user_device or {}).get("status") or {}

    async def get_user_device(self) -> dict[str, Any]:
        """Fetch and cache the REST getDevice userDevice object."""
        self._last_user_device = await self.api.getDevice(self.device_id)
        self._track_user_max_amps(USER_SET_CHARGE_AMP_MAX, self.last_rest_status.get(USER_SET_CHARGE_AMP_MAX))
        return self._last_user_device

    @property
    def live_status(self) -> dict[str, Any]:
        """Return the merged subscription status, empty before the first payload."""
        return self._last_subscription_status or {}

    @property
    def last_subscription_status(self) -> dict[str, Any] | None:
        """Return the merged deviceStatusUpdated payloads, or None before the first one."""
        return self._last_subscription_status

    def apply_subscription_update(self, payload: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
        """Merge a deviceStatusUpdated payload into the cache and return what changed.

        Changes are keyed by field name, with nested fields dotted (``chargeStatus.chargePower``),
        and map to ``(old, new)``.
        """
        if self._last_subscription_status is None:
            self._last_subscription_status = {}
        self._track_user_max_amps(SYS_USER_MAX_CHARGE_AMP_SET, payload.get(SYS_USER_MAX_CHARGE_AMP_SET))
        return merge_changes(self._last_subscription_status, payload)

    def _track_user_max_amps(self, field: str, value: Any) -> None:
        """Read the user max current from field once it changes, or when first seen.

        On first sight userSetChargeAmpMax is preferred, since where both are reported it is the one
        that follows the setting.
        """
        if value is None:
            return
        first_seen = field not in self._user_max_amps_seen
        changed = not first_seen and self._user_max_amps_seen[field] != value
        self._user_max_amps_seen[field] = value
        if field == self.user_max_amps_field:
            return
        if changed or (first_seen and (self.user_max_amps_field is None or field == USER_SET_CHARGE_AMP_MAX)):
            _LOGGER.debug("Reading user max charge current for %s from %s", self.friendly_name, field)
            self.user_max_amps_field = field

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

    @property
    def subscription(self) -> DeviceStatusSubscription:
        """Lazily create the deviceStatusUpdated subscription on first use."""
        if self._subscription is None:
            self._subscription = DeviceStatusSubscription(
                device_id=self.device_id,
                token_provider=self._current_token,
                token_refresh=self._refresh_token,
            )
        return self._subscription

    def _current_token(self) -> str:
        """Return the account's current bearer token."""
        assert self.api.token is not None, "subscription used before authentication"
        return self.api.token

    async def _refresh_token(self) -> tuple[str, float | None]:
        """Refresh authentication and return new token with expiry time."""
        await self.api.refresh_token()
        return self._current_token(), getattr(self.api, "token_expiry_time", None)

    async def _refresh_graphql_token(self) -> tuple[str, float | None]:
        """Refresh for the query client, then move the subscription onto the new token."""
        token = await self._refresh_token()
        if self._subscription is not None:
            await self._subscription.restart()
        return token

    async def close(self):
        """Clean up the GraphQL client and subscription."""
        if self._graphql_client is not None:
            await self._graphql_client.close()
        if self._subscription is not None:
            await self._subscription.close()

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
        # Matches the app, which sends chargeNow and then userForceStart. userForceStart clears
        # the force stop set by stop_charging, without which the charger will not restart.
        success = await self._run_command("chargeNow") and await self._run_command("userForceStart")
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

    async def set_user_max_amps(self, amps: int) -> bool:
        """Set the user maximum charge current, as the app's power settings do."""
        _LOGGER.debug(
            "Setting user max charge current for device %s (%s) to %s A",
            self.device_id,
            self.friendly_name,
            amps,
        )
        success = await self._run_command("userAmpSet", {"userSetChargeAmpMax": amps})
        if not success:
            _LOGGER.warning(
                "Failed to set user max charge current for device %s (%s)",
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

        _LOGGER.debug("Sending GraphQL setAllSchedulesDisabled for device %s", self.device_id)

        result = await self.graphql_client.execute_mutation(
            operation_name="setAllSchedulesDisabled",
            mutation=mutation,
            variables={"deviceId": self.device_id},
        )

        if result is None:
            return False

        _LOGGER.debug("GraphQL setAllSchedulesDisabled response: %s", result)
        return True

    async def _run_command(self, function, params: dict[str, Any] | None = None):
        """Run a command on the device with automatic token refresh."""
        _LOGGER.debug("Sending GraphQL runAEVCommand %s %s for device %s", function, params or "", self.device_id)

        variables: dict[str, Any] = {"deviceId": self.device_id, "functionName": function}
        if params is not None:
            # The API takes params as a JSON-encoded string.
            variables["params"] = json.dumps(params)

        result = await self.graphql_client.execute_mutation(
            operation_name="runAEVCommand",
            mutation=const.GRAPHQL_RUN_COMMAND_QUERY,
            variables=variables,
        )

        if result is None:
            return False

        _LOGGER.debug("GraphQL runAEVCommand %s response: %s", function, result)
        return True

    async def get_detailed_device_status(self):
        """Get the detailed status of the device."""
        _LOGGER.debug(
            "Calling GraphQL getDeviceStatus for device %s (%s)",
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
            _LOGGER.warning("Invalid response format from GraphQL getDeviceStatus")
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
        _LOGGER.debug(
            "Calling GraphQL getDeviceCalculatedChargeLogs for device %s (%s)",
            self.device_id,
            self.friendly_name,
        )
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
            _LOGGER.warning("Invalid response format from GraphQL getDeviceCalculatedChargeLogs")
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
        self._last_solar_status = {
            "boost": status.get("solarOverride"),
            "solarChargeAlways": status.get("solarChargeAlways"),
            "solarMaxGridChargePercent": status.get("solarMaxGridChargePercent"),
            # Not sysScheduleLock: that also drops while a Charge now session overrides a schedule.
            "solarChargeOutsideSchedules": status.get("chargeOutsideSchedules"),
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
            "Setting solar options for device %s (%s): boost=%s, charge_always=%s, max_grid_charge_percent=%s, "
            "charge_outside_schedules=%s",
            self.device_id,
            self.friendly_name,
            boost,
            charge_always,
            max_grid_charge_percent,
            charge_outside_schedules,
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
            "Calling GraphQL getDevice for device %s (%s)",
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
            _LOGGER.warning("Invalid response format from GraphQL getDevice")
            return None

        device_info = result["getDevice"]
        currency = (device_info.get("deviceInfo") or {}).get("currency")
        if currency:
            self.currency = currency
        return device_info
