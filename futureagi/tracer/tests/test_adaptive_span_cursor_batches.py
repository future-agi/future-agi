"""Adaptive acquisition must never alter exact publication or cursor coverage."""

from datetime import timedelta

import pytest

from tracer.selectors import trace_filter_reads as selector
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.tests.test_bounded_trace_filter_reads import (
    END,
    _FakeBuilder,
    _FakeExecutor,
    _time_filter,
)

pytestmark = pytest.mark.unit


class AdaptiveBuilder(_FakeBuilder):
    def recommended_filter_cursor_seed_batch_size(self):
        return 32

    def recommended_filter_cursor_adaptive_seed_batch_size(self):
        return 200


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(selector, "monotonic", lambda: 0)


def subject(rejected=2000, accepted=52):
    # A whole same-timestamp tie spans every batch-size transition.
    rows = [
        {
            "id": f"span-{rejected + accepted - i:05d}",
            "start_time": END - timedelta(minutes=1),
        }
        for i in range(rejected + accepted)
    ]
    return AdaptiveBuilder(rows, match_rows=rows[rejected:])


def read(builder, executor, **overrides):
    options = {
        "builder": builder,
        "analytics": executor,
        "filters": [_time_filter(builder.start, builder.end)],
        "key_field": "id",
        "page_number": 0,
        "page_size": 50,
        "deadline_ms": 30000,
        "max_seed_attempts": 64,
        "max_query_count": 128,
        "max_candidates": 512,
        "classify_batch_size": 200,
        "bounded_continuation": True,
        "include_incomplete_rows": True,
    }
    options.update(overrides)
    return selector.read_bounded_filter_page(**options)


@pytest.mark.parametrize("page_size", [1, 50])
def test_thousands_of_rejects_grow_only_internal_batches_without_skipping_ties(
    page_size,
):
    builder = subject()
    executor = _FakeExecutor(builder)
    result = read(builder, executor, page_size=page_size)
    assert result.complete and result.has_more
    assert result.rows == builder.match_rows[:page_size]
    seeds = [params for kind, params in executor.calls if kind == "seed"]
    initial = max(32, page_size + 1)
    assert [p["limit"] for p in seeds[:4]] == [
        initial,
        initial * 2,
        min(200, initial * 4),
        200,
    ]
    assert len(seeds) < 17
    assert all(p["limit"] <= 200 for p in seeds)
    # A full page preceding growth is not mistaken for an exhausted slice.
    assert all(p["slice_start"] == seeds[0]["slice_start"] for p in seeds)
    assert all(p["slice_end"] == seeds[0]["slice_end"] for p in seeds)
    assert all(
        p["before_start_time"] == builder.rows[0]["start_time"] for p in seeds[1:]
    )
    classified = [p["candidate_ids"] for kind, p in executor.calls if kind == "match"]
    assert max(map(len, classified)) <= 200


def test_complete_high_yield_prefix_does_not_grow_or_change_public_page_size():
    builder = subject(rejected=0)
    executor = _FakeExecutor(builder)
    result = read(builder, executor)
    assert result.complete and result.rows == builder.match_rows[:50]
    assert [p["limit"] for q, p in executor.calls if q == "seed"] == [51]


@pytest.mark.parametrize(
    "classify_size,max_candidates,cap", [(64, 512, 64), (200, 96, 96)]
)
def test_growth_preserves_explicit_classifier_and_candidate_working_sets(
    classify_size, max_candidates, cap
):
    builder = subject(rejected=120, accepted=4)
    executor = _FakeExecutor(builder)
    result = read(
        builder,
        executor,
        page_size=1,
        classify_batch_size=min(classify_size, max_candidates),
        max_candidates=max_candidates,
    )
    assert result.complete and result.rows == builder.match_rows[:1]
    limits = [p["limit"] for q, p in executor.calls if q == "seed"]
    assert max(limits) == cap
    assert all(
        len(p["candidate_ids"]) <= cap for q, p in executor.calls if q == "match"
    )


def test_classifier_failure_after_growth_keeps_previous_checkpoint_and_exact_resume():
    builder = subject(rejected=220, accepted=4)

    class FailingExecutor(_FakeExecutor):
        classifiers = 0

        def execute_ch_query(self, query, params, **kwargs):
            if query == "match":
                self.classifiers += 1
                if self.classifiers == 3:
                    self.calls.append((query, params))
                    raise ReadDeadlineExceeded("diagnostic")
            return super().execute_ch_query(query, params, **kwargs)

    executor = FailingExecutor(builder)
    interrupted = read(builder, executor, page_size=1)
    assert not interrupted.complete and interrupted.rows == []
    seeds = [p for q, p in executor.calls if q == "seed"]
    assert [p["limit"] for p in seeds] == [32, 64, 128]
    assert interrupted.continuation_before_id == builder.rows[95]["id"]
    resumed_executor = _FakeExecutor(builder)
    resumed = read(
        builder,
        resumed_executor,
        page_size=1,
        continuation_slice_start=interrupted.continuation_slice_start,
        continuation_slice_end=interrupted.continuation_slice_end,
        continuation_before_start_time=interrupted.continuation_before_start_time,
        continuation_before_id=interrupted.continuation_before_id,
    )
    assert resumed.complete and resumed.rows == builder.match_rows[:1]
    first = next(p for q, p in resumed_executor.calls if q == "seed")
    assert first["before_id"] == builder.rows[95]["id"]
    assert first["slice_start"] == seeds[-1]["slice_start"]
    assert first["slice_end"] == seeds[-1]["slice_end"]


@pytest.mark.parametrize("recommendation", [0, -1, True, 2.5])
def test_invalid_adaptive_budget_rejected_before_any_database_read(recommendation):
    builder = subject()
    builder.recommended_filter_cursor_adaptive_seed_batch_size = lambda: recommendation
    executor = _FakeExecutor(builder)
    with pytest.raises(ValueError, match="adaptive seed batch"):
        read(builder, executor)
    assert executor.calls == []


def test_builder_without_explicit_opt_in_retains_old_acquisition_size():
    builder = subject(rejected=80)
    builder.recommended_filter_cursor_adaptive_seed_batch_size = lambda: None
    executor = _FakeExecutor(builder)
    assert read(builder, executor).complete
    assert {p["limit"] for q, p in executor.calls if q == "seed"} == {51}
