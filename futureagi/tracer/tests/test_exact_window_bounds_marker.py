"""Exact graph snapshots say their window was read at microsecond precision.

The exact system and agent graph builders bind a fractional-second request
bound as epoch microseconds. A refresh worker that predates that reads such a
window truncated to the second, and during a rolling deploy it can take a job
keyed by the current identity (it ignores ``payload_version``) and cache its
result there. A current worker writes ``EXACT_WINDOW_BOUNDS_FIELD`` into the
payload it caches; the readers of ``observe-system-graph`` and
``observe-agent-graph`` treat a snapshot without it as a miss, and remove the
field before serving a hit. The user and session graphs did not change and
keep serving unmarked snapshots.
"""

from __future__ import annotations

import importlib
from contextlib import nullcontext

import pytest

from tracer.services import exact_aggregation_cache
from tracer.services.clickhouse import graph_dispatch
from tracer.tests import test_observe_system_graph_payload_version as version_tests
from tracer.tests.test_observe_system_graph_payload_version import (
    _OLD_WORKER_VALUE,
    ORG,
    PROJECT,
    _cached_payload,
    _seed_cache_on_first_read,
    _served_values,
    _trace_graph,
    _users_graph,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        not hasattr(graph_dispatch, "stamp_exact_window_bounds"),
        reason="the microsecond-window cache marker is added by this follow-up",
    ),
]

clean_cache = version_tests.clean_cache
FIELD = "window_bound_precision"
MICROSECOND = "microsecond"


def _without_marker(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != FIELD}


def _agent_payload(**extra) -> dict:
    return {
        "nodes": [
            {
                "id": "agent",
                "span_count": 3,
                "cost_coverage": {
                    "status": "complete",
                    "known_subtotal": 0.0,
                    "known_fact_count": 0,
                    "unknown_fact_count": 0,
                },
            }
        ],
        "edges": [],
        "path_edges": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        **extra,
    }


def _agent_graph(monkeypatch, payload):
    _seed_cache_on_first_read(monkeypatch, graph_dispatch, payload)
    return graph_dispatch.fetch_agent_graph_ch(
        project_id=PROJECT,
        filters=[version_tests._WINDOW_FILTER],
        organization_id=ORG,
    )


def test_marker_constants_are_the_cached_field():
    assert graph_dispatch.EXACT_WINDOW_BOUNDS_FIELD == FIELD
    assert graph_dispatch.EXACT_WINDOW_BOUNDS_MICROSECOND == MICROSECOND


@pytest.mark.parametrize(
    ("namespace", "stamped"),
    [
        ("observe-system-graph", True),
        ("observe-all-system-graphs", True),
        ("observe-agent-graph", True),
        ("observe-user-system-graph", False),
        ("observe-session-system-graph", False),
        ("observe-eval-graph", False),
        ("dashboard-query", False),
    ],
)
def test_stamp_marks_only_the_exact_window_namespaces(namespace, stamped):
    payload = {"query_status": "complete"}

    marked = graph_dispatch.stamp_exact_window_bounds(namespace, payload)

    assert payload == {"query_status": "complete"}
    if stamped:
        assert marked == {"query_status": "complete", FIELD: MICROSECOND}
    else:
        assert marked is payload
    assert graph_dispatch.stamp_exact_window_bounds(namespace, [payload]) == [payload]


@pytest.mark.parametrize(
    ("namespace", "reader", "module", "stamped"),
    [
        (
            "observe-system-graph",
            "fetch_background_raw_system_metric_graph",
            "tracer.services.clickhouse.graph_dispatch",
            True,
        ),
        (
            "observe-all-system-graphs",
            "read_exact_all_system_metrics",
            "tracer.services.clickhouse.exact_graph_reads",
            True,
        ),
        (
            "observe-agent-graph",
            "read_exact_agent_graph",
            "tracer.services.clickhouse.exact_graph_reads",
            True,
        ),
        (
            "observe-user-system-graph",
            "read_exact_user_system_graph",
            "tracer.services.clickhouse.exact_graph_reads",
            False,
        ),
        (
            "observe-session-system-graph",
            "read_exact_session_system_graph",
            "tracer.services.clickhouse.exact_graph_reads",
            False,
        ),
    ],
)
def test_worker_marks_the_payload_it_caches(
    monkeypatch, namespace, reader, module, stamped
):
    from tracer.tasks import exact_aggregation

    monkeypatch.setattr(
        importlib.import_module(module), reader, lambda **_call: {"ok": True}
    )
    monkeypatch.setattr(
        exact_aggregation, "_reauthorize_exact_observe_project", lambda _identity: None
    )
    monkeypatch.setattr(
        exact_aggregation, "_exact_observe_analytics", lambda: nullcontext("analytics")
    )

    payload = exact_aggregation._load_exact_payload(
        namespace,
        {
            "project_id": PROJECT,
            "filters": [],
            "interval": "day",
            "metric_id": "tokens",
        },
    )

    assert (payload.get(FIELD) == MICROSECOND) is stamped
    # The series statistic is the other marker a current worker writes.
    assert {
        key: value
        for key, value in payload.items()
        if key not in {FIELD, "metric_statistic"}
    } == {"ok": True}


@pytest.mark.usefixtures("clean_cache")
@pytest.mark.parametrize("metric_id", ["latency", "tokens", "cost"])
def test_unmarked_system_graph_snapshot_is_a_miss(monkeypatch, metric_id):
    old = _without_marker(_cached_payload(metric_id, metric_statistic="mean"))
    seeded = _seed_cache_on_first_read(monkeypatch, graph_dispatch, old)

    payload = _trace_graph(monkeypatch, metric_id)

    assert seeded == ["observe-system-graph"]
    assert _OLD_WORKER_VALUE not in _served_values(payload), payload
    assert payload.get("query_status") != "complete", payload
    assert payload.get("query_cached") is not True, payload


@pytest.mark.usefixtures("clean_cache")
@pytest.mark.parametrize("metric_id", ["latency", "tokens", "cost"])
def test_marked_system_graph_snapshot_is_served_without_the_marker(
    monkeypatch, metric_id
):
    current = _cached_payload(metric_id, metric_statistic="mean")
    assert current[FIELD] == MICROSECOND
    _seed_cache_on_first_read(monkeypatch, graph_dispatch, current)

    payload = _trace_graph(monkeypatch, metric_id)

    assert payload.get("query_status") == "complete", payload
    assert payload.get("query_cached") is True
    assert _served_values(payload) == [_OLD_WORKER_VALUE]
    assert FIELD not in payload


@pytest.mark.usefixtures("clean_cache")
def test_unmarked_agent_graph_snapshot_is_a_miss(monkeypatch):
    payload = _agent_graph(monkeypatch, _agent_payload())

    assert payload.get("query_status") != "complete", payload
    assert payload.get("query_cached") is not True, payload
    assert payload["nodes"] == []


@pytest.mark.usefixtures("clean_cache")
def test_marked_agent_graph_snapshot_is_served_without_the_marker(monkeypatch):
    payload = _agent_graph(monkeypatch, _agent_payload(**{FIELD: MICROSECOND}))

    assert payload.get("query_status") == "complete", payload
    assert payload.get("query_cached") is True
    assert payload["nodes"] == _agent_payload()["nodes"]
    assert FIELD not in payload


@pytest.mark.usefixtures("clean_cache")
def test_users_graph_still_serves_an_unmarked_snapshot(monkeypatch):
    old = _without_marker(_cached_payload("latency", metric_statistic="mean"))
    _seed_cache_on_first_read(monkeypatch, graph_dispatch, old)

    payload = _users_graph(monkeypatch, "latency")

    assert payload.get("query_status") == "complete", payload
    assert _served_values(payload) == [_OLD_WORKER_VALUE]


@pytest.mark.usefixtures("clean_cache")
def test_published_snapshot_keeps_the_marker_in_the_cache():
    identity = exact_aggregation_cache.normalize_exact_observe_identity(
        {
            "project_id": PROJECT,
            "filters": [version_tests._WINDOW_FILTER],
            "interval": "day",
            "metric_id": "tokens",
            "observe_type": "trace",
            "payload_version": graph_dispatch.OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION,
        }
    )
    payload = graph_dispatch.stamp_exact_window_bounds(
        "observe-system-graph", _without_marker(_cached_payload("tokens"))
    )

    exact_aggregation_cache.publish_exact_snapshot(
        "observe-system-graph", identity, payload
    )
    cached = exact_aggregation_cache.read_exact_snapshot(
        "observe-system-graph", identity
    )

    assert cached[FIELD] == MICROSECOND
