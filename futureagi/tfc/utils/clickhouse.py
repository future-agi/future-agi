import threading
import time
import weakref

import structlog
from clickhouse_driver import Client, errors

from tfc.settings import settings

logger = structlog.get_logger(__name__)

_APPLICATION_READ_TIMEOUT_MS = 9_500
_APPLICATION_READ_MAX_BYTES = 36 * 1024 * 1024 * 1024
_APPLICATION_READ_MAX_RESULT_ROWS = 100_000
_APPLICATION_READ_MAX_RESULT_BYTES = 64 * 1024 * 1024
_APPLICATION_READ_ADMISSION_SLOTS = 4
_APPLICATION_READ_ADMISSION = threading.BoundedSemaphore(
    _APPLICATION_READ_ADMISSION_SLOTS
)
_NATIVE_CLIENT_LOCKS_GUARD = threading.Lock()
_NATIVE_CLIENT_LOCKS = weakref.WeakKeyDictionary()
_UNWEAKREFABLE_NATIVE_CLIENT_LOCK = threading.Lock()


def _native_client_read_lock(client):
    """Return the process-wide read mutex for one native driver client."""

    with _NATIVE_CLIENT_LOCKS_GUARD:
        try:
            lock = _NATIVE_CLIENT_LOCKS.get(client)
        except TypeError:
            return _UNWEAKREFABLE_NATIVE_CLIENT_LOCK
        if lock is None:
            lock = threading.Lock()
            _NATIVE_CLIENT_LOCKS[client] = lock
        return lock


def _normalized_read_settings(
    settings=None,
    *,
    timeout_ms=None,
    max_result_rows=None,
    max_result_bytes=None,
):
    """Build the finite ordinary-read envelope for the legacy native client."""

    normalized = dict(settings or {})
    requested_timeout = normalized.get("max_execution_time")
    requested_timeout_ms = (
        _APPLICATION_READ_TIMEOUT_MS
        if requested_timeout is None or float(requested_timeout) <= 0
        else max(1, int(float(requested_timeout) * 1000))
    )
    timeout_ms = (
        requested_timeout_ms
        if timeout_ms is None
        else min(int(timeout_ms), requested_timeout_ms)
    )
    if max_result_rows is None:
        max_result_rows = normalized.get(
            "max_result_rows",
            _APPLICATION_READ_MAX_RESULT_ROWS,
        )
    if max_result_bytes is None:
        max_result_bytes = normalized.get(
            "max_result_bytes",
            _APPLICATION_READ_MAX_RESULT_BYTES,
        )
    timeout_ms = int(timeout_ms)
    max_result_rows = int(max_result_rows)
    max_result_bytes = int(max_result_bytes)
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    if max_result_rows <= 0:
        raise ValueError("max_result_rows must be positive")
    if max_result_bytes <= 0:
        raise ValueError("max_result_bytes must be positive")

    normalized.pop("max_rows_to_read", None)
    normalized["readonly"] = 2
    normalized["max_execution_time"] = (
        min(
            timeout_ms,
            _APPLICATION_READ_TIMEOUT_MS,
        )
        / 1000.0
    )
    normalized["max_memory_usage"] = min(
        int(normalized.get("max_memory_usage", 0) or _APPLICATION_READ_MAX_BYTES),
        _APPLICATION_READ_MAX_BYTES,
    )
    normalized["max_bytes_to_read"] = min(
        int(normalized.get("max_bytes_to_read", 0) or _APPLICATION_READ_MAX_BYTES),
        _APPLICATION_READ_MAX_BYTES,
    )
    normalized["max_threads"] = max(
        1,
        min(int(normalized.get("max_threads", 1)), 4),
    )
    normalized["max_result_rows"] = min(
        max_result_rows,
        _APPLICATION_READ_MAX_RESULT_ROWS,
    )
    normalized["max_result_bytes"] = min(
        max_result_bytes,
        _APPLICATION_READ_MAX_RESULT_BYTES,
    )
    normalized["read_overflow_mode"] = "throw"
    normalized["result_overflow_mode"] = "throw"
    normalized["timeout_overflow_mode"] = "throw"
    return normalized


def _is_read_statement(query):
    """Use the shared SQL lexer so legacy ``execute`` cannot bypass read policy."""

    from tracer.services.clickhouse.server_readonly import (
        _MUTATION_STATEMENTS,
        _top_level_tokens,
        ensure_read_statement,
    )

    tokens = [token for token, _, _ in _top_level_tokens(query)]
    first_token = tokens[0] if tokens else ""
    if first_token in _MUTATION_STATEMENTS:
        return False
    # A query that looks like a read but fails the shared lexer (for example a
    # multi-statement SELECT or WITH ... DELETE) is invalid, not a mutation to
    # route through the legacy write-capable transport.
    ensure_read_statement(query)
    return True


class ClickHouseClientSingleton:
    _instance = None
    _client = None

    # def __new__(cls):
    #     if cls._instance is None:
    #         cls._instance = super(ClickHouseClientSingleton, cls).__new__(cls)
    #         cls._instance.initialize_client()
    #     return cls._instance

    def __init__(self) -> None:
        self.initialize_client()

    def initialize_client(self):
        # Reads clamp this client's lazy connection inside their deadline.
        self._client = Client(
            host=settings.CLICKHOUSE["CH_HOST"],
            port=settings.CLICKHOUSE["CH_PORT"],
            user=settings.CLICKHOUSE["CH_USERNAME"],
            password=settings.CLICKHOUSE["CH_PASSWORD"],
            database=settings.CLICKHOUSE["CH_DATABASE"],
        )

    def _execute_guarded_read(self, query, params, query_settings):
        """Run one legacy SELECT inside one admission/transport wall."""

        timeout_seconds = float(query_settings["max_execution_time"])
        deadline = time.monotonic() + timeout_seconds
        gate = _APPLICATION_READ_ADMISSION
        acquired = gate.acquire(timeout=timeout_seconds)
        if not acquired:
            raise TimeoutError("ClickHouse read admission deadline exhausted")
        client_lock = None
        client_lock_acquired = False
        connection = None
        socket = None
        original_connect_timeout = None
        original_send_receive_timeout = None
        original_socket_timeout = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ClickHouse read deadline exhausted")
            if not self._client:
                self.initialize_client()
            client = self._client
            client_lock = _native_client_read_lock(client)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not client_lock.acquire(timeout=remaining):
                raise TimeoutError("ClickHouse client read deadline exhausted")
            client_lock_acquired = True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ClickHouse read deadline exhausted")
            connection = getattr(client, "connection", None)
            if connection is not None:
                connected = getattr(connection, "connected", None)
                original_connect_timeout = getattr(connection, "connect_timeout", None)
                original_send_receive_timeout = getattr(
                    connection, "send_receive_timeout", None
                )
                socket = getattr(connection, "socket", None)
                if socket is not None:
                    original_socket_timeout = socket.gettimeout()
                if original_connect_timeout is not None:
                    connection.connect_timeout = min(
                        float(original_connect_timeout), remaining
                    )
                if original_send_receive_timeout is not None:
                    connection.send_receive_timeout = min(
                        float(original_send_receive_timeout), remaining
                    )
                if connected is False:
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
                    if original_socket_timeout is None:
                        original_socket_timeout = original_send_receive_timeout
                    socket.settimeout(remaining)
            query_settings = dict(query_settings)
            query_settings["max_execution_time"] = min(
                float(query_settings["max_execution_time"]), remaining
            )
            return client.execute(query, params, settings=query_settings)
        finally:
            if socket is not None and original_socket_timeout is not None:
                try:
                    socket.settimeout(original_socket_timeout)
                except Exception:
                    pass
            if connection is not None:
                if original_connect_timeout is not None:
                    connection.connect_timeout = original_connect_timeout
                if original_send_receive_timeout is not None:
                    connection.send_receive_timeout = original_send_receive_timeout
            if client_lock_acquired:
                client_lock.release()
            gate.release()

    @property
    def client(self):
        # Health check before returning the client
        if not self.is_connection_alive():
            self.reconnect()
        return self._client
        # return self._instance.client

    def is_connection_alive(self):
        try:
            # You can use a basic query as a health check
            if not self._client:
                return False
            start_time = time.time()
            self._execute_guarded_read(
                "SELECT 1",
                None,
                _normalized_read_settings(
                    timeout_ms=1_000,
                    max_result_rows=1,
                ),
            )
            end_time = time.time()
            logger.debug(
                "Health check query execution time: %.3f seconds", end_time - start_time
            )
            return True
        except errors.NetworkError:
            return False

    def reconnect(self):
        attempts = 3
        for _ in range(attempts):
            try:
                self.initialize_client()
                return
            except errors.NetworkError:
                time.sleep(1)  # Wait for a second before retrying
        raise ConnectionError(
            "Could not reconnect to ClickHouse after multiple attempts"
        )

    def execute(self, query, params=None, *, settings=None):
        logger.debug("Executing query %s", query)
        logger.debug("Params of query %s", params)
        if _is_read_statement(query):
            return self._execute_guarded_read(
                query,
                params,
                _normalized_read_settings(settings),
            )
        start_time = time.time()
        try:
            if not self._client:
                self.initialize_client()
            result = (
                self._client.execute(query, params)
                if settings is None
                else self._client.execute(query, params, settings=settings)
            )
            end_time = time.time()
            logger.debug("Query execution time: %.3f seconds", end_time - start_time)
            return result
        except errors.NetworkError as e:
            # Handle connection error, try to reconnect and re-execute the query
            self.reconnect()
            if not self._client:
                raise ConnectionError(
                    "Failed to establish ClickHouse connection"
                ) from e
            result = (
                self._client.execute(query, params)
                if settings is None
                else self._client.execute(query, params, settings=settings)
            )
            end_time = time.time()
            logger.debug(
                "Query execution time (after reconnect): %.3f seconds",
                end_time - start_time,
            )
            return result
        except Exception as e:
            # Handle or log other exceptions if necessary
            end_time = time.time()
            logger.exception("Exception while executing query %s", query)
            logger.debug("Params of query %s", params)
            logger.debug(
                "Query execution time (failed): %.3f seconds", end_time - start_time
            )
            raise e

    def execute_read(
        self,
        query,
        params=None,
        *,
        timeout_ms=_APPLICATION_READ_TIMEOUT_MS,
        settings=None,
        max_result_rows=_APPLICATION_READ_MAX_RESULT_ROWS,
        max_result_bytes=_APPLICATION_READ_MAX_RESULT_BYTES,
    ):
        """Execute one read with the same finite policy as analytics reads."""

        from tracer.services.clickhouse.server_readonly import ensure_read_statement

        ensure_read_statement(query)
        return self._execute_guarded_read(
            query,
            params,
            _normalized_read_settings(
                settings,
                timeout_ms=timeout_ms,
                max_result_rows=max_result_rows,
                max_result_bytes=max_result_bytes,
            ),
        )

    def close(self):
        """Close the ClickHouse connection"""
        if self._client:
            try:
                self._client.disconnect()
            except Exception as e:
                logger.warning(f"Error while closing ClickHouse connection: {e}")
            finally:
                self._client = None

    def __del__(self):
        """Ensure connection is closed when object is destroyed"""
        self.close()

    def execute_paginated(self, query, params=None, page=1, page_size=10):
        page = int(page)
        page_size = int(page_size)
        if page <= 0 or page_size <= 0:
            raise ValueError("page and page_size must be positive")
        offset = (page - 1) * page_size
        paginated_query = f"{query} LIMIT {page_size} OFFSET {offset}"

        # Get the total count
        count_query = f"SELECT count() FROM ({query})"
        total_records = self.execute_read(
            count_query,
            params,
            max_result_rows=1,
        )[0][0]
        total_pages = -(-total_records // page_size)  # Ceiling division

        result = self.execute_read(
            paginated_query,
            params,
            max_result_rows=page_size,
        )
        return result, total_pages
