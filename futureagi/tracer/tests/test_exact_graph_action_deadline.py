from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from clickhouse_driver.errors import ServerException
from django.conf import settings as django_settings

from tracer.services.clickhouse import exact_graph_reads as exact_reads


@pytest.mark.unit
def test_exact_graph_action_deadline_uses_reviewed_background_wall():
    assert (
        exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS
        == django_settings.GRAPH_BACKGROUND_WALL_MS
    )
    assert (
        exact_reads.EXACT_GRAPH_QUERY_TIMEOUT_MS
        == exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS
    )
    assert (
        exact_reads.EXACT_GRAPH_TRACE_ANCHOR_QUERY_TIMEOUT_MS
        == exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS
    )
    assert (
        exact_reads.EXACT_GRAPH_TRACE_WITNESS_QUERY_TIMEOUT_MS
        == exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS
    )
    assert (
        exact_reads.EXACT_GRAPH_TRACE_CLASSIFIER_QUERY_TIMEOUT_MS
        == exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS
    )
    assert (
        exact_reads.EXACT_GRAPH_TRACE_CONTRIBUTION_QUERY_TIMEOUT_MS
        == exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS
    )
    assert (
        exact_reads.EXACT_GRAPH_SPAN_PARTITION_QUERY_TIMEOUT_MS
        == exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS
    )


@pytest.mark.unit
def test_remaining_exact_graph_budget_shrinks_and_rounds_down(monkeypatch):
    wall_ms = exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS
    wall_seconds = wall_ms / 1_000
    clock = iter((0.0, 0.2504, wall_seconds - 0.0249))
    monkeypatch.setattr(exact_reads, "monotonic", lambda: next(clock))

    assert exact_reads._remaining_exact_graph_timeout_ms(0.0, wall_ms) == wall_ms
    # The fractional millisecond is never rounded into a grant past the wall.
    assert exact_reads._remaining_exact_graph_timeout_ms(0.0, wall_ms) == wall_ms - 251
    with pytest.raises(exact_reads.ExactGraphReadError, match="bounded deadline"):
        exact_reads._remaining_exact_graph_timeout_ms(0.0, wall_ms)


@pytest.mark.unit
def test_trace_contribution_subqueries_share_one_shrinking_budget(monkeypatch):
    timeouts: list[int] = []
    clock = iter((0.25, 2.5))
    wall_ms = exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS

    class Builder:
        @staticmethod
        def build_exact_trace_contribution_batch(trace_ids):
            return "TRACE CONTRIBUTION", {"trace_ids": tuple(trace_ids)}

        @staticmethod
        def format_result(rows, columns):
            assert rows == []
            assert "time_bucket" in columns
            return {"latency": [], "traffic": []}

    class Analytics:
        @staticmethod
        def execute_ch_query(_query, _params, *, timeout_ms, settings):
            assert settings == exact_reads.EXACT_GRAPH_TRACE_CONTRIBUTION_READ_SETTINGS
            timeouts.append(timeout_ms)
            return SimpleNamespace(data=[], columns=[])

    monkeypatch.setattr(
        exact_reads,
        "_enumerate_exact_trace_ids",
        lambda **_kwargs: (["trace-1", "trace-2"], 0, 0),
    )
    monkeypatch.setattr(exact_reads, "EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE", 1)
    monkeypatch.setattr(exact_reads, "monotonic", lambda: next(clock))

    _metrics, query_count, _rows_returned = (
        exact_reads._read_exact_filtered_trace_graph(
            analytics=Analytics(),
            builder=Builder(),
            project_id="project",
            filters=[],
            annotation_label_ids=None,
            started=0.0,
        )
    )

    assert query_count == 2
    assert timeouts == [wall_ms - 250, wall_ms - 2_500]
    assert all(
        timeout <= exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS for timeout in timeouts
    )


@pytest.mark.unit
def test_span_partition_deadline_stops_before_an_over_budget_subquery(monkeypatch):
    timeouts: list[int] = []
    wall_ms = exact_reads.EXACT_GRAPH_WALL_DEADLINE_MS
    wall_seconds = wall_ms / 1_000
    clock = iter((0.25, 2.5, wall_seconds - 0.024))
    start = datetime(2026, 8, 1)

    class Builder:
        @staticmethod
        def build_exact_span_partition(**kwargs):
            return "SPAN PARTITION", kwargs

        @staticmethod
        def format_result(_rows, _columns):
            raise AssertionError("a partial span graph must not be formatted")

    class Analytics:
        @staticmethod
        def execute_ch_query(_query, _params, *, timeout_ms, settings):
            assert settings == exact_reads.EXACT_GRAPH_SPAN_PARTITION_READ_SETTINGS
            timeouts.append(timeout_ms)
            return SimpleNamespace(data=[], columns=[], query_time_ms=1_000)

    monkeypatch.setattr(exact_reads, "monotonic", lambda: next(clock))

    with pytest.raises(exact_reads.ExactGraphReadError, match="bounded deadline"):
        exact_reads._read_exact_filtered_span_graph(
            analytics=Analytics(),
            builder=Builder(),
            exact_filter_plan=object(),
            start_date=start,
            end_date=start + timedelta(hours=3),
            started=0.0,
        )

    assert timeouts == [wall_ms - 250, wall_ms - 2_500]
    assert timeouts == sorted(timeouts, reverse=True)


@pytest.mark.unit
@pytest.mark.parametrize("proof,failures", [(True, ()), (True, (1,)), (True, (1, 3)), (False, ())])
def test_span_witness_whole_window_retry_is_disjoint_and_never_publishes_partial(monkeypatch, proof, failures):
    start = datetime(2026, 8, 1)
    end = start + timedelta(hours=3)
    calls, successful = [], []
    formatted = mock.Mock(return_value={})
    builder = SimpleNamespace(_exact_span_candidate_plan=lambda: object() if proof else None,
        build_exact_span_partition=lambda **params: ("partition", params), format_result=formatted)
    monkeypatch.setattr(exact_reads, "EXACT_GRAPH_SPAN_PARTITION_WIDTH", timedelta(hours=1))
    monkeypatch.setattr(exact_reads, "_remaining_exact_graph_timeout_ms", lambda *_: 10000)

    def execute(_sql, params, **_kwargs):
        bounds = params["partition_start"], params["partition_end"]
        calls.append(bounds)
        if len(calls) in failures:
            raise ServerException("fixture memory failure", code=241)
        successful.append(bounds)
        return SimpleNamespace(data=[], columns=[], query_time_ms=100000)

    try:
        exact_reads._read_exact_filtered_span_graph(analytics=SimpleNamespace(execute_ch_query=execute),
            builder=builder, exact_filter_plan=None, start_date=start, end_date=end, started=0)
    except ServerException:
        assert failures == (1, 3) and len(calls) == 3
        formatted.assert_not_called()
        return
    assert failures != (1, 3)
    formatted.assert_called_once()
    assert calls[0] == (start, end if proof else start + timedelta(hours=1))
    expected = [(start, end)] if proof and not failures else [
        (start + timedelta(hours=i), start + timedelta(hours=i + 1)) for i in range(3)]
    assert successful == expected  # Whole-hour, gap-free, nonoverlapping; no failed contribution.


@pytest.mark.unit
@pytest.mark.parametrize("later_failures,edges", [
    ((), [0, 1, 3, 7, 15, 31, 55, 79, 80]),
    ((5,), [0, 1, 3, 7, *range(11, 80, 4), 80]),
    ((4, 5, 6), [0, 1, 3]),
], ids=["empty-regrows-to-24h", "later-8h-failure-caps-4h", "hour-floor-no-partial"])
def test_span_whole_window_failure_preserves_normal_adaptive_growth(monkeypatch, later_failures, edges):
    start = datetime(2026, 8, 1)
    calls, successful = [], []
    formatted = mock.Mock(return_value={})
    builder = SimpleNamespace(_exact_span_candidate_plan=lambda: object(),
        build_exact_span_partition=lambda **params: ("partition", params), format_result=formatted)
    for name, hours in (("MIN_PARTITION_WIDTH", 1), ("PARTITION_WIDTH", 1), ("MAX_PARTITION_WIDTH", 24)):
        monkeypatch.setattr(exact_reads, "EXACT_GRAPH_SPAN_" + name, timedelta(hours=hours))
    monkeypatch.setattr(exact_reads, "EXACT_GRAPH_SPAN_GROW_BELOW_QUERY_MS", 10)
    monkeypatch.setattr(exact_reads, "_remaining_exact_graph_timeout_ms", lambda *_: 10000)

    def execute(_sql, params, **_kwargs):
        bounds = tuple((params[k] - start) // timedelta(hours=1) for k in ("partition_start", "partition_end"))
        assert all(params[k].minute == params[k].second == params[k].microsecond == 0
                   for k in ("partition_start", "partition_end"))
        calls.append(bounds)
        formatted.assert_not_called()
        if len(calls) in (1, *later_failures):
            raise ServerException("fixture memory failure", code=307)
        successful.append(bounds)
        rows = [{"time_bucket": start, "traffic_count": bounds[1] - bounds[0]}] if later_failures else []
        return SimpleNamespace(data=rows, columns=[], query_time_ms=1)

    def read():
        return exact_reads._read_exact_filtered_span_graph(analytics=SimpleNamespace(execute_ch_query=execute),
            builder=builder, exact_filter_plan=None, start_date=start, end_date=start + timedelta(hours=80), started=0)

    if later_failures == (4, 5, 6):
        with pytest.raises(ServerException, match="fixture memory failure"):
            read()
        assert calls == [(0, 80), (0, 1), (1, 3), (3, 7), (3, 5), (3, 4)]
        formatted.assert_not_called()  # Two completed, nonempty states remain unpublished.
    else:
        _, count, rows_returned = read()
        assert count == len(calls) == len(edges) + len(later_failures)
        formatted.assert_called_once()
        merged = formatted.call_args.args[0]
        assert rows_returned == (len(successful) if later_failures else 0)
        assert (len(merged) == 1 and merged[0]["traffic_count"] == 80) if later_failures else merged == []
        if later_failures:
            assert calls[4] == (7, 15) and calls[5] == (7, 11)
    assert calls[0] == (0, 80) and calls[1] == (0, 1)
    assert successful == list(zip(edges, edges[1:], strict=False))  # Independent gap-free/disjoint boundaries.
