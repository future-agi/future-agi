"""
ClickHouse Client for Analytics Backend

Provides connection management and query execution for ClickHouse.
"""

import queue
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from django.conf import settings

from tracer.services.clickhouse.server_readonly import (
    ensure_read_statement,
    without_query_settings,
)

logger = structlog.get_logger(__name__)

_TOO_MANY_SIMULTANEOUS_QUERIES_CODE = 202
_READ_ADMISSION_RETRY_DELAYS_SECONDS = tuple(
    delay_ms / 1_000
    for delay_ms in (
        settings.CLICKHOUSE_READ_ADMISSION_RETRY_FIRST_MS,
        settings.CLICKHOUSE_READ_ADMISSION_RETRY_SECOND_MS,
        settings.CLICKHOUSE_READ_ADMISSION_RETRY_THIRD_MS,
    )
)
_APPLICATION_READ_TIMEOUT_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
_REVIEWED_READ_TIMEOUT_CEILING_MS = settings.CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS
_APPLICATION_READ_MAX_MEMORY_USAGE = (
    settings.CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES
)
_APPLICATION_READ_MAX_BYTES_TO_READ = settings.CLICKHOUSE_APPLICATION_READ_MAX_BYTES
_APPLICATION_READ_DEFAULT_THREADS = settings.CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS
_APPLICATION_READ_MAX_THREADS = settings.CLICKHOUSE_APPLICATION_READ_MAX_THREADS
_APPLICATION_READ_MAX_RESULT_ROWS = settings.CLICKHOUSE_APPLICATION_READ_MAX_RESULT_ROWS
_APPLICATION_READ_MAX_RESULT_BYTES = (
    settings.CLICKHOUSE_APPLICATION_READ_MAX_RESULT_BYTES
)

# Try to import clickhouse-driver, gracefully handle if not installed
try:
    from clickhouse_driver import Client as CHDriver
    from clickhouse_driver.errors import Error as CHError

    CLICKHOUSE_AVAILABLE = True
except ImportError:
    CHDriver = None
    CHError = Exception
    CLICKHOUSE_AVAILABLE = False


def _bounded_read_timeout_ms(
    timeout_ms: int | None,
    *,
    ceiling_ms: int = _APPLICATION_READ_TIMEOUT_MS,
) -> int:
    if type(ceiling_ms) is not int or not (
        1 <= ceiling_ms <= _REVIEWED_READ_TIMEOUT_CEILING_MS
    ):
        raise ValueError(
            "ClickHouse read timeout ceiling is outside [1, "
            f"{_REVIEWED_READ_TIMEOUT_CEILING_MS}] ms"
        )
    return max(
        1,
        min(
            int(ceiling_ms if timeout_ms is None else timeout_ms),
            ceiling_ms,
        ),
    )


class _ManagedNativeReadStream:
    """Own one admitted native read connection for an iterator's lifetime."""

    def __init__(
        self,
        owner: "ClickHouseClient",
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        block_size: int,
    ):
        self._owner = owner
        self._query = query
        self._params = params
        self._timeout_ms = timeout_ms
        self._block_size = block_size
        self._deadline = 0.0
        self._admission_acquired = False
        self._client = None
        self._driver_connection = None
        self._original_connect_timeout = None
        self._original_send_receive_timeout = None
        self._socket = None
        self._original_socket_timeout = None
        self._original_socket_timeout_known = False
        self._exhausted = False

    def _remaining_seconds(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ClickHouse read deadline exhausted")
        return remaining

    @staticmethod
    def _narrow_timeout(original: Any, remaining: float) -> float:
        try:
            value = float(original)
        except (TypeError, ValueError):
            return remaining
        return min(value, remaining) if value > 0 else remaining

    def _capture_transport(self) -> None:
        connection = getattr(self._client, "connection", None)
        if getattr(connection, "connected", None) not in {True, False}:
            return
        self._driver_connection = connection
        self._original_connect_timeout = getattr(connection, "connect_timeout", None)
        self._original_send_receive_timeout = getattr(
            connection, "send_receive_timeout", None
        )
        socket = getattr(connection, "socket", None)
        if socket is not None:
            self._socket = socket
            self._original_socket_timeout = socket.gettimeout()
            self._original_socket_timeout_known = True
        elif self._original_send_receive_timeout is not None:
            # A lazy connection creates its socket during the first next().
            self._original_socket_timeout = self._original_send_receive_timeout
            self._original_socket_timeout_known = True

    def _clamp_transport(self) -> None:
        remaining = self._remaining_seconds()
        connection = self._driver_connection
        if connection is None:
            return
        if self._original_connect_timeout is not None:
            connection.connect_timeout = self._narrow_timeout(
                self._original_connect_timeout, remaining
            )
        if self._original_send_receive_timeout is not None:
            connection.send_receive_timeout = self._narrow_timeout(
                self._original_send_receive_timeout, remaining
            )
        socket = getattr(connection, "socket", None)
        if socket is not None:
            self._socket = socket
            if not self._original_socket_timeout_known:
                self._original_socket_timeout = socket.gettimeout()
                self._original_socket_timeout_known = True
            socket.settimeout(
                self._narrow_timeout(self._original_socket_timeout, remaining)
            )

    def _restore_transport(self) -> bool:
        connection = self._driver_connection
        if connection is None:
            return True
        restored = True
        if self._socket is not None and self._original_socket_timeout_known:
            try:
                self._socket.settimeout(self._original_socket_timeout)
            except Exception:
                restored = False
        if self._original_connect_timeout is not None:
            try:
                connection.connect_timeout = self._original_connect_timeout
            except Exception:
                restored = False
        if self._original_send_receive_timeout is not None:
            try:
                connection.send_receive_timeout = self._original_send_receive_timeout
            except Exception:
                restored = False
        return restored

    def _retire_client(self) -> None:
        if self._client is None:
            return
        try:
            self._client.disconnect()
        except Exception as exc:
            logger.warning(
                "server_readonly_native_disconnect_failed",
                error_type=type(exc).__name__,
                exc_info=True,
            )
        finally:
            self._client = None

    def _cleanup(self, *, reusable: bool) -> None:
        try:
            transport_restored = self._restore_transport()
            reusable = reusable and transport_restored
            if self._client is not None:
                if reusable:
                    client = self._client
                    self._client = None
                    self._owner._return_client(client)
                else:
                    self._retire_client()
        finally:
            if self._admission_acquired:
                self._admission_acquired = False
                self._owner._read_admission.release()

    def __enter__(self) -> Iterator[list[tuple]]:
        self._deadline = time.monotonic() + (self._timeout_ms / 1000.0)
        try:
            self._admission_acquired = self._owner._read_admission.acquire(
                timeout=self._remaining_seconds()
            )
            if not self._admission_acquired:
                raise TimeoutError("ClickHouse read admission deadline exhausted")
            self._remaining_seconds()
            self._client = self._owner._get_client()
            self._remaining_seconds()
            self._capture_transport()
            self._clamp_transport()
            rows = iter(self._client.execute_iter(self._query, self._params))
        except Exception:
            self._cleanup(reusable=False)
            raise

        def blocks() -> Iterator[list[tuple]]:
            block: list[tuple] = []
            while True:
                self._clamp_transport()
                try:
                    row = next(rows)
                except StopIteration:
                    self._remaining_seconds()
                    self._exhausted = True
                    break
                self._clamp_transport()
                block.append(row)
                if len(block) >= self._block_size:
                    yield block
                    block = []
            if block:
                yield block

        return blocks()

    def __exit__(self, exc_type, *_exc) -> None:
        self._cleanup(reusable=exc_type is None and self._exhausted)


class ClickHouseClient:
    """
    ClickHouse client wrapper with connection pooling and error handling.

    Usage:
        client = ClickHouseClient()
        results = client.execute("SELECT * FROM observation_spans LIMIT 10")
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        server_enforced_readonly: bool | None = None,
        *,
        connect_timeout: float | None = None,
        send_timeout: float | None = None,
        receive_timeout: float | None = None,
        pool_size: int | None = None,
        read_timeout_ceiling_ms: int | None = None,
    ):
        """
        Initialize ClickHouse client with connection settings.

        If parameters are not provided, they are read from Django settings.
        """
        ch_settings = getattr(settings, "CLICKHOUSE", {})

        self.host = ch_settings.get("CH_HOST") if host is None else host
        self.port = int(ch_settings.get("CH_PORT", 9000) if port is None else port)
        self.user = ch_settings.get("CH_USERNAME", "default") if user is None else user
        self.password = (
            ch_settings.get("CH_PASSWORD", "") if password is None else password
        )
        self.database = (
            ch_settings.get("CH_DATABASE", "default") if database is None else database
        )
        self.server_enforced_readonly = (
            bool(ch_settings.get("CH_SERVER_ENFORCED_READONLY", False))
            if server_enforced_readonly is None
            else bool(server_enforced_readonly)
        )

        # Connection settings
        self.connect_timeout = (
            ch_settings.get("CH_CONNECT_TIMEOUT", 10)
            if connect_timeout is None
            else float(connect_timeout)
        )
        self.send_timeout = (
            ch_settings.get("CH_SEND_TIMEOUT", 300)
            if send_timeout is None
            else float(send_timeout)
        )
        self.receive_timeout = (
            ch_settings.get("CH_RECEIVE_TIMEOUT", 300)
            if receive_timeout is None
            else float(receive_timeout)
        )
        self.read_timeout_ceiling_ms = (
            _APPLICATION_READ_TIMEOUT_MS
            if read_timeout_ceiling_ms is None
            else read_timeout_ceiling_ms
        )

        # Thread-safe connection pool
        self._pool_size = int(
            ch_settings.get("CH_POOL_SIZE", 10) if pool_size is None else pool_size
        )
        if (
            self.connect_timeout <= 0
            or self.send_timeout <= 0
            or self.receive_timeout <= 0
            or self._pool_size <= 0
        ):
            raise ValueError("ClickHouse transport bounds must be positive")
        _bounded_read_timeout_ms(
            self.read_timeout_ceiling_ms,
            ceiling_ms=self.read_timeout_ceiling_ms,
        )
        self._pool: queue.Queue = queue.Queue(maxsize=self._pool_size)
        self._pool_lock = threading.Lock()
        self._pool_initialized = False
        self._read_admission = threading.BoundedSemaphore(self._pool_size)

    @property
    def is_available(self) -> bool:
        """Check if ClickHouse driver is available."""
        return CLICKHOUSE_AVAILABLE

    @property
    def is_enabled(self) -> bool:
        """Check if ClickHouse is enabled in settings."""
        ch_settings = getattr(settings, "CLICKHOUSE", {})
        return ch_settings.get("CH_ENABLED", False)

    @property
    def is_configured(self) -> bool:
        """Check if ClickHouse connection is configured."""
        return bool(self.host)

    def _create_client(
        self,
        *,
        send_receive_timeout_seconds: float | None = None,
    ) -> CHDriver:
        """Create a new ClickHouse driver connection."""
        if not CLICKHOUSE_AVAILABLE:
            raise RuntimeError(
                "clickhouse-driver is not installed. "
                "Install it with: pip install clickhouse-driver"
            )
        if not self.host:
            raise ValueError("ClickHouse host is not configured")

        driver_settings = (
            None
            if self.server_enforced_readonly
            else {"use_numpy": False, "max_block_size": 100000}
        )

        return CHDriver(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            connect_timeout=self.connect_timeout,
            send_receive_timeout=(
                max(self.send_timeout, self.receive_timeout)
                if send_receive_timeout_seconds is None
                else send_receive_timeout_seconds
            ),
            settings=driver_settings,
        )

    def _get_client(self) -> CHDriver:
        """Acquire a ClickHouse client connection from the pool."""
        try:
            client = self._pool.get_nowait()
            return client
        except queue.Empty:
            # Pool is empty — create a new connection
            return self._create_client()

    def _return_client(self, client: CHDriver) -> None:
        """Return a ClickHouse client connection to the pool."""
        try:
            self._pool.put_nowait(client)
        except queue.Full:
            # Pool is full — discard the connection
            try:
                client.disconnect()
            except Exception:
                pass

    @contextmanager
    def connection(self):
        """
        Context manager that acquires a connection from the pool and
        returns it when done.

        Usage:
            with client.connection() as conn:
                conn.execute("SELECT 1")
        """
        if self.server_enforced_readonly:
            raise RuntimeError(
                "Raw ClickHouse connections are disabled for the "
                "server-enforced read-only client."
            )
        client = self._get_client()
        try:
            yield client
        finally:
            self._return_client(client)

    def execute(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        with_column_types: bool = False,
        settings: dict[str, Any] | None = None,
    ) -> list[tuple]:
        """
        Execute a query and return results.

        Args:
            query: SQL query string
            params: Query parameters for parameterized queries
            with_column_types: If True, returns (results, column_types)
            settings: Optional per-query ClickHouse settings (e.g.
                {"data_type_default_nullable": 0} for DDL that must not be
                auto-wrapped in Nullable when the server profile sets it to 1)

        Returns:
            List of result tuples, or (results, column_types) if with_column_types=True
        """
        if self.server_enforced_readonly:
            query = without_query_settings(query)
            ensure_read_statement(query)
            settings = None
        client = self._get_client()
        t_start = time.monotonic()

        try:
            logger.debug("Executing ClickHouse query", query=query[:200])
            result = client.execute(
                query,
                params or {},
                with_column_types=with_column_types,
                settings=settings,
            )

            query_time_ms = (time.monotonic() - t_start) * 1000
            rows_returned = (
                len(result[0])
                if with_column_types and result
                else len(result)
                if result and not isinstance(result, int)
                else 0
            )
            logger.info(
                "ClickHouse query completed",
                query=query[:200],
                query_time_ms=round(query_time_ms, 2),
                rows_returned=rows_returned,
                backend="clickhouse",
            )

            return result

        except CHError as e:
            query_time_ms = (time.monotonic() - t_start) * 1000
            logger.error(
                "ClickHouse query failed",
                error=str(e),
                query=query[:200],
                query_time_ms=round(query_time_ms, 2),
                backend="clickhouse",
            )
            raise
        finally:
            self._return_client(client)

    def execute_read(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        settings: dict[str, Any] | None = None,
    ) -> tuple[list[tuple], list[tuple], float]:
        """Execute a guarded read while preserving the historical result tuple."""

        result = self._execute_read(
            query,
            params,
            timeout_ms=timeout_ms,
            settings=settings,
            include_progress=False,
        )
        return result[0], result[1], result[2]

    def execute_read_with_progress(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        settings: dict[str, Any] | None = None,
    ) -> tuple[list[tuple], list[tuple], float, int | None, int | None]:
        """Execute a guarded read and include native rows/bytes progress."""

        result = self._execute_read(
            query,
            params,
            timeout_ms=timeout_ms,
            settings=settings,
            include_progress=True,
        )
        assert len(result) == 5
        return result

    def _execute_read(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        settings: dict[str, Any] | None = None,
        *,
        include_progress: bool,
    ) -> (
        tuple[list[tuple], list[tuple], float]
        | tuple[list[tuple], list[tuple], float, int | None, int | None]
    ):
        """
        Execute a read-only query with ClickHouse readonly=2 setting.

        `readonly=2` blocks writes and DDL but permits per-query settings
        overrides (e.g. ``max_threads``, ``join_algorithm``). Queries are
        server-built, so there is no SQL-injection surface for a caller
        to abuse this.

        Args:
            query: SQL query string
            params: Query parameters for parameterized queries
            timeout_ms: Optional query timeout in milliseconds (maps to max_execution_time)

        Returns:
            Tuple of (rows, column_types, query_time_ms), optionally followed
            by native read_rows and read_bytes progress.
        """
        timeout_ms = _bounded_read_timeout_ms(
            timeout_ms,
            ceiling_ms=self.read_timeout_ceiling_ms,
        )
        query_settings: dict[str, Any] | None
        if self.server_enforced_readonly:
            # A ClickHouse profile locked at readonly=1 rejects *all* client
            # setting changes, including an otherwise harmless readonly=2 or
            # max_execution_time override.  The SOS/read-replica lane relies on
            # the server profile for those ceilings, so transmit no settings at
            # connection or query scope.  Production's ordinary application
            # role keeps the existing per-query guardrails below.
            query_settings = None
            query = without_query_settings(query)
            ensure_read_statement(query)
            logger.debug(
                "Using server-enforced ClickHouse read settings",
                requested_setting_keys=sorted((settings or {}).keys()),
                requested_timeout_ms=timeout_ms,
            )
        else:
            query_settings = dict(settings or {})
            query_settings.pop("max_rows_to_read", None)

            def finite_ceiling(name: str, ceiling: int) -> int:
                requested = int(query_settings.get(name, 0) or 0)
                return ceiling if requested <= 0 else min(requested, ceiling)

            query_settings["max_memory_usage"] = finite_ceiling(
                "max_memory_usage", _APPLICATION_READ_MAX_MEMORY_USAGE
            )
            query_settings["max_bytes_to_read"] = finite_ceiling(
                "max_bytes_to_read", _APPLICATION_READ_MAX_BYTES_TO_READ
            )
            requested_threads = int(query_settings.get("max_threads", 0) or 0)
            query_settings["max_threads"] = (
                _APPLICATION_READ_DEFAULT_THREADS
                if requested_threads <= 0
                else min(requested_threads, _APPLICATION_READ_MAX_THREADS)
            )
            query_settings["max_result_rows"] = finite_ceiling(
                "max_result_rows", _APPLICATION_READ_MAX_RESULT_ROWS
            )
            query_settings["max_result_bytes"] = finite_ceiling(
                "max_result_bytes", _APPLICATION_READ_MAX_RESULT_BYTES
            )
            query_settings["result_overflow_mode"] = "throw"
            query_settings["readonly"] = 2
            # max_execution_time is in seconds.
            query_settings["max_execution_time"] = timeout_ms / 1000.0

        t_start = time.monotonic()
        admission_acquired = False
        client = None

        try:
            admission_acquired = self._read_admission.acquire(
                timeout=max(timeout_ms / 1000.0, 0.001)
            )
            if not admission_acquired:
                raise TimeoutError("ClickHouse read admission deadline exhausted")
            client = self._get_client()
            logger.debug(
                "Executing ClickHouse read query",
                query=query[:200],
                timeout_ms=timeout_ms,
            )
            retry_attempt = 0
            while True:
                try:
                    result = self._execute_native_read_with_remaining_timeout(
                        client,
                        query,
                        params or {},
                        query_settings=query_settings,
                        started_at=t_start,
                        timeout_ms=timeout_ms,
                    )
                    break
                except CHError as exc:
                    if getattr(
                        exc, "code", None
                    ) != _TOO_MANY_SIMULTANEOUS_QUERIES_CODE or retry_attempt >= len(
                        _READ_ADMISSION_RETRY_DELAYS_SECONDS
                    ):
                        raise

                    retry_delay = _READ_ADMISSION_RETRY_DELAYS_SECONDS[retry_attempt]
                    elapsed_seconds = time.monotonic() - t_start
                    if (
                        timeout_ms is not None
                        and elapsed_seconds + retry_delay >= timeout_ms / 1000.0
                    ):
                        raise

                    retry_attempt += 1
                    logger.warning(
                        "ClickHouse read admission temporarily saturated",
                        error_code=_TOO_MANY_SIMULTANEOUS_QUERIES_CODE,
                        retry_attempt=retry_attempt,
                        retry_delay_ms=round(retry_delay * 1000),
                        backend="clickhouse",
                    )
                    time.sleep(retry_delay)

                    # Preserve the caller's immutable settings while ensuring
                    # a retry cannot extend the original wall-clock deadline.
                    if query_settings is not None and timeout_ms is not None:
                        remaining_seconds = max(
                            (timeout_ms / 1000.0) - (time.monotonic() - t_start),
                            0.001,
                        )
                        query_settings = {
                            **query_settings,
                            "max_execution_time": remaining_seconds,
                        }

            rows, column_types = result
            query_time_ms = (time.monotonic() - t_start) * 1000
            rows_returned = len(rows) if rows else 0
            progress = getattr(getattr(client, "last_query", None), "progress", None)

            def progress_metric(name: str) -> int | None:
                try:
                    value = int(getattr(progress, name))
                except (AttributeError, TypeError, ValueError):
                    return None
                return value if value >= 0 else None

            read_rows = progress_metric("rows")
            read_bytes = progress_metric("bytes")

            logger.info(
                "ClickHouse read query completed",
                query=query[:200],
                query_time_ms=round(query_time_ms, 2),
                rows_returned=rows_returned,
                read_rows=read_rows,
                read_bytes=read_bytes,
                backend="clickhouse",
            )

            rounded_query_time_ms = round(query_time_ms, 2)
            if include_progress:
                return (
                    rows,
                    column_types,
                    rounded_query_time_ms,
                    read_rows,
                    read_bytes,
                )
            return rows, column_types, rounded_query_time_ms

        except CHError as e:
            query_time_ms = (time.monotonic() - t_start) * 1000
            logger.error(
                "ClickHouse read query failed",
                error=str(e),
                query=query[:200],
                query_time_ms=round(query_time_ms, 2),
                backend="clickhouse",
            )
            raise
        finally:
            if client is not None:
                self._return_client(client)
            if admission_acquired:
                self._read_admission.release()

    @staticmethod
    def _execute_native_read_with_remaining_timeout(
        client: CHDriver,
        query: str,
        params: dict[str, Any],
        *,
        query_settings: dict[str, Any] | None,
        started_at: float,
        timeout_ms: int | None,
    ):
        """Keep connect + socket wait inside the read's remaining wall.

        ClickHouse's ``max_execution_time`` bounds server work, but a pooled
        native connection otherwise retains the process-wide 300-second
        socket timeout. A stalled connect/read could therefore outlive an
        interactive request by minutes even though the query itself was
        capped at 9.5 seconds. Temporarily narrow the exclusive pooled
        connection's connect/socket settings to the remaining wall and restore
        them before it returns to the pool.

        Test doubles and alternate native clients do not necessarily expose a
        clickhouse-driver ``Connection``. They continue through the same
        guarded query settings without transport mutation.
        """

        connection = getattr(client, "connection", None)
        connected = getattr(connection, "connected", None)
        if timeout_ms is None or connected not in {True, False}:
            return client.execute(
                query,
                params,
                with_column_types=True,
                settings=query_settings,
            )

        deadline = started_at + (timeout_ms / 1000.0)
        original_connect_timeout = getattr(connection, "connect_timeout", None)
        original_send_receive_timeout = getattr(
            connection, "send_receive_timeout", None
        )
        socket = None
        original_socket_timeout = None
        try:
            if connected is False:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("ClickHouse read deadline exhausted")
                if original_connect_timeout is not None:
                    connection.connect_timeout = min(
                        float(original_connect_timeout), remaining
                    )
                if original_send_receive_timeout is not None:
                    connection.send_receive_timeout = min(
                        float(original_send_receive_timeout), remaining
                    )
                connection.connect()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ClickHouse read deadline exhausted")
            if original_send_receive_timeout is not None:
                connection.send_receive_timeout = min(
                    float(original_send_receive_timeout), remaining
                )
            socket = getattr(connection, "socket", None)
            if socket is not None:
                original_socket_timeout = socket.gettimeout()
                socket.settimeout(remaining)

            return client.execute(
                query,
                params,
                with_column_types=True,
                settings=query_settings,
            )
        finally:
            if socket is not None and original_socket_timeout is not None:
                try:
                    socket.settimeout(original_socket_timeout)
                except Exception:
                    pass
            if original_connect_timeout is not None:
                connection.connect_timeout = original_connect_timeout
            if original_send_receive_timeout is not None:
                connection.send_receive_timeout = original_send_receive_timeout

    def execute_read_block_stream(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
        block_size: int = 8192,
    ) -> _ManagedNativeReadStream:
        """Return a deadline- and admission-managed native read stream."""

        if block_size <= 0:
            raise ValueError("block_size must be positive")
        if self.server_enforced_readonly:
            query = without_query_settings(query)
        ensure_read_statement(query)
        return _ManagedNativeReadStream(
            self,
            query,
            params or {},
            timeout_ms=_bounded_read_timeout_ms(
                timeout_ms,
                ceiling_ms=self.read_timeout_ceiling_ms,
            ),
            block_size=block_size,
        )

    def execute_iter(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ):
        """
        Execute a query and return an iterator over results.

        Useful for large result sets to avoid loading all data into memory.
        """
        if self.server_enforced_readonly:
            query = without_query_settings(query)
            ensure_read_statement(query)
            raise RuntimeError(
                "Direct execute_iter is disabled for the server-enforced "
                "read-only client; use the managed native block stream."
            )

        client = self._get_client()

        try:
            return client.execute_iter(query, params or {})

        except CHError as e:
            logger.error("ClickHouse query failed", error=str(e), query=query[:200])
            raise

    def insert(
        self,
        table: str,
        data: list[dict[str, Any]],
        columns: list[str] | None = None,
    ) -> int:
        """
        Insert data into a table.

        Args:
            table: Table name
            data: List of dictionaries with column->value mappings
            columns: Optional list of column names (inferred from data if not provided)

        Returns:
            Number of rows inserted
        """
        if self.server_enforced_readonly:
            raise RuntimeError(
                "ClickHouse inserts are disabled for the server-enforced "
                "read-only client."
            )

        if not data:
            return 0

        client = self._get_client()

        # Infer columns from first row if not provided
        if columns is None:
            columns = list(data[0].keys())

        # Convert data to tuple format
        rows = [tuple(row.get(col) for col in columns) for row in data]

        t_start = time.monotonic()
        try:
            logger.debug(
                "Inserting into ClickHouse",
                table=table,
                row_count=len(rows),
            )

            client.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES",
                rows,
            )

            query_time_ms = (time.monotonic() - t_start) * 1000
            logger.info(
                "ClickHouse insert completed",
                table=table,
                row_count=len(rows),
                query_time_ms=round(query_time_ms, 2),
                backend="clickhouse",
            )

            return len(rows)

        except CHError as e:
            query_time_ms = (time.monotonic() - t_start) * 1000
            logger.error(
                "ClickHouse insert failed",
                error=str(e),
                table=table,
                row_count=len(rows),
                query_time_ms=round(query_time_ms, 2),
                backend="clickhouse",
            )
            raise
        finally:
            self._return_client(client)

    def insert_dataframe(self, table: str, df) -> int:
        """
        Insert a pandas DataFrame into a table.

        Args:
            table: Table name
            df: pandas DataFrame

        Returns:
            Number of rows inserted
        """
        data = df.to_dict("records")
        columns = list(df.columns)
        return self.insert(table, data, columns)

    def ping(self) -> bool:
        """Test connection to ClickHouse."""
        try:
            self.execute("SELECT 1")
            return True
        except Exception as e:
            logger.warning("ClickHouse ping failed", error=str(e))
            return False

    def create_database(self, database: str | None = None) -> None:
        """Create database if it doesn't exist."""
        db = database or self.database
        self.execute(f"CREATE DATABASE IF NOT EXISTS {db}")

    def table_exists(self, table: str) -> bool:
        """Check if a table exists."""
        result = self.execute(
            "SELECT count() FROM system.tables WHERE database = %(db)s AND name = %(table)s",
            {"db": self.database, "table": table},
        )
        return result[0][0] > 0

    def get_table_row_count(self, table: str) -> int:
        """Get approximate row count for a table."""
        result = self.execute(f"SELECT count() FROM {table}")
        return result[0][0]

    def check_replication_lag(self) -> dict[str, float]:
        """
        Query CDC replication lag per table.

        Checks the max(_peerdb_synced_at) for each replicated table and
        returns a dict of table_name -> lag_seconds.

        Returns:
            Dict mapping table names to lag in seconds. A value of -1
            indicates the lag could not be determined.
        """
        from datetime import datetime

        # CH25 close-out (2026-05-28): removed `tracer_observation_span`
        # from the CDC lag check. Spans now land in v2 typed-JSON `spans`
        # via fi-collector OTLP — no CDC mirror, no lag to measure.
        tables = [
            "tracer_trace",
            "trace_session",
            "tracer_eval_logger",
        ]
        lag: dict[str, float] = {}
        for table in tables:
            try:
                result = self.execute(
                    f"SELECT max(_peerdb_synced_at) as last_sync FROM {table}"
                )
                if result and result[0][0]:
                    last_sync = result[0][0]
                    if isinstance(last_sync, datetime):
                        lag[table] = (datetime.utcnow() - last_sync).total_seconds()
                    else:
                        lag[table] = -1
                else:
                    lag[table] = -1  # No data
            except Exception as e:
                logger.warning(
                    "CDC lag check failed",
                    table=table,
                    error=str(e),
                    backend="clickhouse",
                )
                lag[table] = -1
        return lag

    def close(self) -> None:
        """Close all connections in the pool."""
        while True:
            try:
                client = self._pool.get_nowait()
                try:
                    client.disconnect()
                except Exception:
                    pass
            except queue.Empty:
                break


# Singleton instance
_clickhouse_client: ClickHouseClient | None = None


def get_clickhouse_client() -> ClickHouseClient:
    """
    Get the singleton ClickHouse client instance.

    Returns:
        ClickHouseClient instance
    """
    global _clickhouse_client

    if _clickhouse_client is None:
        _clickhouse_client = ClickHouseClient()

    return _clickhouse_client


def is_clickhouse_enabled() -> bool:
    """Check if ClickHouse is enabled and configured."""
    client = get_clickhouse_client()
    return client.is_enabled and client.is_configured and client.is_available
