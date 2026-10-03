"""Offline bindings audit of every runtime Session source caller."""
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graph
from tracer.services.clickhouse.v2.query_builders.eval_metrics import (
    EvalMetricsQueryBuilderV2,
)
from tracer.tests import test_exact_session_scalar_witness as existing

pytestmark = pytest.mark.unit
LO = datetime(2026, 8, 8, 12, 15, 0, 123456)
HI = LO + timedelta(days=7, microseconds=530865)


def date_leaf(operation, value):
    return {"column_id": "created_at", "filter_config": {
        "filter_type": "datetime", "filter_op": operation, "filter_value": value}}


def filters():
    return [date_leaf("between", [LO, HI]),
            date_leaf("greater_than", LO + timedelta(microseconds=1)),
            date_leaf("less_than_or_equal", HI - timedelta(microseconds=2)),
            date_leaf("not_equals", LO + timedelta(minutes=5, microseconds=7)),
            existing._leaf("greater_than", 1, "number", "agent.duration_s")]


def assert_snapshot(sql, params, start, end):
    assert set(re.findall(r"%\(([^)]+)\)s", sql)) <= params.keys()
    for prefix in ("snapshot_", ""):
        for edge, value in (("start", start), ("end", end)):
            key = prefix + edge + "_date_us"
            assert type(params[key]) is int
            assert params[key] == graph._unix_microseconds(value)
    for edge in ("start", "end"):
        assert "fromUnixTimestamp64Micro(%(snapshot_" + edge + "_date_us)s)" in sql
    for prefix in ("exact_session_time_exclusion", "exact_session_scalar_time_exclusion"):
        exclusion = {k: v for k, v in params.items() if k.startswith(prefix)}
        assert len(exclusion) == 2
        assert set(exclusion.values()) == {
            graph._unix_microseconds(LO + timedelta(minutes=5, microseconds=7)),
            graph._unix_microseconds(LO + timedelta(minutes=5, microseconds=8)),
        }


@pytest.mark.parametrize("route", ["system", "membership", "eval", "annotation_trace", "annotation_span"])
def test_all_session_callers_preserve_resolved_snapshot_integer_bindings(route, monkeypatch):
    selected = filters()
    start, end, empty = graph._snapshot_window(selected)
    assert not empty
    assert start == LO + timedelta(microseconds=2)
    assert end == HI - timedelta(microseconds=1)
    calls = []
    call_options = []

    def capture(sql, params, **options):
        calls.append((sql, dict(params)))
        call_options.append(options)
        probe = sql.lstrip().startswith("SELECT 1 AS has_raw_witness")
        return SimpleNamespace(data=[{"has_raw_witness": 1}] if probe else [], columns=[])

    analytics = SimpleNamespace(execute_ch_query=capture)
    if route == "system":
        result = graph.read_exact_session_system_graph(analytics=analytics, project_id=existing.PROJECT,
            filters=selected, interval="day", metric_id="latency")
        assert len(calls) == 2 and result["query_count"] == 2
        assert calls[0][0].lstrip().startswith("SELECT 1 AS has_raw_witness")
        assert call_options[0]["settings"] == graph.EXACT_GRAPH_READ_SETTINGS
        assert calls[0][1]["session_absence_start_us"] == graph._unix_microseconds(start.replace(minute=0, second=0, microsecond=0))
        assert calls[0][1]["session_absence_end_us"] == graph._unix_microseconds(end.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        full, full_options = calls[1], call_options[1]
        assert_snapshot(*full, start, end)
        calls.clear()
        call_options.clear()
        with monkeypatch.context() as control:
            control.setattr(graph, "_session_numeric_absence_probe_sql", lambda **kwargs: None)
            baseline = graph.read_exact_session_system_graph(analytics=analytics, project_id=existing.PROJECT,
                filters=selected, interval="day", metric_id="latency")
        assert len(calls) == 1 and baseline["query_count"] == 1
        assert calls[0] == full and call_options[0]["settings"] == full_options["settings"]
        assert {key: type(value) for key, value in calls[0][1].items()} == {key: type(value) for key, value in full[1].items()}
        return

    options = {"candidate_trace_ids_param": "candidate_trace_ids"}
    if route == "eval":
        options = {"candidate_trace_ids_sql": graph._eval_partition_trace_ids_sql()}
    if route == "annotation_span":
        options = {"candidate_trace_ids_sql": graph._span_batch_trace_ids_sql()}
    sql, params = graph._session_trace_membership_sql(project_id=existing.PROJECT,
        filters=selected, start_date=start, end_date=end, **options)
    # Member selection is not session-start anchored. Its generic *_us params
    # are unused; changing an outer partition cannot change its frozen snapshot.
    assert "%(start_date_us)s" not in sql and "%(end_date_us)s" not in sql
    if route == "eval":
        builder = EvalMetricsQueryBuilderV2(project_id=existing.PROJECT,
            custom_eval_config_id="33333333-3333-4333-8333-333333333333",
            start_date=start, end_date=end, interval="day", filters=[], observe_type="trace",
            session_trace_membership_sql=sql, session_trace_membership_params=params)
        sql, params = builder.build()
        assert_snapshot(sql, params, start, end)
        # Contract simulation only: current eval reader uses ONE full-window query.
        narrowed = {**params, "start_date": start + timedelta(days=1),
                    "end_date": start + timedelta(days=2)}
        assert "%(start_date)s" in sql and "%(end_date)s" in sql
        assert "%(start_date_us)s" not in sql and "%(end_date_us)s" not in sql
        assert_snapshot(sql, narrowed, start, end)
    elif route.startswith("annotation_"):
        match = graph._matching_span_ids if route == "annotation_span" else graph._matching_trace_ids
        ids = {"span_ids": ("child",)} if route == "annotation_span" else {"trace_ids": ("root",)}
        match(analytics=analytics, project_id=existing.PROJECT, start_date=start, end_date=end,
              predicate=f"trace_id IN ({sql})", predicate_params=params,
              timeout_ms=1000, settings=graph.EXACT_GRAPH_READ_SETTINGS, **ids)
        assert len(calls) == 1
        assert_snapshot(*calls[0], start, end)
    else:
        assert_snapshot(sql, {**params, "candidate_trace_ids": ("root",)}, start, end)
