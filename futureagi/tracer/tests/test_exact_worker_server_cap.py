"""The exact-refresh worker asks ClickHouse to stop each statement at its wall.

Application reads carry no server deadline: ``timeout_ms`` is admission
arithmetic and ``application_read_settings`` zeroes ``max_execution_time``.
The background worker owns a real wall (``GRAPH_BACKGROUND_WALL_MS``), so
every statement it sends for an Observe refresh carries what is left of that
wall as ``max_execution_time``. A read past the wall is stopped by the server
(``ReadDeadlineExceeded``) and the refresh takes the existing failed path,
instead of reading to the end on the worker's one slot for a result the
reader's own fence then discards.

These tests drive the real worker entry (``_observe_payload`` /
``refresh_exact_aggregation_snapshot``), the real ``AnalyticsQueryService``
and its read policy; only the native ClickHouse client is replaced, so the
assertion is on the settings the driver would send. The live proof that the
cap reaches ``system.query_log`` is ``test_exact_worker_server_cap_ch25``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import structlog
from clickhouse_driver.errors import ErrorCodes, ServerException
from django.conf import settings
from django.core.cache import cache

PROJECT_ID = "7c3a2b11-0000-4000-8000-00000000c001"
ORGANIZATION_ID = "7c3a2b11-0000-4000-8000-00000000c0a1"
LO = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
HI = LO + timedelta(days=7)
WALL_S = settings.GRAPH_BACKGROUND_WALL_MS / 1000.0

WINDOW = {
    "column_id": "created_at",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": [LO.isoformat(), HI.isoformat()],
    },
}


def _attribute(filter_type, op, value, key="customer_tier"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": op,
            "filter_value": value,
        },
    }


class _RecordingClient:
    """The native client the worker builds; records what the driver would send."""

    instances: list[_RecordingClient] = []
    respond = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.sent: list[SimpleNamespace] = []
        self.server_enforced_readonly = False
        self.server_profile_locked = False
        self.closed = False
        type(self).instances.append(self)

    def execute_read_with_progress(self, query, params, *, timeout_ms, settings):
        call = SimpleNamespace(query=query, params=params, settings=dict(settings))
        self.sent.append(call)
        rows, columns = type(self).respond(call)
        return rows, columns, 0.0, 0, 0

    def close(self):
        self.closed = True


def _empty(call):
    del call
    return [], []


@pytest.fixture()
def worker(monkeypatch):
    from tracer.services.clickhouse import client as client_module
    from tracer.services.clickhouse import v2 as v2_module
    from tracer.tasks import exact_aggregation

    _RecordingClient.instances = []
    _RecordingClient.respond = staticmethod(_empty)
    monkeypatch.setattr(client_module, "ClickHouseClient", _RecordingClient)
    monkeypatch.setattr(
        v2_module,
        "get_v2_config",
        lambda: {
            "host": "ch25.internal",
            "tcp_port": 9000,
            "user": "exact-reader",
            "password": "",
            "database": "futureagi",
            "server_enforced_readonly": False,
        },
    )
    monkeypatch.setattr(
        exact_aggregation, "_reauthorize_exact_observe_project", lambda _identity: None
    )
    return exact_aggregation


def _identity(filters, metric_id="latency", **extra):
    return {
        "project_id": PROJECT_ID,
        "organization_id": ORGANIZATION_ID,
        "filters": filters,
        "interval": "hour",
        "metric_id": metric_id,
        **extra,
    }


def _sent():
    return [call for client in _RecordingClient.instances for call in client.sent]


def _assert_capped_at_the_wall(calls):
    assert calls, "the worker sent no statement"
    caps = [call.settings.get("max_execution_time") for call in calls]
    for call, cap in zip(calls, caps, strict=True):
        assert cap and 0 < cap <= WALL_S, (cap, call.query[:120])
    # One wall for the whole refresh: a later statement never gets more time.
    assert caps == sorted(caps, reverse=True), caps


@pytest.mark.unit
def test_session_graph_statement_is_capped_at_the_wall_left(worker):
    payload = worker._observe_payload(
        "observe-session-system-graph", _identity([WINDOW])
    )

    assert payload["query_status"] == "complete"
    calls = _sent()
    assert len(calls) == 1 and "exact_sessions" in calls[0].query
    _assert_capped_at_the_wall(calls)


@pytest.mark.unit
def test_session_graph_witness_and_fallback_are_capped_at_the_wall_left(worker):
    def respond(call):
        if "session_scalar_witness_ids AS (" in call.query:
            # The witness is stopped by the server: the reader must rebuild
            # the unpruned statement, which carries the (smaller) wall left.
            raise ServerException("stopped", code=ErrorCodes.TIMEOUT_EXCEEDED)
        return [], []

    _RecordingClient.respond = staticmethod(respond)
    payload = worker._observe_payload(
        "observe-session-system-graph",
        _identity([WINDOW, _attribute("text", "equals", "gold")]),
    )

    assert payload["query_status"] == "complete"
    calls = _sent()
    assert ["session_scalar_witness_ids AS (" in call.query for call in calls] == [
        True,
        False,
    ]
    # The witness is capped at the WALL left, not its nominal 9.5 s ceiling.
    assert calls[0].settings["max_execution_time"] > 9.5
    _assert_capped_at_the_wall(calls)


@pytest.mark.unit
def test_session_graph_absence_probe_is_capped_at_the_wall_left(worker):
    def respond(call):
        if "has_raw_witness" in call.query:
            return [(1,)], [("has_raw_witness", "UInt8")]
        return [], []

    _RecordingClient.respond = staticmethod(respond)
    worker._observe_payload(
        "observe-session-system-graph",
        _identity([WINDOW, _attribute("number", "greater_than", 5, key="score")]),
    )

    calls = _sent()
    assert "has_raw_witness" in calls[0].query and len(calls) >= 2
    _assert_capped_at_the_wall(calls)


@pytest.mark.unit
@pytest.mark.parametrize(
    "namespace,identity",
    [
        (
            "observe-user-system-graph",
            _identity([WINDOW]),
        ),
        (
            "observe-system-graph",
            _identity(
                [WINDOW, _attribute("text", "equals", "gold")], observe_type="trace"
            ),
        ),
        (
            "observe-system-graph",
            _identity(
                [WINDOW, _attribute("text", "equals", "gold")], observe_type="span"
            ),
        ),
    ],
    ids=["users", "trace", "span"],
)
def test_trace_and_user_background_statements_are_capped(worker, namespace, identity):
    from tracer.services.clickhouse.graph_dispatch import (
        _TRACE_ROLLUP_RESULT_COLUMNS,
    )

    def respond(call):
        if "time_bucket" in call.query and "EXPLAIN" not in call.query:
            # The raw graph statement checks its result columns.
            return [], [
                (name, "String") for name in sorted(_TRACE_ROLLUP_RESULT_COLUMNS)
            ]
        return [], []

    _RecordingClient.respond = staticmethod(respond)
    worker._observe_payload(namespace, identity)

    _assert_capped_at_the_wall(_sent())


@pytest.mark.unit
def test_a_statement_is_not_sent_once_the_wall_is_spent(worker, monkeypatch):
    from tracer.services.clickhouse.read_budget import (
        ReadDeadline,
        ReadDeadlineExceeded,
    )

    with worker._exact_observe_analytics() as analytics:
        monkeypatch.setattr(ReadDeadline, "elapsed_ms", lambda _self: WALL_S * 1000)
        with pytest.raises(ReadDeadlineExceeded):
            analytics.execute_ch_query("SELECT 1", {}, timeout_ms=1_000)

    assert _sent() == []


@pytest.mark.unit
def test_a_server_stop_takes_the_failed_refresh_path(worker, monkeypatch):
    from tracer.services import exact_aggregation_cache
    from tracer.services.exact_aggregation_cache import (
        exact_refresh_state,
        read_or_schedule_exact_snapshot,
    )

    def respond(call):
        if call.settings.get("max_execution_time"):
            raise ServerException("stopped", code=ErrorCodes.TIMEOUT_EXCEEDED)
        return [], []

    _RecordingClient.respond = staticmethod(respond)
    cache.clear()
    monkeypatch.setattr(
        exact_aggregation_cache,
        "_configured_exact_aggregation_task_queue",
        lambda: "exact_aggregation",
    )
    ran = []

    def apply_async(**call):
        ran.append(call["kwargs"])
        with pytest.raises(RuntimeError, match="exact aggregation refresh failed"):
            worker.refresh_exact_aggregation_snapshot._original_func(**call["kwargs"])
        return SimpleNamespace(id=f"workflow-{uuid.uuid4().hex}")

    monkeypatch.setattr(
        worker.refresh_exact_aggregation_snapshot, "apply_async", apply_async
    )
    namespace = "observe-session-system-graph"
    identity = _identity([WINDOW])
    pending = {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }
    with structlog.testing.capture_logs() as logs:
        served = read_or_schedule_exact_snapshot(
            namespace, dict(identity), refresh=False, pending_payload=pending
        )

    assert len(ran) == 1
    assert _sent(), "the worker never reached ClickHouse"
    failures = [
        entry
        for entry in logs
        if entry["event"] == "exact_aggregation_background_refresh_failed"
    ]
    assert failures == [
        {
            "event": "exact_aggregation_background_refresh_failed",
            "namespace": namespace,
            "error_type": "ReadDeadlineExceeded",
            "log_level": "warning",
        }
    ]
    assert served["query_status"] == "pending"
    assert served["query_refresh_failed"] is True
    assert served["query_refreshing"] is False
    assert exact_refresh_state(namespace, ran[0]["identity"]) == "failed"
    # A poll does not re-enqueue behind the failure (the existing contract).
    polled = read_or_schedule_exact_snapshot(
        namespace, dict(identity), refresh=False, pending_payload=pending
    )
    assert polled["query_refresh_failed"] is True and len(ran) == 1
