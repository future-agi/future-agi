"""Exact system-graph snapshots cached before the median change are retired.

``observe-system-graph``, ``observe-session-system-graph`` and
``observe-user-system-graph`` snapshots live for up to 30 days. Before the
median change they held a mean latency. Their identity now carries
``payload_version`` (as the agent graph's does), so every key derived from it
- the snapshot key, the frozen-window alias, the refresh lock and state - is
new, and an old mean can never be served under the "median" label. The
global cache version is not bumped, so unrelated namespaces (dashboards,
eval, annotation, agent graph) keep their snapshots.
"""

from __future__ import annotations

from contextlib import nullcontext
from uuid import uuid4

import pytest

from tracer.services import exact_aggregation_cache
from tracer.services.clickhouse import graph_dispatch, session_graph
from tracer.services.clickhouse.graph_dispatch import (
    OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION,
)

PROJECT = str(uuid4())
ORG = str(uuid4())

_MODEL_FILTER = {
    "column_id": "model",
    "filter_config": {
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "gpt-4",
        "col_type": "SYSTEM_METRIC",
    },
}


class _Analytics:
    supports_per_query_read_settings = True


def _pending(**_):
    return graph_dispatch._pending_graph_payload("latency")


def test_version_is_one():
    assert OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION == 1


def test_trace_graph_identity_is_versioned(monkeypatch):
    calls = []

    def read_or_refresh(**call):
        calls.append(call)
        return _pending()

    monkeypatch.setattr(graph_dispatch, "_read_or_refresh_exact_graph", read_or_refresh)
    graph_dispatch.fetch_system_metric_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=[_MODEL_FILTER],
        interval="day",
        metric_id="latency",
        organization_id=ORG,
    )
    assert calls
    for call in calls:
        assert call["namespace"] == "observe-system-graph"
        assert (
            call["identity"]["payload_version"] == OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION
        )


def test_trace_graph_background_schedule_identity_is_versioned(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: None
    )
    monkeypatch.setattr(
        graph_dispatch,
        "_affordable_raw_graph_seed",
        lambda **_: graph_dispatch._GraphReadUnaffordable(estimated_rows=None),
    )
    monkeypatch.setattr(
        graph_dispatch,
        "_schedule_unaffordable_graph_read",
        lambda **call: scheduled.append(call) or call["pending_payload"],
    )
    graph_dispatch.fetch_system_metric_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=[_MODEL_FILTER],
        interval="day",
        metric_id="latency",
        organization_id=ORG,
    )
    (call,) = scheduled
    assert call["identity"]["payload_version"] == OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION


def test_users_graph_identity_is_versioned(monkeypatch):
    calls = []

    def read_or_refresh(**call):
        calls.append(call)
        return _pending()

    monkeypatch.setattr(graph_dispatch, "_read_or_refresh_exact_graph", read_or_refresh)
    graph_dispatch.fetch_user_system_metric_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=[],
        interval="day",
        metric_id="latency",
        organization_id=ORG,
    )
    (call,) = calls
    assert call["namespace"] == "observe-user-system-graph"
    assert call["identity"]["payload_version"] == OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION


def test_session_graph_identity_is_versioned(monkeypatch):
    calls = []

    def read_or_schedule(namespace, identity, **call):
        calls.append((namespace, identity))
        return call["pending_payload"]

    monkeypatch.setattr(
        session_graph, "read_or_schedule_exact_snapshot", read_or_schedule
    )
    session_graph.fetch_session_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=[_MODEL_FILTER],
        interval="day",
        req_data_config={"type": "SYSTEM_METRIC", "id": "latency"},
        organization_id=ORG,
    )
    ((namespace, identity),) = calls
    assert namespace == "observe-session-system-graph"
    assert identity["payload_version"] == OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION


@pytest.mark.parametrize(
    "namespace",
    [
        "observe-system-graph",
        "observe-session-system-graph",
        "observe-user-system-graph",
    ],
)
def test_versioned_identity_never_reads_a_pre_median_snapshot(namespace):
    legacy = {
        "project_id": PROJECT,
        "filters": [_MODEL_FILTER],
        "interval": "day",
        "metric_id": "latency",
        "organization_id": ORG,
    }
    versioned = {**legacy, "payload_version": OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION}

    assert exact_aggregation_cache.snapshot_cache_key(
        namespace, versioned
    ) != exact_aggregation_cache.snapshot_cache_key(namespace, legacy)
    assert exact_aggregation_cache._observe_identity_alias_key(
        namespace, versioned
    ) != exact_aggregation_cache._observe_identity_alias_key(namespace, legacy)
    # The freezing boundary keeps the version in the identity it schedules.
    frozen = exact_aggregation_cache.normalize_exact_observe_identity(versioned)
    assert frozen["payload_version"] == OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION


@pytest.mark.parametrize(
    ("namespace", "reader", "module"),
    [
        (
            "observe-system-graph",
            "fetch_background_raw_system_metric_graph",
            "tracer.services.clickhouse.graph_dispatch",
        ),
        (
            "observe-session-system-graph",
            "read_exact_session_system_graph",
            "tracer.services.clickhouse.exact_graph_reads",
        ),
        (
            "observe-user-system-graph",
            "read_exact_user_system_graph",
            "tracer.services.clickhouse.exact_graph_reads",
        ),
    ],
)
def test_worker_ignores_the_version_key(monkeypatch, namespace, reader, module):
    import importlib

    from tracer.tasks import exact_aggregation

    received = []
    monkeypatch.setattr(
        importlib.import_module(module),
        reader,
        lambda **call: received.append(call) or {"ok": True},
    )
    monkeypatch.setattr(
        exact_aggregation, "_reauthorize_exact_observe_project", lambda _identity: None
    )
    monkeypatch.setattr(
        exact_aggregation, "_exact_observe_analytics", lambda: nullcontext("analytics")
    )
    payload = exact_aggregation._observe_payload(
        namespace,
        {
            "project_id": PROJECT,
            "filters": [],
            "interval": "day",
            "metric_id": "latency",
            "payload_version": OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION,
        },
    )
    assert payload == {"ok": True}
    (call,) = received
    assert "payload_version" not in call
    assert call["metric_id"] == "latency"
