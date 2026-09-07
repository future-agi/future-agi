"""One native mutation on the same connection whose server was just verified.

This intentionally does not call Client.execute for the mutation: its connection
establishment step may reconnect after the identity read. The installed native
driver's protocol is used directly, without reconnect or retry, and the
exclusive connection is disconnected before it can return to the pool.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from contextlib import contextmanager
from time import monotonic
from typing import Any

from .write_admission import WriteMember

_IDENTITY_SQL = (
    "SELECT hostName(), toString(serverUUID()), currentDatabase(), currentUser()"
)
_READ_IDENTITY_SQL = (
    _IDENTITY_SQL
    + ", toUInt64(value), toUInt8(readonly) FROM system.settings WHERE name='readonly'"
)


class NativeWriteTransportError(RuntimeError):
    """Native catalog dispatch was not safely acknowledged."""


def insert_once(
    driver: Any,
    *,
    member: WriteMember,
    database: str,
    user: str,
    sql: str,
    values: list[tuple],
    query_id: str,
    settings: dict,
    timeout_ms: int,
    before_send: Callable[[], None],
) -> int:
    """Verify the actual connection, durably mark sent, then INSERT once.

    ``before_send`` must fsync the exact attempt before the first INSERT byte.
    Any exception after that point is uncertain; callers must not replay it.
    """
    return _dispatch_once(
        driver,
        member=member,
        database=database,
        user=user,
        sql=sql,
        query_id=query_id,
        settings=settings,
        timeout_ms=timeout_ms,
        before_send=before_send,
        method="process_insert_query",
        positional=(values,),
        arguments={},
    )


def ordinary_once(
    driver: Any,
    *,
    member: WriteMember,
    database: str,
    user: str,
    sql: str,
    query_id: str,
    settings: dict,
    timeout_ms: int,
    before_send: Callable[[], None],
) -> Any:
    """Dispatch one ordinary command after identity proof and durable intent.

    ``sql`` must already be the exact authorized command; no substitution or
    retry is performed. ``before_send`` must persist its intent before the first
    command byte. A failure after that point is uncertain, not proof of absence.
    The ordinary driver response is returned unchanged.
    """
    return _dispatch_once(
        driver,
        member=member,
        database=database,
        user=user,
        sql=sql,
        query_id=query_id,
        settings=settings,
        timeout_ms=timeout_ms,
        before_send=before_send,
        method="process_ordinary_query",
        positional=(),
        arguments={"params": None, "with_column_types": False},
    )


def _dispatch_once(
    driver,
    *,
    member,
    database,
    user,
    sql,
    query_id,
    settings,
    timeout_ms,
    before_send,
    method,
    positional,
    arguments,
):
    """Shared exclusive identity check, wall deadline and one protocol dispatch.

    The socket watchdog bounds the whole connected exchange, including a server
    that sends progress forever. Connect waits use the remaining deadline.
    """
    if (
        type(timeout_ms) is not int
        or not 1 <= timeout_ms <= 30_000
        or type(member) is not WriteMember
        or getattr(driver, "database", None) != database
        or getattr(driver, "user", None) != user
        or getattr(driver, "server_enforced_readonly", None) is not False
        or not callable(before_send)
    ):
        raise NativeWriteTransportError("native dispatch identity/deadline invalid")
    with _verified_connection(
        lambda remaining: driver.connection(),
        member=member,
        database=database,
        user=user,
        timeout_ms=timeout_ms,
        method=method,
    ) as (native, check):
        native.make_query_settings(dict(settings))
        check()
        before_send()
        check()
        return getattr(native, method)(
            sql,
            *positional,
            **arguments,
            external_tables=None,
            query_id=query_id,
            # Preserve the normal Enum/string codec. Journal/pinned columns
            # already validate input types; native codec errors remain fatal.
            types_check=False,
            columnar=False,
        )


@contextmanager
def _verified_connection(
    borrow,
    *,
    member,
    database,
    user,
    timeout_ms,
    method,
    readonly=False,
    error=NativeWriteTransportError,
):
    """Private shared lifetime; wrappers retain their distinct authority checks.

    ``borrow`` receives the shrinking wall, including SOURCE pool admission.
    The yielded check must run immediately before raw protocol dispatch, after
    any parameter binding, query settings or durable write-intent callback.
    """
    deadline = monotonic() + timeout_ms / 1000
    operation = "source read" if readonly else "catalog write"
    dispatch = "read" if readonly else "mutation"

    def remaining():
        seconds = deadline - monotonic()
        if seconds <= 0:
            raise TimeoutError(f"native {operation} deadline exhausted")
        return seconds

    with borrow(remaining) as native:
        connection = getattr(native, "connection", None)
        expired = threading.Event()
        pinned_channel = None

        def expire():
            expired.set()
            # Only this exclusive connection belongs to the attempt. Never use
            # KILL QUERY, a shared pool client, or a global connection timeout.
            channel = (
                pinned_channel
                if pinned_channel is not None
                else getattr(connection, "socket", None)
            )
            if channel is not None:
                try:
                    channel.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        original_connect_timeout = getattr(connection, "connect_timeout", None)
        original_send_receive_timeout = getattr(
            connection, "send_receive_timeout", None
        )
        watchdog = None
        watchdog_started = False
        try:
            if (
                getattr(connection, "connected", None) not in {True, False}
                or original_connect_timeout is None
                or original_send_receive_timeout is None
                or not callable(getattr(native, "make_query_settings", None))
                or not callable(getattr(native, method, None))
            ):
                raise error(f"unsupported native {dispatch} transport")
            if readonly and (
                getattr(native, "settings", None) != {}
                or len(getattr(native, "connections", (None,))) != 0
                or len(getattr(connection, "hosts", ())) != 1
            ):
                raise error("native read requires one settings-free direct connection")
            watchdog = threading.Timer(remaining(), expire)
            watchdog.daemon = True
            # Always disconnect and restore settings before returning to the
            # pool, including setup failures before the watchdog starts.
            # The adapter creates no extra worker doing the mutation.
            connection.connect_timeout = remaining()
            connection.send_receive_timeout = remaining()
            channel = getattr(connection, "socket", None)
            if channel is not None:
                channel.settimeout(remaining())
            watchdog.start()
            watchdog_started = True
            observed = native.execute(
                _READ_IDENTITY_SQL if readonly else _IDENTITY_SQL,
                {},
                settings=None
                if readonly
                else {
                    "readonly": 2,
                    "max_threads": 1,
                    "max_result_rows": 1,
                    "max_result_bytes": 4096,
                    "max_execution_time": remaining(),
                    "result_overflow_mode": "throw",
                    "timeout_overflow_mode": "throw",
                    "use_query_cache": 0,
                },
            )
            expected = (member.hostname, member.server_uuid, database, user)
            if readonly:
                expected += (1, 1)
            if observed != [expected]:
                raise error(f"native {dispatch} reached another identity")
            if native.connection is not connection:
                raise error("native identity connection changed")
            if connection.connected is not True or connection.socket is None:
                raise error("native identity connection was lost")
            pinned_channel = connection.socket
            pinned_channel.settimeout(remaining())

            def check():
                # send_query reconnects when disconnected. Only this thread
                # owns the client; the watchdog shuts down its socket without
                # changing connected. Recheck after callbacks, then invoke the
                # protocol directly without ping, reconnect or Client.execute.
                if (
                    native.connection is not connection
                    or connection.socket is not pinned_channel
                ):
                    raise error(f"native {dispatch} connection changed")
                if connection.connected is not True:
                    raise error(f"native {dispatch} connection was lost")
                remaining()
                if expired.is_set():
                    raise TimeoutError(f"native {operation} deadline exhausted")

            check()
            yield native, check
            # Writes retain their existing post-response deadline semantics;
            # SOURCE additionally checks its socket after receiving the rows.
            remaining()
            if expired.is_set():
                raise TimeoutError(f"native {operation} deadline exhausted")
        finally:
            if watchdog is not None:
                watchdog.cancel()
            # The callback only performs a nonblocking socket shutdown. Join it
            # before releasing this client, so it cannot hit a later borrower.
            if watchdog_started:
                watchdog.join()
            try:
                native.disconnect()
            finally:
                if original_connect_timeout is not None:
                    connection.connect_timeout = original_connect_timeout
                if original_send_receive_timeout is not None:
                    connection.send_receive_timeout = original_send_receive_timeout
