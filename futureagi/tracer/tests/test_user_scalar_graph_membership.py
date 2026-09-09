"""Offline contracts for attribute-only Users system-graph membership."""

import re
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graph

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
END = datetime(2026, 9, 4, 12, 30, 0, 654321)


def _leaf(
    key="company_id",
    value="company",
    *,
    kind="text",
    op="equals",
    source="SPAN_ATTRIBUTE",
):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": source,
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


def _date(days=7):
    return _leaf(
        "created_at",
        [END - timedelta(days=days), END],
        kind="datetime",
        op="between",
        source="SYSTEM_METRIC",
    )


class _Analytics:
    def __init__(self):
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, dict(params), dict(settings)))
        return SimpleNamespace(data=[], columns=[])


def _read(filters):
    analytics = _Analytics()
    graph.read_exact_user_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=filters,
        interval="day",
        metric_id="active_users",
    )
    assert len(analytics.calls) == 1
    return analytics.calls[0]


def _assert_fused_positive_membership_and_metrics(
    sql, filters, *, bucket_fn="toStartOfDay"
):
    rows, having, _ = graph._user_membership_having(filters, project_id=PROJECT)
    assert rows
    assert having == " AND ".join(
        f"countIf(user_member_match_{index}) > 0" for index in range(len(rows))
    )
    normalized = " ".join(sql.split())
    assert "AS scalar_user_rows" not in sql
    # The retained span-seeded remap still reads latest_spans. Fusion removes
    # only the separate attribute-membership consumer, not every extra scan.
    assert len(re.findall(r"\bFROM latest_spans\b", sql)) == 2
    for index, row in enumerate(rows):
        suffix = f" AS user_member_match_{index}"
        assert row.endswith(suffix)
        predicate = " ".join(row.removesuffix(suffix).split())
        assert f"countIf({predicate}) AS user_member_trace_{index}" in normalized
        assert (
            f"sum(user_member_trace_{index}) AS user_member_bucket_{index}"
            in normalized
        )
        assert (
            f"sum(user_member_bucket_{index}) OVER (PARTITION BY end_user_id) "
            f"AS user_member_window_{index}"
        ) in normalized
    # No bucket partition, ORDER BY/running frame, or trace-level membership
    # filtering: independent witnesses can occur in different traces/buckets.
    assert re.findall(r"OVER \(([^)]*)\)", normalized) == [
        "PARTITION BY end_user_id"
    ] * len(rows)
    assert (
        "AS user_window_rows WHERE "
        + " AND ".join(f"user_member_window_{index} > 0" for index in range(len(rows)))
        in normalized
    )
    span_where = normalized.split("WHERE rs.end_user_id IS NOT NULL", 1)[1].split(
        "GROUP BY end_user_id, trace_id", 1
    )[0]
    assert "attrs_" not in span_where and "user_member_" not in span_where
    assert graph._active_user_dimension_membership_sql() in sql
    for expression in (
        "min(rs.start_time) AS min_start",
        f"{bucket_fn}(min_start) AS time_bucket",
        "avg(rs.latency_ms) AS span_avg_latency",
        "avg(span_avg_latency) AS user_avg_latency",
        "avg(user_avg_latency) AS avg_latency",
        "sum(rs.cost) AS span_total_cost",
        "sum(span_total_cost) AS user_total_cost",
        "avg(user_total_cost) AS avg_cost",
        "sum(user_total_cost) AS total_cost_sum",
        "max(if(rs.status = 'ERROR', 1, 0)) AS span_has_error",
        "max(span_has_error) AS user_has_error",
        "countIf(user_has_error = 1) * 100.0 / greatest(count(), 1) AS error_rate",
        "count() AS user_traces",
        "avg(user_traces) AS avg_traces_per_user",
        "GROUP BY end_user_id, trace_id",
        "GROUP BY time_bucket, end_user_id",
    ):
        assert expression in normalized


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("count", [1, 2, 5, 10])
def test_raw_only_graph_uses_flags_not_user_metrics_and_keeps_all_span_output(
    days, count
):
    filters = [_date(days), *[_leaf(f"key_{i}") for i in range(count)]]
    original = deepcopy(filters)
    _, _, flag_params = graph._user_membership_having(filters, project_id=PROJECT)
    sql, params, settings = _read(filters)
    assert all(params[name] == value for name, value in flag_params.items())
    assert params["project_id"] == PROJECT
    _assert_fused_positive_membership_and_metrics(
        sql, filters, bucket_fn="toMonday" if days == 365 else "toStartOfDay"
    )
    assert sql.count("FROM spans FINAL") == 1
    assert len(re.findall(r"\blatest_spans AS \(", sql)) == 1
    assert len(re.findall(r"\beu_survivor_map AS \(", sql)) == 1
    assert "trace_session_id_remap" not in sql
    assert "user_span_metrics AS" not in sql and "user_eval_metrics AS" not in sql
    assert "FROM latest_spans AS rs" in sql
    assert "sum(rs.cost) AS span_total_cost" in sql
    assert "GROUP BY end_user_id, trace_id" in sql
    assert "WHERE snapshot_spans.is_deleted = 0" in sql
    assert "fromUnixTimestamp64Micro(%(user_snapshot_start_us)s, 'UTC')" in sql
    assert (
        params["start_date"]
        == params["snapshot_start_date"]
        == END - timedelta(days=days)
    )
    assert params["end_date"] == params["snapshot_end_date"] == END
    assert settings["optimize_move_to_prewhere_if_final"] == 0
    assert settings["use_skip_indexes_if_final"] == 0
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()
    assert "SAMPLE " not in sql and "LIMIT " not in sql
    assert "groupUniqArray" not in sql
    assert filters == original


@pytest.mark.parametrize(
    "kind,op,value,flag_count",
    [
        ("text", "not_equals", "x", 2),
        ("text", "not_in", ["x", "y"], 2),
        ("text", "not_contains", "x", 2),
        ("number", "not_between", [1, 2], 2),
        ("boolean", "not_equals", True, 2),
        ("array", "not_contains", ["x", 1, True], 2),
        ("map", "not_contains", {"x": 1}, 2),
        ("json", "not_contains", {"x": True}, 2),
        ("text", "is_null", None, 1),
        ("number", "is_null", None, 1),
        ("boolean", "is_null", None, 1),
        ("array", "is_null", None, 1),
        ("map", "is_null", None, 1),
        ("text", "is_not_null", None, 1),
    ],
)
def test_typed_negative_and_null_use_unchanged_entity_flags(
    kind, op, value, flag_count
):
    filters = [_date(), _leaf(kind=kind, op=op, value=value)]
    rows, having, params = graph._user_membership_having(filters, project_id=PROJECT)
    sql, actual_params, _ = _read(filters)
    assert len(rows) == flag_count
    for index, row in enumerate(rows):
        suffix = f" AS user_member_match_{index}"
        assert row.endswith(suffix)
        assert (
            f"countIf({row.removesuffix(suffix)}) AS user_member_trace_{index}" in sql
        )
        assert f"sum(user_member_trace_{index}) AS user_member_bucket_{index}" in sql
        assert (
            f"sum(user_member_bucket_{index}) OVER (PARTITION BY end_user_id) "
            f"AS user_member_window_{index}"
        ) in sql
    assert "mapContains" in rows[0] or "JSONHas" in rows[0]
    if op.startswith("not_"):
        assert (
            having
            == "countIf(user_member_match_0) > 0 AND countIf(user_member_match_1) = 0"
        )
    elif op == "is_null":
        assert having == "countIf(user_member_match_0) = 0"
    expected_membership = (
        "user_member_window_0 > 0 AND user_member_window_1 = 0"
        if op.startswith("not_")
        else f"user_member_window_0 {'= 0' if op == 'is_null' else '> 0'}"
    )
    assert "AS user_window_rows WHERE " + expected_membership in " ".join(sql.split())
    assert all(actual_params[name] == value for name, value in params.items())
    assert actual_params["project_id"] == PROJECT


@pytest.mark.parametrize(
    "key",
    [
        "user_id",
        "session_id",
        "total_cost",
        "num_sessions",
        "eval_score",
        "has_eval",
        "has_annotation",
        "start_time",
    ],
)
def test_explicit_raw_native_name_collisions_still_use_the_narrow_path(key):
    sql, params, _ = _read([_date(), _leaf(key)])
    _assert_fused_positive_membership_and_metrics(sql, [_date(), _leaf(key)])
    assert "user_span_metrics AS" not in sql
    assert key in params.values() or f"'{key}'" in sql


@pytest.mark.parametrize(
    "extra",
    [
        _leaf(
            "total_cost", 1, kind="number", op="greater_than", source="SYSTEM_METRIC"
        ),
        _leaf(
            "eval_score", 80, kind="number", op="greater_than", source="SYSTEM_METRIC"
        ),
        _leaf("quality", 1, kind="number", source="EVAL_METRIC"),
        _leaf("review", "good", source="ANNOTATION"),
        _leaf("user_id", "u", source="TRACE_END_USER"),
        _leaf("has_eval", True, kind="boolean", source="SYSTEM_METRIC"),
        _leaf("company_id", "c", source="NORMAL"),
        _leaf("company_id", "c", source=""),
    ],
)
def test_mixed_native_eval_relation_and_legacy_leaves_use_existing_selector(
    monkeypatch, extra
):
    calls = []

    def full_selector(**kwargs):
        calls.append(kwargs)
        return "SELECT end_user_id FROM existing_full_selector", {}, False

    monkeypatch.setattr(graph, "_user_id_membership_sql", full_selector)
    filters = [_date(), _leaf(), extra]
    sql, _, _ = _read(filters)
    assert "existing_full_selector" in sql and "AS user_window_rows" not in sql
    assert calls[0]["filters"] == filters
    assert calls[0]["all_snapshot_users"] and calls[0]["reuse_outer_snapshot"]


def test_date_only_keeps_existing_active_dimension_path():
    sql, _, _ = _read([_date()])
    assert graph._active_user_dimension_membership_sql() in sql
    assert "AS user_window_rows" not in sql and "user_span_metrics AS" not in sql


def test_camel_case_explicit_source_and_implicit_dates(monkeypatch):
    start = END - timedelta(days=7)
    monkeypatch.setattr(graph, "_snapshot_window", lambda _filters: (start, END, False))
    filters = [
        {
            "columnId": "company_id",
            "filterConfig": {
                "colType": "SPAN_ATTRIBUTE",
                "filterType": "text",
                "filterOp": "equals",
                "filterValue": "x",
            },
        }
    ]
    sql, params, _ = _read(filters)
    _assert_fused_positive_membership_and_metrics(sql, filters)
    assert params["start_date"] == params["snapshot_start_date"] == start
    assert "candidate_trace_ids AS" not in sql


def test_compiler_errors_are_not_silently_retried_or_broadened(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("compiler defect")

    monkeypatch.setattr(graph, "_user_membership_having", broken)
    with pytest.raises(RuntimeError, match="compiler defect"):
        _read([_date(), _leaf()])
