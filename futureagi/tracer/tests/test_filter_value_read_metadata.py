"""Focused metadata contract for finite filter-value picker reads."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.filter_value_reads import (
    FILTER_VALUE_CURSOR_INITIAL_SEGMENT,
    FILTER_VALUE_CURSOR_MIN_SEGMENT,
    FILTER_VALUE_READ_TIMEOUT_MS,
    FilterValueRead,
    _value_digest,
    read_end_user_filter_value_cursor_page,
    read_session_filter_value_cursor_page,
    read_span_system_filter_value_cursor_page,
    read_span_system_filter_values,
)
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
PROJECT_ID = "00000000-0000-4000-8000-000000000001"


def test_system_filter_values_use_the_reviewed_thirty_second_wall():
    assert FILTER_VALUE_READ_TIMEOUT_MS == 30_000


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


def test_non_cursor_system_values_consume_the_request_owned_property_deadline():
    class Deadline:
        calls = []

        def remaining_ms(self, per_query_cap_ms):
            self.calls.append(per_query_cap_ms)
            return 3_650

    class Analytics:
        call = None

        def execute_ch_query(self, query, params, **kwargs):
            self.call = (query, params, kwargs)
            return SimpleNamespace(data=[])

    deadline = Deadline()
    analytics = Analytics()
    read_span_system_filter_values(
        analytics,
        project_ids=[PROJECT_ID],
        metric_name="ended_reason",
        deadline=deadline,
        now=NOW,
    )

    assert deadline.calls == [FILTER_VALUE_READ_TIMEOUT_MS]
    assert analytics.call[2]["timeout_ms"] == 3_650


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


def test_end_user_cursor_consumes_the_request_owned_property_deadline():
    class Deadline:
        calls = []

        def remaining_ms(self, per_query_cap_ms):
            self.calls.append(per_query_cap_ms)
            return 3_750

    class Analytics:
        call = None

        def execute_ch_query(self, query, params, **kwargs):
            self.call = (query, params, kwargs)
            return SimpleNamespace(data=[])

    deadline = Deadline()
    analytics = Analytics()
    read_end_user_filter_value_cursor_page(
        analytics,
        project_ids=[PROJECT_ID],
        source_column="user_id",
        page_size=2,
        deadline=deadline,
    )

    assert deadline.calls == [FILTER_VALUE_READ_TIMEOUT_MS]
    assert analytics.call[2]["timeout_ms"] == 3_750


def test_session_values_search_curated_labels_before_keyset_pagination():
    session_ids = [
        "00000000-0000-4000-8000-000000000010",
        "00000000-0000-4000-8000-000000000020",
        "00000000-0000-4000-8000-000000000030",
    ]

    class Analytics:
        calls = []

        def execute_ch_query(self, query, params, **kwargs):
            self.calls.append((query, params, kwargs))
            rows = session_ids
            if params.get("value_after") is not None:
                rows = [value for value in rows if value > params["value_after"]]
            return SimpleNamespace(data=[{"val": value} for value in rows[:3]])

    analytics = Analytics()
    first = read_session_filter_value_cursor_page(
        analytics,
        project_ids=[PROJECT_ID],
        page_size=2,
        search="customer-session",
        overlay_session_ids=[session_ids[1]],
    )
    second = read_session_filter_value_cursor_page(
        analytics,
        project_ids=[PROJECT_ID],
        page_size=2,
        search="customer-session",
        value_after=first.next_value_after,
        overlay_session_ids=[session_ids[2]],
    )

    assert first.values == tuple(session_ids[:2])
    assert first.has_more is True
    assert first.next_value_after == session_ids[1]
    assert second.values == (session_ids[2],)
    assert second.has_more is False
    sql, params, settings = analytics.calls[0]
    assert "FROM trace_sessions" in sql
    assert "argMax(is_deleted, version) AS latest_is_deleted" in sql
    assert "trace_session_id_remap" in sql
    assert "arrayExists(" in sql
    assert "resolved_session_id IN %(overlay_session_ids)s" in sql
    assert "FROM spans" not in sql
    assert params["filter_value_search"] == "customer-session"
    assert params["overlay_session_ids"] == (session_ids[1],)
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
        # Exercise a rolling cursor created before the production-qualified
        # default moved to the five-second floor.
        segment_start=NOW - timedelta(minutes=5),
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


def test_system_value_cursor_starts_at_the_exact_floor():
    class Analytics:
        calls = []

        def execute_ch_query(self, _query, params, **_kwargs):
            type(self).calls.append(dict(params))
            return SimpleNamespace(data=[{"val": "completed"}])

    read = read_span_system_filter_value_cursor_page(
        Analytics(),
        project_ids=[PROJECT_ID],
        metric_name="status",
        page_size=1,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert len(Analytics.calls) == 1
    assert Analytics.calls[0]["window_end"] - Analytics.calls[0]["window_start"] == (
        FILTER_VALUE_CURSOR_MIN_SEGMENT
    )
    assert read.values == ("completed",)
    assert read.has_more is True


def test_system_value_floor_failure_keeps_values_from_completed_slices():
    class Analytics:
        calls = 0

        def execute_ch_query(self, *_args, **_kwargs):
            type(self).calls += 1
            if type(self).calls == 1:
                return SimpleNamespace(
                    data=[{"val": "agent_hangup"}, {"val": "user_hangup"}]
                )
            raise ReadDeadlineExceeded("dense older system value slice")

    read = read_span_system_filter_value_cursor_page(
        Analytics(),
        project_ids=[PROJECT_ID],
        metric_name="ended_reason",
        page_size=10,
        window_start=NOW - timedelta(seconds=10),
        window_end=NOW,
        segment_start=NOW - FILTER_VALUE_CURSOR_MIN_SEGMENT,
    )

    assert read.values == ("agent_hangup", "user_hangup")
    assert read.has_more is True
    assert read.browse_status == "continuation"
    assert read.next_segment_end == NOW - FILTER_VALUE_CURSOR_MIN_SEGMENT
    assert read.next_segment_start == NOW - timedelta(seconds=10)


def test_system_value_floor_failure_keeps_duplicate_only_slice_progress():
    duplicate_digest = _value_digest("agent_hangup")

    class Analytics:
        calls = 0

        def execute_ch_query(self, *_args, **_kwargs):
            type(self).calls += 1
            if type(self).calls == 1:
                return SimpleNamespace(data=[{"val": "agent_hangup"}])
            raise ReadDeadlineExceeded("dense older system value slice")

    read = read_span_system_filter_value_cursor_page(
        Analytics(),
        project_ids=[PROJECT_ID],
        metric_name="ended_reason",
        page_size=10,
        window_start=NOW - timedelta(seconds=10),
        window_end=NOW,
        segment_start=NOW - FILTER_VALUE_CURSOR_MIN_SEGMENT,
        seen_value_digests=(duplicate_digest,),
    )

    assert read.values == ()
    assert read.has_more is True
    assert read.browse_status == "continuation"
    assert read.next_segment_end == NOW - FILTER_VALUE_CURSOR_MIN_SEGMENT
    assert read.next_segment_start == NOW - timedelta(seconds=10)


def test_system_value_cursor_shares_one_deadline_across_adjacent_slices(monkeypatch):
    class Deadline:
        calls = 0

        @classmethod
        def start(cls, _total_ms):
            return cls()

        def remaining_ms(self, _cap_ms=None):
            type(self).calls += 1
            if type(self).calls == 1:
                return 9_000
            raise ReadDeadlineExceeded("shared filter-value deadline")

    class Analytics:
        calls = []

        def execute_ch_query(self, _query, _params, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(data=[])

    monkeypatch.setattr(
        "tracer.services.clickhouse.filter_value_reads.ReadDeadline",
        Deadline,
    )
    analytics = Analytics()
    read = read_span_system_filter_value_cursor_page(
        analytics,
        project_ids=[PROJECT_ID],
        metric_name="status",
        page_size=10,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert len(analytics.calls) == 1
    assert analytics.calls[0]["timeout_ms"] == 9_000
    assert read.values == ()
    assert read.has_more is True
    assert read.next_segment_end == NOW - FILTER_VALUE_CURSOR_INITIAL_SEGMENT


def test_system_value_cursor_inherits_elapsed_request_deadline(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(
        "tracer.services.clickhouse.read_budget.time.monotonic",
        lambda: clock["now"],
    )
    deadline = ReadDeadline.start(6_000)
    clock["now"] += 1.25

    class Analytics:
        calls = []

        def execute_ch_query(self, _query, _params, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(data=[])

    analytics = Analytics()
    read = read_span_system_filter_value_cursor_page(
        analytics,
        project_ids=[PROJECT_ID],
        metric_name="ended_reason",
        page_size=20,
        window_start=NOW - FILTER_VALUE_CURSOR_INITIAL_SEGMENT,
        window_end=NOW,
        deadline=deadline,
    )

    assert len(analytics.calls) == 1
    assert analytics.calls[0]["timeout_ms"] == 4_750
    assert read.has_more is False
