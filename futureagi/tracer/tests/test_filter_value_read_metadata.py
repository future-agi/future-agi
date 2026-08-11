"""Focused metadata contract for finite filter-value picker reads."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.filter_value_reads import (
    FILTER_VALUE_CURSOR_INITIAL_SEGMENT,
    FILTER_VALUE_CURSOR_MIN_SEGMENT,
    FilterValueRead,
    _value_digest,
    read_end_user_filter_value_cursor_page,
    read_span_system_filter_value_cursor_page,
    read_span_system_filter_values,
)
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
PROJECT_ID = "00000000-0000-4000-8000-000000000001"


def _read(
    values: tuple[str, ...],
    *,
    complete: bool,
    error_code: str | None,
) -> FilterValueRead:
    return FilterValueRead(
        values,
        complete,
        error_code,
        NOW - timedelta(days=7),
        NOW,
    )


def test_filter_value_metadata_labels_only_usable_finite_caps_as_sampled():
    complete = _read(("one",), complete=True, error_code=None)
    sampled = _read(("one",), complete=False, error_code="sample_limit")
    empty_cap = _read((), complete=False, error_code="sample_limit")
    resource_failure = _read(
        ("one",),
        complete=False,
        error_code="read_budget_exceeded",
    )

    assert complete.metadata()["query_status"] == "complete"
    assert sampled.metadata()["query_status"] == "sampled"
    assert empty_cap.metadata()["query_status"] == "degraded"
    assert resource_failure.metadata()["query_status"] == "degraded"


def test_system_filter_value_cap_produces_a_labelled_sample():
    class Analytics:
        def execute_ch_query(self, *_args, **_kwargs):
            return SimpleNamespace(data=[{"val": "one"}, {"val": "two"}])

    read = read_span_system_filter_values(
        Analytics(),
        project_ids=[PROJECT_ID],
        metric_name="model",
        limit=1,
        now=NOW,
    )

    assert read.values == ("one",)
    assert read.has_more is True
    assert read.query_complete is False
    assert read.query_error_code == "sample_limit"
    assert read.metadata()["query_status"] == "sampled"


@pytest.mark.parametrize(
    ("metric_name", "expected_value", "sql_markers"),
    [
        (
            "call_status",
            "completed",
            (
                "multiIf(",
                "('ended', 'done', 'complete', 'completed'",
                "attrs_string",
            ),
        ),
        (
            "cost_cents",
            "12.2",
            ("'call_cost', 'combined_cost'", "'cost_breakdown.total'", "* 100"),
        ),
        (
            "call_id",
            "provider-call-123",
            (
                "'raw_log', 'id'",
                "'raw_log', 'conversation_id'",
                "'metadata', 'call_execution_id'",
            ),
        ),
        (
            "call_type",
            "inbound",
            (
                "'raw_log', 'type'",
                "'raw_log', 'direction'",
                "attrs_string['call_type']",
            ),
        ),
        (
            "ended_reason",
            "customer-ended-call",
            ("attrs_string['ended_reason']",),
        ),
    ],
)
def test_voice_system_suggestions_use_normalized_list_expressions(
    metric_name,
    expected_value,
    sql_markers,
):
    class Analytics:
        call = None

        def execute_ch_query(self, query, params, **kwargs):
            self.call = (query, params, kwargs)
            return SimpleNamespace(data=[{"val": expected_value}])

    analytics = Analytics()
    read = read_span_system_filter_values(
        analytics,
        project_ids=[PROJECT_ID],
        metric_name=metric_name,
        now=NOW,
    )

    assert read.values == (expected_value,)
    query, _, _ = analytics.call
    assert "latest_observation_type = 'conversation'" in query
    assert "latest_parent_span_id IS NULL" in query
    assert "attributes_extra" in query
    for marker in sql_markers:
        assert marker in query


def test_end_user_values_use_exact_latest_state_keyset_pages():
    class Analytics:
        calls = []

        def execute_ch_query(self, query, params, **kwargs):
            self.calls.append((query, params, kwargs))
            after = params.get("value_after")
            rows = ["alice", "bob", "carol"]
            if after is not None:
                rows = [value for value in rows if value > after]
            return SimpleNamespace(data=[{"val": value} for value in rows[:3]])

    analytics = Analytics()
    first = read_end_user_filter_value_cursor_page(
        analytics,
        project_ids=[PROJECT_ID],
        source_column="user_id",
        page_size=2,
    )
    second = read_end_user_filter_value_cursor_page(
        analytics,
        project_ids=[PROJECT_ID],
        source_column="user_id",
        page_size=2,
        value_after=first.next_value_after,
    )

    assert first.values == ("alice", "bob")
    assert first.has_more is True
    assert first.next_value_after == "bob"
    assert second.values == ("carol",)
    assert second.has_more is False
    sql, _, settings = analytics.calls[0]
    assert "argMax(is_deleted, version) AS latest_is_deleted" in sql
    assert "argMax(tuple(user_id), version).1 AS raw_value" in sql
    assert "FINAL" not in sql
    assert settings["settings"]["timeout_overflow_mode"] == "throw"


def test_system_values_cursor_exhausts_dense_slice_without_duplicates():
    class Analytics:
        def execute_ch_query(self, _query, params, **_kwargs):
            rows = ["completed", "failed", "queued"]
            after = params.get("value_after")
            if after is not None:
                rows = [value for value in rows if value > after]
            return SimpleNamespace(data=[{"val": value} for value in rows])

    first = read_span_system_filter_value_cursor_page(
        Analytics(),
        project_ids=[PROJECT_ID],
        metric_name="status",
        page_size=2,
        window_start=NOW - FILTER_VALUE_CURSOR_INITIAL_SEGMENT,
        window_end=NOW,
    )
    second = read_span_system_filter_value_cursor_page(
        Analytics(),
        project_ids=[PROJECT_ID],
        metric_name="status",
        page_size=2,
        window_start=NOW - FILTER_VALUE_CURSOR_INITIAL_SEGMENT,
        window_end=NOW,
        segment_end=first.next_segment_end,
        segment_start=first.next_segment_start,
        value_after=first.next_value_after,
        seen_value_digests=first.seen_value_digests,
    )

    assert first.values == ("completed", "failed")
    assert first.has_more is True
    assert first.next_value_after == "failed"
    assert second.values == ("queued",)
    assert second.has_more is False
    assert second.browse_status == "exhausted"


def test_system_values_cursor_uses_exact_count_only_state_past_4096():
    class Analytics:
        def execute_ch_query(self, _query, _params, **_kwargs):
            return SimpleNamespace(data=[{"val": "completed"}, {"val": "new-status"}])

    completed_digest = _value_digest("completed")
    read = read_span_system_filter_value_cursor_page(
        Analytics(),
        project_ids=[PROJECT_ID],
        metric_name="status",
        page_size=10,
        window_start=NOW - FILTER_VALUE_CURSOR_INITIAL_SEGMENT,
        window_end=NOW,
        seen_value_digests=(),
        seen_value_contains=lambda digest: digest == completed_digest,
        seen_value_count=4_097,
    )

    assert read.values == ("new-status",)
    assert read.appended_value_digests == (_value_digest("new-status"),)
    assert read.seen_value_digests == read.appended_value_digests
    assert read.seen_value_count == 4_098
    assert read.has_more is False
    assert read.browse_status == "exhausted"


def test_system_value_budget_backoff_changes_cursor_then_fails_at_floor():
    class Analytics:
        def execute_ch_query(self, *_args, **_kwargs):
            raise ReadDeadlineExceeded("dense system value slice")

    first = read_span_system_filter_value_cursor_page(
        Analytics(),
        project_ids=[PROJECT_ID],
        metric_name="status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    assert first.values == ()
    assert first.has_more is True
    assert first.next_segment_end == NOW
    assert first.next_segment_start == NOW - FILTER_VALUE_CURSOR_MIN_SEGMENT

    with pytest.raises(ReadDeadlineExceeded, match="dense system value slice"):
        read_span_system_filter_value_cursor_page(
            Analytics(),
            project_ids=[PROJECT_ID],
            metric_name="status",
            page_size=10,
            window_start=NOW - timedelta(hours=1),
            window_end=NOW,
            segment_end=first.next_segment_end,
            segment_start=first.next_segment_start,
            seen_value_digests=first.seen_value_digests,
        )
