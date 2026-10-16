"""Tests for GraphQL client (persistent session with token refresh)."""
# pylint: disable=protected-access

import asyncio
import logging
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
        sent_request = mock_session.execute.call_args[0][0]
        assert sent_request.variable_values is None

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
        mock_client, _ = self._make_mock_client(execute_side_effect=TransportServerError("Server error", code=500))

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
        mock_client, _ = self._make_mock_client(execute_side_effect=ConnectionError("Network error"))

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
                mutation=("mutation runCommand($id: ID!) { runCommand(id: $id) { success } }"),
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


class TestDSLSchema:
    """Test get_dsl_schema helper."""

    def test_returns_dsl_schema(self):
        """Test that get_dsl_schema returns a DSLSchema instance."""
        from gql.dsl import DSLSchema

        from andersen_ev.konnect.graphql_client import get_dsl_schema

        ds = get_dsl_schema()
        assert isinstance(ds, DSLSchema)

    def test_schema_has_solar_types(self):
        """Test that the schema includes solar query and mutation fields."""
        from andersen_ev.konnect.graphql_client import get_dsl_schema

        ds = get_dsl_schema()
        # Query.getSolar and Mutation.setSolar should exist
        assert hasattr(ds.Query, "getSolar")
        assert hasattr(ds.Mutation, "setSolar")
        # SolarSettings type should have the expected fields
        assert hasattr(ds.SolarSettings, "override")
        assert hasattr(ds.SolarSettings, "chargeAlways")
        assert hasattr(ds.SolarSettings, "maxGridChargePercent")

    def test_returns_cached_instance(self):
        """Test that get_dsl_schema returns the same cached instance."""
        from andersen_ev.konnect.graphql_client import get_dsl_schema

        ds1 = get_dsl_schema()
        ds2 = get_dsl_schema()
        assert ds1 is ds2


class TestExecuteDocument:
    """Test execute_document method."""

    @staticmethod
    def _make_mock_client(execute_return=None, execute_side_effect=None):
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
        async def _refresh():
            return "refreshed_token", None

        return _refresh

    @pytest.mark.asyncio
    async def test_execute_document_success(self):
        """Test successful execution of a DocumentNode."""
        from gql import gql as parse_gql

        data = {"getSolar": {"return_value": 1}}
        mock_client, mock_session = self._make_mock_client(execute_return=data)

        doc = parse_gql('query { getSolar(deviceId: "x") { return_value } }')

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_document(
                doc,
                operation_name="getSolar",
            )
            await client.close()

        assert result == data
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_document_401_retry(self):
        """Test 401 triggers token refresh and retry for execute_document."""
        from gql import gql as parse_gql

        success_data = {"setSolar": {"return_value": 1}}
        mock_client, mock_session = self._make_mock_client(
            execute_side_effect=[
                TransportServerError("Unauthorized", code=401),
                success_data,
            ]
        )

        doc = parse_gql('mutation { setSolar(deviceId: "x") { return_value } }')

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="old_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_document(doc)
            await client.close()

        assert result == success_data
        assert mock_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_document_unauthenticated_retry(self):
        """Test TransportQueryError with UNAUTHENTICATED code triggers refresh and retry."""
        from gql import gql as parse_gql

        success_data = {"getDeviceStatus": {"status": "ok"}}
        mock_client, mock_session = self._make_mock_client(
            execute_side_effect=[
                TransportQueryError(
                    "UNAUTHENTICATED",
                    errors=[{"extensions": {"code": "UNAUTHENTICATED"}}],
                ),
                success_data,
            ]
        )

        doc = parse_gql('query { getDeviceStatus(deviceId: "x") { status } }')

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="old_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_document(doc)
            await client.close()

        assert result == success_data
        assert mock_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_document_server_error(self):
        """Test non-401 server errors return None."""
        from gql import gql as parse_gql

        mock_client, _ = self._make_mock_client(
            execute_side_effect=TransportServerError("Server error", code=500),
        )
        doc = parse_gql('query { getSolar(deviceId: "x") { return_value } }')

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_document(
                doc,
                operation_name="getSolar",
            )
            await client.close()

        assert result is None

    @pytest.mark.asyncio
    async def test_execute_document_with_variables(self):
        """Test execute_document passes variable_values through."""
        from gql import gql as parse_gql

        data = {"getDevice": {"id": "123"}}
        mock_client, mock_session = self._make_mock_client(execute_return=data)
        doc = parse_gql("query getDevice($id: ID!) { getDevice(id: $id) { id } }")

        with patch(
            "andersen_ev.konnect.graphql_client.Client",
            return_value=mock_client,
        ):
            client = GraphQLClient(
                token="test_token",
                token_refresh=self._dummy_refresh(),
            )
            result = await client.execute_document(
                doc,
                variable_values={"id": "123"},
                operation_name="getDevice",
            )
            await client.close()

        assert result == data
        sent_request = mock_session.execute.call_args[0][0]
        assert sent_request.variable_values == {"id": "123"}
        assert sent_request.operation_name == "getDevice"


class TestSolarOperations:
    """Test set_solar convenience method."""

    @staticmethod
    def _make_mock_client(execute_return=None, execute_side_effect=None):
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
        async def _refresh():
            return "refreshed_token", None

        return _refresh

    @pytest.mark.asyncio
    async def test_set_solar_success(self):
        """Test set_solar returns True on success."""
        mock_client, _ = self._make_mock_client(
            execute_return={"setSolar": {"return_value": 1}},
        )

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=self._dummy_refresh())
            result = await client.set_solar("dev1", {"override": True, "chargeAlways": False})
            await client.close()

        assert result is True

    @pytest.mark.asyncio
    async def test_set_solar_failure(self):
        """Test set_solar returns False when execute_document fails."""
        mock_client, _ = self._make_mock_client(
            execute_side_effect=TransportServerError("err", code=500),
        )

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=self._dummy_refresh())
            result = await client.set_solar("dev1", {"override": True})
            await client.close()

        assert result is False


class TestGqlTransportFilter:
    """Tests for the _GqlTransportFilter log filter."""

    def test_allows_warning_and_above_regardless_of_integration_level(self):
        """WARNING+ records always pass, regardless of the integration's log level."""
        from andersen_ev.konnect.graphql_client import _GqlTransportFilter

        record = logging.LogRecord("gql.transport.aiohttp", logging.WARNING, "", 0, "msg", None, None)
        assert _GqlTransportFilter().filter(record) is True

    def test_blocks_info_when_integration_not_debug(self):
        """INFO records are dropped when the integration logger is above DEBUG."""
        from andersen_ev.konnect.graphql_client import _INTEGRATION_LOGGER, _GqlTransportFilter

        original_level = _INTEGRATION_LOGGER.level
        _INTEGRATION_LOGGER.setLevel(logging.INFO)
        try:
            record = logging.LogRecord("gql.transport.aiohttp", logging.INFO, "", 0, "msg", None, None)
            assert _GqlTransportFilter().filter(record) is False
        finally:
            _INTEGRATION_LOGGER.setLevel(original_level)

    def test_allows_info_when_integration_is_debug(self):
        """INFO records pass through once the integration logger is set to DEBUG."""
        from andersen_ev.konnect.graphql_client import _INTEGRATION_LOGGER, _GqlTransportFilter

        original_level = _INTEGRATION_LOGGER.level
        _INTEGRATION_LOGGER.setLevel(logging.DEBUG)
        try:
            record = logging.LogRecord("gql.transport.aiohttp", logging.INFO, "", 0, "msg", None, None)
            assert _GqlTransportFilter().filter(record) is True
        finally:
            _INTEGRATION_LOGGER.setLevel(original_level)


class TestConnectionManagement:
    """Tests for connection setup/teardown edge cases."""

    @staticmethod
    def _make_mock_client(execute_return=None, execute_side_effect=None):
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
        async def _refresh():
            return "refreshed_token", None

        return _refresh

    @pytest.mark.asyncio
    async def test_ensure_connected_concurrent_calls_connect_once(self):
        """Concurrent _ensure_connected() calls only create/connect the client once."""
        mock_client, mock_session = self._make_mock_client(execute_return={})

        async def slow_connect():
            await asyncio.sleep(0.01)
            return mock_session

        mock_client.connect_async = AsyncMock(side_effect=slow_connect)

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=self._dummy_refresh())
            await asyncio.gather(client._ensure_connected(), client._ensure_connected())
            await client.close()

        assert mock_client.connect_async.call_count == 1

    @pytest.mark.asyncio
    async def test_reconnect_with_token_swallows_close_error(self):
        """_reconnect_with_token() logs and continues if closing the old client errors."""
        mock_client, _ = self._make_mock_client(execute_return={})
        mock_client.close_async = AsyncMock(side_effect=OSError("close failed"))

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=self._dummy_refresh())
            await client.execute_query("test", "query { test }")

            await client._reconnect_with_token("new-token")

            assert client.token == "new-token"
            await client.close()

    @pytest.mark.asyncio
    async def test_close_cancels_pending_refresh_task(self):
        """close() cancels an in-flight proactive refresh task."""
        mock_client, _ = self._make_mock_client(execute_return={})

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=self._dummy_refresh())
            await client.execute_query("test", "query { test }")

            async def _never_finishes():
                await asyncio.sleep(10)

            client._refresh_task = asyncio.create_task(_never_finishes())

            await client.close()
            await asyncio.sleep(0)

        assert client._refresh_task is None

    @pytest.mark.asyncio
    async def test_close_swallows_client_close_error(self):
        """close() logs and continues if closing the gql client errors."""
        mock_client, _ = self._make_mock_client(execute_return={})
        mock_client.close_async = AsyncMock(side_effect=TransportServerError("err", code=500))

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=self._dummy_refresh())
            await client.execute_query("test", "query { test }")

            await client.close()  # should not raise

        assert client._client is None
        assert client._session is None


class TestHasUnauthenticatedError:
    """Tests for the _has_unauthenticated_error() static helper."""

    def test_non_dict_error_items_are_ignored(self):
        """Non-dict entries in errors are skipped without raising."""
        err = TransportQueryError("boom", errors=["not-a-dict", {"extensions": {"code": "OTHER"}}])
        assert GraphQLClient._has_unauthenticated_error(err) is False

    def test_dict_error_with_unauthenticated_code_returns_true(self):
        """A dict error with code UNAUTHENTICATED is detected."""
        err = TransportQueryError("boom", errors=[{"extensions": {"code": "UNAUTHENTICATED"}}])
        assert GraphQLClient._has_unauthenticated_error(err) is True

    def test_no_errors_returns_false(self):
        """A missing errors list is treated as no UNAUTHENTICATED error."""
        err = TransportQueryError("boom", errors=None)
        assert GraphQLClient._has_unauthenticated_error(err) is False


class TestProactiveRefreshScheduling:
    """Tests for the internals of proactive token refresh scheduling."""

    @staticmethod
    def _make_mock_client(execute_return=None, execute_side_effect=None):
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
        async def _refresh():
            return "refreshed_token", None

        return _refresh

    @pytest.mark.asyncio
    async def test_schedule_token_refresh_cancels_existing_handle(self):
        """Scheduling a new refresh cancels any previously scheduled handle."""
        mock_client, _ = self._make_mock_client(execute_return={})

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=self._dummy_refresh())
            await client.execute_query("test", "query { test }")

            client._schedule_token_refresh(time.time() + 600)
            first_handle = client._refresh_handle
            assert first_handle is not None

            client._schedule_token_refresh(time.time() + 900)

            assert client._refresh_handle is not first_handle
            assert first_handle.cancelled()

            await client.close()

    @pytest.mark.asyncio
    async def test_schedule_token_refresh_immediate_when_already_near_expiry(self):
        """A near/past expiry time still schedules an (immediate) refresh handle."""
        mock_client, _ = self._make_mock_client(execute_return={})

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=self._dummy_refresh())
            await client.execute_query("test", "query { test }")

            client._schedule_token_refresh(time.time() - 10)

            assert client._refresh_handle is not None
            await client.close()

    @pytest.mark.asyncio
    async def test_create_refresh_task_sets_and_clears_task(self):
        """_create_refresh_task() stores the task and clears it once it completes."""
        mock_client, _ = self._make_mock_client(execute_return={})

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=self._dummy_refresh())
            await client.execute_query("test", "query { test }")

            client._create_refresh_task()
            assert client._refresh_task is not None

            await client._refresh_task
            await asyncio.sleep(0)

            assert client._refresh_task is None
            await client.close()

    @pytest.mark.asyncio
    async def test_proactive_refresh_success(self):
        """_proactive_refresh() refreshes and reconnects with the new token."""
        mock_client, _ = self._make_mock_client(execute_return={})

        async def refresh():
            return "new_token", None

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=refresh)
            await client.execute_query("test", "query { test }")

            await client._proactive_refresh()

            assert client.token == "new_token"
            await client.close()

    @pytest.mark.asyncio
    async def test_proactive_refresh_failure_is_logged_not_raised(self):
        """_proactive_refresh() swallows and logs errors from the refresh callback."""
        mock_client, _ = self._make_mock_client(execute_return={})

        async def failing_refresh():
            raise OSError("network down")

        with patch("andersen_ev.konnect.graphql_client.Client", return_value=mock_client):
            client = GraphQLClient(token="tok", token_refresh=failing_refresh)
            await client.execute_query("test", "query { test }")

            await client._proactive_refresh()  # should not raise

            await client.close()
