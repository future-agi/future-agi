"""A filtered Session cursor must reach the request window and then stop.

The bounded selector widens a seed slice only after the narrower one was
exhausted.  Capping that schedule below the request window made a sparse
twelve-month session walk hand out checkpoint page after checkpoint page - each
answering ``rows: 0, has_more: true`` long after the whole result had been
delivered.  These tests pin the builder contract that removes the cap, the
halving valve that keeps one widened read safe, and the end-to-end walk.

They also pin the third consumer of these hooks - the eval-task session lane -
on the *other* side of the same contract: it reads a single fully buffered
page with no continuation and never passes the valve, so it must not receive
the widening either.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from clickhouse_driver.errors import Error as ClickHouseError
from clickhouse_driver.errors import ErrorCodes

from tracer.models.eval_task import RowType
from tracer.selectors.eval_tasks import row_resolver
from tracer.selectors.trace_filter_reads import (
    BoundedFilterPage,
    read_bounded_filter_page,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

NOW = datetime(2026, 9, 1, 0, 0)
WINDOW = timedelta(days=365)
MATCH_AT = NOW - WINDOW + timedelta(days=25)
SHARED_MAX_SLICE = timedelta(days=2)
PROJECT = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


def _filters(*, window: timedelta = WINDOW) -> list[dict]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [(NOW - window).isoformat(), NOW.isoformat()],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "not_equals",
                "filter_value": "Rejected",
            },
        },
    ]


def _builder(**kwargs) -> SessionListQueryBuilderV2:
    options = {
        "project_id": PROJECT,
        "filters": _filters(),
        "page_number": 0,
        "page_size": 25,
        "sort_params": [],
        "bounded_internal_scan": True,
        **kwargs,
    }
    return SessionListQueryBuilderV2(**options)


class _SparseHistory:
    """One matching session at the oldest end of the request window."""

    def __init__(self, *, overflow_above: timedelta | None = None):
        self.overflow_above = overflow_above
        self.slices: list[tuple[datetime, datetime]] = []

    @property
    def widest_slice(self) -> timedelta:
        return max((end - start for start, end in self.slices), default=timedelta(0))

    def execute_ch_query(self, query, params, *, timeout_ms=None, settings=None):
        if "filter_slice_start" in params:
            start, end = params["filter_slice_start"], params["filter_slice_end"]
            self.slices.append((start, end))
            if self.overflow_above is not None and end - start > self.overflow_above:
                error = ClickHouseError("read budget exceeded")
                error.code = ErrorCodes.TOO_MANY_BYTES
                raise error
            return SimpleNamespace(
                data=[{"session_id": SESSION_ID, "start_time": MATCH_AT}]
                if start <= MATCH_AT < end
                else []
            )
        if "candidate_filter_session_ids" in params:
            matched = SESSION_ID in tuple(params["candidate_filter_session_ids"])
            return SimpleNamespace(
                data=[{"session_id": SESSION_ID, "start_time": MATCH_AT}]
                if matched
                else []
            )
        return SimpleNamespace(data=[])


def _walk(
    executor_factory, *, max_pages: int = 40, retry: bool | None = None
) -> list[dict]:
    """Follow the signed continuation exactly as the session view does."""

    if retry is None:
        retry = _builder().should_retry_filter_wide_read_budget()
    continuation: dict = {}
    checkpoints: list[tuple] = []
    walked: list[dict] = []
    for _ in range(max_pages):
        executor = executor_factory()
        page = read_bounded_filter_page(
            builder=_builder(),
            analytics=executor,
            filters=_filters(),
            key_field="session_id",
            page_number=0,
            page_size=25,
            deadline_ms=30_000,
            max_candidates=200,
            max_seed_attempts=24,
            max_query_count=48,
            classify_batch_size=50,
            include_incomplete_rows=True,
            bounded_continuation=True,
            carry_continuation_slice_width=True,
            retry_wide_read_budget=retry,
            **continuation,
        )
        walked.append(
            {
                "rows": len(page.rows),
                "complete": page.complete,
                "widest_slice": executor.widest_slice,
                "stalled": not page.complete and page.continuation_slice_end is None,
            }
        )
        if page.complete or page.continuation_slice_end is None:
            break
        checkpoint = (page.continuation_slice_start, page.continuation_slice_end)
        assert checkpoint not in checkpoints, "checkpoint did not advance"
        checkpoints.append(checkpoint)
        continuation = {
            "continuation_slice_start": page.continuation_slice_start,
            "continuation_slice_end": page.continuation_slice_end,
            "continuation_before_start_time": page.continuation_before_start_time,
            "continuation_before_id": page.continuation_before_id,
        }
    return walked


@pytest.mark.unit
def test_exhausted_session_slice_may_widen_to_the_request_window():
    builder = _builder()
    start, end = builder.parse_time_range(builder.filters)

    assert builder.recommended_filter_max_slice_width() == end - start
    assert builder.should_retry_filter_wide_read_budget() is True


@pytest.mark.unit
def test_sampled_and_sub_minimum_session_lanes_keep_the_shared_ceiling():
    sampled = _builder(bounded_sampling_salt="salt", bounded_sampling_rate=10.0)
    narrow = _builder(filters=_filters(window=timedelta(minutes=1)))

    assert sampled.recommended_filter_max_slice_width() is None
    assert sampled.should_retry_filter_wide_read_budget() is False
    assert narrow.recommended_filter_max_slice_width() is None


@pytest.mark.unit
def test_sparse_twelve_month_session_cursor_finishes_without_empty_pages():
    walked = _walk(_SparseHistory, retry=True)

    assert [page["rows"] for page in walked] == [1]
    assert walked[-1]["complete"] is True
    assert walked[0]["widest_slice"] > SHARED_MAX_SLICE


@pytest.mark.unit
def test_widened_session_seed_that_exceeds_its_read_budget_still_terminates():
    def dense_region() -> _SparseHistory:
        return _SparseHistory(overflow_above=timedelta(days=16))

    walked = _walk(dense_region, retry=True)

    assert walked[-1]["complete"] is True
    assert sum(page["rows"] for page in walked) == 1
    # Without the halving valve the widened seed fails on every attempt, so the
    # read publishes no checkpoint at all and the view answers 503 instead.
    unvalved = _walk(dense_region, retry=False)
    assert unvalved[-1]["complete"] is False
    assert unvalved[-1]["stalled"] is True


@pytest.mark.unit
def test_session_filter_page_read_carries_its_slice_schedule_across_a_cursor():
    from tracer.views.trace_session import _read_session_filter_page

    builder = _builder()
    deadline = SimpleNamespace(remaining_ms=lambda _default: 30_000)
    with mock.patch(
        "tracer.views.trace_session.read_bounded_filter_page"
    ) as bounded_read:
        _read_session_filter_page(builder, object(), deadline, cursor_enabled=True)
        _read_session_filter_page(builder, object(), deadline, cursor_enabled=False)

    cursor_call, numbered_call = bounded_read.call_args_list
    assert cursor_call.kwargs["carry_continuation_slice_width"] is True
    assert cursor_call.kwargs["retry_wide_read_budget"] is True
    assert numbered_call.kwargs["carry_continuation_slice_width"] is False
    assert numbered_call.kwargs["retry_wide_read_budget"] is True


@pytest.mark.unit
def test_eval_task_session_lane_gets_neither_the_widening_nor_the_valve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The eval-task session lane is the third consumer of these two hooks.

    ``tracer/selectors/eval_tasks/row_resolver.py`` builds ``SESSION_LIST`` for
    ``row_type=sessions`` and reads one fully buffered page: no
    ``bounded_continuation``, no ``include_incomplete_rows`` and no
    ``retry_wide_read_budget``.  A widened seed there could only raise
    ``EvalTaskReadBudgetExceeded`` for the whole task, because there is no
    checkpoint to fall back to - the exact half-a-fix
    ``test_widened_session_seed_that_exceeds_its_read_budget_still_terminates``
    declares unsafe.  That lane always constructs the builder with a
    sampling salt/rate pair, so both hooks stay off together and its slice
    ceiling is exactly what it was before this PR.  Pin the pairing so the
    combination cannot appear by accident.
    """

    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[{"session_id": SESSION_ID, "start_time": MATCH_AT}],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=1,
            elapsed_ms=10,
            query_count=1,
            rows_returned=1,
            result_payload_bytes=20,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql="must-not-run-legacy-id-order",
        params={"start_date": NOW - WINDOW, "end_date": NOW},
        project_id=PROJECT,
        salt="task-salt",
        sampling_rate=100.0,
        filters={"filters": _filters(), "date_range": [NOW - WINDOW, NOW]},
        limit=25,
        batch_size=200,
        row_type=RowType.SESSIONS,
    )

    assert ids == [SESSION_ID]
    builder = captured["builder"]
    assert builder.recommended_filter_max_slice_width() is None
    assert builder.should_retry_filter_wide_read_budget() is False
    assert captured.get("retry_wide_read_budget", False) is False
    assert captured.get("bounded_continuation", False) is False
    assert captured.get("include_incomplete_rows", False) is False
