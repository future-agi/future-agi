"""
ClickHouse Client for Analytics Backend

Provides connection management and query execution for ClickHouse.
"""

import queue
import threading
import time
from contextlib import contextmanager
from typing import Any

import structlog
from django.conf import settings

from tracer.services.clickhouse.server_readonly import (
    ensure_read_statement,
    without_query_settings,
)

logger = structlog.get_logger(__name__)

_QUERY_TRANSPORT_GRACE_SECONDS = 5.0
_TOO_MANY_SIMULTANEOUS_QUERIES_CODE = 202
_READ_ADMISSION_RETRY_DELAYS_SECONDS = (0.025, 0.075, 0.150)

# Try to import clickhouse-driver, gracefully handle if not installed
try:
    from clickhouse_driver import Client as CHDriver
    from clickhouse_driver.errors import Error as CHError

    CLICKHOUSE_AVAILABLE = True
except ImportError:
    CHDriver = None
    CHError = Exception
    CLICKHOUSE_AVAILABLE = False


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
        self.connect_timeout = ch_settings.get("CH_CONNECT_TIMEOUT", 10)
        self.send_timeout = ch_settings.get("CH_SEND_TIMEOUT", 300)
        self.receive_timeout = ch_settings.get("CH_RECEIVE_TIMEOUT", 300)

        # Thread-safe connection pool
        self._pool_size = int(ch_settings.get("CH_POOL_SIZE", 10))
        self._pool: queue.Queue = queue.Queue(maxsize=self._pool_size)
        self._pool_lock = threading.Lock()
        self._pool_initialized = False

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
            Tuple of (rows, column_types, query_time_ms)
        """
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
            query_settings = {**(settings or {}), "readonly": 2}
            if timeout_ms is not None:
                # max_execution_time is in seconds
                query_settings["max_execution_time"] = max(timeout_ms / 1000.0, 0.001)

        configured_transport_timeout = float(
            max(self.send_timeout, self.receive_timeout)
        )
        requested_transport_timeout = configured_transport_timeout
        if timeout_ms is not None:
            requested_transport_timeout = max(
                configured_transport_timeout,
                (timeout_ms / 1000.0) + _QUERY_TRANSPORT_GRACE_SECONDS,
            )
        # Pooled connections are created with the ordinary application
        # transport timeout. A long background exact query needs a matching
        # native socket envelope, but widening a pooled connection would leak
        # that policy into later API reads. Use and retire one dedicated
        # connection only when the requested query deadline exceeds the pool.
        dedicated_client = requested_transport_timeout > configured_transport_timeout
        client = (
            self._create_client(
                send_receive_timeout_seconds=requested_transport_timeout,
            )
            if dedicated_client
            else self._get_client()
        )
        t_start = time.monotonic()

        try:
            logger.debug(
                "Executing ClickHouse read query",
                query=query[:200],
                timeout_ms=timeout_ms,
            )
            retry_attempt = 0
            while True:
                try:
                    result = client.execute(
                        query,
                        params or {},
                        with_column_types=True,
                        settings=query_settings,
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

            logger.info(
                "ClickHouse read query completed",
                query=query[:200],
                query_time_ms=round(query_time_ms, 2),
                rows_returned=rows_returned,
                backend="clickhouse",
            )

            return rows, column_types, round(query_time_ms, 2)

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
            if dedicated_client:
                try:
                    client.disconnect()
                except Exception:
                    pass
            else:
                self._return_client(client)

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
