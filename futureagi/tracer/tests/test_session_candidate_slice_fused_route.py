"""A candidate slice is not issued where the statement has no root scan.

The user-detail Sessions page with a scalar-attribute filter proves membership
and root-ness from one all-span replay seeded by the user's own sessions. Its
builder emits no root CTE, so the floor a slice binds is never read: the
"sliced" statement is the unsliced one byte for byte and reads the same rows.
Measured on the high-volume tenant through the view's entry point at three,
six and twelve months, the slice reader spent eleven or twelve density probes
and one or two such slices around the unsliced statement, 4.3-6.9 s of page
wall for a page that statement alone answered in 1.3-1.8 s warm.

These tests pin the repair from both sides: the builder answers whether a
raised floor narrows its statement by asking the rendered text, and the reader
issues that statement once, unsliced, when the answer is no - while every
shape whose statement does carry a root scan keeps the slice it had.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.selectors.session_candidate_slice import read_candidate_slice_page
from tracer.services.clickhouse.query_builders.session_list import (
    CANDIDATE_ROOT_SCAN_FLOOR_PARAM,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

PROJECT = str(UUID(int=1))
USER = str(UUID(int=7))
END = datetime(2026, 9, 12)
START = END - timedelta(days=92)
FLOOR_TOKEN = f"%({CANDIDATE_ROOT_SCAN_FLOOR_PARAM})s"
PAGE_SIZE = 25


def _window():
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _user():
    return {
        "column_id": "end_user_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": [USER],
        },
    }


def _long_text_in():
    return {
        "column_id": "metadata",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["x" * 508, "y" * 498],
            "attribute_value_types": ["string", "string"],
        },
    }


def _boolean_equals():
    return {
        "column_id": "lk.interrupted",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": True,
        },
    }


def _number_equals():
    return {
        "column_id": "tier",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "number",
            "filter_op": "equals",
            "filter_value": 1,
        },
    }


# The shapes the view routes through the slice reader, by whether the
# statement they render carries a root scan for the floor to bound.
FUSED_SHAPES = {
    "user_detail_long_text_in": [_long_text_in(), _user(), _window()],
    "user_detail_boolean_equals": [_boolean_equals(), _user(), _window()],
    "user_detail_number_equals": [_number_equals(), _user(), _window()],
}
ROOT_SCAN_SHAPES = {
    "default_date_only": [_window()],
    "user_only": [_user(), _window()],
    "attribute_without_user": [_number_equals(), _window()],
}


def _builder(filters, page_size=PAGE_SIZE):
    return SessionListQueryBuilderV2(
        project_id=PROJECT,
        filters=filters,
        page_number=0,
        page_size=page_size,
        bounded_internal_scan=True,
    )


# --------------------------------------------------------------------------
# The builder's answer
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("shape", sorted(FUSED_SHAPES))
def test_a_fused_statement_reports_that_the_floor_narrows_nothing(shape):
    builder = _builder(FUSED_SHAPES[shape])
    assert builder.supports_candidate_cursor_page()

    assert builder.candidate_slice_narrows_root_scan() is False

    unsliced, _ = builder.build_candidate_cursor_page_query()
    sliced, params = builder.build_candidate_cursor_page_query(
        scan_start_time=END - timedelta(hours=195)
    )
    # The floor is bound and nothing reads it: same text, same rows.
    assert sliced == unsliced
    assert CANDIDATE_ROOT_SCAN_FLOOR_PARAM in params
    assert FLOOR_TOKEN not in sliced
    assert "candidate_root_identities" not in sliced


@pytest.mark.unit
@pytest.mark.parametrize("shape", sorted(ROOT_SCAN_SHAPES))
def test_a_statement_with_a_root_scan_reports_that_the_floor_narrows_it(shape):
    builder = _builder(ROOT_SCAN_SHAPES[shape])
    assert builder.supports_candidate_cursor_page()

    assert builder.candidate_slice_narrows_root_scan() is True

    sliced, _ = builder.build_candidate_cursor_page_query(
        scan_start_time=END - timedelta(hours=195)
    )
    assert FLOOR_TOKEN in sliced
    assert "candidate_root_identities" in sliced


@pytest.mark.unit
@pytest.mark.parametrize("shape", sorted({**FUSED_SHAPES, **ROOT_SCAN_SHAPES}))
def test_asking_the_builder_leaves_its_bindings_untouched(shape):
    """The verifier reads the request window out of ``builder.params``.

    The answer comes from a render, and a render that bound the floor into
    the builder's own params would make the full-state verifier verify the
    slice against itself. The render's only write to the builder is the
    ``start_date``/``end_date`` pair, re-parsed from the filters exactly as
    the page render re-parses it, on every shape the reader is handed.
    """

    builder = _builder({**FUSED_SHAPES, **ROOT_SCAN_SHAPES}[shape])
    before_params = dict(builder.params)
    before = dict(vars(builder))

    builder.candidate_slice_narrows_root_scan()

    assert builder.params == before_params
    assert CANDIDATE_ROOT_SCAN_FLOOR_PARAM not in builder.params
    after = dict(vars(builder))
    changed = {
        name
        for name in set(before) | set(after)
        if before.get(name, object()) != after.get(name, object())
    }
    assert changed <= {"start_date", "end_date"}
    assert (after["start_date"], after["end_date"]) == builder.parse_time_range(
        builder.filters
    )


# --------------------------------------------------------------------------
# The reader's schedule
# --------------------------------------------------------------------------


class _Server:
    """A fake CH that records what the reader asks of it.

    The density probe answers with an estimate far above the row budget, so
    a reader that asked it WOULD narrow; the candidate statement answers with
    the configured rows whatever floor it is bound with.
    """

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []
        self.queries: list[str] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.queries.append(query)
        if query.lstrip().startswith("EXPLAIN ESTIMATE"):
            self.calls.append(("probe", params))
            return SimpleNamespace(
                data=[{"table": "spans", "rows": 300_000_000}],
                columns=["table", "rows"],
            )
        if "AS remaining_count" in query:
            self.calls.append(("candidate", params))
            return SimpleNamespace(
                data=[{**row, "remaining_count": len(self.rows)} for row in self.rows],
                columns=["session_id", "session_start", "remaining_count"],
            )
        self.calls.append(("full_state", params))
        return SimpleNamespace(data=[], columns=["session_id", "start_time"])

    @property
    def kinds(self):
        return [kind for kind, _params in self.calls]


def _read(builder, server, **cursor):
    return read_candidate_slice_page(
        builder=builder,
        analytics=server,
        deadline=ReadDeadline.start(30_000),
        read_settings=lambda rows: {"max_result_rows": rows},
        query_timeout_ms=9_000,
        **cursor,
    )


def _rows(count):
    return [
        {
            "session_id": str(UUID(int=100 + i)),
            "session_start": END - timedelta(hours=i),
        }
        for i in range(count)
    ]


@pytest.mark.unit
@pytest.mark.parametrize("shape", sorted(FUSED_SHAPES))
@pytest.mark.parametrize(
    "found",
    [0, 18, PAGE_SIZE + 1],
    ids=["empty", "short_of_a_page", "full_page"],
)
def test_a_fused_statement_is_issued_once_unsliced_with_no_probe(shape, found):
    """One statement, the whole window, and the answer is the page.

    Before the repair the empty page and the page short of ``page_size``
    widened: probes, a slice, more probes, a second slice, and finally the
    unsliced statement, every candidate statement identical. The full page is
    where exactness is observable: a slice would have had to verify its
    candidates against full state before publishing, and the unsliced
    statement's order is the published order with nothing to verify.
    """

    server = _Server(rows=_rows(found))
    page = _read(_builder(FUSED_SHAPES[shape]), server)

    assert server.kinds == ["candidate"]
    ((_kind, params),) = server.calls
    # The whole request window, not a raised floor.
    assert params[CANDIDATE_ROOT_SCAN_FLOOR_PARAM] == params["start_date_us"]
    assert page.slice_start is None
    assert page.statement_count == 1
    assert [row["session_id"] for row in page.rows] == [
        row["session_id"] for row in _rows(found)[:PAGE_SIZE]
    ]
    assert page.has_more is (found > PAGE_SIZE)
    # ``count() OVER()`` of the unsliced statement is the exact total.
    assert page.remaining_count == found


@pytest.mark.unit
@pytest.mark.parametrize("shape", sorted(FUSED_SHAPES))
def test_a_fused_continuation_is_the_same_single_statement(shape):
    """A cursor hop on the fused route is one unsliced statement too.

    Nothing was narrowed, so the root-scan ceiling is not pulled down to the
    cursor instant either: both root bindings stay on the request window and
    only the keyset clause moves the page.
    """

    server = _Server(rows=_rows(PAGE_SIZE + 1))
    page = _read(
        _builder(FUSED_SHAPES[shape]),
        server,
        before_start_time=END - timedelta(hours=40),
        before_session_id=str(UUID(int=140)),
    )

    assert server.kinds == ["candidate"]
    ((_kind, params),) = server.calls
    assert params[CANDIDATE_ROOT_SCAN_FLOOR_PARAM] == params["start_date_us"]
    assert params["candidate_root_scan_end_us"] == params["end_date_us"]
    assert "cursor_before_start_us" in params
    assert "cursor_before_session_id" in params
    assert "cursor_before_start_us" in server.queries[0]
    assert page.slice_start is None
    assert page.statement_count == 1
    assert page.has_more is True
    assert page.remaining_count == PAGE_SIZE + 1


@pytest.mark.unit
def test_a_statement_with_a_root_scan_still_narrows():
    """The slice the default page relies on is not what this repair removes."""

    server = _Server(rows=_rows(30))
    page = _read(_builder(ROOT_SCAN_SHAPES["default_date_only"]), server)

    assert server.kinds[0] == "probe"
    assert "candidate" in server.kinds
    assert page.rows


@pytest.mark.unit
def test_a_builder_that_cannot_answer_is_taken_to_narrow():
    """Every builder this lane had before the question existed narrows."""

    inner = _builder(ROOT_SCAN_SHAPES["default_date_only"])

    class _Silent:
        def __getattr__(self, name):
            if name == "candidate_slice_narrows_root_scan":
                raise AttributeError(name)
            return getattr(inner, name)

    server = _Server(rows=_rows(30))
    _read(_Silent(), server)

    assert server.kinds[0] == "probe"
