"""Request-local transport admission for background analysis, inert otherwise.

No Django import at module load: shared clients can use this in OSS/interactive
processes. Never mutate a shared client's adapters, timeouts or retry policy.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_guard = ContextVar("background_analysis_transport", default=None)


def current_guard():
    return _guard.get()


@contextmanager
def analysis_transport_scope(guard):
    # A nested call with no explicit context must not clear its parent's guard.
    token = _guard.set(guard if guard is not None else current_guard())
    try:
        yield
    finally:
        _guard.reset(token)


def _budget(guard, timeout):
    remaining = guard.remaining
    if remaining <= 0:
        guard.abort("analysis_deadline_exceeded")
    if isinstance(timeout, tuple):
        return tuple(min(t, remaining) if t is not None else remaining for t in timeout)
    return min(timeout, remaining) if timeout is not None else remaining


def before_send(timeout=None):
    guard = current_guard()
    if guard is None:
        return timeout
    guard.check()
    return _budget(guard, timeout)


async def before_async_send(timeout=None):
    guard = current_guard()
    if guard is None:
        return timeout
    await guard.checkpoint()
    return _budget(guard, timeout)


def _cap_httpx(request, timeout):
    limits = request.extensions.setdefault("timeout", {})
    for key in ("connect", "read", "write", "pool"):
        value = limits.get(key)
        limits[key] = min(value, timeout) if value is not None else timeout


def _admission_event(name):
    # httpcore traces run AFTER pool/connect/TLS waits and BEFORE header/body
    # sends. Do not throw during response_closed cleanup after an abort.
    return name.endswith(".started") and any(
        part in name
        for part in ("send_", "connect_", "start_tls", "receive_response_headers")
    )


async def _httpx_request(request):
    _cap_httpx(request, await before_async_send())

    async def trace(name, info):
        if _admission_event(name):
            remaining = await before_async_send()
            _cap_httpx(request, remaining)
            if "timeout" in info:
                info["timeout"] = (
                    min(info["timeout"], remaining)
                    if info["timeout"] is not None
                    else remaining
                )

    request.extensions["trace"] = trace


def _sync_httpx_request(request):
    _cap_httpx(request, before_send())

    def trace(name, info):
        if _admission_event(name):
            remaining = before_send()
            _cap_httpx(request, remaining)
            if "timeout" in info:
                info["timeout"] = (
                    min(info["timeout"], remaining)
                    if info["timeout"] is not None
                    else remaining
                )

    request.extensions["trace"] = trace


def httpx_post(url, **kwargs):
    import httpx

    if current_guard() is None:
        return httpx.post(url, **kwargs)
    # No transport retries; admission also runs on httpcore's wire-send events.
    with httpx.Client(event_hooks={"request": [_sync_httpx_request]}) as client:
        return client.post(url, **kwargs)


def sync_client_options():
    """Instance-local request and httpcore wire admission for owned clients."""
    if current_guard() is None:
        return {}
    return {"event_hooks": {"request": [_sync_httpx_request]}}


def async_client_options():
    if current_guard() is None:
        return {}
    return {"event_hooks": {"request": [_httpx_request]}}


def _guarded_pool_classes():
    from urllib3.connection import HTTPConnection, HTTPSConnection
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

    class GuardedConnection:
        def send(self, data):
            # http.client.send can connect internally. Recheck after that wait,
            # immediately before sending bytes (including proxy CONNECT bodies).
            self.timeout = before_send(self.timeout)
            if self.sock is None:
                self.connect()
            self.sock.settimeout(before_send(self.timeout))
            return super().send(data)

    class HTTP(GuardedConnection, HTTPConnection):
        pass

    class HTTPS(GuardedConnection, HTTPSConnection):
        pass

    class HTTPPool(HTTPConnectionPool):
        ConnectionCls = HTTP

    class HTTPSPool(HTTPSConnectionPool):
        ConnectionCls = HTTPS

    return {"http": HTTPPool, "https": HTTPSPool}


def guarded_http_pool():
    """Private ClickHouse HTTP pool: bootstrap, redirects and retries all gated."""
    from urllib3 import PoolManager
    from urllib3.util import Timeout

    guard = current_guard()

    class GuardedPool(PoolManager):
        def urlopen(self, method, url, **kwargs):
            with analysis_transport_scope(guard):
                remaining = before_send()
                configured = kwargs.get("timeout")
                connect = read = remaining
                if isinstance(configured, Timeout):
                    connect = _budget(guard, configured.connect_timeout)
                    read = _budget(guard, configured.read_timeout)
                kwargs["timeout"] = Timeout(total=remaining, connect=connect, read=read)
                kwargs["retries"] = 0
                return super().urlopen(method, url, **kwargs)

    pool = GuardedPool()
    pool.pool_classes_by_scheme = _guarded_pool_classes()
    return pool


def requests_send(session, method, url, **kwargs):
    """One guarded adapter send per attempt, including redirects; zero retries."""
    import requests
    from requests.adapters import HTTPAdapter

    if current_guard() is None:
        return getattr(session if session is not None else requests, method)(
            url, **kwargs
        )
    pools = _guarded_pool_classes()

    class GuardedAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **options):
            super().init_poolmanager(*args, **options)
            self.poolmanager.pool_classes_by_scheme = pools

        def proxy_manager_for(self, *args, **options):
            manager = super().proxy_manager_for(*args, **options)
            manager.pool_classes_by_scheme = pools
            return manager

        def send(self, request, **options):
            options["timeout"] = before_send(options.get("timeout"))
            return super().send(request, **options)

    # A cached serving client may already have retrying adapters. Do not alter it
    # (other threads may be interactive). This short-lived client has no retries.
    with requests.Session() as guarded:
        adapter = GuardedAdapter(max_retries=0)
        guarded.mount("http://", adapter)
        guarded.mount("https://", adapter)
        if session is not None:
            guarded.headers.update(session.headers)
        kwargs["timeout"] = before_send(kwargs.get("timeout"))
        return getattr(guarded, method)(url, **kwargs)


# Ruling 298: native DATA admission is per block of at most 64 rows, including
# external tables. This is a ROW bound, not a serialized-byte or wall-time bound.
# An admitted block may finish serialization/flushes; every next block rechecks.
NATIVE_CHUNK_ROWS = 64


def _native_blocks(block):
    from clickhouse_driver.block import ColumnOrientedBlock

    if block.num_rows <= NATIVE_CHUNK_ROWS:
        yield block  # Includes the protocol's empty end-of-data block.
        return
    for start in range(0, block.num_rows, NATIVE_CHUNK_ROWS):
        end = start + NATIVE_CHUNK_ROWS
        data = (
            [column[start:end] for column in block.data]
            if isinstance(block, ColumnOrientedBlock)
            else block.data[start:end]
        )
        yield type(block)(
            block.columns_with_types,
            data,
            info=block.info,
            types_check=block.types_check,
        )


def guard_native_client(client):
    """Install on a PRIVATE ClickHouse client only, never the shared Django one.

    The driver may connect/ping while execute is preparing. Gate actual query
    and insert-block sends after that wait, not just the caller's execute().
    Reconnection is disabled; no retry may reuse admitted query bytes.
    """
    guard = current_guard()
    if guard is None:
        return client
    client._analysis_transport_guard = guard
    connection = client.connection
    connection.disable_reconnect = True

    def wrap(send):
        def guarded(*args, **kwargs):
            with analysis_transport_scope(guard):
                # send_query can connect internally. Complete that blocking
                # handshake first, then authorize the actual query bytes.
                if send.__name__ == "send_query" and not connection.connected:
                    before_send()
                    connection.connect()
                timeout = before_send()
                connection.connect_timeout = _budget(guard, connection.connect_timeout)
                connection.send_receive_timeout = _budget(
                    guard, connection.send_receive_timeout
                )
                connection.sync_request_timeout = _budget(
                    guard, connection.sync_request_timeout
                )
                sync_timeout = connection.sync_request_timeout
                if connection.socket is not None:
                    # A caller may have installed a tighter per-read wall (for
                    # example the vector existence read's 9.5 seconds). Neither
                    # the analysis budget nor a driver default may widen it.
                    operation_timeout = (
                        sync_timeout
                        if send.__name__ == "ping"
                        else connection.send_receive_timeout
                    )
                    socket_timeout = connection.socket.gettimeout()
                    timeout = min(
                        timeout,
                        operation_timeout,
                        socket_timeout if socket_timeout is not None else timeout,
                    )
                    connection.socket.settimeout(timeout)
                    if send.__name__ == "ping":
                        # The real driver installs sync_request_timeout inside
                        # ping(); cap that temporary override as well.
                        connection.sync_request_timeout = timeout
                try:
                    return send(*args, **kwargs)
                finally:
                    connection.sync_request_timeout = sync_timeout

        def chunked(block, table_name=""):
            with analysis_transport_scope(guard):
                before_send()
                for piece in _native_blocks(block):
                    # Fresh access/budget/timeout admission for EACH bounded
                    # block, including direct calls and named external tables.
                    guarded(piece, table_name=table_name)

        return chunked if send.__name__ == "send_data" else guarded

    # Keep the driver's insert loop in charge of profile-event acknowledgments:
    # one response is consumed per data block on modern protocol revisions.
    # Cap at use time, so per-query settings cannot widen the block size. The
    # connection wrapper below additionally bounds direct/external-table blocks.
    send_insert = client.send_data

    def insert(*args, **kwargs):
        with analysis_transport_scope(guard):
            before_send()
            settings = connection.context.client_settings
            connection.context.client_settings = {
                **settings,
                "insert_block_size": min(
                    settings["insert_block_size"], NATIVE_CHUNK_ROWS
                ),
            }
            try:
                return send_insert(*args, **kwargs)
            finally:
                connection.context.client_settings = settings

    client.send_data = insert

    # Protocol pings and handshake are reads, but also get a bounded live gate.
    for name in (
        "send_query",
        "send_data",
        "send_external_tables",
        "send_hello",
        "send_addendum",
        "send_cancel",
        "ping",
    ):
        setattr(connection, name, wrap(getattr(connection, name)))
    connection.connect_timeout = before_send(connection.connect_timeout)
    connection.send_receive_timeout = before_send(connection.send_receive_timeout)
    return client
