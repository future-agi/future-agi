"""Live ClickHouse proof that the cached end-user reader serves concurrent threads.

``end_user_dict_reader`` caches one HTTP client per process, and the API's
request threads share it. A clickhouse-connect client that carries a session
id refuses a second in-flight query on that session ("Attempt to execute
concurrent queries within the same session"), which surfaced as intermittent
500s from the Sessions list when several pages loaded at once.

Reads only: ``dictGetOrNull`` over random ids, no DDL and no DML. The target is
the loopback ``test_*`` database conftest proves is the test sidecar.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest

from conftest import _open_ch_test_http_client
from tracer.services.clickhouse.v2 import end_user_dict_reader as reader

pytestmark = pytest.mark.integration

THREADS = 8
ROUNDS = 20


@pytest.fixture
def live_reader(monkeypatch):
    """Point the reader's cached HTTP client at the proven test ClickHouse."""
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
        reader,
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
    monkeypatch.setattr(reader, "_client", None)
    yield reader
    reader._reset_client()


def test_cached_client_serves_concurrent_request_threads(live_reader):
    end_user_id = str(uuid.uuid4())
    barrier = threading.Barrier(THREADS)
    errors: list[BaseException] = []

    def lookups():
        results = []
        for _ in range(ROUNDS):
            barrier.wait(timeout=30)
            try:
                results.append(live_reader.resolve_end_user_fields([end_user_id]))
            except Exception as exc:  # collected so every thread keeps its rounds
                errors.append(exc)
        return results

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = [pool.submit(lookups) for _ in range(THREADS)]
        results = [result for future in futures for result in future.result()]

    assert errors == []
    missing = {"user_id": None, "user_id_type": None, "user_id_hash": None}
    assert results == [{end_user_id: missing}] * (THREADS * ROUNDS)
