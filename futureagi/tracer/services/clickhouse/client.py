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

logger = structlog.get_logger(__name__)

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
    ):
        """
        Initialize ClickHouse client with connection settings.

        If parameters are not provided, they are read from Django settings.
        """
        ch_settings = getattr(settings, "CLICKHOUSE", {})

        self.host = host or ch_settings.get("CH_HOST")
        self.port = int(port or ch_settings.get("CH_PORT", 9000))
        self.user = user or ch_settings.get("CH_USERNAME", "default")
        self.password = password or ch_settings.get("CH_PASSWORD", "")
        self.database = database or ch_settings.get("CH_DATABASE", "default")

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

    def _create_client(self) -> CHDriver:
        """Create a new ClickHouse driver connection."""
        if not CLICKHOUSE_AVAILABLE:
            raise RuntimeError(
                "clickhouse-driver is not installed. "
                "Install it with: pip install clickhouse-driver"
            )
        if not self.host:
            raise ValueError("ClickHouse host is not configured")

        return CHDriver(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            connect_timeout=self.connect_timeout,
            send_receive_timeout=max(self.send_timeout, self.receive_timeout),
            settings={
                "use_numpy": False,
                "max_block_size": 100000,
            },
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
        client = self._get_client()
        t_start = time.monotonic()

        query_settings = {**(settings or {}), "readonly": 2}
        if timeout_ms is not None:
            # max_execution_time is in seconds
            query_settings["max_execution_time"] = max(timeout_ms / 1000.0, 0.001)

        try:
            logger.debug(
                "Executing ClickHouse read query",
                query=query[:200],
                timeout_ms=timeout_ms,
            )
            result = client.execute(
                query,
                params or {},
                with_column_types=True,
                settings=query_settings,
            )

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
