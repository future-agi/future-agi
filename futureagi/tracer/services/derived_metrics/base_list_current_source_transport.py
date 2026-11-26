"""Request-owned no-replay gate around the existing clickhouse-connect transport.

clickhouse-connect 1.1.1 can silently retry a remotely closed INSERT, even with
query_retries=0, and its ordinary INSERT return does not reject a late exception
body. Neither behavior is terminal source evidence. Do not mutate its shared
client/pool: this lane leases a private pool using the SAME existing config.
No new endpoint, credentials, background worker or source writer is introduced.
"""

from contextlib import contextmanager

import clickhouse_connect
from urllib3 import PoolManager

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.derived_metrics.base_list_current_source import SourceUnavailable


class _OneShotPool(PoolManager):
    """All underlying HTTP retries/redirects disabled for the armed INSERT."""

    def __init__(self):
        super().__init__(num_pools=1, maxsize=1)
        self.expected_query_id = None
        self.started = False
        self.terminal_query_id = None

    def arm(self, query_id):
        if self.started or self.expected_query_id is not None:
            raise SourceUnavailable("source transport already used")
        self.expected_query_id = query_id

    def disarm(self):
        if self.terminal_query_id != self.expected_query_id or not self.started:
            raise SourceUnavailable("source transport lacks terminal acknowledgement")
        self.expected_query_id = None

    def request(self, method, url, *args, **kwargs):
        if self.expected_query_id is None:
            return super().request(method, url, *args, **kwargs)
        if self.started:
            # The driver may call again after RemoteDisconnected. Do not send.
            raise SourceUnavailable("ambiguous source send cannot be replayed")
        self.started = True
        kwargs.update(retries=False, redirect=False, preload_content=False)
        response = super().request(method, url, *args, **kwargs)
        try:
            body = response.read(4097)
            if (
                response.status != 200
                or body
                or response.headers.get("X-ClickHouse-Exception-Code")
                or response.headers.get("X-ClickHouse-Query-Id")
                != self.expected_query_id
                or response.length_remaining not in (None, 0)
            ):
                raise SourceUnavailable(
                    "source insert response is not exact terminal success"
                )
            self.terminal_query_id = self.expected_query_id
            return response
        finally:
            response.close()
            response.release_conn()


@contextmanager
def lease_trace_source_client():
    """Exclusive existing-driver client. Never change the cached native client."""
    pool = _OneShotPool()
    client = None
    try:
        cfg = get_v2_config()
        client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=15,
            connect_timeout=10,
            pool_mgr=pool,
        )
        yield client, pool
    finally:
        if client is not None:
            client.close()
        pool.clear()
