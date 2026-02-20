"""GraphQL client for Andersen EV API using gql library."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import (
    TransportQueryError,
    TransportServerError,
)
from graphql import DocumentNode

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


class GraphQLClient:  # pylint: disable=too-many-instance-attributes
    """Async GraphQL client for Andersen EV API using gql[aiohttp].

    Maintains a persistent session and handles token refresh automatically,
    both reactively (on HTTP 401) and proactively (via a scheduled timer
    that fires 5 minutes before the token expires).
    """

    def __init__(
        self,
        token: str,
        token_refresh: Callable[[], Any],
        url: str = const.GRAPHQL_URL,
        token_expiry_time: float | None = None,
    ) -> None:
        """Initialize the GraphQL client.

        Args:
            token: Bearer token for authentication.
            token_refresh: Async callback that refreshes the token and
                returns a ``(new_token, new_expiry_time)`` tuple.
            url: GraphQL endpoint URL.
            token_expiry_time: Absolute epoch time when ``token`` expires.
                If provided, a proactive refresh is scheduled 5 min before.
        """
        self._token = token
        self.url = url
        self._token_refresh = token_refresh
        self._transport: AIOHTTPTransport | None = None
        self._client: Client | None = None
        self._session = None  # AsyncClientSession from gql
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._refresh_task: asyncio.Task | None = None
        self._initial_expiry_time = token_expiry_time

        # Synchronization primitives to serialize connect/reconnect/close and
        # to track active in-flight requests so reconnect/close waits for them.
        self._conn_cond: asyncio.Condition = asyncio.Condition()
        self._active_requests: int = 0
        self._reconnecting: bool = False

    @property
    def token(self) -> str:
        """Return the current bearer token."""
        return self._token

    # -- connection management ---------------------------------------------

    async def _ensure_connected(self) -> None:
        """Lazily create and connect the gql client session (serialized)."""
        async with self._conn_cond:
            await self._ensure_connected_locked()

    async def _ensure_connected_locked(self) -> None:
        """Create and connect the gql client session assuming the connection
        condition is already held by the caller.
        """
        if self._session is not None or self._reconnecting:
            return

        # Create transport and client while holding the lock so multiple
        # coroutines don't race to create sessions.
        self._transport = AIOHTTPTransport(
            url=self.url,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        self._client = Client(
            transport=self._transport,
            fetch_schema_from_transport=False,
        )
        # Awaiting connect while holding the condition serializes connect attempts.
        self._session = await self._client.connect_async()

        # Schedule proactive refresh on first connect if we know expiry
        if self._initial_expiry_time is not None:
            self._schedule_token_refresh(self._initial_expiry_time)
            self._initial_expiry_time = None

    async def _reconnect_with_token(self, token: str) -> None:
        """Close the current session and reconnect with a new token.

        This method serializes reconnects and waits for outstanding in-flight
        requests to finish before closing the current client. While reconnecting
        new requests will wait until reconnect completes.
        """
        # Mark reconnecting and capture token under the connection condition
        async with self._conn_cond:
            self._reconnecting = True
            self._token = token
            # Wait until no active requests are running
            while self._active_requests > 0:
                await self._conn_cond.wait()

        # At this point no requests are active and _reconnecting prevents new ones.
        # Close existing client outside the lock to avoid blocking other coroutines.
        if self._client is not None:
            try:
                await self._client.close_async()
            except Exception as err:  # preserve original behavior for close errors
                _LOGGER.debug("Error closing client during reconnect: %s", err)

        # Create new transport/client and connect
        new_transport = AIOHTTPTransport(
            url=self.url,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        new_client = Client(
            transport=new_transport,
            fetch_schema_from_transport=False,
        )
        new_session = await new_client.connect_async()

        # Swap in the new client/session under the lock and clear reconnecting
        async with self._conn_cond:
            self._transport = new_transport
            self._client = new_client
            self._session = new_session
            self._reconnecting = False
            self._conn_cond.notify_all()

    async def close(self) -> None:
        """Close the client session and cancel any pending refresh timer.

        This waits for active requests to finish and serializes the close so
        nothing replaces the session while closing.
        """
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

        # Prevent new requests and wait for in-flight requests to finish
        async with self._conn_cond:
            self._reconnecting = True
            while self._active_requests > 0:
                await self._conn_cond.wait()

        # Close the client outside the lock
        if self._client is not None:
            try:
                await self._client.close_async()
            except Exception as err:
                _LOGGER.debug("Error closing client: %s", err)

        # Clear references under the lock and allow others to proceed
        async with self._conn_cond:
            self._client = None
            self._session = None
            self._transport = None
            self._reconnecting = False
            self._conn_cond.notify_all()

    # -- execution ---------------------------------------------------------

    @staticmethod
    def _parse_document(query: str) -> DocumentNode:
        """Parse a GraphQL query string into a DocumentNode."""
        return gql(query)

    async def execute_query(
        self,
        operation_name: str,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL query.

        Handles 401 auth failures by refreshing the token and retrying once.

        Returns:
            The ``data`` portion of the response, or *None* on error.
        """
        document = self._parse_document(query)

        # Track whether this logical request currently holds an active slot.
        active_counted = False

        # First attempt (may be retried below)
        try:
            # Ensure connected and register this request as active
            async with self._conn_cond:
                while self._reconnecting:
                    await self._conn_cond.wait()
                await self._ensure_connected_locked()
                self._active_requests += 1
                active_counted = True

            try:
                return await self._session.execute(
                    document,
                    variable_values=variables,
                    operation_name=operation_name,
                )
            except TransportServerError as err:
                if err.code == 401:
                    _LOGGER.debug(
                        "Token expired during %s, refreshing and retrying",
                        operation_name,
                    )

                    # Release active slot for the refresh so reconnect can wait for
                    # zero active requests, and mark we don't hold an active slot.
                    async with self._conn_cond:
                        if active_counted:
                            self._active_requests -= 1
                            active_counted = False
                            if self._active_requests == 0:
                                self._conn_cond.notify_all()

                    # Refresh token and reconnect (this will wait for in-flight
                    # requests to finish before swapping sessions)
                    await self._refresh_and_reconnect()

                    # After refresh, try again: ensure connected and re-register
                    async with self._conn_cond:
                        while self._reconnecting:
                            await self._conn_cond.wait()
                        await self._ensure_connected_locked()
                        self._active_requests += 1
                        active_counted = True

                    try:
                        return await self._session.execute(
                            document,
                            variable_values=variables,
                            operation_name=operation_name,
                        )
                    except (
                        TransportServerError,
                        TransportQueryError,
                        OSError,
                    ) as retry_err:
                        _LOGGER.error(
                            "Retry after token refresh failed for %s: %s",
                            operation_name,
                            retry_err,
                        )
                        return None
                _LOGGER.warning(
                    "Failed %s, HTTP status code: %s", operation_name, err.code
                )
                return None
            except TransportQueryError as err:
                _LOGGER.warning("GraphQL errors in %s: %s", operation_name, err.errors)
                return None
            except OSError as err:
                _LOGGER.error(
                    "Error executing GraphQL query %s: %s", operation_name, err
                )
                return None
        finally:
            # Release active slot if still held
            if active_counted:
                async with self._conn_cond:
                    self._active_requests -= 1
                    if self._active_requests == 0:
                        self._conn_cond.notify_all()

    async def execute_mutation(
        self,
        operation_name: str,
        mutation: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL mutation (delegates to execute_query)."""
        return await self.execute_query(operation_name, mutation, variables)

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
            self._refresh_task = asyncio.ensure_future(self._proactive_refresh())
            return

        _LOGGER.debug("Scheduled proactive token refresh in %d seconds", int(delay))
        loop = asyncio.get_running_loop()
        self._refresh_handle = loop.call_later(
            delay,
            lambda: asyncio.ensure_future(self._proactive_refresh()),
        )

    async def _proactive_refresh(self) -> None:
        """Proactive refresh triggered by the scheduled timer."""
        self._refresh_handle = None
        try:
            _LOGGER.debug("Proactive token refresh triggered")
            await self._refresh_and_reconnect()
            _LOGGER.debug("Proactive token refresh completed successfully")
        except (TransportServerError, TransportQueryError, OSError) as err:
            _LOGGER.warning("Proactive token refresh failed: %s", err)
