"""Short-lived diagnostic connections, isolated from application reader pools."""

from time import monotonic

from tracer.services.clickhouse.client import ClickHouseClient


class HealthCheckClient(ClickHouseClient):
    """Reuse read-only query guardrails without changing ordinary read transport.

    Health probes use the same destination and credentials as their reader, but
    never borrow its sockets or admission slots. Each probe closes this client
    in a finally block, including after a timeout or an incomplete native packet.
    """

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
