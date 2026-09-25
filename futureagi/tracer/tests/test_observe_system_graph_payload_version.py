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
    payload = exact_aggregation._load_exact_payload(
        namespace,
        {
            "project_id": PROJECT,
            "filters": [],
            "interval": "day",
            "metric_id": "latency",
            "payload_version": OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION,
        },
    )
    # The worker writes the statistic into the payload it caches: the marker
    # that tells new readers this snapshot was computed by median-aware code.
    # (``_load_exact_payload`` is what refresh_exact_aggregation_snapshot
    # publishes.)
    assert payload == {"ok": True, "metric_statistic": "median"}
    (call,) = received
    assert "payload_version" not in call
    assert call["metric_id"] == "latency"


# ---------------------------------------------------------------------------
# Rolling deploy: a pre-median worker can still pick up a refresh job keyed by
# the new identity. It ignores payload_version, computes the old mean and
# caches it under the new key for up to 30 days. Only median-aware workers
# write ``metric_statistic`` into the payload, so a latency snapshot without
# ``metric_statistic == "median"`` is a cache miss, never served.
# ---------------------------------------------------------------------------

_WINDOW_FILTER = {
    "column_id": "created_at",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": ["2026-06-01T00:00:00+00:00", "2026-06-08T00:00:00+00:00"],
    },
}
_OLD_WORKER_MEAN = 777.5


def _cached_payload(metric_id, **extra):
    return {
        "metric_name": metric_id,
        "data": [
            {
                "timestamp": "2026-06-01T00:00:00+00:00",
                "value": _OLD_WORKER_MEAN,
                "primary_traffic": 3,
            }
        ],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        **extra,
    }


@pytest.fixture
def clean_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _seed_cache_on_first_read(monkeypatch, module, payload):
    """Publish ``payload`` under exactly the identity the reader asks for,
    as a worker would have, just before the reader's first cache read."""

    real = module.read_or_schedule_exact_snapshot
    seeded = []

    def read(namespace, identity, **options):
        if not seeded:
            exact_aggregation_cache.publish_exact_snapshot(
                namespace,
                exact_aggregation_cache.normalize_exact_observe_identity(identity),
                payload,
            )
            seeded.append(namespace)
        return real(namespace, identity, **options)

    monkeypatch.setattr(module, "read_or_schedule_exact_snapshot", read)
    # A miss never reaches Temporal in these tests.
    monkeypatch.setattr(
        exact_aggregation_cache,
        "_configured_exact_aggregation_task_queue",
        lambda: None,
    )
    return seeded


def _trace_graph(monkeypatch, metric_id):
    monkeypatch.setattr(
        graph_dispatch,
        "_affordable_raw_graph_seed",
        lambda **_: graph_dispatch._GraphReadUnaffordable(estimated_rows=None),
    )
    monkeypatch.setattr(
        graph_dispatch,
        "_schedule_unaffordable_graph_read",
        lambda **call: call["pending_payload"],
    )
    return graph_dispatch.fetch_system_metric_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=[_WINDOW_FILTER, _MODEL_FILTER],
        interval="day",
        metric_id=metric_id,
        organization_id=ORG,
    )


def _users_graph(monkeypatch, metric_id):
    monkeypatch.setattr(
        graph_dispatch,
        "_affordable_user_graph_read",
        lambda **_: graph_dispatch._GraphReadUnaffordable(estimated_rows=None),
    )
    monkeypatch.setattr(
        graph_dispatch,
        "_schedule_unaffordable_graph_read",
        lambda **call: call["pending_payload"],
    )
    return graph_dispatch.fetch_user_system_metric_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=[_WINDOW_FILTER, _MODEL_FILTER],
        interval="day",
        metric_id=metric_id,
        organization_id=ORG,
    )


def _session_graph(monkeypatch, metric_id):
    del monkeypatch
    return session_graph.fetch_session_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=[_WINDOW_FILTER, _MODEL_FILTER],
        interval="day",
        req_data_config={"type": "SYSTEM_METRIC", "id": metric_id},
        organization_id=ORG,
    )


_SURFACES = [
    pytest.param(graph_dispatch, _trace_graph, id="observe-system-graph"),
    pytest.param(graph_dispatch, _users_graph, id="observe-user-system-graph"),
    pytest.param(session_graph, _session_graph, id="observe-session-system-graph"),
]


def _served_values(payload):
    return [point.get("value") for point in payload.get("data") or []]


@pytest.mark.usefixtures("clean_cache")
@pytest.mark.parametrize(("module", "read_graph"), _SURFACES)
def test_latency_snapshot_cached_by_a_pre_median_worker_is_a_miss(
    monkeypatch, module, read_graph
):
    seeded = _seed_cache_on_first_read(monkeypatch, module, _cached_payload("latency"))
    payload = read_graph(monkeypatch, "latency")

    assert seeded, "the reader never consulted the snapshot cache"
    assert _OLD_WORKER_MEAN not in _served_values(payload), payload
    assert payload.get("query_status") != "complete", payload
    assert payload.get("query_cached") is not True, payload
    # The public boundary still names the statistic of what it will serve.
    assert payload.get("metric_statistic") == "median"


@pytest.mark.usefixtures("clean_cache")
@pytest.mark.parametrize(("module", "read_graph"), _SURFACES)
def test_latency_snapshot_marked_median_is_served(monkeypatch, module, read_graph):
    _seed_cache_on_first_read(
        monkeypatch,
        module,
        _cached_payload("latency", metric_statistic="median"),
    )
    payload = read_graph(monkeypatch, "latency")

    assert payload.get("query_status") == "complete", payload
    assert payload.get("query_cached") is True
    assert _served_values(payload) == [_OLD_WORKER_MEAN]
    assert payload.get("metric_statistic") == "median"


@pytest.mark.usefixtures("clean_cache")
@pytest.mark.parametrize(("module", "read_graph"), _SURFACES)
def test_non_latency_snapshot_without_a_marker_is_still_served(
    monkeypatch, module, read_graph
):
    # Only latency changed meaning; an old worker's token sum is still right.
    _seed_cache_on_first_read(monkeypatch, module, _cached_payload("tokens"))
    payload = read_graph(monkeypatch, "tokens")

    assert payload.get("query_status") == "complete", payload
    assert _served_values(payload) == [_OLD_WORKER_MEAN]
