"""Live ClickHouse proof that trace_writer's cached client serves concurrent threads.

``trace_writer`` caches one HTTP client per process for
``mirror_traces_to_clickhouse``. ``TraceView.update_tags`` mirrors on commit
inside the request thread, and the bulk Add tags popover sends its PATCHes in
parallel (TH-8026). A clickhouse-connect client that carries a session id
refuses a second in-flight query on that session ("Attempt to execute
concurrent queries within the same session"); the mirror swallows that error,
so the trace list would keep the old tags.

Reads only (``SELECT sleep``), no DDL and no DML. The target is the loopback
``test_*`` database conftest proves is the test sidecar.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest

from conftest import _open_ch_test_http_client
from tracer.services.clickhouse.v2 import trace_writer as writer

pytestmark = pytest.mark.integration

THREADS = 8
ROUNDS = 20


@pytest.fixture
def live_writer(monkeypatch):
    """Point trace_writer's cached HTTP client at the proven test ClickHouse."""
    pytest.importorskip("clickhouse_connect")
    database = os.getenv("CH25_DATABASE") or ""
    if not database.lower().lstrip("_").startswith("test_"):
        pytest.skip("test ClickHouse database is not a test_* database")
    probe = _open_ch_test_http_client(database=database, connect_timeout=5)
    try:
        target = urlparse(probe.url)
    finally:
        probe.close()
    monkeypatch.setattr(
        writer,
        "get_v2_config",
        lambda: {
            "host": target.hostname,
            "http_port": target.port,
            "tcp_port": 0,
            "user": os.getenv("CH25_USER") or "default",
            "password": os.getenv("CH25_PASSWORD") or "",
            "database": database,
            "server_enforced_readonly": False,
        },
    )
    monkeypatch.setattr(writer, "_client", None)
    yield writer
    writer._reset_client()


def test_cached_client_serves_concurrent_request_threads(live_writer):
    barrier = threading.Barrier(THREADS)
    errors: list[BaseException] = []

    def queries():
        done = 0
        for _ in range(ROUNDS):
            barrier.wait(timeout=30)
            try:
                live_writer._get_client().query("SELECT sleep(0.01)")
                done += 1
            except Exception as exc:  # collected so every thread keeps its rounds
                errors.append(exc)
        return done

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = [pool.submit(queries) for _ in range(THREADS)]
        done = sum(future.result() for future in futures)

    assert errors == []
    assert done == THREADS * ROUNDS
