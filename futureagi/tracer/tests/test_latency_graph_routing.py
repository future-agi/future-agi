"""An unfiltered LATENCY graph takes the exact path; every other metric keeps
the fast unfiltered path.

The fast path reads ``spans``'s hourly aggregate states. They hold latency
only as t-digest states over every physically inserted row (no latency sum,
no version de-duplication), so they cannot publish the mean the filtered
path publishes. An unfiltered latency request therefore runs the filtered
path's own statement with an empty filter set - including its cost gate,
background hand-off, snapshot cache and pending envelope - so a chart with no
filter equals the same chart with a filter that matches everything. Graph
requests are per metric (``metric_id`` is part of the exact-snapshot
identity), so only latency requests move.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from tracer.services.clickhouse import graph_dispatch, session_graph

pytestmark = pytest.mark.unit

PROJECT_ID = "44444444-4444-4444-8444-444444444444"
ORG = "55555555-5555-4555-8555-555555555555"
WINDOW = {
    "column_id": "created_at",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": ["2026-08-05T00:00:00Z", "2026-08-12T00:00:00Z"],
    },
}
ALWAYS_TRUE = {
    "column_id": "model",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "gpt-4.1",
    },
}

# Every id the trace dispatcher publishes as the latency series.
LATENCY_IDS = ["latency", " Latency ", "", None, "time_to_first_token"]
FAST_PATH_IDS = [
    "tokens",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "cost",
    "traffic",
    "error_rate",
]


class _RecordingAnalytics:
    """Answer every statement with an empty, correctly shaped graph result."""

    supports_per_query_read_settings = True

    def __init__(self):
        self.statements: list[str] = []

    def execute_ch_query(self, query, params=None, **_):
        self.statements.append(query)
        return SimpleNamespace(
            data=[], columns=sorted(graph_dispatch._TRACE_ROLLUP_RESULT_COLUMNS)
        )


@pytest.fixture(autouse=True)
def _affordable_reads(monkeypatch):
    """The cost probe answers "fits"; this module tests routes, not the gate."""

    from django.core.cache import cache

    cache.clear()
    monkeypatch.setattr(
        graph_dispatch, "estimate_raw_graph_scan_rows", lambda **_: 1_000
    )
    yield
    cache.clear()


def _trace_graph(metric_id, filters, *, observe_type="trace", analytics=None):
    analytics = analytics or _RecordingAnalytics()
    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        metric_id=metric_id,
        observe_type=observe_type,
    )
    return response, analytics


def _is_hourly_state_statement(sql: str) -> bool:
    return "countMerge(n)" in sql and "latency_q" in sql


def _is_raw_span_statement(sql: str) -> bool:
    return "countMerge" not in sql and "FROM spans" in sql and "latency_ms" in sql


# ---------------------------------------------------------------------------
# Trace and span graphs: statement shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("metric_id", LATENCY_IDS)
def test_unfiltered_latency_runs_the_raw_span_statement(observe_type, metric_id):
    response, analytics = _trace_graph(metric_id, [WINDOW], observe_type=observe_type)

    assert response["query_status"] == "complete", response
    assert analytics.statements, "no statement ran"
    assert not any(_is_hourly_state_statement(sql) for sql in analytics.statements)
    graph_statements = [
        sql for sql in analytics.statements if _is_raw_span_statement(sql)
    ]
    assert len(graph_statements) == 1, analytics.statements
    # The filtered path's provenance, not the rollup's.
    assert response["query_provenance"] != "materialized_rollup"
    assert response["metric_statistic"] == "mean"


@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("metric_id", FAST_PATH_IDS)
def test_unfiltered_other_metrics_keep_the_hourly_state_statement(
    observe_type, metric_id
):
    response, analytics = _trace_graph(metric_id, [WINDOW], observe_type=observe_type)

    assert response["query_status"] == "complete", response
    assert len(analytics.statements) == 1
    assert _is_hourly_state_statement(analytics.statements[0])
    assert response["query_provenance"] == "materialized_rollup"


@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_unfiltered_latency_is_the_always_true_filter_statement(observe_type):
    """The same statement shape as a filter that removes no row, minus the
    filter's own predicate: one raw span read, the same scalar source."""

    unfiltered, plain = _trace_graph("latency", [WINDOW], observe_type=observe_type)
    filtered, matched = _trace_graph(
        "latency", [WINDOW, ALWAYS_TRUE], observe_type=observe_type
    )
    assert unfiltered["query_status"] == filtered["query_status"] == "complete"
    assert unfiltered["query_provenance"] == filtered["query_provenance"]
    for statements in (plain.statements, matched.statements):
        assert [_is_raw_span_statement(sql) for sql in statements].count(True) == 1


@pytest.mark.parametrize("filters", [[], [WINDOW]], ids=["no-filters", "window"])
def test_unfiltered_latency_accepts_an_empty_filter_set(monkeypatch, filters):
    """No filter at all (the default 30-day window) is a valid exact read."""

    direct = mock.Mock(
        return_value={
            "metric_name": "latency",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }
    )
    rollup = mock.Mock()
    monkeypatch.setattr(graph_dispatch, "_fetch_direct_raw_system_metric_graph", direct)
    monkeypatch.setattr(graph_dispatch, "_fetch_rollup_system_metric_graph", rollup)

    response, _analytics = _trace_graph("latency", list(filters))

    rollup.assert_not_called()
    direct.assert_called_once()
    assert direct.call_args.kwargs["filters"] == filters
    assert direct.call_args.kwargs["metric_id"] == "latency"
    assert response["query_status"] == "complete"


# ---------------------------------------------------------------------------
# Trace graph: the inline/background decision and the snapshot cache
# ---------------------------------------------------------------------------


def test_unfiltered_latency_too_big_for_the_wall_is_scheduled(monkeypatch):
    scheduled = []

    def schedule(**call):
        scheduled.append(call)
        return call["pending_payload"]

    monkeypatch.setattr(
        graph_dispatch,
        "_affordable_raw_graph_seed",
        lambda **_: graph_dispatch._GraphReadUnaffordable(estimated_rows=None),
    )
    monkeypatch.setattr(graph_dispatch, "_schedule_unaffordable_graph_read", schedule)
    rollup = mock.Mock()
    monkeypatch.setattr(graph_dispatch, "_fetch_rollup_system_metric_graph", rollup)

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=_RecordingAnalytics(),
        project_id=PROJECT_ID,
        filters=[WINDOW],
        interval="day",
        metric_id="latency",
        organization_id=ORG,
    )

    rollup.assert_not_called()
    (call,) = scheduled
    assert call["identity"]["filters"] == [WINDOW]
    assert call["identity"]["metric_id"] == "latency"
    assert call["identity"]["payload_version"] == (
        graph_dispatch.OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION
    )
    assert response["query_status"] == "pending"
    assert response["metric_statistic"] == "mean"


def test_unfiltered_latency_serves_its_cached_snapshot(monkeypatch):
    cached = {
        "metric_name": "latency",
        "data": [{"timestamp": "2026-08-05T00:00:00", "value": 12.5}],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        "metric_statistic": "mean",
    }
    probes = []

    def read_or_refresh(**call):
        probes.append(call)
        return cached

    monkeypatch.setattr(graph_dispatch, "_read_or_refresh_exact_graph", read_or_refresh)
    analytics = _RecordingAnalytics()

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[WINDOW],
        interval="day",
        metric_id="latency",
        organization_id=ORG,
    )

    assert analytics.statements == []
    assert probes[0]["namespace"] == "observe-system-graph"
    assert probes[0]["identity"]["filters"] == [WINDOW]
    assert response["data"] == cached["data"]


def test_unfiltered_latency_no_longer_needs_per_query_read_settings(monkeypatch):
    """The exact path runs with the filtered path's own contract; only the
    fast path needs per-query settings, and it still refuses without them."""

    direct = mock.Mock(
        return_value={
            "metric_name": "latency",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }
    )
    monkeypatch.setattr(graph_dispatch, "_fetch_direct_raw_system_metric_graph", direct)
    locked = _RecordingAnalytics()
    locked.supports_per_query_read_settings = False

    latency, _ = _trace_graph("latency", [WINDOW], analytics=locked)
    tokens, _ = _trace_graph("tokens", [WINDOW], analytics=locked)

    direct.assert_called_once()
    assert latency["query_status"] == "complete"
    assert tokens["query_status"] == "degraded"
    assert tokens["query_provenance"] == "server_read_policy_unavailable"


# ---------------------------------------------------------------------------
# Session graph
# ---------------------------------------------------------------------------


def _session_graph(metric_id, filters, monkeypatch):
    scheduled = []
    rollup = mock.Mock(
        return_value={
            "metric_name": metric_id,
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }
    )

    def read_or_schedule(namespace, identity, **options):
        scheduled.append((namespace, identity))
        return options["pending_payload"]

    monkeypatch.setattr(session_graph, "_fetch_rollup_system_metric_graph", rollup)
    monkeypatch.setattr(
        session_graph, "read_or_schedule_exact_snapshot", read_or_schedule
    )
    response = session_graph.fetch_session_graph_ch(
        analytics=_RecordingAnalytics(),
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        req_data_config={"type": "SYSTEM_METRIC", "id": metric_id},
        organization_id=ORG,
    )
    return response, rollup, scheduled


@pytest.mark.parametrize("filters", [[], [WINDOW]], ids=["no-filters", "window"])
def test_unfiltered_session_latency_schedules_the_exact_snapshot(monkeypatch, filters):
    response, rollup, scheduled = _session_graph("latency", filters, monkeypatch)

    rollup.assert_not_called()
    ((namespace, identity),) = scheduled
    assert namespace == "observe-session-system-graph"
    assert identity["filters"] == filters
    assert identity["metric_id"] == "latency"
    assert response["query_status"] == "pending"
    assert response["metric_statistic"] == "mean"


@pytest.mark.parametrize(
    "metric_id",
    sorted(session_graph._SESSION_ROLLUP_METRICS - {"latency"}),
)
def test_unfiltered_session_metrics_other_than_latency_keep_the_rollup(
    monkeypatch, metric_id
):
    response, rollup, scheduled = _session_graph(metric_id, [WINDOW], monkeypatch)

    rollup.assert_called_once()
    assert scheduled == []
    assert response["query_status"] == "complete"


# ---------------------------------------------------------------------------
# Users graph: it has no fast unfiltered path, so nothing moves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metric_id", ["latency", "active_users"])
def test_unfiltered_users_graph_reads_the_exact_statement(monkeypatch, metric_id):
    reads = []
    monkeypatch.setattr(graph_dispatch, "_affordable_user_graph_read", lambda **_: None)
    monkeypatch.setattr(
        graph_dispatch,
        "read_exact_user_system_graph",
        lambda **call: (
            reads.append(call)
            or {
                "metric_name": call["metric_id"],
                "data": [],
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
            }
        ),
    )
    graph_dispatch.fetch_user_system_metric_graph_ch(
        analytics=_RecordingAnalytics(),
        project_id=PROJECT_ID,
        filters=[WINDOW],
        interval="day",
        metric_id=metric_id,
    )
    ((call,),) = [reads]
    assert call["filters"] == [WINDOW]
