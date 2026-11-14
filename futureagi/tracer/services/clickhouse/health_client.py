"""Short-lived diagnostic connections, isolated from application reader pools."""

from time import monotonic

from tracer.services.clickhouse.client import ClickHouseClient

# TIMEOUT_EXCEEDED, TOO_SLOW and SOCKET_TIMEOUT: the statement ran out of time.
_TIMEOUT_ERROR_CODES = frozenset({159, 160, 209})
_ACCESS_DENIED_ERROR_CODE = 497


def is_timeout(exc: BaseException) -> bool:
    """Whether a diagnostic read ran out of time rather than being refused."""
    return isinstance(exc, TimeoutError) or (
        getattr(exc, "code", None) in _TIMEOUT_ERROR_CODES
    )


def is_access_denied(exc: BaseException) -> bool:
    """Whether the server refused a read for missing privileges (ACCESS_DENIED)."""
    return getattr(exc, "code", None) == _ACCESS_DENIED_ERROR_CODE


class HealthCheckClient(ClickHouseClient):
    """Reuse read-only query guardrails without changing ordinary read transport.

    Health probes use the same destination and credentials as their reader, but
    never borrow its sockets or admission slots. One instance serves one health
    check for one destination: its statements share a single connection and
    native handshake, and the check closes it in a finally block, including
    after a timeout or an incomplete native packet.
    """

    # Set once the server answers the native handshake. A later timeout then
    # means the server is slow, not that it is unreachable.
    handshake_completed = False

    @classmethod
    def from_client(cls, source: ClickHouseClient, *, timeout_ms: int):
        seconds = timeout_ms / 1000.0
        return cls(
            host=source.host,
            port=source.port,
            user=source.user,
            password=source.password,
            database=source.database,
            server_enforced_readonly=source.server_enforced_readonly,
            allow_query_settings_with_server_readonly=(
                source.allow_query_settings_with_server_readonly
            ),
            connect_timeout=min(source.connect_timeout, seconds),
            send_timeout=min(source.send_timeout, seconds),
            receive_timeout=min(source.receive_timeout, seconds),
            pool_size=1,
            read_timeout_ceiling_ms=timeout_ms,
        )

    def _create_client(self, **kwargs):
        driver = super()._create_client(**kwargs)
        connection = driver.connection
        receive_hello = connection.receive_hello

        def receive_hello_and_record():
            receive_hello()
            self.handshake_completed = True

        connection.receive_hello = receive_hello_and_record
        return driver

    @staticmethod
    def _execute_native_read_with_remaining_timeout(
        client, query, params, *, query_settings, started_at, timeout_ms
    ):
        # Unlike application reads, a diagnostic must always have a wall budget.
        if timeout_ms is None:
            raise ValueError("ClickHouse health reads require a timeout")
        deadline = started_at + timeout_ms / 1000.0
        connection = client.connection

        def remaining():
            seconds = deadline - monotonic()
            if seconds <= 0:
                raise TimeoutError("ClickHouse health check deadline exhausted")
            return seconds

        # force_connect() pings an already connected native driver, temporarily
        # replacing the socket timeout with this independent setting.
        original_sync_timeout = connection.sync_request_timeout
        original_receive_packet = connection.receive_packet
        # That ping is one more network round trip per statement. A health
        # connection serves one check, back to back, and is never idle in a
        # pool, so a closed socket fails the statement itself. Skip the ping.
        original_ping = getattr(connection, "ping", None)

        def receive_packet():
            # Progress packets must not renew the socket's entire idle timeout.
            # This also bounds readonly=1 profiles that forbid query settings.
            connection.socket.settimeout(remaining())
            packet = original_receive_packet()
            remaining()
            return packet

        try:
            connection.sync_request_timeout = min(original_sync_timeout, remaining())
            connection.receive_packet = receive_packet
            if original_ping is not None:
                connection.ping = lambda: True
            result = ClickHouseClient._execute_native_read_with_remaining_timeout(
                client,
                query,
                params,
                query_settings=query_settings,
                started_at=started_at,
                timeout_ms=timeout_ms,
            )
            remaining()
            return result
        finally:
            connection.sync_request_timeout = original_sync_timeout
            connection.receive_packet = original_receive_packet
            if original_ping is not None:
                connection.ping = original_ping
