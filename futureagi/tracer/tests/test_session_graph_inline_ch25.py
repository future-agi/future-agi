"""Real-ClickHouse proof of the inline Sessions latency path.

``test_session_graph_inline`` pins the inline decision with a recording fake;
this module runs it through the real request-side ``AnalyticsQueryService``
(its own native ``ClickHouseClient`` and read policy) and the real exact
worker (``_exact_observe_analytics``) against the test sidecar, and reads what
the server saw from ``system.query_log``:

* every lean shape - window only, a post-aggregate filter (duration, trace
  count) and session-id ``in`` / ``not_in`` - is computed inline, enqueues
  nothing, and publishes exactly the numbers the worker publishes for the
  same scope;
* the inline statement reached ClickHouse with one thread and a server cap of
  at most ``SESSION_GRAPH_INLINE_WALL_MS``; the worker's with its own thread
  budget and what was left of ``GRAPH_BACKGROUND_WALL_MS``;
* with a complete snapshot cached for the scope, the request serves it and
  sends no estimate and no inline read (the cache is looked at first).
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import override_settings

from tracer.tests import test_exact_worker_server_cap_ch25 as _worker_cap
from tracer.tests.test_exact_worker_server_cap_ch25 import (
    HI,
    ORGANIZATION_ID,
    PROJECT_ID,
    _marker,
    _uid,
    _window,
)

# The worker-cap module's seeded sidecar database and production worker.
store = _worker_cap.store
worker = _worker_cap.worker

pytestmark = pytest.mark.integration


def _system(column, filter_type, op, value):
    return {
        "column_id": column,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": filter_type,
            "filter_op": op,
            "filter_value": value,
        },
    }


# Each shape gets its own window end, so its statements are told apart in
# ``system.query_log`` by the bound they carry.
LEAN_SHAPES = {
    "window": [],
    "duration": [_system("duration", "number", "greater_than", 0)],
    "traces_count": [_system("traces_count", "number", "greater_than", 2)],
    "session_id_in": [
        _system(
            "session_id",
            "text",
            "in",
            [_uid("session", index) for index in (3, 40, 41, 299)],
        )
    ],
    "session_id_not_in": [
        _system("session_id", "text", "not_in", [_uid("session", 7)])
    ],
}


def _hi(shape):
    return HI - timedelta(minutes=10 * (1 + list(LEAN_SHAPES).index(shape)))


def _filters(shape, hi):
    return [_window(hi), *LEAN_SHAPES[shape]]


@contextmanager
def _request_analytics(database):
    """The request side's own service, as the Sessions graph view gets it."""

    from django.conf import settings

    from conftest import _ch_test_native_port
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    ceiling_ms = int(settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS)
    client = ClickHouseClient(
        host=os.environ.get("CH25_HOST", "127.0.0.1"),
        port=_ch_test_native_port().port,
        user=os.environ.get("CH25_USER") or os.environ.get("CH_USERNAME") or "default",
        password=os.environ.get("CH25_PASSWORD") or os.environ.get("CH_PASSWORD") or "",
        database=database,
        server_enforced_readonly=False,
        read_timeout_ceiling_ms=ceiling_ms,
    )
    try:
        yield AnalyticsQueryService(
            ch_client=client, read_timeout_ceiling_ms=ceiling_ms
        )
    finally:
        client.close()


def _enqueue_runs_the_worker(worker, monkeypatch):
    """Run each enqueued refresh at once on the real worker; record it."""

    ran = []

    def apply_async(**call):
        ran.append(call["kwargs"])
        worker.refresh_exact_aggregation_snapshot._original_func(**call["kwargs"])
        return SimpleNamespace(id=f"workflow-{uuid.uuid4().hex}")

    monkeypatch.setattr(
        worker.refresh_exact_aggregation_snapshot, "apply_async", apply_async
    )
    return ran


def _fetch(analytics, filters):
    from tracer.services.clickhouse.session_graph import fetch_session_graph_ch

    return fetch_session_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="hour",
        req_data_config={"type": "SYSTEM_METRIC", "id": "latency"},
        organization_id=ORGANIZATION_ID,
    )


def _logged(store, hi, fragment):
    """Finished statements of one window end containing ``fragment``."""

    store.client.execute("SYSTEM FLUSH LOGS")
    return store.client.execute(
        "SELECT type, exception_code, Settings['max_threads'],"
        " Settings['max_execution_time']"
        " FROM system.query_log"
        " WHERE current_database = %(database)s"
        "   AND event_date >= yesterday()"
        "   AND type != 'QueryStart'"
        "   AND position(query, %(fragment)s) > 0"
        "   AND position(query, 'system.query_log') = 0"
        "   AND position(query, %(marker)s) > 0"
        " ORDER BY event_time_microseconds",
        {"database": store.database, "marker": _marker(hi), "fragment": fragment},
    )


@pytest.mark.parametrize("shape", list(LEAN_SHAPES))
def test_a_lean_shape_is_inline_and_equals_the_worker(
    store, worker, monkeypatch, shape
):
    from django.conf import settings

    from tracer.services.clickhouse.session_graph import SESSION_GRAPH_INLINE_WALL_MS

    ran = _enqueue_runs_the_worker(worker, monkeypatch)
    hi = _hi(shape)
    filters = _filters(shape, hi)

    with _request_analytics(store.database) as analytics:
        inline = _fetch(analytics, filters)
    assert ran == [], "an affordable scope must not be queued"
    assert inline["query_status"] == "complete", inline
    assert inline["metric_statistic"] == "mean"
    assert any(point["value"] for point in inline["data"]), inline["data"]

    with override_settings(SESSION_GRAPH_INLINE_MAX_ESTIMATED_ROWS=0):
        with _request_analytics(store.database) as analytics:
            background = _fetch(analytics, filters)
    assert len(ran) == 1
    assert background["query_status"] == "complete", background

    # Same statement, same numbers, bucket by bucket.
    assert [
        (point["timestamp"], point["value"], point["primary_traffic"])
        for point in inline["data"]
    ] == [
        (point["timestamp"], point["value"], point["primary_traffic"])
        for point in background["data"]
    ]

    logged = _logged(store, hi, "AS exact_sessions")
    assert [row[:2] for row in logged] == [("QueryFinish", 0)] * 2, logged
    (_, _, inline_threads, inline_cap), (_, _, worker_threads, worker_cap) = logged
    assert inline_threads == str(settings.FILTER_SELECTOR_MAX_THREADS) == "1"
    assert 0 < float(inline_cap) <= SESSION_GRAPH_INLINE_WALL_MS / 1000, inline_cap
    # query_log lists only settings that differ from the server's default; the
    # sidecar's default max_threads may already be the worker's budget.
    assert worker_threads in {str(settings.EXACT_GRAPH_SESSION_READ_MAX_THREADS), ""}
    assert 0 < float(worker_cap) <= settings.GRAPH_BACKGROUND_WALL_MS / 1000


def test_a_cached_scope_is_served_without_an_estimate_or_inline_read(
    store, worker, monkeypatch
):
    ran = _enqueue_runs_the_worker(worker, monkeypatch)
    hi = HI - timedelta(hours=3)
    filters = [_window(hi)]

    with override_settings(SESSION_GRAPH_INLINE_MAX_ESTIMATED_ROWS=0):
        with _request_analytics(store.database) as analytics:
            published = _fetch(analytics, filters)
    assert len(ran) == 1 and published["query_status"] == "complete"
    assert _logged(store, hi, "EXPLAIN ESTIMATE") == []
    assert len(_logged(store, hi, "AS exact_sessions")) == 1

    with _request_analytics(store.database) as analytics:
        served = _fetch(analytics, filters)

    assert len(ran) == 1
    assert served["query_status"] == "complete"
    assert served["data"] == published["data"]
    # The cache answered: no estimate, no second read of the scope.
    assert _logged(store, hi, "EXPLAIN ESTIMATE") == []
    assert len(_logged(store, hi, "AS exact_sessions")) == 1
