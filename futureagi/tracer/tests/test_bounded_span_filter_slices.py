"""Tests for bounded span-attribute list scans across value types."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest import mock

import pytest
from clickhouse_driver.errors import ErrorCodes, ServerException

from tracer.services.clickhouse.page_dedup import paginate_deduped
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_service import QueryResult
from tracer.views.observation_span import _execute_bounded_span_filter_prefix


def _filters(start: datetime, end: datetime) -> list[dict]:
    return [
        {
            "column_id": "arbitrary_string_key",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "arbitrary-value",
            },
        },
        {
            "column_id": "start_time",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        },
    ]


def _builder(
    start: datetime,
    end: datetime,
    *,
    page_number: int = 0,
    page_size: int = 2,
) -> SpanListQueryBuilder:
    return SpanListQueryBuilder(
        project_id="11111111-1111-1111-1111-111111111111",
        page_number=page_number,
        page_size=page_size,
        filters=_filters(start, end),
    )


def _unfiltered_builder(
    start: datetime,
    end: datetime,
    *,
    page_number: int = 0,
    page_size: int = 2,
) -> SpanListQueryBuilder:
    return SpanListQueryBuilder(
        project_id="11111111-1111-1111-1111-111111111111",
        page_number=page_number,
        page_size=page_size,
        filters=[
            {
                "column_id": "start_time",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start.isoformat(), end.isoformat()],
                },
            }
        ],
    )


class _Analytics:
    def __init__(
        self,
        pages: list[list[dict]] | None = None,
        exc: Exception | None = None,
        classifications: dict[str, dict | None] | None = None,
        cross_slice_ids: set[str] | None = None,
    ):
        self.pages = list(pages or [])
        self.exc = exc
        self.classifications = classifications or {}
        self.cross_slice_ids = cross_slice_ids or set()
        self.seed_rows_by_id: dict[str, dict] = {}
        self.calls: list[tuple[str, dict, int, dict]] = []

    def execute_ch_query(self, query, params, timeout_ms, settings):
        self.calls.append((query, dict(params), timeout_ms, dict(settings)))
        if self.exc:
            raise self.exc
        if "cross_slice_span_ids" in params:
            data = [
                {"id": span_id}
                for span_id in params["cross_slice_span_ids"]
                if span_id in self.cross_slice_ids
            ]
        elif "candidate_span_ids" in params:
            data = []
            for span_id in params["candidate_span_ids"]:
                if span_id in self.classifications:
                    row = self.classifications[span_id]
                else:
                    row = self.seed_rows_by_id.get(span_id)
                if row is not None:
                    data.append(dict(row))
        else:
            raw_data = self.pages.pop(0) if self.pages else []
            data = []
            for index, source_row in enumerate(raw_data):
                row = dict(source_row)
                if "start_time" not in row and "candidate_slice_end" in params:
                    row["start_time"] = params["candidate_slice_end"] - timedelta(
                        microseconds=index + 1
                    )
                data.append(row)
                span_id = str(row.get("id", ""))
                if span_id:
                    self.seed_rows_by_id.setdefault(span_id, dict(row))
        return QueryResult(data, len(data), "clickhouse", 1)


def _seed_calls(analytics):
    return [
        call
        for call in analytics.calls
        if "candidate_span_ids" not in call[1]
        and "cross_slice_span_ids" not in call[1]
        and "future_tail_start" not in call[1]
    ]


def _classifier_calls(analytics):
    return [call for call in analytics.calls if "candidate_span_ids" in call[1]]


def _cross_slice_calls(analytics):
    return [call for call in analytics.calls if "cross_slice_span_ids" in call[1]]


class _FastAttemptThenSlicesAnalytics(_Analytics):
    def __init__(self, fast_exc: Exception, pages: list[list[dict]]):
        super().__init__(pages)
        self.fast_exc = fast_exc

    def execute_ch_query(self, query, params, timeout_ms, settings):
        self.calls.append((query, dict(params), timeout_ms, dict(settings)))
        if self.fast_exc is not None:
            exc, self.fast_exc = self.fast_exc, None
            raise exc
        data = self.pages.pop(0) if self.pages else []
        return QueryResult(data, len(data), "clickhouse", 1)


class _ScriptedAnalytics(_Analytics):
    def __init__(self, outcomes: list[list[dict] | Exception]):
        super().__init__()
        self.outcomes = list(outcomes)

    def execute_ch_query(self, query, params, timeout_ms, settings):
        self.calls.append((query, dict(params), timeout_ms, dict(settings)))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return QueryResult(outcome, len(outcome), "clickhouse", 1)


class _Clock:
    def __init__(self, *values: float):
        self.values = list(values)
        self.last = values[-1] if values else 0

    def __call__(self) -> float:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def test_adjacent_slices_preserve_exact_newest_first_prefix():
    end = datetime(2026, 7, 30, 12, 3)
    start = end - timedelta(minutes=3)
    analytics = _Analytics(
        [
            [{"id": "newest-2"}, {"id": "newest-1"}],
            [{"id": "older-2"}, {"id": "older-1"}],
        ]
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        clock=_Clock(0, 0.01, 0.02, 0.03),
    )

    assert [row["id"] for row in result.data] == [
        "newest-2",
        "newest-1",
        "older-2",
        "older-1",
    ]
    assert complete is True
    assert full_window is True
    seed_calls = _seed_calls(analytics)
    classifier_calls = _classifier_calls(analytics)
    assert len(seed_calls) == len(classifier_calls) == 2
    first_query, first_params, first_timeout, first_settings = seed_calls[0]
    second_query, second_params, second_timeout, _ = seed_calls[1]
    assert first_query == second_query
    assert "FROM spans FINAL" not in first_query
    assert "GROUP BY id" not in first_query
    assert "argMax(" not in first_query
    assert "span_attr_str" in first_query
    assert "arbitrary_string_key" in first_query
    assert first_params["candidate_slice_end"] == end
    assert first_params["candidate_slice_start"] == end - timedelta(minutes=1)
    assert second_params["candidate_slice_end"] == first_params["candidate_slice_start"]
    assert second_params["candidate_slice_start"] == start
    assert first_params["candidate_seed_limit"] == 6
    assert second_params["candidate_seed_limit"] == 4
    assert 0 < second_timeout <= first_timeout <= 750
    assert first_settings["timeout_overflow_mode"] == "throw"
    assert first_settings["read_overflow_mode"] == "throw"
    assert first_settings["max_result_rows"] == 6
    assert first_settings["max_threads"] == 1
    assert first_settings["max_block_size"] == 8192
    assert all(
        "start_time >= %(start_date)s" in query
        and "latest_is_deleted = 0" in query
        and "latest_attr_value_0" in query
        for query, _, _, _ in classifier_calls
    )


def test_unfiltered_span_prefix_uses_adjacent_low_memory_slices():
    end = datetime(2026, 7, 30, 12, 2)
    start = end - timedelta(minutes=2)
    analytics = _Analytics(
        [
            [{"id": "newest"}],
            [{"id": "older"}],
        ]
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _unfiltered_builder(start, end),
        analytics,
        clock=lambda: 0,
    )

    assert [row["id"] for row in result.data] == ["newest", "older"]
    assert complete is True
    assert full_window is True
    seed_calls = _seed_calls(analytics)
    assert len(seed_calls) == 2
    assert len(_classifier_calls(analytics)) == 2
    for query, params, timeout_ms, settings in seed_calls:
        assert "FROM spans FINAL" not in query
        assert "ORDER BY start_time DESC, id DESC" in query
        assert "start_time >= %(candidate_slice_start)s" in query
        assert "start_time < %(candidate_slice_end)s" in query
        assert params["candidate_seed_limit"] > 0
        assert 0 < timeout_ms <= 750
        assert settings["max_threads"] == 1
        assert settings["max_block_size"] == 8192


def test_duplicate_ids_across_slices_do_not_fill_the_unique_prefix():
    end = datetime(2026, 7, 30, 12, 3)
    start = end - timedelta(minutes=3)
    analytics = _Analytics(
        [
            [{"id": "newest"}, {"id": "newest"}, {"id": "second"}],
            [
                {"id": "second"},
                {"id": "third"},
                {"id": "third"},
                {"id": "fourth"},
            ],
        ]
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        clock=lambda: 0,
    )

    assert [row["id"] for row in result.data] == [
        "newest",
        "second",
        "third",
        "fourth",
    ]
    assert len(_seed_calls(analytics)) == 2
    assert len(_classifier_calls(analytics)) == 2
    assert complete is True
    assert full_window is False


def test_duplicate_saturated_slice_classifies_id_once_before_continuation():
    end = datetime(2026, 7, 30, 12, 3)
    start = end - timedelta(minutes=3)
    analytics = _Analytics(
        [
            [{"id": "duplicate"} for _ in range(6)],
            [{"id": "must-not-be-read"}],
        ]
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        clock=lambda: 0,
    )

    assert [row["id"] for row in result.data].count("duplicate") == 1
    assert len(_classifier_calls(analytics)) >= 1
    assert list(_classifier_calls(analytics)[0][1]["candidate_span_ids"]) == [
        "duplicate"
    ]
    assert all(
        "duplicate" not in call[1]["candidate_span_ids"]
        for call in _classifier_calls(analytics)[1:]
    )
    assert complete is True
    assert full_window is True


@pytest.mark.parametrize("newer_state", ["nonmatch", "key_clear", "tombstone"])
def test_newer_cross_slice_state_is_marked_seen_before_classification(newer_state):
    end = datetime(2026, 7, 30, 12, 2)
    start = end - timedelta(minutes=2)
    span_id = f"mutable-{newer_state}"
    analytics = _Analytics(
        pages=[
            [{"id": span_id, "start_time": end - timedelta(seconds=30)}],
            [{"id": span_id, "start_time": start + timedelta(seconds=30)}],
        ],
        # An omitted classifier row represents each of the three conclusive
        # latest states: current value does not match, key absent, or deleted.
        classifications={span_id: None},
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end, page_size=1),
        analytics,
        clock=lambda: 0,
    )

    assert result.data == []
    assert complete is True
    assert full_window is True
    assert len(_seed_calls(analytics)) == 2
    assert len(_classifier_calls(analytics)) == 1
    assert _classifier_calls(analytics)[0][1]["candidate_span_ids"] == (span_id,)


def test_classifier_read_budget_failure_stops_before_older_slice():
    end = datetime(2026, 7, 30, 12, 2)
    start = end - timedelta(minutes=2)

    class _ClassifierFailureAnalytics(_Analytics):
        def execute_ch_query(self, query, params, timeout_ms, settings):
            if "candidate_span_ids" in params:
                self.calls.append((query, dict(params), timeout_ms, dict(settings)))
                raise ServerException(
                    "classifier exceeded read budget",
                    code=ErrorCodes.TIMEOUT_EXCEEDED,
                )
            return super().execute_ch_query(query, params, timeout_ms, settings)

    analytics = _ClassifierFailureAnalytics(
        pages=[
            [{"id": "newer", "start_time": end - timedelta(seconds=30)}],
            [{"id": "must-not-be-read", "start_time": start}],
        ]
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end, page_size=1),
        analytics,
        clock=lambda: 0,
    )

    assert result.data == []
    assert complete is False
    assert full_window is False
    assert len(_seed_calls(analytics)) == 1
    assert len(_classifier_calls(analytics)) == 1
    assert len(analytics.pages) == 1


def test_classifier_canonical_time_requires_frontier_proof_before_stopping():
    end = datetime(2026, 7, 30, 12, 3)
    start = end - timedelta(minutes=3)
    analytics = _Analytics(
        pages=[
            [
                {"id": "a", "start_time": end - timedelta(seconds=1)},
                {"id": "b", "start_time": end - timedelta(seconds=2)},
            ],
            [{"id": "c", "start_time": end - timedelta(minutes=2)}],
        ],
        classifications={
            "a": {"id": "a", "start_time": start + timedelta(seconds=10)},
            "b": {"id": "b", "start_time": start + timedelta(seconds=20)},
            "c": {"id": "c", "start_time": end - timedelta(minutes=2)},
        },
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end, page_size=1),
        analytics,
        clock=lambda: 0,
    )

    assert [row["id"] for row in result.data] == ["c", "b", "a"]
    # The older slice is saturated at its one-row cap, so one keyset
    # continuation proves that slice exhausted before completion is declared.
    assert len(_seed_calls(analytics)) == 3
    assert len(_classifier_calls(analytics)) == 2
    assert complete is True
    assert full_window is True


def test_empty_result_is_conclusive_only_after_every_slice_completes():
    end = datetime(2026, 7, 30, 12, 2)
    start = end - timedelta(minutes=2)
    analytics = _Analytics([[], []])

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        clock=lambda: 0,
    )

    assert result.data == []
    assert len(_seed_calls(analytics)) == 2
    assert _classifier_calls(analytics) == []
    assert complete is True
    assert full_window is True


def test_wide_low_volume_window_uses_only_adjacent_scalar_slices():
    end = datetime(2026, 7, 30, 12)
    start = end - timedelta(days=1)
    analytics = _Analytics([[{"id": "match-from-yesterday"}]])

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        clock=lambda: 0,
    )

    assert [row["id"] for row in result.data] == ["match-from-yesterday"]
    assert complete is True
    assert full_window is True
    seed_calls = _seed_calls(analytics)
    assert len(seed_calls) > 1
    _, params, timeout_ms, settings = seed_calls[0]
    assert params["candidate_slice_start"] == end - timedelta(minutes=1)
    assert params["candidate_slice_end"] == end
    assert 0 < timeout_ms <= 750
    assert all("FINAL" not in query for query, _, _, _ in analytics.calls)
    assert settings["timeout_overflow_mode"] == "throw"
    assert settings["read_overflow_mode"] == "throw"


def test_slice_timeout_stops_without_retrying_as_control_flow():
    end = datetime(2026, 7, 30, 12)
    start = end - timedelta(hours=1)
    analytics = _FastAttemptThenSlicesAnalytics(
        ServerException(
            "whole window exceeded sub-budget",
            code=ErrorCodes.TIMEOUT_EXCEEDED,
        ),
        [[{"id": "newest-match"}], []],
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        max_slices=2,
        clock=lambda: 0,
    )

    assert result.data == []
    assert complete is False
    assert full_window is False
    assert len(_seed_calls(analytics)) == 1
    _, first_slice_params, first_slice_timeout, _ = _seed_calls(analytics)[0]
    assert first_slice_params["candidate_slice_start"] == end - timedelta(minutes=1)
    assert first_slice_params["candidate_slice_end"] == end
    assert 0 < first_slice_timeout <= 750


def test_empty_future_tail_is_proven_before_bounded_span_slices():
    now = datetime(2026, 7, 31, 2, 50)
    end = now + timedelta(hours=4)
    start = now - timedelta(hours=1)
    analytics = _Analytics([[], []])

    with mock.patch("tracer.views.observation_span.timezone.now", return_value=now):
        result, complete, full_window = _execute_bounded_span_filter_prefix(
            _builder(start, end),
            analytics,
            max_slices=1,
            clock=lambda: 0,
        )

    assert result.data == []
    assert complete is False
    assert full_window is False
    assert len(analytics.calls) == 2
    tail_query, tail_params, tail_timeout, tail_settings = analytics.calls[0]
    _, first_slice_params, _, _ = analytics.calls[1]
    assert "FROM spans" in tail_query
    assert "FINAL" not in tail_query
    assert "parent_span_id" not in tail_query
    assert tail_params["future_tail_start"] == now + timedelta(minutes=5)
    assert tail_params["future_tail_end"] == end
    assert tail_timeout == 100
    assert tail_settings["max_threads"] == 1
    assert tail_settings["max_memory_usage"] == 64 * 1024 * 1024
    assert first_slice_params["candidate_slice_end"] == now + timedelta(minutes=5)
    assert first_slice_params["candidate_slice_start"] == now + timedelta(minutes=4)


def test_future_skewed_span_fails_closed_without_using_partial_fallback():
    now = datetime(2026, 7, 31, 2, 50)
    end = now + timedelta(hours=4)
    start = now - timedelta(hours=1)
    analytics = _Analytics([[{"future_tail_row": 1}], [{"id": "must-not-be-used"}]])

    with mock.patch("tracer.views.observation_span.timezone.now", return_value=now):
        result, complete, full_window = _execute_bounded_span_filter_prefix(
            _builder(start, end),
            analytics,
            max_slices=1,
            clock=lambda: 0,
        )

    assert result.data == []
    assert complete is False
    assert full_window is False
    assert len(analytics.calls) == 1


def test_completed_sparse_slices_expand_without_gaps_to_find_an_old_match():
    end = datetime(2026, 7, 30, 12)
    start = end - timedelta(minutes=15)
    analytics = _Analytics([[], [], [], [{"id": "old-match"}]])

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        max_slices=16,
        clock=lambda: 0,
    )

    assert [row["id"] for row in result.data] == ["old-match"]
    assert complete is True
    assert full_window is True
    assert [
        (params["candidate_slice_start"], params["candidate_slice_end"])
        for _, params, _, _ in _seed_calls(analytics)
    ] == [
        (end - timedelta(minutes=1), end),
        (end - timedelta(minutes=3), end - timedelta(minutes=1)),
        (end - timedelta(minutes=7), end - timedelta(minutes=3)),
        (start, end - timedelta(minutes=7)),
    ]


def test_failed_wide_slice_retries_only_the_unread_range_at_half_width():
    end = datetime(2026, 7, 30, 12)
    start = end - timedelta(minutes=4)
    analytics = _ScriptedAnalytics(
        [
            [],
            ServerException(
                "widened slice exceeded read budget",
                code=ErrorCodes.TOO_MANY_ROWS_OR_BYTES,
            ),
            [],
            [],
        ]
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        max_slices=4,
        clock=lambda: 0,
    )

    assert result.data == []
    assert complete is True
    assert full_window is True
    assert [
        (params["candidate_slice_start"], params["candidate_slice_end"])
        for _, params, _, _ in _seed_calls(analytics)
    ] == [
        (end - timedelta(minutes=1), end),
        (end - timedelta(minutes=3), end - timedelta(minutes=1)),
        (end - timedelta(minutes=2), end - timedelta(minutes=1)),
        (start, end - timedelta(minutes=2)),
    ]


def test_exhausted_final_slice_keeps_all_unique_rows_for_exact_total():
    end = datetime(2026, 7, 30, 12, 1)
    start = end - timedelta(minutes=1)
    analytics = _Analytics([[{"id": f"span-{index}"} for index in range(5)]])

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        clock=lambda: 0,
    )

    assert [row["id"] for row in result.data] == [
        "span-0",
        "span-1",
        "span-2",
        "span-3",
        "span-4",
    ]
    assert complete is True
    assert full_window is True


def test_shared_deadline_returns_an_explicit_incomplete_exact_prefix():
    end = datetime(2026, 7, 30, 12, 10)
    start = end - timedelta(minutes=10)
    analytics = _Analytics([[{"id": "newest-match"}]])

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        clock=_Clock(0, 0.2, 0.3, 2.11, 2.12),
    )

    # The deadline expires before the cross-slice proof. A locally classified
    # row is still provisional and therefore cannot leak into the response.
    assert result.data == []
    assert len(_seed_calls(analytics)) == len(_classifier_calls(analytics)) == 1
    assert all(call[2] <= 750 for call in analytics.calls)
    assert complete is False
    assert full_window is False


def test_read_budget_error_is_not_exposed_or_mistaken_for_empty():
    end = datetime(2026, 7, 30, 12, 10)
    start = end - timedelta(minutes=10)
    analytics = _Analytics(
        exc=ServerException(
            "sensitive ClickHouse internals",
            code=ErrorCodes.TIMEOUT_EXCEEDED,
        )
    )

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end),
        analytics,
        clock=lambda: 0,
    )

    assert result.data == []
    assert complete is False
    assert full_window is False


def test_programming_error_is_not_hidden_as_an_empty_result():
    end = datetime(2026, 7, 30, 12, 10)
    start = end - timedelta(minutes=10)
    analytics = _Analytics(exc=RuntimeError("query contract bug"))

    with pytest.raises(RuntimeError, match="query contract bug"):
        _execute_bounded_span_filter_prefix(
            _builder(start, end),
            analytics,
            clock=lambda: 0,
        )


def test_deep_page_low_volume_window_returns_exact_deep_result():
    end = datetime(2026, 7, 30, 12, 1)
    start = end - timedelta(minutes=1)
    source_rows = [
        {
            "id": f"span-{index:06d}",
            "start_time": end - timedelta(seconds=index / 100),
        }
        for index in range(1200)
    ]
    analytics = _Analytics([source_rows])

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end, page_number=3, page_size=500),
        analytics,
        clock=lambda: 0,
    )

    page, has_more = paginate_deduped(result.data, "id", 3, 500)
    assert page == []
    assert has_more is False
    assert result.data == source_rows
    assert complete is True
    assert full_window is True
    assert len(_seed_calls(analytics)) == 1
    assert len(_classifier_calls(analytics)) == 19
    assert all(
        len(call[1]["candidate_span_ids"]) <= 64
        for call in _classifier_calls(analytics)
    )
    seed_call = _seed_calls(analytics)[0]
    assert seed_call[1]["candidate_seed_limit"] == 2000
    assert seed_call[3]["max_result_rows"] == 2000


def test_deep_page_dense_slice_uses_keyset_and_returns_exact_page():
    end = datetime(2026, 7, 30, 12, 1)
    start = end - timedelta(minutes=1)
    first_rows = [
        {
            "id": f"span-{index:06d}",
            "start_time": end - timedelta(milliseconds=index),
        }
        for index in range(2000)
    ]
    second_rows = [
        {
            "id": f"span-{2000 + index:06d}",
            "start_time": end - timedelta(milliseconds=2000 + index),
        }
        for index in range(750)
    ]
    analytics = _Analytics([first_rows, second_rows])

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end, page_number=3, page_size=500),
        analytics,
        clock=lambda: 0,
    )

    page, has_more = paginate_deduped(result.data, "id", 3, 500)
    assert [row["id"] for row in page] == [
        f"span-{index:06d}" for index in range(1500, 2000)
    ]
    assert has_more is True
    assert len(result.data) == 2750
    assert complete is True
    assert full_window is True
    seed_calls = _seed_calls(analytics)
    assert len(seed_calls) == 2
    assert len(_classifier_calls(analytics)) == 44
    first_query, first_params, _, first_settings = seed_calls[0]
    assert "candidate_before_start_time" not in first_query
    assert (
        first_params["candidate_seed_limit"]
        == first_settings["max_result_rows"]
        == 2000
    )


def test_deep_equal_timestamp_page_keeps_exact_id_tiebreaker():
    end = datetime(2026, 7, 30, 12, 1)
    start = end - timedelta(minutes=1)
    newest_time = end - timedelta(seconds=1)
    older_time = end - timedelta(seconds=2)
    first_rows = [
        {
            "id": f"span-{index:06d}",
            "start_time": newest_time,
        }
        for index in range(3000, 1000, -1)
    ]
    second_rows = [
        {"id": "span-002000", "start_time": older_time},
        *[
            {
                "id": f"span-{index:06d}",
                "start_time": older_time,
            }
            for index in range(1000, 498, -1)
        ],
    ]
    analytics = _Analytics([first_rows, second_rows])

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end, page_number=3, page_size=500),
        analytics,
        clock=lambda: 0,
    )

    ids = [row["id"] for row in result.data]
    assert ids[:3] == ["span-003000", "span-002999", "span-002998"]
    assert len(ids) == 2502
    assert len(set(ids)) == len(ids)
    assert complete is True
    assert full_window is True
    seed_calls = _seed_calls(analytics)
    assert len(seed_calls) == 2
    assert len(_classifier_calls(analytics)) == 40


def test_deep_page_deadline_returns_exact_incomplete_prefix():
    end = datetime(2026, 7, 30, 12, 1)
    start = end - timedelta(minutes=1)
    first_rows = [
        {
            "id": f"span-{index:06d}",
            "start_time": end - timedelta(milliseconds=index),
        }
        for index in range(2000)
    ]
    analytics = _Analytics([first_rows])

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        _builder(start, end, page_number=3, page_size=500),
        analytics,
        clock=_Clock(0, 0, 2.101, 2.102),
    )

    # The seed consumed the remaining deadline before point classification, so
    # no unclassified candidate may leak into the response.
    assert result.data == []
    assert len(_seed_calls(analytics)) == 1
    assert _classifier_calls(analytics) == []
    assert complete is False
    assert full_window is False


def test_custom_sort_fails_closed_because_time_slices_cannot_preserve_it():
    end = datetime(2026, 7, 30, 12, 10)
    start = end - timedelta(minutes=10)
    analytics = _Analytics()
    builder = _builder(start, end)
    builder.sort_params = [{"column_id": "latency", "direction": "asc"}]

    result, complete, full_window = _execute_bounded_span_filter_prefix(
        builder,
        analytics,
        clock=lambda: 0,
    )

    assert result.data == []
    assert analytics.calls == []
    assert complete is False
    assert full_window is False
