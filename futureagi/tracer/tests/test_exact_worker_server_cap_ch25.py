"""Real-ClickHouse proof: the worker's Sessions statement carries a server cap.

The exact-refresh worker is the only lane that runs the Sessions graph
statement, and in the US deployment it has one slot. Before this change the
statement reached ClickHouse with ``max_execution_time = 0``: a twelve-month
read past the 180 s wall read everything, held the slot, and its result was
then discarded by the reader's own deadline fence.

This module runs the worker exactly as production does - the real
``_exact_observe_analytics`` (its own native ``ClickHouseClient``, the real
``AnalyticsQueryService`` and read policy), the real refresh activity and the
real exact cache - against the test sidecar, and reads what the server saw
from ``system.query_log``:

* the statement is logged with ``max_execution_time`` = what was left of
  ``GRAPH_BACKGROUND_WALL_MS`` (positive, at most the wall);
* when the wall left is 1 ms, the SERVER stops the statement, and the refresh
  takes the existing failed path: the activity fails, the identity's state is
  ``failed``, nothing is published and a poll does not re-enqueue.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import structlog

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_native_client,
    _ch_test_native_port,
    _ch_test_owned_database,
)

pytestmark = pytest.mark.integration

PROJECT_ID = "7c3a2b11-0000-4000-8000-00000000d001"
ORGANIZATION_ID = "7c3a2b11-0000-4000-8000-00000000d0a1"
LO = datetime(2026, 6, 10, 0, tzinfo=UTC)
HI = LO + timedelta(days=2)
NAMESPACE = "observe-session-system-graph"


def _window(hi):
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [LO.isoformat(), hi.isoformat()],
        },
    }


def _marker(hi):
    """The window end as the statement spells it: each test reads its own."""

    return str(int(hi.timestamp() * 1_000_000))


_COLUMNS = (
    "project_id",
    "observation_type",
    "start_time",
    "end_time",
    "trace_id",
    "id",
    "parent_span_id",
    "name",
    "latency_ms",
    "status",
    "trace_session_id",
    "is_deleted",
    "_version",
)


def _uid(*parts):
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, "worker-cap/" + "/".join(map(str, parts)))
    )


def _rows():
    rows = []
    for session in range(300):
        session_id = uuid.UUID(_uid("session", session))
        for trace in range(3):
            start = LO + timedelta(minutes=7 * session + trace, seconds=session % 50)
            trace_id = _uid("trace", session, trace)
            root = _uid("root", session, trace)
            latency = 50 + (session * 37 + trace * 11) % 4000
            rows.append(
                (
                    uuid.UUID(PROJECT_ID),
                    "chain",
                    start,
                    start + timedelta(milliseconds=latency),
                    trace_id,
                    root,
                    "",
                    "root",
                    latency,
                    "OK",
                    session_id,
                    0,
                    1,
                )
            )
            for child in range(2):
                rows.append(
                    (
                        uuid.UUID(PROJECT_ID),
                        "llm",
                        start + timedelta(milliseconds=child),
                        start + timedelta(milliseconds=child + 5),
                        trace_id,
                        _uid("child", session, trace, child),
                        root,
                        "child",
                        5,
                        "OK",
                        session_id,
                        0,
                        1,
                    )
                )
    return rows


@pytest.fixture(scope="module")
def store():
    with _ch_test_owned_database("test_worker_cap_") as database:
        _ch_test_apply_v2_schema(database)
        with _ch_test_native_client(database=database) as client:
            client.execute(f"INSERT INTO spans ({', '.join(_COLUMNS)}) VALUES", _rows())
            yield SimpleNamespace(database=database, client=client)


@pytest.fixture()
def worker(store, monkeypatch):
    """The production worker, pointed at the sidecar database."""

    from django.core.cache import cache

    from tracer.services import exact_aggregation_cache
    from tracer.services.clickhouse import v2 as v2_module
    from tracer.tasks import exact_aggregation

    monkeypatch.setattr(
        v2_module,
        "get_v2_config",
        lambda: {
            "host": os.environ.get("CH25_HOST", "127.0.0.1"),
            "tcp_port": _ch_test_native_port().port,
            "user": os.environ.get("CH25_USER")
            or os.environ.get("CH_USERNAME")
            or "default",
            "password": os.environ.get("CH25_PASSWORD")
            or os.environ.get("CH_PASSWORD")
            or "",
            "database": store.database,
            "server_enforced_readonly": False,
        },
    )
    monkeypatch.setattr(
        exact_aggregation, "_reauthorize_exact_observe_project", lambda _identity: None
    )
    monkeypatch.setattr(
        exact_aggregation_cache,
        "_configured_exact_aggregation_task_queue",
        lambda: "exact_aggregation",
    )
    cache.clear()
    return exact_aggregation


def _identity(hi):
    return {
        "project_id": PROJECT_ID,
        "organization_id": ORGANIZATION_ID,
        "filters": [_window(hi)],
        "interval": "hour",
        "metric_id": "latency",
    }


def _statements(store, hi):
    store.client.execute("SYSTEM FLUSH LOGS")
    return store.client.execute(
        "SELECT type, exception_code, Settings['max_execution_time']"
        " FROM system.query_log"
        " WHERE current_database = %(database)s"
        "   AND event_date >= yesterday()"
        "   AND type != 'QueryStart'"
        "   AND position(query, 'AS exact_sessions') > 0"
        "   AND position(query, %(marker)s) > 0"
        "   AND position(query, 'system.query_log') = 0"
        " ORDER BY event_time_microseconds",
        {"database": store.database, "marker": _marker(hi)},
    )


def _pending():
    return {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }


def _run_refresh(worker, monkeypatch, hi, *, expect_failure):
    from tracer.services.exact_aggregation_cache import read_or_schedule_exact_snapshot

    ran = []

    def apply_async(**call):
        ran.append(call["kwargs"])
        if expect_failure:
            with pytest.raises(RuntimeError, match="exact aggregation refresh failed"):
                worker.refresh_exact_aggregation_snapshot._original_func(
                    **call["kwargs"]
                )
        else:
            worker.refresh_exact_aggregation_snapshot._original_func(**call["kwargs"])
        return SimpleNamespace(id=f"workflow-{uuid.uuid4().hex}")

    monkeypatch.setattr(
        worker.refresh_exact_aggregation_snapshot, "apply_async", apply_async
    )
    with structlog.testing.capture_logs() as logs:
        served = read_or_schedule_exact_snapshot(
            NAMESPACE, _identity(hi), refresh=False, pending_payload=_pending()
        )
    return served, ran, logs


def test_the_worker_statement_reaches_clickhouse_with_the_wall_left(
    store, worker, monkeypatch
):
    from django.conf import settings

    served, ran, _logs = _run_refresh(worker, monkeypatch, HI, expect_failure=False)

    assert len(ran) == 1
    assert served["query_status"] == "complete", served
    assert sum(point["primary_traffic"] for point in served["data"]) == 300
    logged = _statements(store, HI)
    assert len(logged) == 1, logged
    kind, code, cap = logged[0]
    assert (kind, code) == ("QueryFinish", 0)
    # The server saw what was left of the 180 s wall, not "no limit".
    wall_s = settings.GRAPH_BACKGROUND_WALL_MS / 1000
    assert 0 < float(cap) <= wall_s, cap
    assert float(cap) > wall_s - 30, cap


def test_a_statement_past_the_wall_is_stopped_by_the_server(store, worker, monkeypatch):
    from tracer.services.clickhouse.read_budget import ReadDeadline
    from tracer.services.exact_aggregation_cache import (
        exact_refresh_state,
        read_exact_snapshot,
        read_or_schedule_exact_snapshot,
    )

    # One millisecond of the wall is left when the statement is sent: the
    # server, not the reader's fence, has to stop it.
    monkeypatch.setattr(
        ReadDeadline, "remaining_ms", lambda _self, cap_ms=None, *, floor_ms=25: 1
    )
    hi = HI - timedelta(hours=1)
    served, ran, logs = _run_refresh(worker, monkeypatch, hi, expect_failure=True)

    assert len(ran) == 1
    logged = _statements(store, hi)
    assert len(logged) == 1, logged
    kind, code, cap = logged[0]
    assert kind in {"ExceptionWhileProcessing", "ExceptionBeforeStart"}, logged
    # TIMEOUT_EXCEEDED; a cap this small can also surface as QUERY_WAS_CANCELLED.
    # Both are read-budget errors and both take the failed path below.
    assert code in {159, 394}, logged
    assert float(cap) == pytest.approx(0.001), cap
    failures = [
        entry["error_type"]
        for entry in logs
        if entry["event"] == "exact_aggregation_background_refresh_failed"
    ]
    assert failures in (["ReadDeadlineExceeded"], ["ServerException"]), failures
    assert served["query_status"] == "pending"
    assert served["query_refresh_failed"] is True
    identity = ran[0]["identity"]
    assert exact_refresh_state(NAMESPACE, identity) == "failed"
    assert read_exact_snapshot(NAMESPACE, identity) is None
    polled = read_or_schedule_exact_snapshot(
        NAMESPACE, _identity(hi), refresh=False, pending_payload=_pending()
    )
    assert polled["query_refresh_failed"] is True and len(ran) == 1
