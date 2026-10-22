"""Authentication client for the Andersen Konnect cloud API."""

import asyncio
import logging
import time

import boto3
import requests
from botocore.exceptions import ClientError
from pycognito.aws_srp import AWSSRP

from . import const
from .device import KonnectDevice
from .exceptions import AndersenApiError, AndersenAuthError, AndersenConnectionError

POOL_ID = "eu-west-1_t5HV3bFjl"
POOL_REGION = "eu-west-1"
CLIENT_ID = "23s0olnnniu5472ons0d9uoqt9"

_LOGGER = logging.getLogger(__name__)

# REST response keys holding account emails, kept out of debug logs.
_REDACT_KEYS = {"customer_id", "customerId", "email"}


def _redact(data: object) -> object:
    """Return a copy of a REST response with account emails replaced."""
    if isinstance(data, dict):
        return {key: "**REDACTED**" if key in _REDACT_KEYS else _redact(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_redact(item) for item in data]
    return data


class KonnectClient:
    """Authenticate with AWS Cognito and access the Konnect API."""

    email: str
    username: str | None = None
    password: str

    token: str | None = None
    token_type: str | None = None
    token_expires_in: int | None = None
    token_expiry_time: float | None = None  # New field to track token expiration time
    refresh_token_value: str | None = None
    device_key: str | None = None

    def __init__(self, email, password) -> None:
        """Initialize the client."""
        self.email = email
        self.password = password
        self.token = None
        self.token_type = None
        self.token_expires_in = None
        self.token_expiry_time = None
        self.refresh_token_value = None  # Keeping property for compatibility with storage
        self.device_key = None

    async def authenticate_user(self):
        """Authenticate with AWS Cognito using SRP."""
        # Before we can sign in, we need to determine the username. This is done
        # by making a request that for a given email, it will return the username
        # (if it exists.)
        self.username = await self.__fetchUsername()

        try:
            # Run the AWS SRP authentication in an executor
            # to avoid blocking the event loop
            aws_response, device_key = await asyncio.get_event_loop().run_in_executor(
                None, self.__authenticate_with_aws_srp
            )

            aws_result = aws_response["AuthenticationResult"]
            self.token = aws_result["IdToken"]
            self.token_type = aws_result["TokenType"]
            self.token_expires_in = aws_result["ExpiresIn"]
            # Calculate absolute expiry time (subtract 90 seconds for safety margin)
            self.token_expiry_time = time.time() + aws_result["ExpiresIn"] - 90
            self.refresh_token_value = aws_result["RefreshToken"]
            self.device_key = device_key

            if device_key:
                _LOGGER.debug(
                    "Authentication successful, device key captured (...%s); "
                    "refresh will use REFRESH_TOKEN_AUTH; "
                    "token expires in %s seconds (effective expiry %s)",
                    device_key[-4:] if len(device_key) >= 4 else device_key,
                    aws_result["ExpiresIn"],
                    self._expiry_str(),
                )
            else:
                _LOGGER.warning(
                    "Authentication succeeded but NO device key was captured; "
                    "token refresh will fall back to full SRP re-auth every cycle; "
                    "token expires in %s seconds (effective expiry %s)",
                    aws_result["ExpiresIn"],
                    self._expiry_str(),
                )

        except Exception as e:
            _LOGGER.error("Authentication failed: %s", str(e))
            raise AndersenAuthError(f"Failed to sign in: {e!s}") from e

    def __authenticate_with_aws_srp(self):
        # This is executed in the executor pool
        aws_srp = AWSSRP(
            username=self.username,
            password=self.password,
            pool_id=POOL_ID,
            pool_region=POOL_REGION,
            client_id=CLIENT_ID,
        )
        tokens = aws_srp.authenticate_user()

        device_key = None
        ndm = tokens["AuthenticationResult"].get("NewDeviceMetadata")
        if ndm:
            device_key = ndm.get("DeviceKey")
            try:
                aws_srp.confirm_device(tokens)
                _LOGGER.debug(
                    "Device confirmed with Cognito (device tracking active), key ...%s",
                    device_key[-4:] if device_key and len(device_key) >= 4 else device_key,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Device confirmation failed (refresh may fall back to full auth): %s",
                    err,
                )

        return tokens, device_key

    async def refresh_token(self):
        """Renew the id_token via REFRESH_TOKEN_AUTH (with DEVICE_KEY).

        Falls back to a full SRP login only if the refresh token is dead.
        """
        if not self.refresh_token_value or not self.device_key:
            _LOGGER.debug("No refresh token / device key; performing full authentication")
            await self.authenticate_user()
            return
        _LOGGER.debug(
            "Refreshing token via REFRESH_TOKEN_AUTH, device key ...%s",
            self.device_key[-4:] if len(self.device_key) >= 4 else self.device_key,
        )
        try:
            result = await asyncio.get_event_loop().run_in_executor(None, self.__refresh_with_device_key)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code == "NotAuthorizedException":
                _LOGGER.info("Refresh token expired/revoked; performing full re-authentication")
                await self.authenticate_user()
                return
            raise
        self.token = result["IdToken"]
        self.token_type = result["TokenType"]
        self.token_expires_in = result["ExpiresIn"]
        self.token_expiry_time = time.time() + result["ExpiresIn"] - 90
        # REFRESH_TOKEN_AUTH does not rotate the refresh token; keep the stored one
        # unless the response unexpectedly includes a new one.
        if "RefreshToken" in result:
            self.refresh_token_value = result["RefreshToken"]
        _LOGGER.debug(
            "Token refreshed via REFRESH_TOKEN_AUTH, expires in %s seconds (effective expiry %s)",
            result["ExpiresIn"],
            self._expiry_str(),
        )

    def __refresh_with_device_key(self):
        """Blocking REFRESH_TOKEN_AUTH including the DEVICE_KEY the token is bound to."""
        client = boto3.client("cognito-idp", region_name=POOL_REGION)
        response = client.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={
                "REFRESH_TOKEN": self.refresh_token_value,
                "DEVICE_KEY": self.device_key,
            },
        )
        return response["AuthenticationResult"]

    def _expiry_str(self):
        """Local-time string for the effective token expiry (includes the 90s safety margin)."""
        if not self.token_expiry_time:
            return "unknown"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.token_expiry_time))

    async def is_token_valid(self):
        """Check if the current token is still valid."""
        if not self.token or not self.token_expiry_time:
            return False
        return time.time() < self.token_expiry_time

    async def getDevices(self):
        """Get list of devices from the API."""
        await self.ensure_valid_auth()
        devices = []

        url = const.API_DEVICES_URL
        _LOGGER.debug("Calling REST GET %s", url)

        # Run blocking requests call in an executor to avoid blocking the event loop
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: requests.get(
                url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30,
            ),
        )

        if response.status_code != 200:
            if response.status_code == 401:
                # Token expired during request, refresh and retry
                _LOGGER.debug("Token expired during REST getDevices request, refreshing")
                await self.refresh_token()
                return await self.getDevices()

            _LOGGER.error(
                "REST getDevices failed. Status Code: %s, Response: %s",
                response.status_code,
                response.text,
            )
            raise AndersenConnectionError(f"API returned status {response.status_code}")

        response_body = response.json()

        if "devices" not in response_body:
            raise AndersenApiError("Unexpected API response shape: missing 'devices' key")

        if not response_body["devices"]:
            _LOGGER.warning("No devices found in API response")
            return devices

        # Debug log number of devices found
        _LOGGER.debug("REST getDevices returned %s devices", len(response_body["devices"]))

        for device in response_body["devices"]:
            _LOGGER.debug("REST getDevices device details: %s", _redact(device))
            # Use "Andersen" as default friendly name if not set or empty
            friendly_name = device.get("friendlyName") or "Andersen"
            devices.append(
                KonnectDevice(
                    api=self,
                    device_id=device["id"],
                    friendly_name=friendly_name,
                    user_lock=device.get("userLock", False),
                )
            )

        return devices

    async def getDevice(self, device_id: str) -> dict:
        """Get a device's REST userDevice object (device, info and status)."""
        await self.ensure_valid_auth()
        response = await self.__get_device_response(device_id)
        if response.status_code == 401:
            _LOGGER.debug("Token expired during REST getDevice request, refreshing")
            await self.refresh_token()
            response = await self.__get_device_response(device_id)

        if response.status_code != 200:
            _LOGGER.error(
                "REST getDevice failed for device %s. Status Code: %s, Response: %s",
                device_id,
                response.status_code,
                response.text,
            )
            raise AndersenConnectionError(f"API returned status {response.status_code}")

        try:
            body = response.json()
        except ValueError as err:
            raise AndersenApiError("Unexpected API response: getDevice body is not JSON") from err
        _LOGGER.debug("REST getDevice response for device %s: %s", device_id, _redact(body))
        user_device = body.get("userDevice") if isinstance(body, dict) else None
        if not isinstance(user_device, dict) or not isinstance(user_device.get("status"), dict):
            raise AndersenApiError("Unexpected API response shape: missing 'userDevice.status'")
        return user_device

    async def __get_device_response(self, device_id: str) -> requests.Response:
        _LOGGER.debug("Calling REST GET %s?deviceId=%s", const.API_DEVICE_URL, device_id)
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: requests.get(
                    const.API_DEVICE_URL,
                    params={"deviceId": device_id},
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=30,
                ),
            )
        except requests.RequestException as err:
            raise AndersenConnectionError(f"getDevice request failed: {err}") from err

    async def __fetchUsername(self):
        url = const.GRAPHQL_USER_MAP_URL
        body = {"email": self.email}

        # Run blocking requests call in an executor to avoid blocking the event loop
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: requests.post(url, json=body, timeout=30)
        )

        if response.status_code != 200:
            raise AndersenAuthError("Incorrect email address")

        # {'error': 'Pending user with email "x" not found'}
        # {'username': 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:x'}
        response_body = response.json()
        if "username" not in response_body:
            raise AndersenAuthError("Incorrect email address")

        return response_body["username"]

    async def ensure_valid_auth(self):
        """Ensure we have a valid authentication token."""
        if not await self.is_token_valid():
            _LOGGER.debug("Token invalid or expired, refreshing")
            await self.refresh_token()
        else:
            _LOGGER.debug(
                "Token still valid, expiry in %s seconds (effective expiry %s)",
                int(self.token_expiry_time - time.time()) if self.token_expiry_time else "unknown",
                self._expiry_str(),
            )
