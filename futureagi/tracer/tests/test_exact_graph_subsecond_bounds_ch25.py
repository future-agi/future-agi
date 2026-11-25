"""Sub-second exact system graph windows, executed on the CH25 sidecar.

Rows sit one microsecond either side of a whole second. Every exact system
graph route must count exactly the latest live rows in the half-open request
window, including when a bound has a fractional second. The raw interactive
lane (``resolve_span_versions=False``) counts live physical rows instead, so
an unmerged older version still counts there; it must still use the exact
window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings

from tracer.services.clickhouse import exact_graph_reads, graph_dispatch
from tracer.services.clickhouse.query_builders import base as base_query_module
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.exact_aggregation_cache import normalize_exact_observe_identity
from tracer.tests import test_graph_native_cost_coverage as graph_fixtures
from tracer.tests.test_graph_native_cost_coverage import (
    PROJECT,
    NativeAnalytics,
    insert_span,
)

native_graph_db = graph_fixtures.native_graph_db
pytestmark = pytest.mark.integration

T = datetime(2026, 10, 20, 12, 56, 54, tzinfo=UTC)
US = timedelta(microseconds=1)
PROD = {"stage": "prod"}

# (span id, start, attrs). Every span is the root of its own trace.
BOUNDARY_ROWS = [
    ("a", T - US, PROD),
    ("b", T, PROD),
    ("c", T + US, PROD),
    ("d", T + timedelta(milliseconds=500), PROD),
    ("x", T + timedelta(milliseconds=750), {"stage": "dev"}),
    ("e", T + timedelta(microseconds=999_999), PROD),
    ("f", T + timedelta(seconds=1), PROD),
    ("h", T + timedelta(milliseconds=1250), PROD),
]

# (window, filtered traffic, unfiltered traffic, raw-lane filtered traffic,
# filtered trace ids). Traffic and trace ids count the latest version of each
# span. The raw lane counts live physical rows, so both unmerged versions of
# ``r`` count wherever they fall inside the window: v1 alone in the
# one-microsecond window, v1 and v2 in the whole second.
WINDOWS = {
    "one-microsecond": ((T, T + US), 1, 1, 2, ["b"]),
    "fractional-both-edges": (
        (T + timedelta(milliseconds=500), T + timedelta(milliseconds=1500)),
        4,
        5,
        4,
        ["d", "e", "f", "h"],
    ),
    "whole-second": (
        (T, T + timedelta(seconds=1)),
        5,
        6,
        6,
        ["b", "c", "d", "e", "r"],
    ),
}


def _window_filter(start: datetime, end: datetime) -> dict:
    return {
        "column_id": "start_time",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start, end],
        },
    }


def _stage_filter() -> dict:
    return {
        "column_id": "stage",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["prod"],
        },
    }


def _seed_boundary_rows(client) -> None:
    client.execute("SYSTEM STOP MERGES spans")
    for span_id, start, attrs in BOUNDARY_ROWS:
        insert_span(client, span_id=span_id, start=start, attrs=attrs)
    # A newer version moves ``r`` out of the one-microsecond window inside the
    # same replacement-key hour; only that latest version may count.
    insert_span(client, span_id="r", start=T, attrs=PROD, version=1)
    insert_span(client, span_id="r", start=T + US, attrs=PROD, version=2)


def _graph_traffic(payload: dict) -> int:
    return sum(point["primary_traffic"] for point in payload["data"])


def _bundle_traffic(metrics: dict) -> int:
    return sum(point["traffic"] for point in metrics["traffic"])


def _builder(filters, observe_type, **kwargs) -> TimeSeriesQueryBuilder:
    start, end = TimeSeriesQueryBuilder.parse_time_range(filters, strict=True)
    return TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=filters,
        interval="hour",
        exact_snapshot=True,
        observe_type=observe_type,
        start_date=start,
        end_date=end,
        **kwargs,
    )


@pytest.mark.parametrize("window", sorted(WINDOWS))
def test_every_exact_route_counts_the_exact_window(
    native_graph_db, monkeypatch, window
):
    client = native_graph_db
    try:
        _seed_boundary_rows(client)
        (
            (start, end),
            filtered_traffic,
            unfiltered_traffic,
            raw_filtered_traffic,
            filtered_trace_ids,
        ) = WINDOWS[window]
        analytics = NativeAnalytics(client)
        unfiltered = [_window_filter(start, end)]
        filtered = [*unfiltered, _stage_filter()]
        enumerated_trace_ids = []
        enumerate_trace_ids = exact_graph_reads._enumerate_exact_trace_ids

        def record_enumerated_trace_ids(**kwargs):
            result = enumerate_trace_ids(**kwargs)
            enumerated_trace_ids.append(sorted(result[0]))
            return result

        monkeypatch.setattr(
            exact_graph_reads,
            "_enumerate_exact_trace_ids",
            record_enumerated_trace_ids,
        )

        bundle = exact_graph_reads.read_exact_all_system_metrics(
            analytics=analytics, project_id=PROJECT, filters=unfiltered, interval="hour"
        )
        assert _bundle_traffic(bundle) == unfiltered_traffic

        for observe_type in ("span", "trace"):
            graph = exact_graph_reads.read_exact_system_graph(
                analytics=analytics,
                project_id=PROJECT,
                filters=unfiltered,
                interval="hour",
                metric_id="latency",
                observe_type=observe_type,
            )
            assert graph["query_complete"] is True
            assert _graph_traffic(graph) == unfiltered_traffic, observe_type

            # The filtered readers: trace enumeration then contribution
            # batches, and the span reader's adaptive partitions.
            graph = exact_graph_reads.read_exact_system_graph(
                analytics=analytics,
                project_id=PROJECT,
                filters=filtered,
                interval="hour",
                metric_id="latency",
                observe_type=observe_type,
            )
            assert graph["query_complete"] is True, observe_type
            assert _graph_traffic(graph) == filtered_traffic, observe_type

            for resolve, expected in (
                (True, filtered_traffic),
                (False, raw_filtered_traffic),
            ):
                builder = _builder(
                    filtered, observe_type, resolve_span_versions=resolve
                )
                query, params = builder.build()
                result = analytics.execute_ch_query(query, params)
                metrics = builder.format_result(result.data, result.columns)
                assert _bundle_traffic(metrics) == expected, (
                    observe_type,
                    resolve,
                )

        # Only the filtered trace graph enumerates trace ids. Its classifier
        # applies the window to each trace's latest root, so the stale ``r``
        # version inside the one-microsecond window does not make ``r`` a member.
        assert enumerated_trace_ids == [filtered_trace_ids]

        # The trace reader passes only fully classified member traces.
        builder = _builder(filtered, "trace")
        query, params = builder.build_exact_trace_contribution_batch(
            [span_id for span_id, _, attrs in BOUNDARY_ROWS if attrs == PROD] + ["r"]
        )
        result = analytics.execute_ch_query(query, params)
        rows, columns = exact_graph_reads._merge_exact_trace_contribution_rows(
            [(result.data, result.columns)]
        )
        assert _bundle_traffic(builder.format_result(rows, columns)) == (
            filtered_traffic
        )

        builder = _builder(filtered, "span")
        query, params = builder.build_exact_span_partition(
            # The filter parser yields naive UTC bounds; partitions match it.
            partition_start=datetime(2026, 10, 20, 12),
            partition_end=datetime(2026, 10, 20, 13),
        )
        result = analytics.execute_ch_query(query, params)
        rows, columns = exact_graph_reads._merge_exact_trace_contribution_rows(
            [(result.data, result.columns)]
        )
        assert _bundle_traffic(builder.format_result(rows, columns)) == (
            filtered_traffic
        )

        # Interactive direct lane: cost resolves versions, traffic reads the
        # raw lane's physical rows.
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: None
        )
        monkeypatch.setattr(
            graph_dispatch, "_affordable_raw_graph_seed", lambda **_: (None, 0)
        )
        for observe_type in ("span", "trace"):
            for metric_id, expected in (
                ("traffic", raw_filtered_traffic),
                ("cost", filtered_traffic),
            ):
                graph = graph_dispatch.fetch_system_metric_graph_ch(
                    analytics=analytics,
                    project_id=PROJECT,
                    filters=filtered,
                    interval="hour",
                    metric_id=metric_id,
                    observe_type=observe_type,
                )
                assert _graph_traffic(graph) == expected, (
                    observe_type,
                    metric_id,
                )
    finally:
        client.execute("SYSTEM START MERGES spans")


@pytest.mark.parametrize("observe_type", ["span", "trace"])
def test_equals_operator_counts_exactly_that_microsecond(native_graph_db, observe_type):
    client = native_graph_db
    try:
        _seed_boundary_rows(client)
        analytics = NativeAnalytics(client)
        filters = [
            {
                "column_id": "start_time",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "equals",
                    "filter_value": T,
                },
            },
            _stage_filter(),
        ]
        builder = _builder(filters, observe_type)
        query, params = builder.build()
        result = analytics.execute_ch_query(query, params)
        assert _bundle_traffic(builder.format_result(result.data, result.columns)) == 1
    finally:
        client.execute("SYSTEM START MERGES spans")


PARENT = {
    "span_id": "parent",
    "trace": "run",
    "kind": "agent",
    "name": "pydantic_ai.agent.run",
    "cost": 1.25,
    "tokens": 120,
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "attrs": {"fi.cost.kind": "provider_reported"},
}
CHILD = {
    "span_id": "child",
    "trace": "run",
    "parent": "parent",
    "cost": 1.25,
    "tokens": 120,
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "attrs": {"fi.cost.kind": "provider_reported"},
}


@pytest.mark.parametrize(
    "parent_at,child_at,window,traffic,mean_cost",
    [
        # The usage child sits in [floor(hi), hi): it proves the repeat, so
        # the summary's usage is masked and the child's is counted.
        (
            timedelta(milliseconds=250),
            timedelta(milliseconds=1250),
            (timedelta(0), timedelta(milliseconds=1500)),
            2,
            0.625,
        ),
        # The usage child sits in [floor(lo), lo): it is outside the window,
        # so it cannot mask the in-window summary.
        (
            timedelta(milliseconds=750),
            timedelta(milliseconds=250),
            (timedelta(milliseconds=500), timedelta(seconds=2)),
            1,
            1.25,
        ),
    ],
    ids=["child-in-upper-sliver", "child-in-lower-sliver"],
)
def test_repeated_summary_usage_fence_uses_the_exact_window(
    native_graph_db, parent_at, child_at, window, traffic, mean_cost
):
    client = native_graph_db
    insert_span(client, **PARENT, start=T + parent_at)
    insert_span(client, **CHILD, start=T + child_at)
    analytics = NativeAnalytics(client)
    filters = [_window_filter(T + window[0], T + window[1])]

    bundle = exact_graph_reads.read_exact_all_system_metrics(
        analytics=analytics, project_id=PROJECT, filters=filters, interval="hour"
    )

    assert _bundle_traffic(bundle) == traffic
    assert bundle["cost"][0]["value"] == mean_cost
    assert bundle["cost"][0]["cost_coverage"]["known_fact_count"] == 1
    assert bundle["cost"][0]["cost_coverage"]["known_subtotal"] == 1.25
    assert bundle["tokens"][0]["value"] == 120
    for observe_type in ("span", "trace"):
        graph = exact_graph_reads.read_exact_system_graph(
            analytics=analytics,
            project_id=PROJECT,
            filters=filters,
            interval="hour",
            metric_id="cost",
            observe_type=observe_type,
        )
        assert graph["data"][0]["value"] == mean_cost, observe_type
        assert _graph_traffic(graph) == traffic, observe_type


def test_frozen_default_window_counts_both_fractional_edges(
    native_graph_db, monkeypatch
):
    client = native_graph_db
    now = datetime(2026, 10, 20, 12, 56, 54, 500000)
    lookback = timedelta(days=settings.ANALYTICS_DEFAULT_LOOKBACK_DAYS)
    try:
        _seed_boundary_rows(client)
        # Inside [floor(lo), lo): outside the frozen window.
        insert_span(
            client,
            span_id="before-lower",
            start=(now - lookback - timedelta(milliseconds=250)).replace(tzinfo=UTC),
        )
        insert_span(
            client,
            span_id="at-lower",
            start=(now - lookback).replace(tzinfo=UTC),
        )

        class FrozenDateTime(datetime):
            @classmethod
            def utcnow(cls):
                return now

        with monkeypatch.context() as patched:
            patched.setattr(base_query_module, "datetime", FrozenDateTime)
            frozen = normalize_exact_observe_identity(
                {"project_id": PROJECT, "filters": [], "metric_id": "latency"}
            )
        assert frozen["filters"][0]["filter_config"]["filter_value"][1] == (
            "2026-10-20T12:56:54.500000Z"
        )

        bundle = exact_graph_reads.read_exact_all_system_metrics(
            analytics=NativeAnalytics(client),
            project_id=PROJECT,
            filters=frozen["filters"],
            interval="day",
        )

        # at-lower, a, b, c and the latest version of r. d sits exactly on the
        # exclusive upper bound.
        assert _bundle_traffic(bundle) == 5
    finally:
        client.execute("SYSTEM START MERGES spans")
