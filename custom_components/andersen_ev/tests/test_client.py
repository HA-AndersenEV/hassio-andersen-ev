"""Tests for KonnectClient Cognito auth and device-key-aware token refresh."""
# pylint: disable=protected-access

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from andersen_ev.konnect.client import KonnectClient
from andersen_ev.konnect.exceptions import AndersenApiError, AndersenAuthError, AndersenConnectionError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_srp_auth_response(device_key=None):
    """Return a minimal AWS SRP authentication response dict."""
    result = {
        "AuthenticationResult": {
            "IdToken": "test-id-token",
            "TokenType": "Bearer",
            "ExpiresIn": 3600,
            "RefreshToken": "test-refresh-token",
        }
    }
    if device_key is not None:
        result["AuthenticationResult"]["NewDeviceMetadata"] = {"DeviceKey": device_key}
    return result


def _make_cognito_refresh_response():
    """Return a minimal Cognito REFRESH_TOKEN_AUTH AuthenticationResult (no RefreshToken)."""
    return {
        "AuthenticationResult": {
            "IdToken": "refreshed-id-token",
            "TokenType": "Bearer",
            "ExpiresIn": 3600,
        }
    }


# ---------------------------------------------------------------------------
# authenticate_user() — device key capture & device confirmation
# ---------------------------------------------------------------------------


class TestAuthenticate:
    """Tests for authenticate_user() device key capture and device confirmation."""

    @pytest.mark.asyncio
    async def test_authenticate_captures_device_key_and_confirms_device(self):
        """authenticate_user() stores device_key and calls confirm_device on NewDeviceMetadata."""
        client = KonnectClient("user@example.com", "password")
        auth_response = _make_srp_auth_response(device_key="dk-1")

        mock_aws_srp = MagicMock()
        mock_aws_srp.authenticate_user.return_value = auth_response

        with patch("andersen_ev.konnect.client.AWSSRP", return_value=mock_aws_srp):
            with patch.object(
                client,
                "_KonnectClient__fetchUsername",
                new=AsyncMock(return_value="test-username"),
            ):
                await client.authenticate_user()

        assert client.device_key == "dk-1"
        assert client.token == "test-id-token"
        assert client.refresh_token_value == "test-refresh-token"
        mock_aws_srp.confirm_device.assert_called_once_with(auth_response)

    @pytest.mark.asyncio
    async def test_authenticate_no_device_metadata_leaves_device_key_none(self):
        """authenticate_user() leaves device_key as None when no NewDeviceMetadata present."""
        client = KonnectClient("user@example.com", "password")
        auth_response = _make_srp_auth_response()  # no NewDeviceMetadata

        mock_aws_srp = MagicMock()
        mock_aws_srp.authenticate_user.return_value = auth_response

        with patch("andersen_ev.konnect.client.AWSSRP", return_value=mock_aws_srp):
            with patch.object(
                client,
                "_KonnectClient__fetchUsername",
                new=AsyncMock(return_value="test-username"),
            ):
                await client.authenticate_user()

        assert client.device_key is None
        assert client.token == "test-id-token"
        mock_aws_srp.confirm_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_authenticate_continues_when_confirm_device_fails(self):
        """authenticate_user() succeeds and logs a warning if confirm_device raises."""
        client = KonnectClient("user@example.com", "password")
        auth_response = _make_srp_auth_response(device_key="dk-warn")

        mock_aws_srp = MagicMock()
        mock_aws_srp.authenticate_user.return_value = auth_response
        mock_aws_srp.confirm_device.side_effect = Exception("confirm failed")

        with patch("andersen_ev.konnect.client.AWSSRP", return_value=mock_aws_srp):
            with patch.object(
                client,
                "_KonnectClient__fetchUsername",
                new=AsyncMock(return_value="test-username"),
            ):
                # Should NOT raise despite confirm_device failure
                await client.authenticate_user()

        # The token should still be set; device_key captured from NewDeviceMetadata
        assert client.token == "test-id-token"
        assert client.device_key == "dk-warn"


# ---------------------------------------------------------------------------
# refresh_token() — device-key-aware path and fallbacks
# ---------------------------------------------------------------------------


class TestRefreshToken:
    """Tests for refresh_token() device-key-aware refresh and fallback behaviour."""

    @pytest.mark.asyncio
    async def test_refresh_uses_device_key(self):
        """refresh_token() calls REFRESH_TOKEN_AUTH with DEVICE_KEY and updates token fields."""
        client = KonnectClient("user@example.com", "password")
        client.refresh_token_value = "rt"
        client.device_key = "dk"

        mock_cognito = MagicMock()
        mock_cognito.initiate_auth.return_value = _make_cognito_refresh_response()

        with patch("andersen_ev.konnect.client.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_cognito
            await client.refresh_token()

        # Verify the Cognito client was created for the correct region
        mock_boto3.client.assert_called_once_with("cognito-idp", region_name="eu-west-1")

        # Verify initiate_auth was called with the expected parameters
        mock_cognito.initiate_auth.assert_called_once_with(
            ClientId="23s0olnnniu5472ons0d9uoqt9",
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": "rt", "DEVICE_KEY": "dk"},
        )

        # Verify token fields were updated
        assert client.token == "refreshed-id-token"
        assert client.token_type == "Bearer"
        assert client.token_expires_in == 3600

    @pytest.mark.asyncio
    async def test_dead_refresh_token_falls_back_to_full_auth(self):
        """refresh_token() falls back to authenticate_user() on NotAuthorizedException."""
        client = KonnectClient("user@example.com", "password")
        client.refresh_token_value = "expired-rt"
        client.device_key = "dk"

        not_authorized = ClientError(
            {"Error": {"Code": "NotAuthorizedException", "Message": "Invalid Refresh Token"}},
            "InitiateAuth",
        )

        mock_cognito = MagicMock()
        mock_cognito.initiate_auth.side_effect = not_authorized

        with patch("andersen_ev.konnect.client.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_cognito
            with patch.object(client, "authenticate_user", new_callable=AsyncMock) as mock_full_auth:
                await client.refresh_token()

        mock_full_auth.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_token_or_device_key_falls_back_to_full_auth(self):
        """refresh_token() falls back immediately when refresh_token_value or device_key is absent."""
        client = KonnectClient("user@example.com", "password")
        # Both are None by default — guard should trigger before any Cognito call

        with patch.object(client, "authenticate_user", new_callable=AsyncMock) as mock_full_auth:
            await client.refresh_token()

        mock_full_auth.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_only_device_key_missing_falls_back_to_full_auth(self):
        """refresh_token() falls back when refresh_token_value is set but device_key is None."""
        client = KonnectClient("user@example.com", "password")
        client.refresh_token_value = "rt"
        # device_key remains None

        with patch.object(client, "authenticate_user", new_callable=AsyncMock) as mock_full_auth:
            await client.refresh_token()

        mock_full_auth.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_token_not_rotated_when_absent_from_response(self):
        """refresh_token() keeps the existing refresh_token_value when Cognito does not return a new one."""
        client = KonnectClient("user@example.com", "password")
        client.refresh_token_value = "original-rt"
        client.device_key = "dk"

        mock_cognito = MagicMock()
        mock_cognito.initiate_auth.return_value = _make_cognito_refresh_response()

        with patch("andersen_ev.konnect.client.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_cognito
            await client.refresh_token()

        # The stored refresh token must NOT have been overwritten
        assert client.refresh_token_value == "original-rt"

    @pytest.mark.asyncio
    async def test_refresh_updates_refresh_token_if_rotated(self):
        """refresh_token() replaces refresh_token_value when Cognito unexpectedly rotates it."""
        client = KonnectClient("user@example.com", "password")
        client.refresh_token_value = "old-rt"
        client.device_key = "dk"

        rotated_response = {
            "AuthenticationResult": {
                "IdToken": "new-id-token",
                "TokenType": "Bearer",
                "ExpiresIn": 3600,
                "RefreshToken": "rotated-rt",
            }
        }

        mock_cognito = MagicMock()
        mock_cognito.initiate_auth.return_value = rotated_response

        with patch("andersen_ev.konnect.client.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_cognito
            await client.refresh_token()

        assert client.refresh_token_value == "rotated-rt"

    @pytest.mark.asyncio
    async def test_non_auth_client_error_is_reraised(self):
        """refresh_token() re-raises ClientError codes other than NotAuthorizedException."""
        client = KonnectClient("user@example.com", "password")
        client.refresh_token_value = "rt"
        client.device_key = "dk"

        service_error = ClientError(
            {"Error": {"Code": "TooManyRequestsException", "Message": "slow down"}},
            "InitiateAuth",
        )

        mock_cognito = MagicMock()
        mock_cognito.initiate_auth.side_effect = service_error

        with patch("andersen_ev.konnect.client.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_cognito
            with pytest.raises(ClientError):
                await client.refresh_token()

    @pytest.mark.asyncio
    async def test_authenticate_user_wraps_exception_in_andersen_auth_error(self):
        """authenticate_user() wraps any auth failure in AndersenAuthError."""
        client = KonnectClient("user@example.com", "password")

        with patch("andersen_ev.konnect.client.AWSSRP", side_effect=Exception("bad creds")):
            with patch.object(
                client,
                "_KonnectClient__fetchUsername",
                new=AsyncMock(return_value="test-username"),
            ):
                with pytest.raises(AndersenAuthError):
                    await client.authenticate_user()


# ---------------------------------------------------------------------------
# _expiry_str()
# ---------------------------------------------------------------------------


class TestExpiryStr:
    """Tests for the _expiry_str() helper."""

    def test_unknown_when_expiry_not_set(self):
        """_expiry_str() returns 'unknown' when token_expiry_time is not set."""
        client = KonnectClient("user@example.com", "password")
        assert client._expiry_str() == "unknown"

    def test_formats_local_time_when_expiry_set(self):
        """_expiry_str() formats token_expiry_time as a local time string."""
        client = KonnectClient("user@example.com", "password")
        client.token_expiry_time = time.time() + 100

        expected = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(client.token_expiry_time))
        assert client._expiry_str() == expected


# ---------------------------------------------------------------------------
# is_token_valid()
# ---------------------------------------------------------------------------


class TestIsTokenValid:
    """Tests for is_token_valid()."""

    @pytest.mark.asyncio
    async def test_no_token_is_invalid(self):
        """is_token_valid() is False when there is no token."""
        client = KonnectClient("user@example.com", "password")
        client.token = None
        client.token_expiry_time = time.time() + 100

        assert await client.is_token_valid() is False

    @pytest.mark.asyncio
    async def test_no_expiry_time_is_invalid(self):
        """is_token_valid() is False when token_expiry_time is not set."""
        client = KonnectClient("user@example.com", "password")
        client.token = "tok"
        client.token_expiry_time = None

        assert await client.is_token_valid() is False

    @pytest.mark.asyncio
    async def test_expired_token_is_invalid(self):
        """is_token_valid() is False once token_expiry_time has passed."""
        client = KonnectClient("user@example.com", "password")
        client.token = "tok"
        client.token_expiry_time = time.time() - 10

        assert await client.is_token_valid() is False

    @pytest.mark.asyncio
    async def test_valid_token_is_valid(self):
        """is_token_valid() is True while token_expiry_time is in the future."""
        client = KonnectClient("user@example.com", "password")
        client.token = "tok"
        client.token_expiry_time = time.time() + 1000

        assert await client.is_token_valid() is True


# ---------------------------------------------------------------------------
# ensure_valid_auth()
# ---------------------------------------------------------------------------


class TestEnsureValidAuth:
    """Tests for ensure_valid_auth()."""

    @pytest.mark.asyncio
    async def test_refreshes_when_token_invalid(self):
        """ensure_valid_auth() refreshes the token when it is not valid."""
        client = KonnectClient("user@example.com", "password")
        client.is_token_valid = AsyncMock(return_value=False)
        client.refresh_token = AsyncMock()

        await client.ensure_valid_auth()

        client.refresh_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_refresh_when_token_valid(self):
        """ensure_valid_auth() does not refresh when the token is already valid."""
        client = KonnectClient("user@example.com", "password")
        client.is_token_valid = AsyncMock(return_value=True)
        client.refresh_token = AsyncMock()
        client.token_expiry_time = time.time() + 1000

        await client.ensure_valid_auth()

        client.refresh_token.assert_not_awaited()


# ---------------------------------------------------------------------------
# getDevices()
# ---------------------------------------------------------------------------


class TestGetDevices:
    """Tests for getDevices()."""

    @pytest.mark.asyncio
    async def test_get_devices_success(self):
        """getDevices() returns KonnectDevice objects built from the response body."""
        client = KonnectClient("user@example.com", "password")
        client.ensure_valid_auth = AsyncMock()
        client.token = "abc"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "devices": [
                {"id": "dev1", "friendlyName": "My Charger", "userLock": True},
                {"id": "dev2", "friendlyName": "", "userLock": False},
            ]
        }

        with patch("andersen_ev.konnect.client.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            devices = await client.getDevices()

        assert len(devices) == 2
        assert devices[0].device_id == "dev1"
        assert devices[0].friendly_name == "My Charger"
        assert devices[0].user_lock is True
        # Empty friendlyName falls back to the default "Andersen" name.
        assert devices[1].friendly_name == "Andersen"
        client.ensure_valid_auth.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_devices_empty_list(self):
        """getDevices() returns an empty list when the API reports no devices."""
        client = KonnectClient("user@example.com", "password")
        client.ensure_valid_auth = AsyncMock()
        client.token = "abc"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"devices": []}

        with patch("andersen_ev.konnect.client.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            devices = await client.getDevices()

        assert devices == []

    @pytest.mark.asyncio
    async def test_get_devices_missing_devices_key_raises_api_error(self):
        """getDevices() raises AndersenApiError when the response body has no 'devices' key at all."""
        client = KonnectClient("user@example.com", "password")
        client.ensure_valid_auth = AsyncMock()
        client.token = "abc"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"unexpected": "shape"}

        with patch("andersen_ev.konnect.client.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            with pytest.raises(AndersenApiError):
                await client.getDevices()

    @pytest.mark.asyncio
    async def test_get_devices_retries_after_401(self):
        """getDevices() refreshes the token and retries once on a 401."""
        client = KonnectClient("user@example.com", "password")
        client.ensure_valid_auth = AsyncMock()
        client.refresh_token = AsyncMock()
        client.token = "abc"

        unauthorized_response = MagicMock()
        unauthorized_response.status_code = 401
        unauthorized_response.text = "Unauthorized"

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {"devices": []}

        with patch("andersen_ev.konnect.client.requests") as mock_requests:
            mock_requests.get.side_effect = [unauthorized_response, ok_response]
            devices = await client.getDevices()

        assert devices == []
        client.refresh_token.assert_awaited_once()
        assert mock_requests.get.call_count == 2

    @pytest.mark.asyncio
    async def test_get_devices_non_200_raises_connection_error(self):
        """getDevices() raises AndersenConnectionError on an unexpected status code."""
        client = KonnectClient("user@example.com", "password")
        client.ensure_valid_auth = AsyncMock()
        client.token = "abc"

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "Server error"

        with patch("andersen_ev.konnect.client.requests") as mock_requests:
            mock_requests.get.return_value = error_response
            with pytest.raises(AndersenConnectionError):
                await client.getDevices()


# ---------------------------------------------------------------------------
# __fetchUsername()
# ---------------------------------------------------------------------------


class TestFetchUsername:
    """Tests for the private __fetchUsername() helper."""

    @pytest.mark.asyncio
    async def test_non_200_raises_auth_error(self):
        """A non-200 response raises AndersenAuthError."""
        client = KonnectClient("user@example.com", "password")

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("andersen_ev.konnect.client.requests") as mock_requests:
            mock_requests.post.return_value = mock_response
            with pytest.raises(AndersenAuthError):
                await client._KonnectClient__fetchUsername()

    @pytest.mark.asyncio
    async def test_missing_username_raises_auth_error(self):
        """A 200 response without a 'username' key raises AndersenAuthError."""
        client = KonnectClient("user@example.com", "password")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": 'Pending user with email "x" not found'}

        with patch("andersen_ev.konnect.client.requests") as mock_requests:
            mock_requests.post.return_value = mock_response
            with pytest.raises(AndersenAuthError):
                await client._KonnectClient__fetchUsername()

    @pytest.mark.asyncio
    async def test_success_returns_username(self):
        """A 200 response with a 'username' key returns that username."""
        client = KonnectClient("user@example.com", "password")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"username": "abc-123:1"}

        with patch("andersen_ev.konnect.client.requests") as mock_requests:
            mock_requests.post.return_value = mock_response
            username = await client._KonnectClient__fetchUsername()

        assert username == "abc-123:1"
