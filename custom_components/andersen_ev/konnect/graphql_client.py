"""GraphQL client for Andersen EV API using gql library."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from gql import Client, GraphQLRequest, gql
from gql.client import AsyncClientSession
from gql.dsl import DSLMutation, DSLSchema, dsl_gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import (
    TransportQueryError,
    TransportServerError,
)
from graphql import build_schema

from . import const

_LOGGER = logging.getLogger(__name__)

# The integration's top-level logger whose effective level we check.
_INTEGRATION_LOGGER = logging.getLogger("custom_components.andersen_ev")

# Quiet the gql transport logger (INFO-level HTTP lifecycle messages) unless
# the integration itself is set to DEBUG, in which case let INFO through too.
_gql_transport_logger = logging.getLogger("gql.transport.aiohttp")
_gql_transport_logger.setLevel(logging.DEBUG)  # let the filter decide


class _GqlTransportFilter(logging.Filter):  # pylint: disable=too-few-public-methods
    """Allow gql transport INFO logs only when the integration is at DEBUG."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter log records based on integration log level."""
        if record.levelno >= logging.WARNING:
            return True
        # INFO (and DEBUG) from gql only when the integration is in debug mode
        return _INTEGRATION_LOGGER.isEnabledFor(logging.DEBUG)


_gql_transport_logger.addFilter(_GqlTransportFilter())

_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema.graphql"


@lru_cache(maxsize=1)
def get_dsl_schema() -> DSLSchema:
    """Load the Andersen EV GraphQL schema and return a DSLSchema instance.

    The result is cached so the schema file is only read and parsed once.
    """
    schema = build_schema(_SCHEMA_FILE.read_text())
    return DSLSchema(schema)


# Preload schema at module import time to avoid blocking I/O in async context
_PRELOADED_SCHEMA = get_dsl_schema()


class GraphQLClient:
    """Async GraphQL client for Andersen EV API using gql[aiohttp].

    Maintains a persistent session and handles token refresh automatically,
    both reactively (on HTTP 401) and proactively (via a scheduled timer
    that fires 5 minutes before the token expires).
    """

    def __init__(
        self,
        token: str,
        token_refresh: Callable[[], Awaitable[tuple[str, float | None]]],
        url: str = const.GRAPHQL_URL,
        token_expiry_time: float | None = None,
    ) -> None:
        """Initialize the GraphQL client."""
        self._token = token
        self.url = url
        self._token_refresh = token_refresh
        self._client: Client | None = None
        self._session: AsyncClientSession | None = None
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._initial_expiry_time = token_expiry_time
        self._refresh_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()

    @property
    def token(self) -> str:
        """Return the current bearer token."""
        return self._token

    # -- connection management ---------------------------------------------

    async def _ensure_connected(self) -> None:
        """Lazily create and connect the gql client session."""
        if self._session is not None:
            return

        async with self._connect_lock:
            # Re-check after acquiring the lock
            if self._session is not None:
                return

            transport = AIOHTTPTransport(
                url=self.url,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            self._client = Client(
                transport=transport,
                fetch_schema_from_transport=False,
            )
            self._session = await self._client.connect_async()

            # Schedule proactive refresh on first connect if we know expiry
            if self._initial_expiry_time is not None:
                self._schedule_token_refresh(self._initial_expiry_time)
                self._initial_expiry_time = None

    async def _reconnect_with_token(self, token: str) -> None:
        """Close the current session and reconnect with a new token."""
        async with self._connect_lock:
            self._token = token

            if self._client is not None:
                try:
                    await self._client.close_async()
                except (TransportServerError, TransportQueryError, OSError) as err:
                    _LOGGER.debug("Error closing client during reconnect: %s", err)

            self._client = None
            self._session = None

        await self._ensure_connected()

    async def close(self) -> None:
        """Close the client session and cancel any pending refresh timer/task."""
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None

        if self._client is not None:
            try:
                await self._client.close_async()
            except (TransportServerError, TransportQueryError, OSError) as err:
                _LOGGER.debug("Error closing client: %s", err)

        self._client = None
        self._session = None

    # -- execution ---------------------------------------------------------

    @staticmethod
    def _parse_document(query: str) -> GraphQLRequest:
        """Parse a GraphQL query string into a GraphQLRequest."""
        return gql(query)

    async def execute_document(
        self,
        document: GraphQLRequest,
        *,
        variable_values: dict[str, Any] | None = None,
        operation_name: str = "",
    ) -> dict[str, Any] | None:
        """Execute a pre-built GraphQL request.

        This is the core execution method. It handles 401 auth failures by
        refreshing the token and retrying once.

        Args:
            document: A parsed GraphQL request (from gql() or dsl_gql()).
            variable_values: Optional variable values for parameterised queries.
            operation_name: Name of the operation in the document.  Sent to
                the server and used in log messages.  Leave empty for
                anonymous DSL-built documents.

        Returns:
            The ``data`` portion of the response, or *None* on error.
        """
        label = operation_name or "GraphQL operation"
        wire_op_name = operation_name or None
        request = GraphQLRequest(document, variable_values=variable_values, operation_name=wire_op_name)

        try:
            await self._ensure_connected()
            assert self._session is not None
            return await self._session.execute(request)
        except TransportServerError as err:
            if err.code != 401:
                _LOGGER.warning("Failed %s, HTTP status code: %s", label, err.code)
                return None
            _LOGGER.debug("Token expired during %s, refreshing and retrying", label)
            return await self._refresh_and_retry(document, variable_values, wire_op_name, label)
        except TransportQueryError as err:
            if not self._has_unauthenticated_error(err):
                _LOGGER.warning("GraphQL errors in %s: %s", label, err.errors)
                return None
            _LOGGER.debug("Authentication error during %s, refreshing and retrying", label)
            return await self._refresh_and_retry(document, variable_values, wire_op_name, label)
        except OSError as err:
            _LOGGER.error("Error executing GraphQL %s: %s", label, err)
            return None

    async def execute_query(
        self,
        operation_name: str,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL query from a string."""
        document = self._parse_document(query)
        return await self.execute_document(
            document,
            variable_values=variables,
            operation_name=operation_name,
        )

    async def execute_mutation(
        self,
        operation_name: str,
        mutation: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL mutation from a string."""
        return await self.execute_query(operation_name, mutation, variables)

    # -- solar operations --------------------------------------------------

    async def set_solar(
        self,
        device_id: str,
        fields: dict[str, Any],
    ) -> bool:
        """Update solar charging settings for a device.

        *fields* is a mapping of API argument names (``override``,
        ``chargeAlways``, ``maxGridChargePercent``,
        ``chargeOutsideSchedules``) to their desired values.  Only the
        keys present in *fields* are sent to the API.

        Returns *True* on success, *False* on error.
        """
        args: dict[str, Any] = {"deviceId": device_id, **fields}

        ds = get_dsl_schema()
        document = dsl_gql(
            DSLMutation(
                ds.Mutation.setSolar.args(**args).select(
                    ds.SolarSettings.return_value,
                )
            )
        )

        result = await self.execute_document(document)

        if result is None:
            return False

        _LOGGER.debug("setSolar response: %s", result)
        return True

    @staticmethod
    def _has_unauthenticated_error(err: TransportQueryError) -> bool:
        """Return True if any error in the response has code UNAUTHENTICATED."""
        for error in err.errors or []:
            if not isinstance(error, dict):
                continue
            if error.get("extensions", {}).get("code") == "UNAUTHENTICATED":
                return True
        return False

    async def _refresh_and_retry(
        self,
        document: GraphQLRequest,
        variable_values: dict[str, Any] | None,
        wire_op_name: str | None,
        label: str,
    ) -> dict[str, Any] | None:
        """Refresh the token and retry the operation once."""
        request = GraphQLRequest(document, variable_values=variable_values, operation_name=wire_op_name)
        try:
            await self._refresh_and_reconnect()
            assert self._session is not None
            return await self._session.execute(request)
        except (TransportServerError, TransportQueryError, OSError) as retry_err:
            _LOGGER.error(
                "Retry after token refresh failed for %s: %s",
                label,
                retry_err,
            )
            return None

    # -- token refresh -----------------------------------------------------

    async def _refresh_and_reconnect(self) -> None:
        """Call the token-refresh callback and reconnect with new credentials."""
        token, expiry_time = await self._token_refresh()
        await self._reconnect_with_token(token)
        if expiry_time:
            self._schedule_token_refresh(expiry_time)

    def _schedule_token_refresh(self, expiry_time: float) -> None:
        """Schedule an automatic token refresh 5 minutes before expiry."""
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

        delay = expiry_time - time.time() - 300  # 5 minutes before expiry

        if delay <= 0:
            _LOGGER.debug("Token near expiry, scheduling immediate refresh")
            delay = 0

        _LOGGER.debug("Scheduled proactive token refresh in %d seconds", int(delay))
        loop = asyncio.get_running_loop()
        self._refresh_handle = loop.call_later(
            delay,
            self._create_refresh_task,
        )

    def _create_refresh_task(self) -> None:
        """Create a task for proactive token refresh, storing the reference."""
        self._refresh_task = asyncio.create_task(self._proactive_refresh())
        self._refresh_task.add_done_callback(lambda _: setattr(self, "_refresh_task", None))

    async def _proactive_refresh(self) -> None:
        """Proactive refresh triggered by the scheduled timer."""
        self._refresh_handle = None
        try:
            _LOGGER.debug("Proactive token refresh triggered")
            await self._refresh_and_reconnect()
            _LOGGER.debug("Proactive token refresh completed successfully")
        except (TransportServerError, TransportQueryError, OSError) as err:
            _LOGGER.warning("Proactive token refresh failed: %s", err)
