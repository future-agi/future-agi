"""The Voice chart counts the Voice list's population.

A voice call is a trace whose canonical root span is a ``conversation`` span
(``VoiceCallListQueryBuilder._bounded_delegate`` adds that private root leaf to
every voice-list read). Dev QA on 2026-09-25 found the chart and the list
disagreeing on one simulator project and one seven-day window: the Voice list
returned 82 calls while the chart, ``get_graph_methods`` with no voice scope,
summed 83. The 83rd trace had an ``unknown`` root span.

The fixture below reproduces both shapes a trace-level graph can get wrong:

* ``catalog-probe``: a trace whose root is not a conversation (the dev case);
* ``child-conversation``: a conversation span that is a *child*, which an
  any-span ``observation_type`` predicate would admit although the list does
  not.
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse.graph_dispatch import (
    fetch_background_raw_system_metric_graph,
)
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_exact_graph_filter_predicates,
    is_internal_trace_root_filter,
)
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VOICE_CALL_ROOT_FILTER,
)
from tracer.services.exact_aggregation_cache import (
    normalize_exact_observe_identity,
    snapshot_cache_key,
)

PROJECT_ID = "00000000-0000-4000-8000-00000000f0ce"
ROOT_GUARD = "(parent_span_id IS NULL OR parent_span_id = '')"


def _public_observation_type_leaf(col_type):
    return {
        "column_id": "observation_type",
        "filter_config": {
            "col_type": col_type,
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "conversation",
        },
    }


@pytest.mark.unit
def test_exact_graph_row_predicate_binds_voice_invariant_to_the_root():
    plan = compile_exact_graph_row_predicates(
        [VOICE_CALL_ROOT_FILTER],
        project_id=PROJECT_ID,
        observe_type="trace",
    )

    assert len(plan.predicates) == 1
    assert plan.predicates[0].startswith(f"{ROOT_GUARD} AND (")
    assert "observation_type" in plan.predicates[0]
    assert list(plan.params.values()) == ["conversation"]


@pytest.mark.unit
@pytest.mark.parametrize("col_type", ["SYSTEM_METRIC", "INTERNAL_ROOT_METRIC"])
def test_public_observation_type_leaves_keep_any_span_graph_semantics(col_type):
    # Only the unforgeable marker makes the column a root predicate; the
    # request serializer rejects that key, so a column type alone never does.
    plan = compile_exact_graph_row_predicates(
        [_public_observation_type_leaf(col_type)],
        project_id=PROJECT_ID,
        observe_type="trace",
    )

    assert "parent_span_id" not in plan.predicates[0]


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_exact_graph_membership_binds_voice_invariant_to_the_root(observe_type):
    # Eval and annotation graphs compile membership here. Voice membership is a
    # property of the trace, so span-attached rows follow their owning trace.
    sql, params = compile_exact_graph_filter_predicates(
        [VOICE_CALL_ROOT_FILTER],
        project_id=PROJECT_ID,
        observe_type=observe_type,
        annotation_label_ids=(),
    )

    assert ROOT_GUARD in sql
    assert " ".join(sql.split()).startswith("( trace_id IN (")
    assert list(params.values()) == ["conversation"]


# --------------------------------------------------------------------------
# Real ClickHouse: the dev reproduction.
# --------------------------------------------------------------------------

_SPANS_DDL = """
CREATE TABLE spans (
    project_id UUID,
    observation_type LowCardinality(String),
    service_name LowCardinality(String) DEFAULT '',
    start_time DateTime64(6, 'UTC'),
    trace_id String,
    id String,
    parent_span_id String DEFAULT '',
    name String DEFAULT '',
    status LowCardinality(String) DEFAULT 'OK',
    cost Float64 DEFAULT 0,
    total_tokens Int32 DEFAULT 0,
    prompt_tokens Int32 DEFAULT 0,
    completion_tokens Int32 DEFAULT 0,
    latency_ms Int32 DEFAULT 10,
    is_deleted UInt8 DEFAULT 0,
    _version UInt64 DEFAULT 1
) ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toDate(start_time)
ORDER BY (project_id, observation_type, service_name,
          toStartOfHour(start_time), trace_id, id)
"""

# (trace_id, span id, parent span id, observation_type)
_FIXTURE_SPANS = (
    ("voice-call", "voice-root", "", "conversation"),
    ("catalog-probe", "probe-root", "", "unknown"),
    ("child-conversation", "llm-root", "", "llm"),
    ("child-conversation", "conversation-child", "llm-root", "conversation"),
)
VOICE_TRACES = {"voice-call"}
ALL_SPAN_COUNT = len(_FIXTURE_SPANS)


class _LiveAnalytics:
    """Run the product's statements on the test-owned ClickHouse database."""

    supports_per_query_read_settings = True

    def __init__(self, client):
        self._client = client

    def execute_ch_query(self, query, params, *, timeout_ms=None, settings=None):
        del timeout_ms, settings
        bound = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in (params or {}).items()
        }
        rows, columns = self._client.execute(query, bound, with_column_types=True)
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            columns=names,
        )


@pytest.fixture(scope="module")
def ch_database():
    with _ch_test_owned_database("test_voice_graph_scope_") as database:
        yield database


@pytest.fixture()
def ch_spans(ch_database):
    """A fresh ``spans`` table holding the voice/non-voice fixture."""

    hour = (datetime.now(UTC) - timedelta(days=1)).replace(
        minute=0, second=0, microsecond=0
    )

    def load(project_id):
        client.execute(
            "INSERT INTO spans"
            " (project_id, observation_type, start_time, trace_id, id, parent_span_id)"
            " VALUES",
            [
                (
                    uuid.UUID(str(project_id)),
                    observation_type,
                    hour + timedelta(minutes=index),
                    trace_id,
                    span_id,
                    parent_span_id,
                )
                for index, (trace_id, span_id, parent_span_id, observation_type) in (
                    enumerate(_FIXTURE_SPANS)
                )
            ],
        )

    with _ch_test_native_client(database=ch_database) as client:
        client.execute(_SPANS_DDL)
        try:
            yield SimpleNamespace(client=client, hour=hour, load=load)
        finally:
            client.execute("DROP TABLE IF EXISTS spans SYNC")


@pytest.mark.integration
def test_eval_and_annotation_membership_selects_only_voice_calls(ch_spans):
    ch_spans.load(PROJECT_ID)
    start = ch_spans.hour - timedelta(hours=1)
    end = ch_spans.hour + timedelta(hours=2)
    sql, params = compile_exact_graph_filter_predicates(
        [VOICE_CALL_ROOT_FILTER],
        project_id=PROJECT_ID,
        observe_type="trace",
        annotation_label_ids=(),
    )

    rows = ch_spans.client.execute(
        f"""
        SELECT DISTINCT trace_id FROM spans FINAL
        WHERE project_id = toUUID(%(project_id)s) AND is_deleted = 0
          AND start_time >= %(snapshot_start_date)s
          AND start_time < %(snapshot_end_date)s
          AND {sql}
        """,
        {
            **params,
            "project_id": PROJECT_ID,
            "start_date": start,
            "end_date": end,
            "snapshot_start_date": start,
            "snapshot_end_date": end,
        },
    )

    assert {trace_id for (trace_id,) in rows} == VOICE_TRACES


def _post_graph(auth_client, project_id, window_start, window_end, **scope):
    return auth_client.post(
        "/tracer/trace/get_graph_methods/",
        {
            "project_id": str(project_id),
            "interval": "day",
            "property": "average",
            "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
            "filters": [
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            window_start.isoformat(),
                            window_end.isoformat(),
                        ],
                    },
                }
            ],
            **scope,
        },
        format="json",
    )


def _traffic_sum(response):
    body = response.json()
    result = body.get("result", body)
    return sum(point["primary_traffic"] for point in result["data"])


@pytest.mark.integration
@pytest.mark.django_db
def test_voice_graph_counts_the_voice_list_population(
    auth_client, observe_project, ch_spans, monkeypatch
):
    ch_spans.load(observe_project.id)
    monkeypatch.setattr(
        "tracer.views.trace.V2AnalyticsQueryService",
        lambda: _LiveAnalytics(ch_spans.client),
    )
    window_start = ch_spans.hour - timedelta(days=3)
    window_end = ch_spans.hour + timedelta(days=1)

    trace_graph = _post_graph(auth_client, observe_project.id, window_start, window_end)
    voice_graph = _post_graph(
        auth_client,
        observe_project.id,
        window_start,
        window_end,
        observe_type="voice",
    )

    assert trace_graph.status_code == status.HTTP_200_OK, trace_graph.content
    assert voice_graph.status_code == status.HTTP_200_OK, voice_graph.content
    # The unscoped trace graph is unchanged: every span in the window.
    assert _traffic_sum(trace_graph) == ALL_SPAN_COUNT
    # The Voice chart counts exactly the Voice list's calls (one span each).
    assert _traffic_sum(voice_graph) == len(VOICE_TRACES)
    voice_result = voice_graph.json().get("result", voice_graph.json())
    # Attestation names the voice surface and only the caller's own leaves.
    assert voice_result["query_applied_filter_count"] == 0


# --------------------------------------------------------------------------
# Cached and background reads: the population must survive the exact
# identity. The chart's refresh (``?refresh=true``), a read too costly for the
# interactive wall and a degraded interactive read all hand the graph to the
# exact-aggregation worker through ``normalize_exact_observe_identity``, and
# the cache serves what that worker published to every later page load.
# --------------------------------------------------------------------------


def _voice_identity(window_start, window_end, voice_leaf=VOICE_CALL_ROOT_FILTER):
    return {
        "project_id": PROJECT_ID,
        "filters": [
            {
                "column_id": "created_at",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        window_start.isoformat(),
                        window_end.isoformat(),
                    ],
                },
            },
            voice_leaf,
        ],
        "interval": "day",
        "metric_id": "latency",
        "observe_type": "trace",
    }


@pytest.mark.unit
def test_exact_graph_identity_keeps_the_voice_invariant():
    end = datetime(2026, 9, 25, tzinfo=UTC)
    identity = normalize_exact_observe_identity(
        _voice_identity(end - timedelta(days=7), end)
    )

    root_leaves = [
        item for item in identity["filters"] if is_internal_trace_root_filter(item)
    ]
    assert len(root_leaves) == 1
    # The worker compiles exactly this conjunction.
    worker_plan = compile_exact_graph_row_predicates(
        root_leaves,
        project_id=PROJECT_ID,
        observe_type="trace",
    )
    assert worker_plan.predicates[0].startswith(f"{ROOT_GUARD} AND (")
    # Lease, publish and read re-derive the key from the identity they carry.
    assert normalize_exact_observe_identity(identity) == identity
    # A public leaf spelled like the private one is an any-span filter, so it
    # must not share the voice graph's cached payload.
    lookalike = normalize_exact_observe_identity(
        _voice_identity(
            end - timedelta(days=7),
            end,
            voice_leaf=_public_observation_type_leaf("INTERNAL_ROOT_METRIC"),
        )
    )
    assert snapshot_cache_key("observe-system-graph", lookalike) != (
        snapshot_cache_key("observe-system-graph", identity)
    )


@pytest.mark.unit
@pytest.mark.django_db
def test_public_graph_request_cannot_carry_the_private_root_marker(
    auth_client, observe_project
):
    # Keeping the marker in the exact identity is safe only while no request
    # can put it there.
    response = auth_client.post(
        "/tracer/trace/get_graph_methods/",
        {
            "project_id": str(observe_project.id),
            "interval": "day",
            "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
            "filters": [VOICE_CALL_ROOT_FILTER],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "_eval_task_trace_root" in response.content.decode()


@pytest.mark.integration
def test_background_voice_graph_counts_the_voice_list_population(ch_spans):
    ch_spans.load(PROJECT_ID)
    identity = normalize_exact_observe_identity(
        _voice_identity(
            ch_spans.hour - timedelta(days=3),
            ch_spans.hour + timedelta(days=1),
        )
    )

    graph = fetch_background_raw_system_metric_graph(
        analytics=_LiveAnalytics(ch_spans.client),
        project_id=PROJECT_ID,
        filters=identity["filters"],
        interval=identity["interval"],
        metric_id=identity["metric_id"],
        observe_type=identity["observe_type"],
    )

    assert graph["query_status"] == "complete"
    assert sum(point["primary_traffic"] for point in graph["data"]) == len(VOICE_TRACES)


@pytest.mark.integration
@pytest.mark.django_db
@override_settings(EXACT_AGGREGATION_TASK_QUEUE="exact_aggregation")
def test_refreshed_voice_graph_worker_counts_the_voice_list_population(
    auth_client, observe_project, ch_spans, monkeypatch
):
    # The chart's refresh button: the view schedules the worker, which then
    # publishes the payload every later page load is served from cache.
    from tracer.tasks import exact_aggregation

    cache.clear()
    ch_spans.load(observe_project.id)
    analytics = _LiveAnalytics(ch_spans.client)
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(
        exact_aggregation, "_exact_observe_analytics", lambda: nullcontext(analytics)
    )
    window_start = ch_spans.hour - timedelta(days=3)
    window_end = ch_spans.hour + timedelta(days=1)

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        response = auth_client.post(
            "/tracer/trace/get_graph_methods/?refresh=true",
            {
                "project_id": str(observe_project.id),
                "interval": "day",
                "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
                "filters": [
                    {
                        "column_id": "created_at",
                        "filter_config": {
                            "col_type": "SYSTEM_METRIC",
                            "filter_type": "datetime",
                            "filter_op": "between",
                            "filter_value": [
                                window_start.isoformat(),
                                window_end.isoformat(),
                            ],
                        },
                    }
                ],
                "observe_type": "voice",
            },
            format="json",
        )
    cache.clear()

    assert response.status_code == status.HTTP_200_OK, response.content
    assert enqueue.call_count == 1
    task = enqueue.call_args.kwargs["kwargs"]
    assert task["namespace"] == "observe-system-graph"
    payload = exact_aggregation._observe_payload(task["namespace"], task["identity"])

    assert payload["query_status"] == "complete"
    assert sum(point["primary_traffic"] for point in payload["data"]) == len(
        VOICE_TRACES
    )
