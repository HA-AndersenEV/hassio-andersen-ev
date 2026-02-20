"""Tests for GraphQL client (persistent session with token refresh)."""
# pylint: disable=protected-access

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gql.transport.exceptions import TransportQueryError, TransportServerError

from andersen_ev.konnect.graphql_client import GraphQLClient


class TestGraphQLClient:
    """Test GraphQL client functionality."""

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _make_mock_client(execute_return=None, execute_side_effect=None):
        """Create a mock gql Client with controllable execute behavior.

        Returns ``(mock_client_instance, mock_session)``.
        """
        mock_session = AsyncMock()
        if execute_side_effect is not None:
            mock_session.execute.side_effect = execute_side_effect
        else:
            mock_session.execute.return_value = execute_return

        mock_client = MagicMock()
        mock_client.connect_async = AsyncMock(return_value=mock_session)
        mock_client.close_async = AsyncMock()

        return mock_client, mock_session

    @staticmethod
    def _dummy_refresh():
        """Return a basic async refresh callback."""

        async def _refresh():
            return "refreshed_token", None

        return _refresh

    # -- basic query tests -------------------------------------------------

    @pytest.mark.asyncio
    async def test_execute_query_success(self):
        """Test successful query execution through persistent session."""
        data = {"getDevice": {"id": "123", "name": "Test"}}
        mock_client, mock_session = self._make_mock_client(execute_return=data)

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_query(
                operation_name="getDevice",
                query="query getDevice($id: ID!) { getDevice(id: $id) { id name } }",
                variables={"id": "123"},
            )
            await client.close()

        assert result is not None
        assert result["getDevice"]["id"] == "123"
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_query_with_no_variables(self):
        """Test query execution without variables passes None."""
        data = {"devices": []}
        mock_client, mock_session = self._make_mock_client(execute_return=data)

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_query(
                operation_name="listDevices",
                query="query listDevices { devices { id } }",
            )
            await client.close()

        assert result is not None
        call_kwargs = mock_session.execute.call_args[1]
        assert call_kwargs.get("variable_values") is None

    # -- persistent session tests ------------------------------------------

    @pytest.mark.asyncio
    async def test_persistent_session_reuse(self):
        """Test that multiple queries reuse the same session connection."""
        data = {"test": "value"}
        mock_client, mock_session = self._make_mock_client(execute_return=data)

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ) as mock_client_cls:
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            await client.execute_query("op1", "query { test1 }")
            await client.execute_query("op2", "query { test2 }")
            await client.close()

        # Client created and connected only once
        assert mock_client_cls.call_count == 1
        assert mock_client.connect_async.call_count == 1
        # But execute called twice through the same session
        assert mock_session.execute.call_count == 2

    # -- 401 auth refresh tests --------------------------------------------

    @pytest.mark.asyncio
    async def test_401_auto_refresh_and_retry(self):
        """Test 401 triggers token refresh, reconnect, and successful retry."""
        success_data = {"getDevice": {"id": "123"}}
        mock_client, mock_session = self._make_mock_client(
            execute_side_effect=[
                TransportServerError("Unauthorized", code=401),
                success_data,
            ]
        )

        refresh_called = False

        async def mock_refresh():
            nonlocal refresh_called
            refresh_called = True
            return "new_token", None

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="old_token",
                token_refresh=mock_refresh,
            )
            result = await client.execute_query(
                operation_name="getDevice",
                query="query { getDevice { id } }",
            )
            await client.close()

        assert result == success_data
        assert refresh_called
        assert client.token == "new_token"
        # 2 execute calls: first fails with 401, second succeeds after refresh
        assert mock_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_401_retry_also_fails(self):
        """Test that a second failure after refresh returns None."""
        mock_client, mock_session = self._make_mock_client(
            execute_side_effect=[
                TransportServerError("Unauthorized", code=401),
                TransportServerError("Still unauthorized", code=401),
            ]
        )

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="bad_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_query(
                operation_name="test",
                query="query { test }",
            )
            await client.close()

        assert result is None
        assert mock_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_token_updated_after_refresh(self):
        """Test that token value is updated after a refresh cycle."""
        expiry = time.time() + 3600
        mock_client, _ = self._make_mock_client(
            execute_side_effect=[
                TransportServerError("Unauthorized", code=401),
                {"ok": True},
            ]
        )

        async def refresh_with_expiry():
            return "fresh_token", expiry

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="stale_token",
                token_refresh=refresh_with_expiry,
            )
            await client.execute_query("test", "query { test }")
            await client.close()

        assert client.token == "fresh_token"

    # -- error handling tests ----------------------------------------------

    @pytest.mark.asyncio
    async def test_server_error_non_401(self):
        """Test non-401 server errors return None."""
        mock_client, _ = self._make_mock_client(
            execute_side_effect=TransportServerError("Server error", code=500)
        )

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_query("test", "query { test }")
            await client.close()

        assert result is None

    @pytest.mark.asyncio
    async def test_graphql_query_error(self):
        """Test GraphQL query errors return None."""
        mock_client, _ = self._make_mock_client(
            execute_side_effect=TransportQueryError(
                "Invalid query",
                errors=[{"message": "Invalid query"}],
            )
        )

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_query("test", "query { invalid }")
            await client.close()

        assert result is None

    @pytest.mark.asyncio
    async def test_network_exception(self):
        """Test network exceptions return None."""
        mock_client, _ = self._make_mock_client(
            execute_side_effect=ConnectionError("Network error")
        )

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_query("test", "query { test }")
            await client.close()

        assert result is None

    # -- mutation tests ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_execute_mutation_success(self):
        """Test successful mutation delegates to execute_query."""
        data = {"runCommand": {"success": True}}
        mock_client, _mock_session = self._make_mock_client(execute_return=data)

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_mutation(
                operation_name="runCommand",
                mutation=(
                    "mutation runCommand($id: ID!) { runCommand(id: $id) { success } }"
                ),
                variables={"id": "123"},
            )
            await client.close()

        assert result is not None
        assert result["runCommand"]["success"] is True

    # -- auth / transport tests --------------------------------------------

    @pytest.mark.asyncio
    async def test_bearer_auth_in_transport(self):
        """Test that Bearer token is set in AIOHTTPTransport headers."""
        mock_client, _ = self._make_mock_client(execute_return={})

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ) as mock_client_cls:
            client = GraphQLClient(
                token="my_token",
                token_refresh=self._dummy_refresh(),
            )
            await client.execute_query("test", "query { test }")

            # Inspect the transport passed to Client(...)
            call_kwargs = mock_client_cls.call_args[1]
            transport = call_kwargs["transport"]
            assert transport.headers["Authorization"] == "Bearer my_token"

            await client.close()

    @pytest.mark.asyncio
    async def test_custom_url(self):
        """Test client with custom GraphQL URL."""
        custom_url = "https://custom.graphql.endpoint/graphql"
        mock_client, _ = self._make_mock_client(execute_return={})

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ) as mock_client_cls:
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
                url=custom_url,
            )
            await client.execute_query("test", "query { test }")

            call_kwargs = mock_client_cls.call_args[1]
            transport = call_kwargs["transport"]
            assert str(transport.url) == custom_url

            await client.close()

    # -- proactive token refresh timer tests --------------------------------

    @pytest.mark.asyncio
    async def test_proactive_refresh_scheduled_on_connect(self):
        """Test that a refresh timer is scheduled on first connect."""
        mock_client, _ = self._make_mock_client(execute_return={})
        expiry = time.time() + 600  # 10 minutes from now

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
                token_expiry_time=expiry,
            )
            # Timer not scheduled until first connection
            assert client._refresh_handle is None

            await client.execute_query("test", "query { test }")

            # After first execute, timer should be scheduled
            assert client._refresh_handle is not None

            await client.close()

    @pytest.mark.asyncio
    async def test_no_timer_without_expiry(self):
        """Test that no refresh timer is scheduled when expiry is not set."""
        mock_client, _ = self._make_mock_client(execute_return={})

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            await client.execute_query("test", "query { test }")

            assert client._refresh_handle is None

            await client.close()

    @pytest.mark.asyncio
    async def test_close_cancels_timer(self):
        """Test that close() cancels any pending refresh timer."""
        mock_client, _ = self._make_mock_client(execute_return={})
        expiry = time.time() + 600

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
                token_expiry_time=expiry,
            )
            await client.execute_query("test", "query { test }")
            assert client._refresh_handle is not None

            await client.close()
            assert client._refresh_handle is None

    @pytest.mark.asyncio
    async def test_close_closes_client(self):
        """Test that close() properly closes the gql client."""
        mock_client, _ = self._make_mock_client(execute_return={})

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            await client.execute_query("test", "query { test }")
            await client.close()

        mock_client.close_async.assert_called_once()
        assert client._session is None
        assert client._client is None
