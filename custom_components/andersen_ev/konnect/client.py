import asyncio
import json
import time
import logging
import requests
from . import const
from .device import KonnectDevice
from pycognito.aws_srp import AWSSRP

_LOGGER = logging.getLogger(__name__)


class KonnectClient:
    email = None
    username = None
    password = None

    token = None
    tokenType = None
    tokenExpiresIn = None
    tokenExpiryTime = None  # New field to track token expiration time
    refreshToken = None

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.token = None
        self.tokenType = None
        self.tokenExpiresIn = None
        self.tokenExpiryTime = None
        self.refreshToken = None  # Keeping property for compatibility with storage

    async def authenticate_user(self):
        """Authenticate with AWS Cognito using SRP."""
        # Before we can sign in, we need to determine the username. This is done
        # by making a request that for a given email, it will return the username
        # (if it exists.)
        self.username = await self.__fetchUsername()

        try:
            # Run the AWS SRP authentication in an executor to avoid blocking the event loop
            aws_response = await asyncio.get_event_loop().run_in_executor(
                None, self.__authenticate_with_aws_srp
            )

            aws_result = aws_response["AuthenticationResult"]
            self.token = aws_result["IdToken"]
            self.tokenType = aws_result["TokenType"]
            self.tokenExpiresIn = aws_result["ExpiresIn"]
            # Calculate absolute expiry time (subtract 5 minutes for safety margin)
            self.tokenExpiryTime = time.time() + aws_result["ExpiresIn"] - 90
            self.refreshToken = aws_result[
                "RefreshToken"
            ]  # Still store for future use if needed

            _LOGGER.debug(
                "Authentication successful, token will expire in %s seconds",
                aws_result["ExpiresIn"],
            )

        except Exception as e:
            _LOGGER.error("Authentication failed: %s", str(e))
            raise Exception(f"Failed to sign in: {str(e)}")

    def __authenticate_with_aws_srp(self):
        # This is executed in the executor pool
        aws_srp = AWSSRP(
            username=self.username,
            password=self.password,
            pool_id="eu-west-1_t5HV3bFjl",
            pool_region="eu-west-1",
            client_id="23s0olnnniu5472ons0d9uoqt9",
        )
        return aws_srp.authenticate_user()

    async def refresh_token(self):
        """Perform a full re-authentication instead of trying to use refresh tokens."""
        _LOGGER.debug("Performing full re-authentication instead of token refresh")
        await self.authenticate_user()

    async def is_token_valid(self):
        """Check if the current token is still valid."""
        if not self.token:
            return False

        if not self.tokenExpiryTime:
            return False

        # Check if token is expired
        return time.time() < self.tokenExpiryTime

    async def getDevices(self):
        """Get list of devices from the API."""
        await self.ensure_valid_auth()
        devices = []

        url = const.API_DEVICES_URL

        # Run blocking requests call in an executor to avoid blocking the event loop
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: requests.get(
                url, headers={"Authorization": f"Bearer {self.token}"}
            ),
        )

        if response.status_code != 200:
            if response.status_code == 401:
                # Token expired during request, refresh and retry
                _LOGGER.debug("Token expired during getDevices request, refreshing")
                await self.refresh_token()
                return await self.getDevices()

            _LOGGER.error(
                "Failed to get devices. Status Code: %s, Response: %s",
                response.status_code,
                response.text,
            )
            return devices

        response_body = response.json()

        if not response_body.get("devices"):
            _LOGGER.warning("No devices found in API response")
            return devices

        # Debug log number of devices found
        _LOGGER.debug("Found %s devices", len(response_body["devices"]))

        for device in response_body["devices"]:
            # Use "Andersen" as default friendly name if not set or empty
            friendly_name = device.get("friendlyName") or "Andersen"
            devices.append(
                KonnectDevice(
                    api=self,
                    device_id=device["id"],
                    friendly_name=friendly_name,
                    user_lock=device["userLock"],
                )
            )

        return devices

    async def __fetchUsername(self):
        url = const.GRAPHQL_USER_MAP_URL
        body = {"email": self.email}

        # Run blocking requests call in an executor to avoid blocking the event loop
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: requests.post(url, json=body)
        )

        if response.status_code != 200:
            raise Exception("Incorrect email address")

        # {'error': 'Pending user with email "x" not found'}
        # {'username': 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:x'}
        response_body = response.json()
        if "username" not in response_body:
            raise Exception("Incorrect email address")

        return response_body["username"]

    async def ensure_valid_auth(self):
        """Ensure we have a valid authentication token."""
        if not await self.is_token_valid():
            _LOGGER.debug("Token invalid or expired, refreshing")
            await self.refresh_token()
        else:
            _LOGGER.debug(
                "Token still valid, expiry in %s seconds",
                int(self.tokenExpiryTime - time.time())
                if self.tokenExpiryTime
                else "unknown",
            )
