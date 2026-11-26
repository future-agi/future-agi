"""Shared admitted native-stream lifetime and transport deadline machinery."""

import time
from collections.abc import Iterator
from typing import Any

import structlog

from tracer.services.private_boundary_log import PrivateBoundaryLogger

# Keep the original component logger shared after extracting the stream helper.
logger = structlog.get_logger("tracer.services.clickhouse.client")
private_logger = PrivateBoundaryLogger("tracer.services.clickhouse.client")


class _ManagedNativeReadStream:
    """Own one admitted native read connection for an iterator's lifetime."""

    def __init__(
        self,
        owner,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int | None,
        block_size: int,
        query_settings: dict[str, Any] | None = None,
    ):
        self._owner = owner
        self._query = query
        self._params = params
        self._timeout_ms = timeout_ms
        self._block_size = block_size
        self._query_settings = query_settings
        self._deadline = 0.0
        self._admission_acquired = False
        # Duck-typed clickhouse-driver objects and their original settings.
        self._client: Any = None
        self._driver_connection: Any = None
        self._original_connect_timeout: Any = None
        self._original_send_receive_timeout: Any = None
        self._original_sync_request_timeout: Any = None
        self._original_disable_reconnect: Any = None
        self._socket: Any = None
        self._original_socket_timeout: Any = None
        self._original_socket_timeout_known = False
        self._exhausted = False

    def _remaining_seconds(self) -> float | None:
        if self._timeout_ms is None:
            return None
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
        self._original_sync_request_timeout = getattr(
            connection, "sync_request_timeout", None
        )
        self._original_disable_reconnect = getattr(
            connection, "disable_reconnect", None
        )
        socket = getattr(connection, "socket", None)
        if socket is not None:
            self._socket = socket
            self._original_socket_timeout = socket.gettimeout()
            self._original_socket_timeout_known = True
        else:
            # A lazy connection creates its socket during the first next().
            self._original_socket_timeout = self._original_send_receive_timeout
            self._original_socket_timeout_known = True

    def _clamp_transport(self) -> None:
        remaining = self._remaining_seconds()
        connection = self._driver_connection
        if connection is None:
            return
        if remaining is not None and self._original_connect_timeout is not None:
            connection.connect_timeout = self._narrow_timeout(
                self._original_connect_timeout, remaining
            )
        if remaining is not None:
            # force_connect() pings warm sockets using a separate timeout, then
            # normally reconnects on failure. Neither may reset this budget.
            if self._original_sync_request_timeout is not None:
                connection.sync_request_timeout = self._narrow_timeout(
                    self._original_sync_request_timeout, remaining
                )
            if self._original_disable_reconnect is not None:
                connection.disable_reconnect = True
        if remaining is None:
            connection.send_receive_timeout = None
        else:
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
                None
                if remaining is None
                else self._narrow_timeout(self._original_socket_timeout, remaining)
            )

    def _restore_transport(self) -> bool:
        connection = self._driver_connection
        if connection is None:
            return True
        restored = True
        if self._socket is not None and self._original_socket_timeout_known:
            try:
                self._socket.settimeout(self._original_socket_timeout)
            except Exception as exc:
                restored = False
                private_logger.exception(
                    "native_read_restore_failed", exc, setting="socket_timeout"
                )
        for name, original in (
            ("connect_timeout", self._original_connect_timeout),
            ("send_receive_timeout", self._original_send_receive_timeout),
            ("sync_request_timeout", self._original_sync_request_timeout),
            ("disable_reconnect", self._original_disable_reconnect),
        ):
            if original is None and name != "send_receive_timeout":
                continue
            try:
                setattr(connection, name, original)
            except Exception as exc:
                restored = False
                private_logger.exception(
                    "native_read_restore_failed", exc, setting=name
                )
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
        if self._timeout_ms is not None:
            self._deadline = time.monotonic() + (self._timeout_ms / 1000.0)
        try:
            self._admission_acquired = self._owner._read_admission.acquire(
                timeout=(
                    self._owner.read_timeout_ceiling_ms / 1000.0
                    if self._timeout_ms is None
                    else self._remaining_seconds()
                )
            )
            if not self._admission_acquired:
                raise TimeoutError("ClickHouse read admission deadline exhausted")
            self._remaining_seconds()
            self._client = self._owner._get_client()
            self._remaining_seconds()
            self._capture_transport()
            self._clamp_transport()
            kwargs = (
                {"settings": self._query_settings}
                if self._query_settings is not None
                else {}
            )
            rows = iter(self._client.execute_iter(self._query, self._params, **kwargs))
        except BaseException:
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
