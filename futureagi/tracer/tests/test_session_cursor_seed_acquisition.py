"""Contracts for how much a cursor-mode session seed acquires per statement.

Acquisition size is the only thing under test here. The seed statement, the
slice schedule, the exact latest-state classifier, its own batch split and the
per-chunk continuation checkpoint are all untouched, so the page a walk
publishes may not move by a single row - it may only take fewer statements to
reach it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests.test_session_seed_witness_gate import (
    END,
    PROJECT_ID,
    SLICE_START,
    _attribute_filter,
    _builder,
    _SessionWorld,
    _time_filter,
)

pytestmark = pytest.mark.unit


class _CountingWorld(_SessionWorld):
    """The gate test's tenant, plus the seed limit every statement asked for."""

    def __init__(self, sessions: dict[str, tuple[Any, Any]]):
        super().__init__(sessions)
        self.limits_asked: list[int] = []

    def _seed(self, query, params):
        self.limits_asked.append(int(params["filter_seed_limit"]))
        return super()._seed(query, params)


def _seed_sql(builder, limit: int) -> tuple[str, dict[str, Any]]:
    return builder.build_filter_seed_page(
        slice_start=SLICE_START, slice_end=END, limit=limit
    )


def _walk(world: _CountingWorld, builder, cursor_seed_batch: int | None):
    """One cursor-mode page, with the acquisition recommendation forced."""

    builder.recommended_filter_cursor_seed_batch_size = lambda: cursor_seed_batch
    page = read_bounded_filter_page(
        builder=builder,
        analytics=world,
        filters=builder.filters,
        key_field="session_id",
        page_number=0,
        page_size=builder.page_size,
        deadline_ms=30_000,
        max_candidates=200,
        classify_batch_size=builder.recommended_filter_classify_batch_size(),
        include_incomplete_rows=True,
        bounded_continuation=True,
    )
    return page


def _world(matching: int = 25, rejected: int = 95) -> tuple[_CountingWorld, list[str]]:
    """One dense slice: the newest sessions are all rejected by the classifier.

    Only the oldest ``matching`` sessions carry a witness, so no acquisition
    size can fill the page before the walk has crossed every rejected session
    ahead of them - which is the production shape this test exists for.
    """

    total = matching + rejected
    sessions: dict[str, tuple[Any, Any]] = {}
    expected: list[str] = []
    for index in range(total):
        session_id = f"00000000-0000-4000-8000-{index:012d}"
        root = END - timedelta(minutes=index + 1)
        hit = index >= rejected
        sessions[session_id] = (root, root if hit else None)
        if hit:
            expected.append(session_id)
    return _CountingWorld(sessions), expected


def _published(page) -> list[tuple[str, datetime]]:
    return [(str(row["session_id"]), row["start_time"]) for row in page.rows]


def test_the_cursor_seed_reuses_the_numbered_page_acquisition_size() -> None:
    """The legacy ``None`` left cursor reads acquiring one page plus one."""

    for klass in (SessionListQueryBuilder, SessionListQueryBuilderV2):
        builder = _builder(_attribute_filter(), klass=klass)
        recommended = builder.recommended_filter_cursor_seed_batch_size()

        assert recommended is not None
        assert recommended == builder.recommended_filter_seed_batch_size()


def test_only_a_bound_limit_separates_the_two_acquisition_sizes() -> None:
    """Same statement, same parameters, one different bound value."""

    builder = _builder(_attribute_filter())
    small_sql, small_params = _seed_sql(builder, 26)
    large_sql, large_params = _seed_sql(
        builder, builder.recommended_filter_cursor_seed_batch_size()
    )

    assert small_sql == large_sql
    assert sorted(small_params) == sorted(large_params)
    assert small_params["filter_seed_limit"] == 26
    assert large_params["filter_seed_limit"] == 200
    assert {
        key: value for key, value in small_params.items() if key != "filter_seed_limit"
    } == {
        key: value for key, value in large_params.items() if key != "filter_seed_limit"
    }


def test_a_cursor_walk_asks_for_the_recommended_batch() -> None:
    """The recommendation reaches the statement, not just the hook."""

    world, _expected = _world()
    _walk(world, _builder(_attribute_filter()), 200)

    assert world.seed_statements >= 1
    assert world.limits_asked  # every seed carried a limit
    assert max(world.limits_asked) == 200


@pytest.mark.parametrize(("matching", "rejected"), [(25, 95), (25, 0), (3, 140)])
def test_the_wider_acquisition_publishes_the_identical_page(
    matching: int, rejected: int
) -> None:
    """Same rows, same order, same completeness - in fewer statements."""

    narrow_world, expected = _world(matching, rejected)
    wide_world, _ = _world(matching, rejected)

    narrow = _walk(narrow_world, _builder(_attribute_filter()), None)
    wide = _walk(wide_world, _builder(_attribute_filter()), 200)

    assert _published(wide) == _published(narrow)
    assert [session for session, _ in _published(wide)] == expected[: len(wide.rows)]
    assert wide.complete == narrow.complete
    assert wide.has_more == narrow.has_more
    assert wide.query_count <= narrow.query_count
    # The classifier is authoritative in both walks: it must be asked about
    # every candidate the seed acquired, and about no others.
    assert sorted(wide_world.classified) == sorted(narrow_world.classified)


def test_the_wider_acquisition_removes_statements_on_a_dense_slice() -> None:
    """The production shape: many rejected candidates ahead of the page."""

    narrow_world, _expected = _world(25, 95)
    wide_world, _ = _world(25, 95)

    narrow = _walk(narrow_world, _builder(_attribute_filter()), None)
    wide = _walk(wide_world, _builder(_attribute_filter()), 200)

    assert wide.query_count < narrow.query_count
    assert wide_world.seed_statements < narrow_world.seed_statements


def test_the_classifier_split_is_not_widened_with_it() -> None:
    """Acquisition grows; exact latest-state replay keeps its own batch."""

    builder = _builder(_attribute_filter())

    assert builder.recommended_filter_classify_batch_size() == 50
    assert builder.recommended_filter_cursor_seed_batch_size() == 200


def test_the_seed_limit_stays_inside_the_builder_contract() -> None:
    """The builder refuses a seed page outside 1..512; 200 is inside it."""

    builder = _builder(_attribute_filter())
    recommended = builder.recommended_filter_cursor_seed_batch_size()

    assert 1 <= recommended <= 512
    _sql, params = _seed_sql(builder, recommended)
    assert params["filter_seed_limit"] == recommended


def test_the_project_and_window_are_untouched_by_the_recommendation() -> None:
    """Nothing about what is scanned depends on the acquisition size."""

    builder = SessionListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter()],
        page_size=25,
        bounded_internal_scan=True,
    )
    _sql, small = _seed_sql(builder, 26)
    _sql2, large = _seed_sql(builder, 200)

    for key in ("project_id", "filter_slice_start_us", "filter_slice_end_us"):
        assert small[key] == large[key]
