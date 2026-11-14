"""Span-mode trace_name root lookups: bound everywhere, scoped per statement.

A span-mode trace_name leaf resolves a span through its trace's live root
(``(project_id, trace_id) IN (SELECT ... FROM spans FINAL ...)``). Two
contracts:

- every placeholder the lookup adds is bound by every span-mode caller, so a
  leaf from the API or an agent cannot fail at execution;
- a statement with a finite candidate set (a classifier batch, a graph
  partition, a Users slice or batch, a cluster's traces) looks up only those
  traces' roots, instead of every root of the request window per statement.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from tracer.models.monitor import MonitorMetricTypeChoices
from tracer.services.clickhouse import span_attribute_lookups
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.trace_name_sql import TRACE_NAME_ROOT_TRACES_MARKER
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT = "00000000-0000-4000-8000-0000000000a1"
ORG = "00000000-0000-4000-8000-0000000000a2"
USER = "00000000-0000-4000-8000-0000000000a3"
START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(days=30)
PLACEHOLDER = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
ROOT_LOOKUP = "SELECT project_id, trace_id FROM spans FINAL PREWHERE"


def _leaf(col_type="SYSTEM_METRIC"):
    return {
        "column_id": "trace_name",
        "filter_config": {
            "col_type": col_type,
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "checkout",
        },
    }


def _date():
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _assert_bound(sql: str, params: dict) -> None:
    missing = set(PLACEHOLDER.findall(sql)) - set(params)
    assert not missing, f"unbound placeholders {sorted(missing)}"


def _root_scopes(sql: str) -> list[str]:
    """Each root lookup's PREWHERE, the part that decides what it reads."""

    scopes = []
    for match in re.finditer(re.escape(ROOT_LOOKUP), sql):
        tail = sql[match.end() :]
        scopes.append(" ".join(tail[: tail.index("WHERE is_deleted = 0")].split()))
    assert scopes, "no trace_name root lookup in the statement"
    return scopes


def test_cluster_rca_trace_scoping_binds_every_placeholder():
    captured = {}

    class Client:
        def execute_read(self, query, params, **_):
            captured.update(query=query, params=params)
            return [("t-1",)], [], 1

    with (
        mock.patch.object(
            span_attribute_lookups, "is_clickhouse_enabled", return_value=True
        ),
        mock.patch.object(span_attribute_lookups, "ClickHouseClient", Client),
    ):
        result = span_attribute_lookups.scoped_trace_ids(
            PROJECT, ["t-1", "t-2"], [_leaf()]
        )
    assert result == ["t-1"]
    _assert_bound(captured["query"], captured["params"])
    (scope,) = _root_scopes(captured["query"])
    # The agent has the cluster's traces and no window: resolve their roots.
    assert "trace_id IN %(_tids)s" in scope
    assert "start_date" not in scope and "end_date" not in scope


@pytest.mark.parametrize(
    "build",
    [
        lambda b: b.build_metric_value_query(
            MonitorMetricTypeChoices.COUNT_OF_ERRORS, START, END
        ),
        lambda b: b.build_historical_stats_query(
            MonitorMetricTypeChoices.COUNT_OF_ERRORS, START, END
        ),
        lambda b: b.build_time_series_query(
            MonitorMetricTypeChoices.COUNT_OF_ERRORS, START, END, 3600
        ),
    ],
    ids=["value", "historical", "time-series"],
)
@pytest.mark.parametrize("col_type", ["SYSTEM_METRIC", None])
def test_monitor_statements_bind_every_placeholder(build, col_type):
    leaf = _leaf(col_type)
    if col_type is None:
        del leaf["filter_config"]["col_type"]
    builder = MonitorMetricsQueryBuilderV2(
        project_id=PROJECT, filters={"span_attributes_filters": [leaf]}
    )
    sql, params = build(builder)
    _assert_bound(sql, params)
    assert params["end_date"] == params["end_time"]


@pytest.mark.parametrize("full_state", [False, True])
def test_span_list_classifier_reads_only_its_candidates_roots(full_state):
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[] if full_state else [_date(), _leaf()],
        bounded_identity_only=True,
    )
    if full_state:
        builder.filters = [_leaf()]
    sql, params = builder.build_filter_match_query(
        ["a" * 32, "b" * 32], candidate_full_state=full_state
    )
    _assert_bound(sql, params)
    for scope in _root_scopes(sql):
        assert (
            "trace_id IN (SELECT trace_id FROM resolved_annotation_candidates)" in scope
        )
        # Continuous eval tasks classify full state: candidates are not
        # limited to the request window, and neither are their roots.
        assert ("start_date" in scope) is not full_state


def test_full_state_classifier_bounds_its_root_lookup_by_its_candidates():
    """Round 3: full-state classification has no read window, so its root
    lookup is bounded by the batch's own start times (whole hours), which
    lets partition and primary-key pruning apply instead of reading the
    project's whole history per batch."""
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=[], bounded_identity_only=True
    )
    builder.filters = [_leaf()]
    sql, params = builder.build_filter_match_query(
        ["a" * 32, "b" * 32], candidate_full_state=True
    )
    _assert_bound(sql, params)
    for scope in _root_scopes(sql):
        span = (
            "(SELECT (min(start_time), max(start_time)) FROM "
            "resolved_annotation_candidates)"
        )
        assert (
            f"toStartOfHour(start_time) > tupleElement({span}, 1) "
            "- INTERVAL 1 DAY - INTERVAL 1 HOUR"
        ) in scope
        assert (
            f"toStartOfHour(start_time) <= tupleElement({span}, 2) + INTERVAL 1 HOUR"
        ) in scope


def test_cluster_rca_root_lookup_is_bounded_by_its_traces_rows():
    """Round 3: the cluster's traces bound the root lookup in time through
    their traces rows (a primary-key read), not the project's history."""
    captured = {}

    class Client:
        def execute_read(self, query, params, **_):
            captured.update(query=query, params=params)
            return [("t-1",)], [], 1

    with (
        mock.patch.object(
            span_attribute_lookups, "is_clickhouse_enabled", return_value=True
        ),
        mock.patch.object(span_attribute_lookups, "ClickHouseClient", Client),
    ):
        span_attribute_lookups.scoped_trace_ids(PROJECT, ["t-1", "t-2"], [_leaf()])
    _assert_bound(captured["query"], captured["params"])
    (scope,) = _root_scopes(captured["query"])
    assert (
        "toStartOfHour(start_time) > ifNull((SELECT minOrNull(created_at) FROM "
        "traces WHERE project_id = %(_pid)s AND id IN %(_tids)s)"
    ) in scope
    assert "maxOrNull(created_at)" in scope


def test_exact_graph_partition_reads_only_its_partitions_roots():
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=[_leaf()],
        interval="hour",
        observe_type="span",
        start_date=START,
        end_date=END,
        exact_snapshot=True,
        resolve_span_versions=True,
    )
    sql, params = builder.build_exact_span_partition(
        partition_start=START + timedelta(days=3),
        partition_end=START + timedelta(days=3, hours=1),
    )
    _assert_bound(sql, params)
    for scope in _root_scopes(sql):
        assert "%(graph_partition_start)s" in scope
        assert "%(graph_partition_end)s" in scope
        # The request window still bounds how early a root may start.
        assert "%(start_date)s - INTERVAL 1 DAY" in scope


def _users_builder(*filters):
    return UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=[_date(), *filters]
    )


def test_users_walk_statements_read_only_their_ranges_roots():
    builder = _users_builder(_leaf())
    builder.walk_witness = next(
        w for w in builder.matching_activity_witnesses() if w.family == "native"
    )
    statements = [
        builder.build_matching_activity_slice_query(
            slice_start=START, slice_end=START + timedelta(hours=1), limit=10
        ),
        builder.build_matching_activity_instant_query(instant=START, limit=10),
        builder.build_matching_activity_existence_query(
            range_start=START, range_end=START + timedelta(hours=1)
        ),
        builder.build_matching_activity_existence_estimate_query(
            range_start=START, range_end=START + timedelta(hours=1)
        ),
    ]
    for sql, params in statements:
        _assert_bound(sql, params)
        assert TRACE_NAME_ROOT_TRACES_MARKER not in sql
        for scope in _root_scopes(sql):
            assert "%(slice_start_us)s" in scope and "%(slice_end_us)s" in scope


def test_users_finite_batch_reads_only_its_users_roots():
    builder = _users_builder(_leaf())
    sql, params = builder.build_native_span_dimension_query([USER], [(1, _leaf())])
    _assert_bound(sql, params)
    for scope in _root_scopes(sql):
        assert "FROM latest_candidate_spans" in scope


def test_root_lookup_is_whole_hours_and_needs_a_window_or_candidates():
    builder = ClickHouseFilterBuilderV2(project_id=PROJECT, query_mode="span")
    sql, _ = builder.translate([_leaf()])
    (scope,) = _root_scopes(sql)
    assert "toStartOfHour(start_time) >" in scope
    assert "toStartOfHour(start_time) <= %(end_date)s" in scope
    assert "start_time >= %(start_date)s" not in scope
    unbounded = ClickHouseFilterBuilderV2(
        project_id=PROJECT, query_mode="span", trace_name_root_window=None
    )
    with pytest.raises(UnsupportedFilterShapeError):
        unbounded.translate([_leaf()])
    # Candidate traces alone no longer suffice: without a window the lookup
    # needs their time range too, or it would read the project's history.
    traces_only = ClickHouseFilterBuilderV2(
        project_id=PROJECT,
        query_mode="span",
        trace_name_root_window=None,
        trace_name_root_traces_sql="trace_id IN %(_tids)s",
    )
    with pytest.raises(UnsupportedFilterShapeError):
        traces_only.translate([_leaf()])
