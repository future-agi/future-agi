"""Short exact strings may seed acquisition, never change exact membership.

The typed-string lane has two regimes that differ only in which value index
they can prove necessary: long literals anchor the concatenated-lowercase LIKE
index, short ones reuse the compiler's own typed value bloom for
``equals``/``in``. Both publish through the unchanged latest-state classifier.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.conf import settings
from django.test import override_settings

from tracer.selectors.filter_seed_width import FilterSeedWidthPolicy
from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_builders.trace_list import (
    TraceListQueryBuilder,
    _unix_microseconds,
)
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_bounded_trace_filter_reads import (
    _attribute_filter,
    _FakeBuilder,
    _FakeExecutor,
    _render_driver_sql,
    _time_filter,
)
from tracer.tests.test_trace_indexed_coordinate_reads import END, PROJECT
from tracer.tests.test_trace_root_physical_replay import assert_coherent_classifier

pytestmark = pytest.mark.unit

# Neutral tenant-shaped identifiers: short, ASCII, and exactly the picker shape
# the grid sends for a SPAN_ATTRIBUTE list filter.
ACCOUNT_KEY = "account_id"
ACCOUNT_VALUES = ["acct-1", "acct-2"]
LONG_TEXT = "long literal %_\\ café " * 8


def subject(
    width: int = 1,
    *,
    kind: str = "text",
    operation: str = "equals",
    value: Any = ACCOUNT_VALUES[0],
    types: list[str] | None = None,
    window: timedelta = timedelta(days=7),
    end: datetime = END,
    extra_leaves: list[dict[str, Any]] | None = None,
    cls: type = TraceListQueryBuilderV2,
    **kwargs: Any,
):
    leaves = []
    for index in range(width):
        leaf = _attribute_filter(
            f"{ACCOUNT_KEY}_{index}", value, filter_type=kind, operation=operation
        )
        if types is not None:
            leaf["filter_config"]["attribute_value_types"] = types
        leaves.append(leaf)
    return cls(
        project_id=PROJECT,
        filters=[
            _time_filter(end - window, end),
            *leaves,
            *(extra_leaves or []),
        ],
        page_size=25,
        **kwargs,
    )


def picker_leaves(width: int = 1, *, operation: str = "in", **kwargs):
    """The exact shape ``buildApiFilterFromPanelRow`` emits for a value list."""

    return subject(
        width,
        operation=operation,
        value=ACCOUNT_VALUES,
        types=["string"] * len(ACCOUNT_VALUES),
        **kwargs,
    )


@pytest.mark.parametrize("width", [1, 2, 5, 10])
@pytest.mark.parametrize(
    "make",
    [
        lambda width: subject(width),
        lambda width: subject(width, operation="in", value=ACCOUNT_VALUES),
        lambda width: picker_leaves(width),
    ],
    ids=["equals", "in", "picker_in"],
)
def test_short_exact_string_seeds_the_requested_root_population(width, make):
    builder = make(width)
    assert builder._public_short_text_candidate_seed_plan() is not None
    assert builder._public_long_text_candidate_seed_plan() is None
    assert builder._public_text_candidate_seed_plan() is not None
    assert builder._uses_short_text_candidate_seed()
    assert builder.supports_filter_candidate_seed_page()
    # The seed IS the query plan, not an abortable probe, and it proves the
    # published page order by itself.
    assert not builder.filter_candidate_seed_is_optional()
    assert builder.filter_candidate_seed_proves_result_order()
    assert not builder.supports_filter_anchor_probe()

    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=END - timedelta(hours=1), slice_end=END, limit=200
    )
    cte, roots = sql.split("SELECT trace_id, id AS root_span_id", 1)
    assert "matching_scalar_trace_identities" in cte
    assert "AND trace_id IN (" in cte
    assert "WHERE parent_span_id IS NULL OR parent_span_id = ''" in cte
    assert "attrs_string[" in cte
    # The compiler's own value companions, not a lane-specific hint.
    assert "hasAny(arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in cte or (
        "has(arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in cte
    )
    assert "arrayStringConcat" not in cte  # No long-text LIKE anchor here.
    assert "long_text_ngram" not in str(params)
    # No inner LIMIT, tombstone or page keyset may prune the child witness.
    assert "LIMIT" not in cte
    assert "is_deleted" not in cte
    assert "filter_before" not in cte
    assert cte.count("start_time >=") == 1
    assert cte.count("start_time <") == 1
    # Ordered, de-duplicated newest-first roots inside the slice.
    assert "AND (parent_span_id IS NULL OR parent_span_id = '')" in roots
    assert "is_deleted = 0" in roots
    assert "ORDER BY start_time DESC, trace_id DESC" in sql
    assert "LIMIT 1 BY trace_id" in sql
    assert "LIMIT %(filter_seed_limit)s" in sql
    assert params["filter_seed_limit"] == 200
    assert ACCOUNT_VALUES[0] not in sql
    _render_driver_sql(sql, params)

    exact, exact_params = builder.build_filter_identity_match_query_from_seed_rows(
        [{"trace_id": "candidate", "root_span_id": "not-authoritative"}]
    )
    assert_coherent_classifier(exact)
    assert "WHERE latest_is_deleted = 0" in exact
    assert "not-authoritative" not in str(exact_params)
    for index in range(width):
        assert f"latest_attr_value_{index}" in exact


def test_page_keyset_does_not_limit_the_necessary_child_witness():
    builder = subject(2)
    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=END - timedelta(hours=1),
        slice_end=END,
        limit=200,
        before_start_time=END - timedelta(minutes=5),
        before_id="previous",
    )
    cte, roots = sql.split("SELECT trace_id, id AS root_span_id", 1)
    assert "filter_before" not in cte
    assert "filter_before_start_us" in roots
    assert params["filter_before_id"] == "previous"


@pytest.mark.parametrize(
    "operation,value",
    [
        ("contains", ACCOUNT_VALUES[0]),
        ("starts_with", ACCOUNT_VALUES[0]),
        ("ends_with", ACCOUNT_VALUES[0]),
        ("not_equals", ACCOUNT_VALUES[0]),
        ("not_in", ACCOUNT_VALUES),
        ("not_contains", ACCOUNT_VALUES[0]),
        ("is_null", None),
        ("is_not_null", None),
    ],
)
def test_substring_and_negative_short_text_keep_existing_acquisition(operation, value):
    """No value companion exists for these, so the lane must decline."""

    builder = subject(operation=operation, value=value)
    assert builder._public_short_text_candidate_seed_plan() is None
    assert builder._public_scalar_candidate_seed_plan() is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "map", "value": {"text": ACCOUNT_VALUES[0]}},
        {"value": [ACCOUNT_VALUES[0], LONG_TEXT], "operation": "in"},
        {"value": "İ" * 100},
        {"value": "ik" * 100},
        {"types": ["number"], "operation": "in", "value": [1]},
        {"types": ["boolean"], "operation": "in", "value": [True]},
    ],
    ids=[
        "json",
        "mixed_lengths",
        "unanchorable_unicode",
        "unanchorable_ascii",
        "picker_number",
        "picker_boolean",
    ],
)
def test_json_mixed_length_and_non_string_values_stay_on_the_exact_route(kwargs):
    builder = subject(**kwargs)
    assert builder._public_short_text_candidate_seed_plan() is None
    assert not builder._uses_short_text_candidate_seed()


def test_long_text_keeps_its_own_anchored_regime():
    builder = subject(value=LONG_TEXT)
    assert builder._public_long_text_candidate_seed_plan() is not None
    assert builder._public_short_text_candidate_seed_plan() is None
    assert not builder._uses_short_text_candidate_seed()
    # Selective long literals still read the whole request window at once.
    assert builder.recommended_filter_initial_slice_width() == timedelta(days=7)
    assert builder.filter_seed_width_policy() is None


def test_numeric_sibling_keeps_the_numeric_anchor_and_its_full_window():
    builder = subject(
        extra_leaves=[
            _attribute_filter(
                "duration_s", 0.01, filter_type="number", operation="greater_than"
            )
        ]
    )
    plan = builder._public_scalar_candidate_seed_plan()
    assert plan is not None
    assert "span_attr_num[" in plan.raw_graph_value_witness_predicate
    assert not builder._uses_short_text_candidate_seed()
    assert builder.recommended_filter_initial_slice_width() == timedelta(days=7)
    # The numeric lane retains its optional/windowed contract unchanged.
    assert builder.supports_filter_windowed_candidate_seed_page()


def test_boolean_sibling_is_seeded_by_the_short_string_leaf():
    builder = subject(
        extra_leaves=[_attribute_filter("has_eval", True, filter_type="boolean")]
    )
    assert builder._public_boolean_candidate_seed_plan() is None  # Not a lone leaf.
    assert builder._uses_short_text_candidate_seed()
    assert not builder.filter_candidate_seed_is_optional()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sort_params": [{"field": "start_time", "direction": "desc"}]},
        {"search": ACCOUNT_VALUES[0]},
        {"bounded_internal_scan": True},
        {"bounded_identity_only": True},
        {"bounded_sampling_salt": "test", "bounded_sampling_rate": 10},
        {"project_version_id": "00000000-0000-4000-8000-00000000000f"},
    ],
)
def test_other_modes_do_not_acquire_the_short_text_seed(kwargs):
    builder = subject(**kwargs)
    assert builder._public_short_text_candidate_seed_plan() is None
    assert not builder._uses_short_text_candidate_seed()


def test_legacy_builder_is_unchanged():
    builder = subject(cls=TraceListQueryBuilder)
    assert builder._public_scalar_candidate_seed_plan() is None


def test_windowed_fallback_stays_numeric_only():
    """The selector offers it only for an optional seed, and text is required."""

    builder = picker_leaves()
    assert not builder.supports_filter_windowed_candidate_seed_page()
    with pytest.raises(ValueError, match="windowed scalar candidate seed"):
        builder.build_filter_windowed_candidate_seed_page(
            slice_start=END - timedelta(hours=1), slice_end=END, limit=200
        )


def test_short_text_declares_a_row_budget_and_the_other_lanes_do_not():
    """The row budget is the short lane's own, and its floor is single-sourced.

    Floor, initial width and unsignalled cap are all the four hours of the
    fixed ceiling this budget replaced, so the lane can only ever widen away
    from the previous behaviour, never narrow below it. The floor is
    provisional pending the owner decision on bounding the child witness.
    """

    policy = picker_leaves(2).filter_seed_width_policy()
    assert policy is not None
    assert policy.initial_width == timedelta(hours=4)
    assert policy.min_width == timedelta(hours=4)
    assert policy.unsignalled_cap == timedelta(hours=4)
    assert (
        policy.target_read_rows == settings.FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS
    )
    assert subject(value=LONG_TEXT).filter_seed_width_policy() is None
    assert (
        subject(
            extra_leaves=[
                _attribute_filter(
                    "duration_s", 0.01, filter_type="number", operation="greater_than"
                )
            ]
        ).filter_seed_width_policy()
        is None
    )


@pytest.mark.parametrize(
    "window,expected",
    [
        (timedelta(days=7), timedelta(hours=4)),
        (timedelta(hours=2), timedelta(hours=2)),
    ],
)
def test_declared_initial_width_is_the_policy_floor_clamped_to_the_request(
    window, expected
):
    """One constant, and a request narrower than it is still a legal request.

    The candidate-witness contract declines any window of an hour or less, but
    a two-hour window reaches this lane and is narrower than the four-hour
    floor, so the hook clamps rather than handing the selector a width it would
    reject as exceeding the bounded contract.
    """

    builder = picker_leaves(2, window=window)
    policy = builder.filter_seed_width_policy()
    assert builder.recommended_filter_initial_slice_width() == expected
    assert min(window, policy.initial_width) == expected
    # The lane no longer narrows the shared maximum; the budget does that.
    assert builder.recommended_filter_max_slice_width() == window


def test_a_window_inside_the_shared_witness_floor_never_reaches_this_lane():
    """A window this short never reaches the lane, so it declares no policy.

    The candidate-witness contract declines every request window of an hour or
    less. Windows between an hour and the four-hour floor do reach the lane and
    are handled by the clamp above, not here.
    """

    builder = picker_leaves(2, window=timedelta(minutes=20))
    assert builder._public_short_text_candidate_seed_plan() is None
    assert not builder._uses_short_text_candidate_seed()
    assert builder.filter_seed_width_policy() is None
    assert builder.recommended_filter_initial_slice_width() is None


@override_settings(FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS=100_000)
def test_operators_can_lower_the_row_budget():
    assert picker_leaves().filter_seed_width_policy().target_read_rows == 100_000


REQUEST_WIDTH = timedelta(days=7)
# A synthetic policy, not the lane's: a one-hour floor keeps the halving walk
# and the sub-floor rule visible in one fixture. The trace-list lane's own
# floor is four hours and is asserted separately above.
BUDGET = FilterSeedWidthPolicy(
    initial_width=timedelta(hours=1),
    min_width=timedelta(hours=1),
    target_read_rows=2_000_000,
)


def _walk(read_rows, *, statements: int, width=BUDGET.initial_width):
    widths = [width]
    for _ in range(statements - 1):
        width = BUDGET.next_width(width, read_rows, request_width=REQUEST_WIDTH)
        widths.append(width)
    return widths


def test_a_sparse_tail_doubles_to_the_request_width():
    assert _walk(5_000, statements=9) == [
        timedelta(hours=hours) for hours in (1, 2, 4, 8, 16, 32, 64, 128, 168)
    ]


def test_a_dense_slice_halves_to_the_floor_and_stays_there():
    """Only a width grown on sparse history has anywhere to fall back to.

    A slice that doubled across near-empty history and then ran into data walks
    back down to the declared floor and holds there; a read that opens at the
    floor never narrows at all.
    """

    assert _walk(3_600_000, statements=5, width=timedelta(hours=8)) == [
        timedelta(hours=hours) for hours in (8, 4, 2, 1, 1)
    ]
    assert _walk(3_600_000, statements=3) == [timedelta(hours=1)] * 3


@pytest.mark.parametrize(
    "width,request_width,expected",
    [
        # A cursor keyset resumes at the microsecond after the published row.
        (timedelta(microseconds=2), REQUEST_WIDTH, timedelta(microseconds=2)),
        # ``retry_wide_read_budget`` resets to the selector's five minutes.
        (timedelta(minutes=5), REQUEST_WIDTH, timedelta(minutes=5)),
        # A root-time-discovery hit pins the single hour it proved.
        (timedelta(minutes=59), REQUEST_WIDTH, timedelta(minutes=59)),
        # Sub-floor and wider than what is left of the request.
        (timedelta(minutes=30), timedelta(minutes=20), timedelta(minutes=20)),
    ],
)
def test_an_over_budget_slice_below_the_floor_is_never_widened_to_it(
    width, request_width, expected
):
    """Halving narrows or holds; it must never be a way to widen.

    Every width below the floor was chosen by a mechanism that proved that
    exact interval necessary - a keyset resume, the read-budget retry reset, or
    the hour a root-time-discovery hit pinned. An expensive statement is not a
    reason to hand any of them back a wider slice, so the branch returns the
    width it was given, clamped to what is left of the request.
    """

    assert BUDGET.next_width(width, 52_000_000, request_width=request_width) == expected


def test_an_unmeasured_statement_may_widen_only_to_the_unsignalled_cap():
    assert _walk(None, statements=5) == [
        timedelta(hours=hours) for hours in (1, 2, 4, 4, 4)
    ]


@pytest.mark.parametrize(
    "read_rows,expected",
    [
        (0, timedelta(hours=4)),
        (499_999, timedelta(hours=4)),
        (500_000, timedelta(hours=2)),
        (2_000_000, timedelta(hours=2)),
        (2_000_001, timedelta(hours=1)),
    ],
)
def test_the_budget_boundaries_are_a_quarter_of_the_target_and_the_target(
    read_rows, expected
):
    """Below target/4 widen, above target narrow, and hold still in between."""

    assert (
        BUDGET.next_width(timedelta(hours=2), read_rows, request_width=REQUEST_WIDTH)
        == expected
    )


def test_widths_at_or_above_an_hour_stay_whole_hour_powers_of_two():
    # An off-lattice proposal (the numbered lane's remaining/attempts inflation)
    # rounds up, because it is a lower bound on the coverage that lane needs.
    assert BUDGET.unsignalled_width(timedelta(hours=2, minutes=30)) == timedelta(
        hours=4
    )
    assert BUDGET.unsignalled_width(timedelta(hours=21)) == timedelta(hours=4)
    assert BUDGET.unsignalled_width(timedelta(minutes=30)) == timedelta(minutes=30)
    # A carried width may shrink but never widen.
    assert BUDGET.carried_width(timedelta(days=1, hours=8)) == timedelta(hours=4)
    assert BUDGET.carried_width(timedelta(hours=2)) == timedelta(hours=2)


@pytest.mark.parametrize(
    "widths",
    [
        [timedelta(hours=1), timedelta(hours=8)],
        [timedelta(hours=2), timedelta(hours=1)],
    ],
)
def test_the_budget_rejects_an_unordered_or_empty_declaration(widths):
    minimum, initial = widths
    with pytest.raises(ValueError, match="positive and ordered"):
        FilterSeedWidthPolicy(
            initial_width=initial, target_read_rows=1, min_width=minimum
        )
    with pytest.raises(ValueError, match="positive row target"):
        FilterSeedWidthPolicy(
            initial_width=timedelta(hours=1),
            min_width=timedelta(hours=1),
            target_read_rows=0,
        )


@dataclass
class _RowBudgetFakeBuilder(_FakeBuilder):
    """The minimum builder shape the selector needs to apply a row budget."""

    @staticmethod
    def recommended_filter_initial_slice_width() -> timedelta:
        return BUDGET.initial_width

    @staticmethod
    def filter_seed_width_policy() -> FilterSeedWidthPolicy:
        return BUDGET


class _RowBudgetFakeExecutor(_FakeExecutor):
    """Report one statement's native read rows the way the transport does."""

    def __init__(self, builder, *, seed_read_rows: Callable[[int], int | None] | int):
        super().__init__(builder)
        self._seed_read_rows = seed_read_rows
        self.seed_intervals: list[tuple[datetime, datetime]] = []
        self.seed_keysets: list[datetime | None] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        result = super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )
        if query != "seed":
            return result
        self.seed_intervals.append((params["slice_start"], params["slice_end"]))
        self.seed_keysets.append(params["before_start_time"])
        read_rows = self._seed_read_rows
        if callable(read_rows):
            read_rows = read_rows(len(self.seed_intervals))
        return replace(result, read_rows=read_rows)

    @property
    def seed_widths(self) -> list[timedelta]:
        return [end - start for start, end in self.seed_intervals]


def _read(executor, builder, *, window=timedelta(days=7), **kwargs):
    return read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(END - window, END)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=8_000,
        **kwargs,
    )


def _budget_read(*, window, seed_read_rows, **kwargs):
    builder = _RowBudgetFakeBuilder([], start=END - window, end=END)
    executor = _RowBudgetFakeExecutor(builder, seed_read_rows=seed_read_rows)
    page = _read(executor, builder, window=window, **kwargs)
    return executor, page


def _is_contiguous(intervals: list[tuple[datetime, datetime]]) -> bool:
    return all(
        newer_start == older_end
        for (newer_start, _), (_, older_end) in zip(
            intervals, intervals[1:], strict=False
        )
    )


@pytest.mark.parametrize("window", [timedelta(days=7), timedelta(days=30)])
def test_a_sparse_numbered_window_completes_inside_the_seed_attempt_budget(window):
    """Selector-generic coverage, not the lane's own schedule.

    ``_RowBudgetFakeBuilder`` declares no ``recommended_filter_max_slice_width``
    so the selector keeps its 2-day default maximum, which bounds the budget's
    widening here exactly as it bounds the ordinary doubling rule; the
    trace-list lane never meets that default because its own maximum is the
    request width. A year-long window is therefore out of reach for this fake
    at twenty-four statements, and is covered against the real builder instead.
    What this pins is that the budget reaches the scheduler at all and that its
    widening keeps a numbered read inside the attempt budget. The lane's real
    R2 regression is
    ``test_the_real_lane_crosses_a_sparse_window_inside_the_seed_budget``.
    """

    executor, page = _budget_read(
        window=window, seed_read_rows=5_000, max_seed_attempts=24
    )

    assert page.complete is True
    assert page.error_code is None
    assert page.rows == []
    assert page.has_more is False
    assert len(executor.seed_intervals) <= 24
    assert executor.seed_widths[0] <= BUDGET.unsignalled_cap
    assert executor.seed_intervals[0][1] == END
    assert executor.seed_intervals[-1][0] == END - window
    assert _is_contiguous(executor.seed_intervals)
    # The recorded attempts carry the server-side work, not the result size.
    seed_attempts = [attempt for attempt in page.attempts if attempt.kind == "seed"]
    assert len(seed_attempts) == len(executor.seed_intervals)
    assert all(attempt.read_rows == 5_000 for attempt in seed_attempts)
    assert all(attempt.rows_returned == 0 for attempt in seed_attempts)


def test_an_unmeasured_first_statement_caps_the_numbered_lane_inflation():
    """Selector-generic coverage of the inflation branch, not lane behaviour.

    A numbered read whose builder caps slices below the request width inflates
    its first slice so the whole window is scheduled inside this request's
    attempt count. That width is an estimate made before anything has been
    measured, so the unsignalled cap holds it down and the budget governs every
    statement after it. The trace-list lane cannot reach this branch: its
    maximum slice IS the request width, so scheduled coverage always already
    meets the remaining window (``_FakeBuilder``'s implicit 2-day maximum is
    what makes the branch reachable here).
    """

    executor, _page = _budget_read(
        window=timedelta(days=365), seed_read_rows=5_000, max_seed_attempts=24
    )

    assert executor.seed_widths[:3] == [
        timedelta(hours=4),
        timedelta(hours=8),
        timedelta(hours=16),
    ]


def test_a_dense_seed_statement_holds_the_following_slices_at_the_floor():
    """Selector-generic coverage: the budget reaches the scheduler at all.

    The builder here is ``_FakeBuilder``-shaped, so the lane behaviour it
    exercises is the selector's, not the trace-list lane's (see
    ``_RowBudgetFakeBuilder``). The real lane's dense schedule is pinned by
    ``test_the_real_lane_holds_dense_slices_at_the_floor``.
    """

    executor, page = _budget_read(
        window=timedelta(days=7),
        seed_read_rows=3_600_000,
        max_seed_attempts=6,
        include_incomplete_rows=True,
        bounded_continuation=True,
        carry_continuation_slice_width=True,
    )

    assert executor.seed_widths == [timedelta(hours=1)] * 6
    assert _is_contiguous(executor.seed_intervals)
    # A dense project does not lose its place: the scan checkpoint is the
    # oldest boundary these narrowed statements actually reached.
    assert page.complete is False
    assert page.continuation_slice_end == executor.seed_intervals[-1][0]
    assert page.continuation_before_start_time is None


def test_a_carried_slice_without_a_keyset_is_only_a_width_hint():
    """A cursor signed before the budget may carry a slice many statements wide.

    Shrinking it cannot skip rows: the uncovered older part of the carried
    slice is exactly the next contiguous slice's work.
    """

    executor, _page = _budget_read(
        window=timedelta(days=7),
        seed_read_rows=5_000,
        max_seed_attempts=3,
        include_incomplete_rows=True,
        bounded_continuation=True,
        carry_continuation_slice_width=True,
        cursor_start_time=END,
        cursor_order_token="\U0010ffff",
        continuation_slice_start=END - timedelta(hours=32),
        continuation_slice_end=END,
    )

    assert executor.seed_intervals[0] == (END - timedelta(hours=4), END)
    assert executor.seed_intervals[1][1] == END - timedelta(hours=4)
    assert _is_contiguous(executor.seed_intervals)
    assert executor.seed_keysets == [None, None, None]


def test_a_carried_slice_that_owns_a_keyset_is_honoured_verbatim():
    """The keyset is proven inside that exact interval, so it cannot be narrowed.

    A cursor signed before the budget shipped therefore keeps replaying one wide
    slice until it expires; that transient is bounded by the signed cursor's own
    maximum age, and rejecting it with a CURSOR_VERSION bump would be worse.
    """

    executor, _page = _budget_read(
        window=timedelta(days=7),
        seed_read_rows=5_000,
        max_seed_attempts=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
        carry_continuation_slice_width=True,
        cursor_start_time=END,
        cursor_order_token="\U0010ffff",
        continuation_slice_start=END - timedelta(hours=32),
        continuation_slice_end=END,
        continuation_before_start_time=END - timedelta(hours=1),
        continuation_before_id="previous",
    )

    assert executor.seed_intervals[0] == (END - timedelta(hours=32), END)
    assert executor.seed_keysets[0] == END - timedelta(hours=1)


@dataclass
class _CappedRowBudgetFakeBuilder(_RowBudgetFakeBuilder):
    """A lane that declares both a row budget and its own maximum slice."""

    @staticmethod
    def recommended_filter_max_slice_width() -> timedelta:
        return timedelta(days=3)


def test_a_declared_maximum_slice_still_binds_a_row_budgeted_lane():
    """The budget replaces the doubling rule, not the builder's own maximum.

    A declared maximum only ever raises the selector's two-day default (that
    is what ``max(...)`` at the top of the read does), so a maximum below two
    days cannot bind for any builder and this one declares three. Without the
    clamp the eighth slice would be 128 h; with it the schedule flattens at the
    declared 72 h. The trace-list lane is unaffected - its declared maximum is
    the request width, which ``next_width`` already respects.
    """

    builder = _CappedRowBudgetFakeBuilder([], start=END - timedelta(days=30), end=END)
    executor = _RowBudgetFakeExecutor(builder, seed_read_rows=5_000)

    _read(executor, builder, window=timedelta(days=30), max_seed_attempts=16)

    assert executor.seed_widths[:9] == [
        timedelta(hours=hours) for hours in (1, 2, 4, 8, 16, 32, 64, 72, 72)
    ]
    assert max(executor.seed_widths) == timedelta(days=3)
    assert _is_contiguous(executor.seed_intervals)


def test_a_lane_without_a_declared_budget_keeps_its_doubling_schedule():
    builder = _FakeBuilder([], start=END - timedelta(days=7), end=END)
    executor = _RowBudgetFakeExecutor(builder, seed_read_rows=3_600_000)

    _read(
        executor,
        builder,
        max_seed_attempts=4,
        include_incomplete_rows=True,
        bounded_continuation=True,
        carry_continuation_slice_width=True,
    )

    assert executor.seed_widths == [
        timedelta(minutes=5),
        timedelta(minutes=10),
        timedelta(minutes=20),
        timedelta(minutes=40),
    ]


class _LaneTransport:
    """The production transport shape, recording only this lane's seed slices.

    ``supports_bounded_speculative_reads`` is False because that is what
    ``AnalyticsQueryService`` reports in production, so no anchor, witness
    prefilter or micro-seed probe is offered and every statement below is the
    seed itself. Seed statements are recognised by the CTE they carry rather
    than by call order, because a discovery probe may interleave.
    """

    supports_bounded_speculative_reads = False

    def __init__(self, read_rows: int | None):
        self._read_rows = read_rows
        self.seed_intervals: list[tuple[datetime, datetime]] = []
        self.other_statements = 0

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if (
            "matching_scalar_trace_identities" in query
            and "filter_seed_limit" in params
        ):
            self.seed_intervals.append(
                (params["filter_slice_start"], params["filter_slice_end"])
            )
        else:
            self.other_statements += 1
        return QueryResult(
            data=[],
            row_count=0,
            backend_used="clickhouse",
            query_time_ms=1.0,
            read_rows=self._read_rows,
        )

    @property
    def seed_widths(self) -> list[timedelta]:
        return [end - start for start, end in self.seed_intervals]


def _lane_read(
    *,
    window: timedelta,
    read_rows: int | None,
    cursor: bool,
    max_seed_attempts: int = 24,
):
    """Drive the real ``TraceListQueryBuilderV2`` short-text lane end to end.

    The kwargs mirror ``views/trace.py``: ``cursor`` selects the grid's own
    resumable lane, and ``not cursor`` the legacy numbered lane that has no
    cursor to resume from and therefore fails closed. Root-time discovery is
    the one deviation - the view enables it for this shape, and it is off here
    so that the recorded intervals are the seed schedule alone.
    """

    builder = picker_leaves(2, window=window)
    assert builder.filter_seed_width_policy() is not None
    transport = _LaneTransport(read_rows)
    page = read_bounded_filter_page(
        builder=builder,
        analytics=transport,
        filters=builder.filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=9_500,
        query_timeout_ms=2_500,
        max_query_count=64,
        max_seed_attempts=max_seed_attempts,
        include_incomplete_rows=cursor,
        bounded_continuation=cursor,
        carry_continuation_slice_width=cursor,
        root_time_discovery=False,
    )
    return transport, page


@pytest.mark.parametrize(
    "window,statements",
    [(timedelta(days=7), 6), (timedelta(days=30), 8), (timedelta(days=365), 12)],
)
@pytest.mark.parametrize("cursor", [False, True])
def test_the_real_lane_crosses_a_sparse_window_inside_the_seed_budget(
    window, statements, cursor
):
    """R2, on the builder the view actually constructs.

    A fixed four-hour ceiling covered at most twenty-four times four hours per
    request. The numbered lane carries no cursor to resume from, so every
    window longer than ninety-six hours became a deterministic 503; the cursor
    lane merely needed a continuation per ninety-six hours. A statement that
    reads almost nothing now doubles the next one, so a sparse window of any
    length is crossed in a logarithmic number of statements.
    """

    transport, page = _lane_read(window=window, read_rows=5_000, cursor=cursor)

    assert page.complete is True
    assert page.error_code is None
    assert page.rows == []
    assert len(transport.seed_intervals) == statements <= 24
    # Newest-first, contiguous, and the whole window is covered exactly once.
    assert transport.seed_intervals[0][1] == END
    assert transport.seed_intervals[-1][0] == END - window
    assert _is_contiguous(transport.seed_intervals)
    assert transport.seed_widths[0] == timedelta(hours=4)
    # Strictly monotone until the oldest slice is truncated at the request.
    assert transport.seed_widths[:-1] == sorted(transport.seed_widths[:-1])
    assert transport.seed_widths[-2] > timedelta(hours=4)


def test_the_real_lane_holds_dense_slices_at_the_floor():
    """Where the results are, the budget must not buy less than the old ceiling.

    Read rows on a dense slice are dominated by a flat term this seed's
    time-unbounded child witness pays whatever the slice's width, so every
    width overruns the budget. The floor is therefore the four hours of the
    fixed ceiling the budget replaced: a read that opens dense stays there and
    covers exactly what that ceiling covered, never a fraction of it.
    """

    transport, page = _lane_read(
        window=timedelta(days=7),
        read_rows=52_000_000,
        cursor=True,
        max_seed_attempts=6,
    )

    assert transport.seed_widths == [timedelta(hours=4)] * 6
    assert _is_contiguous(transport.seed_intervals)
    # A dense project does not lose its place: the published scan checkpoint is
    # the oldest boundary these statements actually reached.
    assert page.complete is False
    assert page.continuation_slice_end == transport.seed_intervals[-1][0]
    assert page.continuation_slice_end == END - timedelta(hours=24)


def test_an_unmeasured_real_lane_transport_reproduces_the_ninety_six_hour_cap():
    """The negative control: the budget is only as good as the progress it reads.

    A transport that reports no read rows leaves every statement on the
    unsignalled cap, which is exactly the fixed four-hour ceiling this change
    replaced - twenty-four statements, ninety-six hours, and a numbered read
    that fails closed above that. Production fills these counters natively;
    this test exists so a silent transport regression cannot pass for the fix,
    and it pins the floor of the change: an unmeasured lane is no worse than
    what shipped, never better.
    """

    transport, page = _lane_read(window=timedelta(days=7), read_rows=None, cursor=False)

    assert page.complete is False
    assert page.error_code == "scan_budget_exceeded"
    assert len(transport.seed_intervals) == 24
    assert transport.seed_widths == [timedelta(hours=4)] * 24
    assert END - transport.seed_intervals[-1][0] == timedelta(hours=96)


_EPOCH = datetime(1970, 1, 1)
# The cost model the policy hook documents, used here to make read rows a
# function of the slice's contents rather than a constant: this seed's child
# witness is time-unbounded, so its read rows are dominated by a trace-id bloom
# false-positive scan over ~107M retained rows whose pass rate is modelled as
# ``1 - 0.999 ** roots``. One root reads ~107k (under a quarter of the default
# budget, so the next slice doubles); twenty read ~2.1M (over it, so it halves).
_RETAINED_ROWS = 107_000_000


def _bloom_read_rows(roots: int) -> int:
    return int(_RETAINED_ROWS * (1 - 0.999**roots))


def _root_population(clusters: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """``n`` distinct roots at each of the given whole hours before ``END``."""

    population = []
    for hours_back, count in clusters:
        base = END - timedelta(hours=hours_back)
        for index in range(count):
            start_time = base + timedelta(microseconds=index)
            trace_id = f"tr-{int((start_time - _EPOCH).total_seconds() * 1e6)}"
            population.append(
                {
                    "trace_id": trace_id,
                    "root_span_id": f"sp-{trace_id}",
                    "start_time": start_time,
                    "project_id": str(PROJECT),
                    "trace_name": "n",
                    "span_name": "n",
                    "observation_type": "trace",
                    "status": "OK",
                    "end_time": start_time,
                    "latency_ms": 1,
                    "cost": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "model": "m",
                    "provider": "p",
                    "is_deleted": 0,
                    "_root_start_hour": start_time.replace(
                        minute=0, second=0, microsecond=0
                    ),
                    "_root_observation_type": "trace",
                    "_root_service_name": "svc",
                    "_root_version": 1,
                }
            )
    return population


# A sparse newest tail — one root every six hours — over a dense older region.
_SPARSE_TAIL = [(hours, 1) for hours in range(1, 50, 6)]
_DENSE_REGION = [(hours, 20) for hours in range(60, 100, 4)]


class _PopulationLaneTransport(_LaneTransport):
    """The production transport shape, serving a synthetic root population.

    Seeds, the root-time-discovery probe, the latest-state classifier and page
    hydration are all answered from the same population, so what the selector
    publishes can be compared with ground truth. Read rows are a function of
    the roots inside the slice, never a constant: a constant would let a
    sub-floor width report itself as over budget forever and would not exercise
    the widen-then-halve walk this lane exists for.
    """

    def __init__(self, population: list[dict[str, Any]]):
        super().__init__(read_rows=None)
        self.population = population
        self.probes: list[tuple[datetime, datetime, bool]] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if (
            "matching_scalar_trace_identities" in query
            and "filter_seed_limit" in params
        ):
            slice_start = params["filter_slice_start"]
            slice_end = params["filter_slice_end"]
            rows = [
                row
                for row in self.population
                if slice_start <= row["start_time"] < slice_end
            ]
            rows.sort(
                key=lambda row: (row["start_time"], row["trace_id"]), reverse=True
            )
            before_start_time = params.get("filter_before_start_time")
            if before_start_time is not None:
                before_id = params.get("filter_before_id")
                rows = [
                    row
                    for row in rows
                    if (row["start_time"], row["trace_id"])
                    < (before_start_time, before_id)
                ]
            self.seed_intervals.append((slice_start, slice_end))
            limited = [dict(row) for row in rows[: params["filter_seed_limit"]]]
            return QueryResult(
                data=limited,
                row_count=len(limited),
                backend_used="clickhouse",
                query_time_ms=1.0,
                read_rows=_bloom_read_rows(len(rows)),
            )
        if "root_discovery_start_us" in params:
            start_us = params["root_discovery_start_us"]
            end_us = params["root_discovery_end_us"]
            inside = [
                row["start_time"]
                for row in self.population
                if start_us
                <= int((row["start_time"] - _EPOCH).total_seconds() * 1e6)
                < end_us
            ]
            newest = (
                int((max(inside) - _EPOCH).total_seconds() * 1e6) if inside else None
            )
            self.probes.append(
                (
                    _EPOCH + timedelta(microseconds=start_us),
                    _EPOCH + timedelta(microseconds=end_us),
                    newest is not None,
                )
            )
            return QueryResult(
                data=[{"newest_raw_root_us": newest}],
                row_count=1,
                backend_used="clickhouse",
                query_time_ms=1.0,
                read_rows=10,
            )
        for key in ("candidate_trace_ids", "page_hydration_trace_ids"):
            if key in params:
                wanted = set(params[key])
                rows = [
                    dict(row) for row in self.population if row["trace_id"] in wanted
                ]
                rows.sort(
                    key=lambda row: (row["start_time"], row["trace_id"]), reverse=True
                )
                return QueryResult(
                    data=rows,
                    row_count=len(rows),
                    backend_used="clickhouse",
                    query_time_ms=1.0,
                    read_rows=1_000,
                )
        self.other_statements += 1
        return QueryResult(
            data=[],
            row_count=0,
            backend_used="clickhouse",
            query_time_ms=1.0,
            read_rows=10,
        )


def _picker_lane_read(
    population: list[dict[str, Any]],
    *,
    window: timedelta = timedelta(days=7),
    page_size: int = 500,
    max_seed_attempts: int = 24,
    continuation: dict[str, Any] | None = None,
    transport_factory: Callable[[], _PopulationLaneTransport] | None = None,
):
    """The grid's own call: one attribute leaf, cursor lane, discovery enabled.

    ``root_time_discovery=True`` is the kwarg ``views/trace.py`` passes on a
    fresh page-0 cursor, and a single leaf is the only shape that can turn it
    on (``supports_filter_empty_seed_root_time_discovery`` requires exactly
    one). That path can skip a proven-empty interval and pins a single hour
    after a hit, so it is the shape that most nearly interacts with the width
    budget - and therefore the one worth driving end to end.
    """

    builder = picker_leaves(1, window=window)
    assert builder.supports_filter_empty_seed_root_time_discovery() is True
    transport = (
        _PopulationLaneTransport(population)
        if transport_factory is None
        else transport_factory()
    )
    page = read_bounded_filter_page(
        builder=builder,
        analytics=transport,
        filters=builder.filters,
        key_field="trace_id",
        page_number=0,
        page_size=page_size,
        deadline_ms=9_500,
        query_timeout_ms=2_500,
        max_query_count=64,
        max_seed_attempts=max_seed_attempts,
        include_incomplete_rows=True,
        bounded_continuation=True,
        carry_continuation_slice_width=True,
        root_time_discovery=True,
        **(continuation or {}),
    )
    return transport, page


def test_the_production_picker_filter_widens_on_a_sparse_tail_and_holds_at_four_hours():
    """The whole point of the budget, on the builder and kwargs the view sends.

    One root every six hours reads far under a quarter of the budget, so the
    seed doubles away from its four-hour opening width and crosses two days of
    near-empty history in four statements - the regime a fixed four-hour
    ceiling walked one slice at a time. The first slice that reaches twenty
    roots overruns the budget, and the schedule walks straight back down to the
    floor and stays there: four hours, which is exactly what the fixed ceiling
    gave, never a fraction of it.
    """

    transport, page = _picker_lane_read(_root_population(_SPARSE_TAIL + _DENSE_REGION))

    hours = [width.total_seconds() / 3600 for width in transport.seed_widths]
    assert hours == [4, 8, 16, 32, 16, 8, 4, 4, 4, 4]
    assert _is_contiguous(transport.seed_intervals)
    assert page.complete is True
    assert page.error_code is None
    # Widening is what crossed the tail; the floor is what held under it.
    assert max(transport.seed_widths) == timedelta(hours=32)
    assert min(transport.seed_widths) == timedelta(hours=4)
    # Only the region the seeds never reached was proven empty by discovery.
    assert [hit for _, _, hit in transport.probes] == [False, False, False]


# A value absent from the newest hours and dense once found. This is the shape
# the grid sends whenever a picker value stopped being written a day or two ago
# - exactly the request root-time discovery exists for - and it is the one that
# reaches the discovery reset while a row budget is active.
_ABSENT_THEN_DENSE = {
    # The probe covering the newest day hits immediately.
    "hit_on_the_first_probe": [(hours, 10) for hours in range(5, 35)],
    # One empty probe first, then a hit: the task's own thirty-hour case.
    "hit_after_one_empty_probe": [(hours, 10) for hours in range(30, 60)],
    # Three empty probes, so discovery gives up before the data starts and the
    # reset width alone - not the pinned hit hour - decides the next slice.
    "hit_after_discovery_gives_up": [(hours, 10) for hours in range(80, 110)],
}


@pytest.mark.parametrize(
    "clusters", _ABSENT_THEN_DENSE.values(), ids=_ABSENT_THEN_DENSE
)
def test_root_time_discovery_never_pins_this_lane_below_its_floor(clusters):
    """Discovery must not hand the budget a window the budget cannot leave.

    Discovery narrows the following slice so a proven hit lands somewhere cheap
    to read, and a wall-clock lane narrows it to the hour its primary-key prefix
    prunes on. For a row-budgeted lane that hour is *below* the floor, and the
    budget deliberately leaves a sub-floor width alone rather than widening it,
    so the one-hour reset would become permanent the moment the data underneath
    is dense: every slice overruns the budget, the halving branch returns the
    same hour, and the lane crawls an hour at a time for the rest of the request.

    Measured on the thirty-hour case before the reset was clamped to the floor:
    the lane covered 51 h of a seven-day window in 23 statements and returned
    ``scan_budget_exceeded`` with 220 of 300 roots published, against the 96 h
    the fixed four-hour ceiling gave for the same 24 statements. The reset is
    now the floor, so every post-discovery slice is at least the ceiling this
    budget replaced, and that case now crosses the whole window in fourteen.
    """

    transport, page = _picker_lane_read(_root_population(clusters))

    covered = END - transport.seed_intervals[-1][0]
    assert page.complete is True
    assert page.error_code is None
    assert len(transport.seed_intervals) <= 24
    # Coverage is what the defect cost: at least the fixed ceiling's 24 x 4 h,
    # and here the whole request window.
    assert covered >= timedelta(hours=96)
    assert covered == timedelta(days=7)
    # Seeds still walk strictly newest-first and never re-read an interval. The
    # only gaps are the ones a probe proved empty, which is what discovery is
    # for; that nothing real was skipped is what the published count below says.
    assert all(
        later[1] <= earlier[0]
        for earlier, later in zip(
            transport.seed_intervals, transport.seed_intervals[1:], strict=False
        )
    )
    # The discriminating assertion. Not one slice is narrower than the floor,
    # before or after the probe that ended the empty tail.
    assert min(transport.seed_widths) == timedelta(hours=4)
    # The empty newest hours are what makes this shape reachable at all: the
    # seed must have come back empty for discovery to probe.
    assert transport.probes
    # Nothing was traded away for the coverage: every root still publishes.
    assert len(page.rows) == sum(count for _, count in clusters)


def _picker_lane_hop_chain(
    population: list[dict[str, Any]],
    *,
    page_size: int,
    max_seed_attempts: int,
    max_hops: int = 40,
    transport_factory: Callable[[], _PopulationLaneTransport] | None = None,
) -> list[str]:
    """Walk the cursor exactly as ``views/trace.py`` re-signs and resumes it.

    A fresh transport per hop, because every hop is its own HTTP request;
    ``transport_factory`` lets a variant transport answer the same chain.
    """

    cursor: dict[str, Any] | None = None
    published: list[str] = []
    for _ in range(max_hops):
        transport, page = _picker_lane_read(
            population,
            page_size=page_size,
            max_seed_attempts=max_seed_attempts,
            transport_factory=transport_factory,
            continuation={
                "cursor_start_time": cursor["order"][0] if cursor else None,
                "cursor_order_token": cursor["order"][1] if cursor else None,
                "continuation_slice_start": cursor["slice_start"] if cursor else None,
                "continuation_slice_end": cursor["slice_end"] if cursor else None,
                "continuation_before_start_time": (
                    cursor["before_time"] if cursor else None
                ),
                "continuation_before_id": cursor["before_id"] if cursor else None,
            },
        )
        assert transport.other_statements == 0
        published.extend(str(row.get("trace_id")) for row in page.rows)
        emits_cursor = (page.complete and page.has_more) or (
            not page.complete
            and (page.has_more or page.continuation_slice_end is not None)
        )
        if not emits_cursor:
            return published
        if page.rows:
            order = (
                page.rows[-1].get("start_time"),
                str(page.rows[-1].get("trace_id", "")),
            )
        elif cursor is not None:
            order = cursor["order"]
        else:
            checkpoint = (
                page.continuation_before_start_time or page.continuation_slice_end
            )
            assert checkpoint is not None
            order = (
                checkpoint,
                (
                    str(page.continuation_before_id)
                    if page.continuation_before_id is not None
                    else "\U0010ffff"
                ),
            )
        # A page that still has rows behind its own keyset carries no scan
        # slice, exactly as the view drops those fields when ``has_more``.
        cursor = {
            "order": order,
            "slice_start": None if page.has_more else page.continuation_slice_start,
            "slice_end": None if page.has_more else page.continuation_slice_end,
            "before_time": (
                None if page.has_more else page.continuation_before_start_time
            ),
            "before_id": None if page.has_more else page.continuation_before_id,
        }
    raise AssertionError("cursor chain did not terminate")


@pytest.mark.parametrize(
    "page_size,max_seed_attempts",
    [(25, 6), (10, 24), (50, 4)],
    ids=["grid_default", "small_pages", "tight_attempt_budget"],
)
def test_the_production_picker_filter_publishes_every_root_exactly_once(
    page_size, max_seed_attempts
):
    """Exactness is the property the width schedule is not allowed to cost.

    Resizing a slice moves only where acquisition stops; a narrower slice
    defers older history to the next adjacent one rather than skipping it, and
    a wider one cannot re-publish what the cursor's keyset already excluded. So
    however the widths fall out - and across these three page sizes they fall
    out differently, including a hop that exhausts its attempt budget mid-page
    - the chain must publish the ground-truth set once each, newest first.
    """

    population = _root_population(_SPARSE_TAIL + _DENSE_REGION)
    ground_truth = [
        row["trace_id"]
        for row in sorted(
            population,
            key=lambda row: (row["start_time"], row["trace_id"]),
            reverse=True,
        )
    ]

    published = _picker_lane_hop_chain(
        population, page_size=page_size, max_seed_attempts=max_seed_attempts
    )

    assert published == ground_truth
    assert len(published) == len(set(published)) == len(population)


@pytest.mark.parametrize(
    "clusters", _ABSENT_THEN_DENSE.values(), ids=_ABSENT_THEN_DENSE
)
def test_a_discovery_widened_slice_still_publishes_every_root_exactly_once(clusters):
    """The widened reset slice must survive a page that fills inside it.

    Extending the replayed hit hour down to the floor means the grid's own
    twenty-five-row page can now fill in the middle of that slice, so the
    keyset checkpoint it carries names a lower boundary discovery chose rather
    than one the width schedule did. The next hop must resume below that keyset
    inside the same slice and strand nothing between them.
    """

    population = _root_population(clusters)
    ground_truth = [
        row["trace_id"]
        for row in sorted(
            population,
            key=lambda row: (row["start_time"], row["trace_id"]),
            reverse=True,
        )
    ]

    published = _picker_lane_hop_chain(population, page_size=25, max_seed_attempts=6)

    assert published == ground_truth
    assert len(published) == len(set(published)) == len(population)


# ---------------------------------------------------------------------------
# The bounded-witness switch: FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS
#
# Off (zero, the default) the seed statement is the shipped one, byte for byte.
# On, the witness scan is confined to the hours the statement's own roots can
# occupy plus the slack, which narrows CANDIDACY only: the exact latest-state
# classifier stays unbounded, so a published row is still an exact any-span
# match and the switch can only omit a trace whose sole witness lies outside
# the envelope. That omission is the contract change, and it is pinned below.
# ---------------------------------------------------------------------------

# The seed CTE's PREWHERE exactly as HEAD emitted it before the switch existed,
# for ``picker_leaves(1)`` over one four-hour slice with no keyset. ``~`` marks
# the end of a line that is blank apart from the template's own indentation, so
# neither an editor nor a linter trimming trailing whitespace can weaken the
# pin without the marker going missing.
_HEAD_SEED_CTE_PREWHERE = """
        ~
        WITH matching_scalar_trace_identities AS (
            SELECT DISTINCT trace_id
            FROM spans
            PREWHERE project_id = %(project_id)s
              ~
              ~
              AND trace_id IN (
                  SELECT trace_id FROM spans
                  PREWHERE project_id = %(project_id)s
                      AND start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s)
                      AND start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s)
                  WHERE parent_span_id IS NULL OR parent_span_id = ''
              )
        ~
            """

# The whole of the difference the switch may make to the statement.
_ENVELOPE_SQL = (
    "\n              AND start_time >= "
    "fromUnixTimestamp64Micro(%(filter_witness_start_us)s)"
    "\n              AND start_time < "
    "fromUnixTimestamp64Micro(%(filter_witness_end_us)s)"
)

SLICE = (END - timedelta(hours=4), END)


def _head_seed_cte_prewhere() -> str:
    return _HEAD_SEED_CTE_PREWHERE.replace("~", "")


def _seed(builder=None, *, slack: int | None = None, **kwargs):
    """One seed statement, optionally with the switch on."""

    call = {"slice_start": SLICE[0], "slice_end": SLICE[1], "limit": 200, **kwargs}
    if slack is None:
        return (builder or picker_leaves(1)).build_filter_candidate_seed_page(**call)
    with override_settings(FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS=slack):
        return (builder or picker_leaves(1)).build_filter_candidate_seed_page(**call)


def test_the_seed_statement_is_byte_identical_while_the_switch_is_off():
    """Off is not "a narrower envelope of zero hours"; it is no envelope at all.

    Pinned twice over: against the literal statement HEAD emitted before the
    switch existed, and against the switched-on statement, whose only
    difference may be the two-line envelope and its own parameters.
    """

    assert settings.FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS == 0
    sql, params = _seed()

    assert sql.startswith(_head_seed_cte_prewhere())
    assert _ENVELOPE_SQL not in sql
    assert [name for name in params if name.startswith("filter_witness")] == []

    bounded_sql, bounded_params = _seed(slack=1)
    assert bounded_sql.replace(_ENVELOPE_SQL, "") == sql
    assert {
        name: value
        for name, value in bounded_params.items()
        if not name.startswith("filter_witness")
    } == params


@pytest.mark.parametrize(
    "start_offset,end_offset,expected_start,expected_end",
    [
        (
            timedelta(0),
            timedelta(0),
            END - timedelta(hours=5),
            END + timedelta(hours=1),
        ),
        # A slice that does not start on the hour floors before the slack is
        # applied, so the bound always lands on the pruning granularity.
        (
            timedelta(minutes=17),
            timedelta(0),
            END - timedelta(hours=6),
            END + timedelta(hours=1),
        ),
        # ... and one that does not END on the hour CEILS before the slack, so
        # the upper bound still covers the last partial hour of roots. The
        # slice ends at 22:37; the envelope may not stop at 23:37.
        (
            timedelta(0),
            timedelta(hours=1, minutes=23),
            END - timedelta(hours=5),
            END,
        ),
        # Both ends ragged: the two roundings are independent.
        (
            timedelta(minutes=17),
            timedelta(hours=1, minutes=23),
            END - timedelta(hours=6),
            END,
        ),
    ],
    ids=["hour_aligned_slice", "ragged_start", "ragged_end", "ragged_both_ends"],
)
def test_the_switch_bounds_the_witness_scan_on_hour_aligned_parameters(
    start_offset, end_offset, expected_start, expected_end
):
    slice_start, slice_end = SLICE[0] - start_offset, SLICE[1] - end_offset
    with override_settings(FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS=1):
        sql, params = picker_leaves(1).build_filter_candidate_seed_page(
            slice_start=slice_start, slice_end=slice_end, limit=200
        )
    cte, roots = sql.split("SELECT trace_id, id AS root_span_id", 1)

    # The bound belongs to the witness scan, not to the root population.
    assert _ENVELOPE_SQL in cte
    assert _ENVELOPE_SQL not in roots
    assert params["filter_witness_start"] == expected_start
    assert params["filter_witness_end"] == expected_end
    # The envelope contains the slice it bounds, whichever way the ends round.
    assert params["filter_witness_start"] <= slice_start
    assert params["filter_witness_end"] >= slice_end
    for name in ("filter_witness_start", "filter_witness_end"):
        moment = params[name]
        assert (moment.minute, moment.second, moment.microsecond) == (0, 0, 0)
        # SQL reads microseconds; the datetimes are the orchestration contract.
        assert params[f"{name}_us"] == _unix_microseconds(moment)
        assert f"%({name}_us)s" not in roots

    # Everything else about the CTE is exactly what it was: the root-population
    # subquery on the slice, the membership join, no tombstone or page keyset.
    assert "AND trace_id IN (\n                  SELECT trace_id FROM spans" in cte
    assert (
        "AND start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s)" in cte
    )
    assert "WHERE parent_span_id IS NULL OR parent_span_id = ''" in cte
    assert "AND trace_id IN (" in roots
    assert "SELECT trace_id FROM matching_scalar_trace_identities" in roots
    assert "is_deleted" not in cte
    assert "LIMIT" not in cte
    assert "filter_before" not in cte


def test_a_keyset_continuation_tightens_the_envelope_to_the_cursor_hour():
    """No root above the cursor can be published, so none may widen the scan.

    The continuation resumes strictly below its keyset, so the newest root the
    statement can publish is the cursor's own position rather than the slice's
    end - and the envelope that has to carry its witness shrinks with it.
    """

    cursor = END - timedelta(hours=1, minutes=17)
    sql, params = _seed(slack=1, before_start_time=cursor, before_id="tr-mid")
    page_one_params = _seed(slack=1)[1]

    assert params["filter_witness_end"] == END
    assert params["filter_witness_end"] < page_one_params["filter_witness_end"]
    # The lower bound is the slice's, which the cursor does not move.
    assert params["filter_witness_start"] == page_one_params["filter_witness_start"]
    # The keyset itself stays where it was: outside the witness scan.
    cte, roots = sql.split("SELECT trace_id, id AS root_span_id", 1)
    assert "filter_before_start_us" in roots
    assert "filter_before" not in cte


def test_a_ragged_continuation_ceils_the_cursor_hour_not_the_ragged_slice_end():
    """A mid-hour cursor inside a mid-hour slice rounds on its own boundary.

    Page one of this slice ends at 22:37 and has to carry witnesses up to
    midnight. The continuation resumes below 21:43, so the newest root it can
    publish sits an hour lower - and the ceiling it rounds up to is the
    cursor's hour, not the slice's. Both roundings are therefore pinned on
    values that are not whole hours, which the aligned end could hide.
    """

    ragged_end = SLICE[1] - timedelta(hours=1, minutes=23)  # 22:37
    cursor = SLICE[1] - timedelta(hours=2, minutes=17)  # 21:43
    sql, params = _seed(
        slack=1,
        slice_end=ragged_end,
        before_start_time=cursor,
        before_id="tr-ragged",
    )
    page_one_params = _seed(slack=1, slice_end=ragged_end)[1]

    # ceil_hour(21:43) + 1h = 23:00, an hour below ceil_hour(22:37) + 1h.
    assert params["filter_witness_end"] == END - timedelta(hours=1)
    assert page_one_params["filter_witness_end"] == END
    assert params["filter_witness_end"] < page_one_params["filter_witness_end"]
    # floor_hour(20:00) - 1h = 19:00; the cursor never moves the lower bound.
    assert params["filter_witness_start"] == END - timedelta(hours=5)
    assert params["filter_witness_start"] == page_one_params["filter_witness_start"]
    for name in ("filter_witness_start", "filter_witness_end"):
        moment = params[name]
        assert (moment.minute, moment.second, moment.microsecond) == (0, 0, 0)
        assert params[f"{name}_us"] == _unix_microseconds(moment)
    # The envelope still covers every root the continuation can publish.
    assert params["filter_witness_start"] <= SLICE[0]
    assert params["filter_witness_end"] >= cursor
    cte, roots = sql.split("SELECT trace_id, id AS root_span_id", 1)
    assert _ENVELOPE_SQL in cte
    assert _ENVELOPE_SQL not in roots
    assert "filter_before_start_us" in roots
    assert "filter_before" not in cte


def test_org_scope_never_reaches_this_lane_and_keeps_its_composite_keyset():
    """Org reads are outside the lane by construction, so outside the switch.

    ``_uses_attribute_coordinate_replay`` requires a single project, so an
    org-scoped read has no scalar candidate seed to bound at all. Its ordered
    seed - composite ``(trace_id, project_id)`` keyset and all - must therefore
    come out identical in both modes.
    """

    project_b = "00000000-0000-4000-8000-000000000002"
    leaf = _attribute_filter(ACCOUNT_KEY, ACCOUNT_VALUES, operation="in")
    leaf["filter_config"]["attribute_value_types"] = ["string"] * len(ACCOUNT_VALUES)
    builder = TraceListQueryBuilderV2(
        project_ids=[str(PROJECT), project_b],
        filters=[_time_filter(END - timedelta(days=7), END), leaf],
        page_size=25,
    )
    assert not builder._uses_short_text_candidate_seed()
    assert builder.filter_seed_width_policy() is None

    statements = []
    for slack in (0, 1):
        with override_settings(FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS=slack):
            statements.append(
                builder.build_filter_ordered_seed_page(
                    slice_start=SLICE[0],
                    slice_end=SLICE[1],
                    limit=200,
                    before_start_time=END - timedelta(hours=1),
                    before_id=("tr-mid", str(PROJECT)),
                )
            )

    assert statements[0] == statements[1]
    sql, params = statements[0]
    assert "toString(project_id) < %(filter_before_project_id)s" in sql
    assert "matching_scalar_trace_identities" not in sql
    assert _ENVELOPE_SQL not in sql
    assert [name for name in params if name.startswith("filter_witness")] == []


@pytest.mark.parametrize(
    "make",
    [
        lambda: subject(
            extra_leaves=[
                _attribute_filter(
                    "duration_s", 0.01, filter_type="number", operation="greater_than"
                )
            ]
        ),
        lambda: subject(1, kind="boolean", value=True),
        lambda: subject(value=LONG_TEXT),
    ],
    ids=["numeric", "boolean", "long_text"],
)
def test_the_other_seed_lanes_are_untouched_by_the_switch(make):
    """The switch is the short exact-string lane's alone.

    The numeric and long-text lanes prune raw granules by value and the boolean
    lane is a different plan entirely; none of them pays the trace-id bloom
    scan this envelope exists to bound, so none of them may change.
    """

    unbounded_sql, unbounded_params = _seed(make())
    bounded_sql, bounded_params = _seed(make(), slack=1)

    assert bounded_sql == unbounded_sql
    assert bounded_params == unbounded_params
    assert _ENVELOPE_SQL not in bounded_sql
    assert [name for name in bounded_params if name.startswith("filter_witness")] == []
    with override_settings(FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS=24):
        assert make().filter_seed_width_policy() is None


@pytest.mark.parametrize(
    "slack,floor",
    [
        (0, timedelta(hours=4)),
        (1, timedelta(hours=1)),
        (24, timedelta(hours=1)),
        (168, timedelta(hours=1)),
    ],
)
def test_the_width_floor_follows_the_witness_contract(slack, floor):
    """Two cost shapes, two schedules, one setting choosing between them.

    Unbounded, the flat bloom term does not shrink with the slice, so narrowing
    below the four hours of the ceiling this budget replaced would buy less
    coverage for the same statement. Bounded, cost is linear in the envelope's
    hours, so the row budget becomes a real signal and the lane opens at - and
    floors on - the measured schedule's one hour. The unsignalled cap is the
    same four hours in both: a transport that reports nothing justifies no more
    than what already shipped.
    """

    with override_settings(FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS=slack):
        builder = picker_leaves(2)
        policy = builder.filter_seed_width_policy()

        assert policy.initial_width == policy.min_width == floor
        assert policy.unsignalled_cap == timedelta(hours=4)
        assert (
            policy.target_read_rows
            == settings.FILTER_SELECTOR_TEXT_SEED_TARGET_READ_ROWS
        )
        assert builder.recommended_filter_initial_slice_width() == floor
        # The post-discovery reset is single-sourced from the same floor.
        assert max(timedelta(hours=1), policy.min_width) == floor
        # A dense statement walks back down to the floor, never below it.
        assert policy.next_width(
            timedelta(hours=4),
            policy.target_read_rows + 1,
            request_width=timedelta(days=7),
        ) == (timedelta(hours=2) if slack else timedelta(hours=4))


@pytest.mark.parametrize(
    "candidate_ids", [["tr-1"], ["tr-1", "tr-2", "tr-3"]], ids=["one", "many"]
)
def test_the_exact_classifier_is_identical_in_both_modes(candidate_ids):
    """The oracle never moves; only what reaches it does.

    This is what keeps the switch one-sided: because the classifier is still
    unbounded, every row the bounded mode publishes is an exact any-span match
    on the shipped contract. The switch can only subtract candidates.
    """

    unbounded = picker_leaves(1).build_filter_match_query(candidate_ids)
    with override_settings(FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS=1):
        bounded = picker_leaves(1).build_filter_match_query(candidate_ids)

    assert bounded == unbounded
    assert "filter_witness" not in bounded[0]
    assert [name for name in bounded[1] if name.startswith("filter_witness")] == []
    assert_coherent_classifier(bounded[0])


@pytest.mark.parametrize(
    "slack,floor",
    [(0, timedelta(hours=4)), (1, timedelta(hours=1))],
    ids=["unbounded", "bounded"],
)
def test_a_dense_read_holds_at_whichever_floor_its_mode_declares(slack, floor):
    """The schedule change, on the builder and kwargs the view actually sends.

    Every statement here overruns the row budget, so the read sits on its floor
    throughout - which is the whole point of having two: with an unbounded
    witness four hours is the cheapest useful statement, and with a bounded one
    the same budget can afford to stop at an hour. Coverage per statement falls
    accordingly, and the cursor checkpoint follows it, so nothing is skipped.
    """

    with override_settings(FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS=slack):
        transport, page = _lane_read(
            window=timedelta(days=7),
            read_rows=52_000_000,
            cursor=True,
            max_seed_attempts=6,
        )

    assert transport.seed_widths == [floor] * 6
    assert _is_contiguous(transport.seed_intervals)
    assert page.complete is False
    assert page.continuation_slice_end == transport.seed_intervals[-1][0]
    assert page.continuation_slice_end == END - 6 * floor


class _WitnessLaneTransport(_PopulationLaneTransport):
    """A population whose value may be carried by a span other than the root.

    ``witness_at`` maps a trace id to the start time of the only span of that
    trace carrying the filter value; a trace absent from the map is witnessed
    by its own root, which is the shape both modes must agree on. The seed
    branch applies the envelope exactly as the CTE does - a trace is a
    candidate only when its witness starts inside ``[start, end)`` - and only
    when the statement carries one, so the unbounded mode filters nothing.
    Classification and hydration keep seeing the whole population, because the
    classifier this lane publishes through is unbounded in both modes.
    """

    def __init__(self, population, witness_at=None):
        super().__init__(population)
        self.witness_at = dict(witness_at or {})
        self.envelopes: list[tuple[datetime, datetime]] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        witness_start = params.get("filter_witness_start")
        if (
            witness_start is None
            or "matching_scalar_trace_identities" not in query
            or "filter_seed_limit" not in params
        ):
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )
        witness_end = params["filter_witness_end"]
        self.envelopes.append((witness_start, witness_end))
        whole_population = self.population
        self.population = [
            row
            for row in whole_population
            if witness_start
            <= self.witness_at.get(row["trace_id"], row["start_time"])
            < witness_end
        ]
        try:
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )
        finally:
            self.population = whole_population


def _witness_hop_chain(population, witness_at=None, *, slack, page_size=25):
    """The full cursor chain under one mode, plus every envelope it emitted."""

    transports: list[_WitnessLaneTransport] = []

    def factory() -> _WitnessLaneTransport:
        transports.append(_WitnessLaneTransport(population, witness_at))
        return transports[-1]

    with override_settings(FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS=slack):
        published = _picker_lane_hop_chain(
            population,
            page_size=page_size,
            max_seed_attempts=24,
            transport_factory=factory,
        )
    return published, [window for one in transports for window in one.envelopes]


def _newest_first(population) -> list[str]:
    return [
        row["trace_id"]
        for row in sorted(
            population,
            key=lambda row: (row["start_time"], row["trace_id"]),
            reverse=True,
        )
    ]


@pytest.mark.parametrize("slack", [0, 1])
def test_both_modes_are_exact_when_every_witness_is_its_own_root(slack):
    """Where the modes agree they must agree completely, not approximately.

    A root carries the value itself in the overwhelming majority of measured
    traces, and on that population the envelope excludes nothing - so the
    bounded mode has to publish the identical set, in the identical order, once
    each, even though it walks a different width schedule to get there.
    """

    population = _root_population(_SPARSE_TAIL + _DENSE_REGION)

    published, envelopes = _witness_hop_chain(population, slack=slack)

    assert published == _newest_first(population)
    assert len(published) == len(set(published)) == len(population)
    assert bool(envelopes) is bool(slack)
    # Every envelope is whole hours and contains the slack on both sides.
    assert all(
        (start.minute, start.second, start.microsecond) == (0, 0, 0)
        and (end.minute, end.second, end.microsecond) == (0, 0, 0)
        and end - start >= timedelta(hours=2)
        for start, end in envelopes
    )


def test_only_the_bounded_mode_omits_a_witness_outside_its_envelope():
    """THE contract change, pinned as an omission and nothing else.

    One trace's only span carrying the value starts three days after the whole
    request window, so it lies outside every envelope any slice of this read
    can produce. Today's contract publishes that trace; the bounded mode does
    not, and that single row is the entire difference - everything else about
    both pages, including order and exactness, is unchanged.
    """

    population = _root_population([(5, 3), (11, 3)])
    stranded = population[0]["trace_id"]
    witness_at = {stranded: END + timedelta(days=3)}

    unbounded, _ = _witness_hop_chain(population, witness_at, slack=0)
    bounded, envelopes = _witness_hop_chain(population, witness_at, slack=1)

    assert unbounded == _newest_first(population)
    assert bounded == [trace for trace in unbounded if trace != stranded]
    assert set(unbounded) - set(bounded) == {stranded}
    assert len(bounded) == len(set(bounded)) == len(population) - 1
    # Not an accident of an empty read: the envelopes really were emitted, and
    # the stranded witness really does sit outside all of them.
    assert envelopes
    assert all(end <= END + timedelta(hours=1) for _start, end in envelopes)
