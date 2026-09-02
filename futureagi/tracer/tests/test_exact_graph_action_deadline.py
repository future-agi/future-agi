from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
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
