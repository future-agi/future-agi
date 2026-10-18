"""Numeric raw-absence gate contracts; mocked terminal I/O, no DB/network."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver.errors import ServerException

from tracer.services.clickhouse import exact_graph_reads as graph
from tracer.tests import test_exact_session_scalar_witness as existing

pytestmark = pytest.mark.unit


def _filters(leaf=None):
    return existing._filters(leaf or existing._leaf("greater_than", 1, "number", "agent.duration_s"))


def _probe(selected, start=existing.START, end=existing.END):
    return graph._session_numeric_absence_probe_sql(
        project_id=existing.PROJECT, filters=selected, start_date=start, end_date=end)


@pytest.mark.parametrize("leaf", [existing._leaf(), existing._leaf("equals", 0, "number"),
    existing._leaf("greater_than", -1, "number"), existing._leaf("not_equals", 1, "number")])
def test_non_numeric_or_missing_default_matching_predicates_skip_probe(leaf):
    assert _probe(_filters(leaf)) is None


def test_multiple_leaves_skip_probe():
    assert _probe(_filters() + [existing._leaf()]) is None


def test_probe_is_raw_superset_with_exact_integer_hour_fences():
    start = existing.START.replace(microsecond=123456)
    end = existing.END.replace(microsecond=654321)
    sql, params = _probe(_filters(), start, end)
    assert params["session_absence_start_us"] == graph._unix_microseconds(start.replace(minute=0, second=0, microsecond=0))
    assert params["session_absence_end_us"] == graph._unix_microseconds(end.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    assert type(params["session_absence_start_us"]) is int and type(params["session_absence_end_us"]) is int
    assert "LIMIT 1" in sql and "attrs_number[" in sql
    assert all(word not in sql for word in ("FINAL", "is_deleted", "trace_session_id", "parent_span_id", "SAMPLE"))


def _read(execute):
    return graph.read_exact_session_system_graph(
        analytics=SimpleNamespace(execute_ch_query=execute), project_id=existing.PROJECT,
        filters=_filters(), interval="day", metric_id="latency")


@pytest.mark.parametrize("present", [False, True])
def test_branch_counts_unchanged_full_query_and_normal_empty_format(monkeypatch, present):
    calls = []

    def execute(sql, params, **options):
        probe = sql.lstrip().startswith("SELECT 1 AS has_raw_witness")
        calls.append((sql, params, options))
        return SimpleNamespace(data=[{"has_raw_witness": 1}] if probe and present else [], columns=[])

    with monkeypatch.context() as baseline_only:
        baseline_only.setattr(graph, "_session_numeric_absence_probe_sql", lambda **kwargs: None)
        baseline = _read(execute)
    baseline_call, = calls
    assert baseline["query_count"] == 1
    calls.clear()
    candidate = _read(execute)
    assert len(calls) == (2 if present else 1)
    assert candidate["query_count"] == len(calls)
    assert candidate["data"] == baseline["data"] and len(candidate["data"]) > 1
    assert candidate["query_complete"] is True and candidate["query_sampled"] is False
    assert calls[0][0].lstrip().startswith("SELECT 1 AS has_raw_witness")
    assert calls[0][2]["settings"] == graph.EXACT_GRAPH_READ_SETTINGS
    if present:
        assert calls[1][:2] == baseline_call[:2]
        assert {key: type(value) for key, value in calls[1][1].items()} == {key: type(value) for key, value in baseline_call[1].items()}
        assert calls[1][2]["settings"] == baseline_call[2]["settings"]


@pytest.mark.parametrize("error", [RuntimeError("bad query"), ServerException("read failure", code=159)])
def test_probe_exception_never_means_empty_or_fallback(error):
    calls = []

    def execute(sql, params, **options):
        calls.append(sql)
        raise error

    with pytest.raises(type(error)) as caught:
        _read(execute)
    assert caught.value is error
    assert len(calls) == 1 and calls[0].lstrip().startswith("SELECT 1 AS has_raw_witness")
