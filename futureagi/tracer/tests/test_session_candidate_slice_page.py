"""Session candidate pages read from a bounded slice, and stay exact.

The statement under test replays latest state over whatever window it is
given, so on a high-volume tenant a month-long window does not return inside
the request wall. These tests pin the two halves of the repair: the scan floor
moves without the SQL text moving, and a narrowed scan never publishes a row
whose start it inflated.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from uuid import UUID

import pytest

from tracer.selectors.session_candidate_slice import (
    SessionCandidateSlicePage,
    read_candidate_slice_page,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

PROJECT = str(UUID(int=1))
END = datetime(2026, 9, 12)
WINDOW_HOURS = 720
START = END - timedelta(hours=WINDOW_HOURS)
# The production shape this repair exists for: the newest stretch of the window
# is nearly empty and everything older is dense, so a width chosen by duration
# alone lands either short of the data or on top of all of it.
SPARSE_HOURS = 216
DENSE_ROWS_PER_HOUR = 100_000
BUDGET = 1_000_000


def _filters(start=START, end=END):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        }
    ]


def _builder(page_size=2, **kwargs):
    return SessionListQueryBuilderV2(
        project_id=PROJECT,
        filters=_filters(**kwargs),
        page_number=0,
        page_size=page_size,
        bounded_internal_scan=True,
    )


def _sha(sql: str) -> str:
    return hashlib.sha256(sql.strip().rstrip(";").encode()).hexdigest()[:16]


def _sid(index: int) -> str:
    return str(UUID(int=100 + index))


def _moment(micros: int) -> datetime:
    return datetime(1970, 1, 1) + timedelta(microseconds=micros)


def _micros(moment: datetime) -> int:
    return int((moment - datetime(1970, 1, 1)) / timedelta(microseconds=1))


class _Server:
    """A fake CH that answers the three statements this lane issues.

    ``rows`` are the candidates, each with the start its NEWEST-side root gives
    it; a slice with floor ``T`` discovers exactly those whose start is at or
    above ``T``, which is the real statement's semantics. ``full_state`` maps a
    session id to its true start over the whole window, so a session present in
    both with different values is a displaced one.

    Density follows the production shape this repair exists for: nothing in the
    newest ``SPARSE_HOURS``, and a flat dense rate before that.
    """

    def __init__(self, *, rows, full_state, estimate_columns=("table", "rows")):
        self.rows = rows
        self.full_state = full_state
        self.estimate_columns = estimate_columns
        self.calls: list[tuple[str, dict]] = []

    @property
    def kinds(self) -> list[str]:
        return [kind for kind, _params in self.calls]

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if query.lstrip().startswith("EXPLAIN ESTIMATE"):
            self.calls.append(("probe", params))
            return self._estimate(params)
        if "AS remaining_count" in query:
            self.calls.append(("slice", params))
            return self._slice(params)
        self.calls.append(("full_state", params))
        return self._match(params)

    def _estimate(self, params):
        floor = _moment(params["candidate_density_start_us"])
        dense_hours = max(
            0.0, ((END - timedelta(hours=SPARSE_HOURS)) - floor) / timedelta(hours=1)
        )
        rows = int(dense_hours * DENSE_ROWS_PER_HOUR)
        if self.estimate_columns is None:
            return SimpleNamespace(data=[], columns=None)
        return SimpleNamespace(
            data=[{"table": "spans", "rows": rows}] if rows else [],
            columns=list(self.estimate_columns),
        )

    def _slice(self, params):
        # The floor the statement actually binds under its root scan. The
        # request window stays on ``start_date_us`` so the membership scans
        # inside the same statement keep reading it.
        floor = _moment(params["candidate_root_scan_start_us"])
        found = sorted(
            (row for row in self.rows if row["session_start"] >= floor),
            key=lambda row: row["session_start"],
            reverse=True,
        )
        return SimpleNamespace(
            data=[{**row, "remaining_count": len(found)} for row in found],
            columns=["session_id", "session_start", "remaining_count"],
        )

    def _match(self, params):
        wanted = set(params["candidate_filter_session_id_array"])
        return SimpleNamespace(
            data=[
                {"session_id": sid, "start_time": start}
                for sid, start in self.full_state.items()
                if sid in wanted
            ],
            columns=["session_id", "start_time"],
        )


def _read(builder, server, **kwargs) -> SessionCandidateSlicePage:
    return read_candidate_slice_page(
        builder=builder,
        analytics=server,
        deadline=ReadDeadline.start(30_000),
        read_settings=lambda rows: {"max_result_rows": rows},
        query_timeout_ms=9_000,
        **kwargs,
    )


# --------------------------------------------------------------------------
# The statement itself
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_raised_scan_floor_moves_only_the_binding_not_the_statement():
    builder = _builder()
    unsliced, unsliced_params = builder.build_candidate_cursor_page_query()
    floor = END - timedelta(hours=SPARSE_HOURS)
    sliced, sliced_params = builder.build_candidate_cursor_page_query(
        scan_start_time=floor
    )

    assert sliced == unsliced, "a narrowed scan must be the same statement"
    assert _sha(sliced) == _sha(unsliced)
    # The floor moves the ROOT scan's binding and that one only. It used to
    # move ``start_date_us``, which every scan in the statement reads - the
    # membership scans included - and that is the defect these tests now pin:
    # see the membership-evidence section below.
    assert sliced_params["candidate_root_scan_start_us"] == _micros(floor)
    assert sliced_params["candidate_root_scan_end_us"] == unsliced_params["end_date_us"]
    assert sliced_params["start_date_us"] == unsliced_params["start_date_us"]
    assert sliced_params["end_date_us"] == unsliced_params["end_date_us"]
    assert unsliced_params["candidate_root_scan_start_us"] == _micros(START)
    # The verifier reads the request window out of the builder's own params.
    # A floor bound there instead would make it verify the slice against
    # itself, which is not a verification at all.
    assert builder.params["start_date_us"] == unsliced_params["start_date_us"]
    assert "candidate_root_scan_start_us" not in builder.params


@pytest.mark.unit
@pytest.mark.parametrize(
    "floor", [START - timedelta(hours=1), END, END + timedelta(hours=1)]
)
def test_scan_floor_outside_the_request_window_is_refused(floor):
    with pytest.raises(ValueError, match="scan floor"):
        _builder().build_candidate_cursor_page_query(scan_start_time=floor)


@pytest.mark.unit
def test_sliced_continuation_narrows_the_root_scan_to_the_cursor_instant():
    builder = _builder()
    cursor_at = END - timedelta(hours=SPARSE_HOURS + 1)
    floor = END - timedelta(hours=SPARSE_HOURS * 2)
    _sql, params = builder.build_candidate_cursor_page_query(
        before_start_time=cursor_at,
        before_session_id=_sid(0),
        scan_start_time=floor,
    )
    # Half-open, so the cursor instant itself is still read and the keyset's
    # id tie-break - not the scan - separates a session tied on that start.
    # The ceiling is a ROOT argument, so like the floor it moves the root
    # bindings only; it used to move ``end_date_us`` and truncate the
    # membership scans with it.
    assert params["candidate_root_scan_end_us"] == params["cursor_before_start_us"] + 1
    assert params["end_date_us"] == _micros(END)
    # Without a raised floor the whole-window statement keeps its bindings, so
    # its text and its cost are untouched by this lane.
    _unsliced_sql, unsliced = builder.build_candidate_cursor_page_query(
        before_start_time=cursor_at, before_session_id=_sid(0)
    )
    assert unsliced["candidate_root_scan_end_us"] > unsliced["cursor_before_start_us"]


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_displaced_candidate_is_dropped_and_the_rest_of_the_page_publishes():
    """The production case: the slice's NEWEST row truly started far earlier."""
    newest = END - timedelta(hours=100)
    second = END - timedelta(hours=120)
    third = END - timedelta(hours=140)
    server = _Server(
        rows=[
            {"session_id": _sid(0), "session_start": newest},
            {"session_id": _sid(1), "session_start": second},
            {"session_id": _sid(2), "session_start": third},
        ],
        full_state={
            # Inflated: its first root is three weeks older than the slice saw.
            _sid(0): START + timedelta(hours=4),
            _sid(1): second,
            _sid(2): third,
        },
    )
    page = _read(_builder(page_size=2), server)

    assert [row["session_id"] for row in page.rows] == [_sid(1), _sid(2)]
    assert page.has_more is True
    assert page.slice_start is not None
    assert server.kinds.count("full_state") == 1
    assert "slice" in server.kinds


@pytest.mark.unit
def test_page_short_of_survivors_widens_and_ends_on_the_unsliced_statement():
    kept = END - timedelta(hours=120)
    server = _Server(
        rows=[
            {"session_id": _sid(0), "session_start": END - timedelta(hours=100)},
            {"session_id": _sid(1), "session_start": kept},
            {"session_id": _sid(2), "session_start": END - timedelta(hours=140)},
        ],
        full_state={
            _sid(0): START + timedelta(hours=4),
            _sid(1): kept,
            _sid(2): START + timedelta(hours=6),
        },
    )
    page = _read(_builder(page_size=2), server)

    # One survivor cannot fill a two-row page, and what is below the floor is
    # unseen, so the read may not publish a short page. It widens instead, and
    # the last statement is the unsliced one - today's behaviour, still exact.
    assert server.kinds.count("slice") > 1
    assert page.slice_start is None
    assert [row["session_id"] for row in page.rows] == [_sid(0), _sid(1)]


@pytest.mark.unit
def test_a_slice_too_small_to_settle_a_page_widens_without_a_verifier():
    rows = [{"session_id": _sid(0), "session_start": END - timedelta(hours=100)}]
    server = _Server(rows=rows, full_state={_sid(0): rows[0]["session_start"]})
    page = _read(_builder(page_size=2), server)

    # One candidate cannot settle a two-row page whatever it resolves to, so
    # the full-window verifier - the expensive statement here - is never run
    # against a slice that could not publish anyway.
    assert "full_state" not in server.kinds
    assert page.slice_start is None
    assert [row["session_id"] for row in page.rows] == [_sid(0)]
    assert page.has_more is False


@pytest.mark.unit
def test_every_candidate_surviving_publishes_without_a_second_statement():
    rows = [
        {"session_id": _sid(0), "session_start": END - timedelta(hours=100)},
        {"session_id": _sid(1), "session_start": END - timedelta(hours=120)},
        {"session_id": _sid(2), "session_start": END - timedelta(hours=140)},
    ]
    server = _Server(
        rows=rows,
        full_state={row["session_id"]: row["session_start"] for row in rows},
    )
    page = _read(_builder(page_size=2), server)

    assert [row["session_id"] for row in page.rows] == [_sid(0), _sid(1)]
    assert page.has_more is True
    assert page.slice_start is not None
    assert server.kinds.count("slice") == 1


@pytest.mark.unit
def test_a_candidate_full_state_drops_entirely_is_not_published():
    rows = [
        {"session_id": _sid(0), "session_start": END - timedelta(hours=100)},
        {"session_id": _sid(1), "session_start": END - timedelta(hours=120)},
        {"session_id": _sid(2), "session_start": END - timedelta(hours=140)},
    ]
    server = _Server(
        rows=rows,
        # Full state no longer places the newest candidate in the window at all.
        full_state={
            _sid(1): rows[1]["session_start"],
            _sid(2): rows[2]["session_start"],
        },
    )
    page = _read(_builder(page_size=2), server)

    assert [row["session_id"] for row in page.rows] == [_sid(1), _sid(2)]


# --------------------------------------------------------------------------
# The width
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_the_issued_slice_stays_inside_the_row_budget():
    rows = [
        {"session_id": _sid(0), "session_start": END - timedelta(hours=100)},
        {"session_id": _sid(1), "session_start": END - timedelta(hours=120)},
        {"session_id": _sid(2), "session_start": END - timedelta(hours=140)},
    ]
    server = _Server(
        rows=rows,
        full_state={row["session_id"]: row["session_start"] for row in rows},
    )
    page = _read(_builder(page_size=2), server)

    assert page.slice_start is not None
    dense_hours = max(
        0.0,
        ((END - timedelta(hours=SPARSE_HOURS)) - page.slice_start) / timedelta(hours=1),
    )
    assert dense_hours * DENSE_ROWS_PER_HOUR <= BUDGET
    # The probe reads the index only, and the search is geometric, so a whole
    # request window is bracketed in a bounded handful of them.
    from tracer.selectors.session_candidate_slice import _MAX_DENSITY_PROBES

    assert 1 <= server.kinds.count("probe") <= _MAX_DENSITY_PROBES


@pytest.mark.unit
def test_a_window_inside_the_budget_is_never_narrowed():
    builder = _builder(page_size=2, start=END - timedelta(hours=SPARSE_HOURS))
    rows = [
        {"session_id": _sid(0), "session_start": END - timedelta(hours=100)},
        {"session_id": _sid(1), "session_start": END - timedelta(hours=120)},
    ]
    server = _Server(
        rows=rows,
        full_state={row["session_id"]: row["session_start"] for row in rows},
    )
    page = _read(builder, server)

    assert page.slice_start is None
    assert server.kinds == ["probe", "slice"]
    assert page.has_more is False


@pytest.mark.unit
def test_an_unreadable_estimate_reads_the_window_whole_rather_than_guessing():
    rows = [{"session_id": _sid(0), "session_start": END - timedelta(hours=100)}]
    server = _Server(
        rows=rows,
        full_state={_sid(0): rows[0]["session_start"]},
        estimate_columns=None,
    )
    page = _read(_builder(page_size=2), server)

    assert page.slice_start is None
    assert server.kinds == ["probe", "slice"]


@pytest.mark.unit
def test_a_builder_without_the_verifier_reads_the_window_whole():
    builder = _builder(page_size=2)
    rows = [{"session_id": _sid(0), "session_start": END - timedelta(hours=100)}]
    server = _Server(rows=rows, full_state={})
    with mock.patch.object(builder, "supports_bounded_filter_scan", return_value=False):
        page = _read(builder, server)

    # Narrowing without the full-state verifier would publish inflated starts,
    # so a request that cannot run one is not narrowed at all - and it never
    # pays for a probe either.
    assert page.slice_start is None
    assert server.kinds == ["slice"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "rows,columns,expected",
    [
        (
            [{"table": "spans", "rows": 5}, {"table": "spans", "rows": 7}],
            ["table", "rows"],
            12,
        ),
        ([{"table": "other", "rows": 5}], ["table", "rows"], None),
        ([{"table": "spans", "rows": "many"}], ["table", "rows"], None),
        ([{"table": "spans", "rows": 5}], ["parts"], None),
    ],
)
def test_density_estimate_reduces_only_shapes_it_can_read(rows, columns, expected):
    estimate = _builder().candidate_slice_density_estimate(rows, columns)
    assert (estimate if isinstance(estimate, int) else None) == expected


@pytest.mark.unit
def test_an_estimate_naming_no_part_is_not_reported_as_the_integer_zero():
    from tracer.selectors.filter_seed_width import EMPTY_DENSITY_ESTIMATE

    assert (
        _builder().candidate_slice_density_estimate([], ["table", "rows"])
        is EMPTY_DENSITY_ESTIMATE
    )


@pytest.mark.unit
@pytest.mark.parametrize("narrowed", [False, True])
def test_a_narrowed_page_publishes_its_count_as_a_lower_bound(narrowed):
    """A floor the scan cannot see below cannot produce a window total."""
    from tracer.tests.test_session_list_bounded_view import _view_and_request
    from tracer.views.trace_session import TraceSessionView

    rows = [
        {"session_id": _sid(0), "session_start": END - timedelta(hours=100)},
        {"session_id": _sid(1), "session_start": END - timedelta(hours=120)},
        {"session_id": _sid(2), "session_start": END - timedelta(hours=140)},
    ]
    server = _Server(
        rows=rows,
        full_state={row["session_id"]: row["session_start"] for row in rows},
        # Without a readable estimate the read is not narrowed, so the same
        # page is published with the exact count it has always carried.
        estimate_columns=("table", "rows") if narrowed else None,
    )
    view, request = _view_and_request()
    selected = TraceSessionView._select_session_page(
        view,
        request,
        project_id=PROJECT,
        project=None,
        analytics=server,
        validated_data={
            "filters": _filters(),
            "sort_params": [],
            "page_number": 0,
            "page_size": 2,
            "cursor_mode": True,
        },
    )

    assert selected.candidate_cursor is True
    assert selected.candidate_total_is_lower_bound is narrowed
    assert [row["session_id"] for row in selected.page_candidates] == [
        _sid(0),
        _sid(1),
    ]


@pytest.mark.unit
def test_the_density_probe_reads_the_index_and_names_no_projection_key():
    sql, params = _builder().build_candidate_slice_density_probe_query(
        slice_start=END - timedelta(hours=SPARSE_HOURS), slice_end=END
    )
    assert sql.lstrip().startswith("EXPLAIN ESTIMATE")
    # Spelled as the key expression, the optimizer could answer this from an
    # aggregate projection and report ITS rows, approving the widest slice.
    assert "toStartOfHour" not in sql
    assert " IN (" not in sql and "FINAL" not in sql
    assert "is_deleted" not in sql
    assert params["candidate_density_start_us"] < params["candidate_density_end_us"]


@pytest.mark.unit
def test_the_density_probe_stays_inside_the_request_window():
    builder = _builder()
    with pytest.raises(ValueError, match="inside the window"):
        builder.build_candidate_slice_density_probe_query(
            slice_start=START - timedelta(hours=1), slice_end=END
        )


# --------------------------------------------------------------------------
# Membership evidence
#
# A slice narrows what the statement READS, and the gate's second rule turns
# on what that costs the read: a session the slice did not discover is assumed
# to have no root inside it, hence a true start below the floor, hence a rank
# below every published row. That holds for evidence carried by ROOT spans. It
# does not hold for the span that proves a session matches the FILTER, which
# any span in the session may carry: narrow the window that evidence is
# gathered over and a session whose root sits inside the slice is never
# discovered at all, and the row it should have occupied is taken by one that
# ranks below it.
# --------------------------------------------------------------------------

USER = str(UUID(int=7))

# Where each shape gathers the evidence that a session matches the filter.
# None of these are root scans, so none of them may be narrowed.
_MEMBERSHIP_CTES = {
    "end_user_id": ("candidate_user_span_identities", "latest_user_spans"),
    "attribute": (
        "candidate_scalar_span_identities",
        "latest_candidate_scalar_spans",
    ),
}
_ROOT_CTES = ("candidate_root_identities", "latest_roots")

_LOWER = re.compile(
    r"start_time\s*>=\s*(?:toStartOfHour\()?fromUnixTimestamp64Micro\(%\((\w+)\)s"
)
_UPPER = re.compile(
    r"start_time\s*<\s*(?:toStartOfHour\()?fromUnixTimestamp64Micro\(%\((\w+)\)s"
)


def _cte(sql: str, name: str) -> str:
    start = re.search(r"\b" + name + r" AS \(", sql).end()
    depth = 1
    for end in range(start, len(sql)):
        depth += (sql[end] == "(") - (sql[end] == ")")
        if depth == 0:
            return sql[start:end]
    raise AssertionError(f"unclosed CTE {name}")


def _scalar_relation(sql: str, name: str) -> str:
    """The attribute witness is a scalar subquery, not a named CTE."""
    end = sql.index(f") AS {name}")
    return sql[sql.rindex("(SELECT", 0, end) : end]


def _floor_of(fragment: str, params: dict) -> datetime:
    return _moment(params[_LOWER.search(fragment).group(1)])


def _ceiling_of(fragment: str, params: dict) -> datetime:
    return _moment(params[_UPPER.search(fragment).group(1)])


def _membership_filters(shape, start=START, end=END):
    if shape == "end_user_id":
        leaf = {
            "column_id": "end_user_id",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": [USER],
            },
        }
    else:
        leaf = {
            "column_id": "tier",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 1,
            },
        }
    return [*_filters(start=start, end=end), leaf]


def _membership_builder(shape, page_size=2):
    return SessionListQueryBuilderV2(
        project_id=PROJECT,
        filters=_membership_filters(shape),
        page_number=0,
        page_size=page_size,
        bounded_internal_scan=True,
    )


@pytest.mark.unit
@pytest.mark.parametrize("shape", sorted(_MEMBERSHIP_CTES))
def test_a_narrowed_scan_gathers_membership_evidence_over_the_whole_window(shape):
    """The slice may bound roots. It may not bound what proves membership.

    Gate rule (2) reads a session's absence from the slice as proof that its
    roots are below the floor. That inference is only available when discovery
    turns on root spans alone. The evidence that a session MATCHES the filter
    can sit on any span, so a floor raised under it hides a session whose root
    the slice can see - and rule (2) then vouches for a page it never proved.
    """

    builder = _membership_builder(shape)
    floor = END - timedelta(hours=SPARSE_HOURS)
    sql, params = builder.build_candidate_cursor_page_query(scan_start_time=floor)

    for name in _ROOT_CTES:
        assert _floor_of(_cte(sql, name), params) == floor, name
    for name in _MEMBERSHIP_CTES[shape]:
        assert _floor_of(_cte(sql, name), params) == START, name
    if shape == "attribute":
        witness = _scalar_relation(sql, "candidate_witness_session_ids")
        assert _floor_of(witness, params) == START


@pytest.mark.unit
@pytest.mark.parametrize("shape", sorted(_MEMBERSHIP_CTES))
def test_a_sliced_continuation_does_not_truncate_membership_evidence(shape):
    """The same rule on the upper bound.

    A continuation narrows the scan's ceiling to the cursor instant, which is
    exact for roots: a session whose true start is above the cursor has no root
    at or below it. Membership evidence is again not a root - a span above the
    cursor can be the only proof a session matches - so the ceiling may not be
    lowered under it either.
    """

    builder = _membership_builder(shape)
    cursor_at = END - timedelta(hours=SPARSE_HOURS + 1)
    sql, params = builder.build_candidate_cursor_page_query(
        before_start_time=cursor_at,
        before_session_id=_sid(0),
        scan_start_time=END - timedelta(hours=SPARSE_HOURS * 2),
    )

    # Half-open, so the cursor instant itself is still read.
    root_ceiling = cursor_at + timedelta(microseconds=1)
    for name in _ROOT_CTES:
        assert _ceiling_of(_cte(sql, name), params) == root_ceiling, name
    for name in _MEMBERSHIP_CTES[shape]:
        assert _ceiling_of(_cte(sql, name), params) == END, name


class _MembershipServer(_Server):
    """A CH double that discovers on the statement's OWN window bindings.

    Discovery needs two pieces of evidence and the statement asks for them in
    separate scans: a live root, and the span that proves the session matches
    the filter. This double reads each scan's window out of the rendered
    statement instead of assuming the two agree, so it models whatever the
    builder binds rather than what this test expects it to bind.
    """

    def __init__(self, *, membership_at, **kwargs):
        super().__init__(**kwargs)
        self.membership_at = membership_at
        self._sql = ""

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "AS remaining_count" in query:
            self._sql = query
        return super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )

    def _slice(self, params):
        roots = _cte(self._sql, "candidate_root_identities")
        member = _cte(self._sql, "candidate_user_span_identities")
        root_window = (_floor_of(roots, params), _ceiling_of(roots, params))
        member_window = (_floor_of(member, params), _ceiling_of(member, params))
        found = sorted(
            (
                row
                for row in self.rows
                if root_window[0] <= row["session_start"] < root_window[1]
                and member_window[0]
                <= self.membership_at[row["session_id"]]
                < member_window[1]
            ),
            key=lambda row: row["session_start"],
            reverse=True,
        )
        return SimpleNamespace(
            data=[{**row, "remaining_count": len(found)} for row in found],
            columns=["session_id", "session_start", "remaining_count"],
        )


@pytest.mark.unit
def test_a_session_proved_a_member_below_the_floor_is_not_displaced():
    """The page row the narrowed scan silently loses.

    ``_sid(0)`` has one live root, inside whatever slice the width search
    approves, and the only span that proves it matches the filter sits at the
    very start of the request window. It belongs at the top of the page. A
    scan that gathers membership evidence only inside the slice never
    discovers it, every candidate it does discover survives the gate with an
    unchanged start, and the page publishes the two rows BELOW the one it
    lost.
    """

    rows = [
        {"session_id": _sid(index), "session_start": END - timedelta(hours=hours)}
        for index, hours in enumerate((100, 120, 140, 160))
    ]
    server = _MembershipServer(
        rows=rows,
        full_state={row["session_id"]: row["session_start"] for row in rows},
        membership_at={
            # Proved a member by a span at the request start, and a member of
            # the page by a root the slice can see.
            _sid(0): START,
            **{row["session_id"]: row["session_start"] for row in rows[1:]},
        },
    )
    page = _read(_membership_builder("end_user_id", page_size=2), server)

    assert [row["session_id"] for row in page.rows] == [_sid(0), _sid(1)]
    assert page.has_more is True


# --------------------------------------------------------------------------
# Set-valued page predicates
#
# The sibling of the defect above, on the other bound. Gate rule (2) reads a
# session's absence from the slice as proof that its roots lie below the floor.
# That holds while admission turns on root EXISTENCE. Some page predicates are
# a function of the root SET instead - an aggregate HAVING, a message argMin or
# argMax, a duration over min and max, the org-scope project count - and the
# continuation's CEILING truncates that set above the cursor without moving the
# session's rank. The predicate's VALUE changes, the session never enters the
# statement, and its true start sits ABOVE the floor and BELOW the cursor,
# exactly where rule (2) claims nothing can hide.
#
# The floor is immune by the inference rule (2) already uses: a set the floor
# truncates belongs to a session with a root below the floor.
# --------------------------------------------------------------------------

# Newest-first, and inside the sparse stretch, so the width search approves a
# slice whose floor reaches well below it. The page test asserts that rather
# than assume it.
ROOT_SET_CURSOR = END - timedelta(hours=100)
MIN_TRACES = 2

_SET_VALUED_COLUMNS = {
    "traces_count": ("number", "greater_than", MIN_TRACES),
    "duration": ("number", "greater_than", 5),
    "total_cost": ("number", "greater_than", 1),
    "total_tokens": ("number", "greater_than", 1),
    "last_message": ("text", "contains", "hello"),
    "first_message": ("text", "contains", "hello"),
}


def _root_set_filters(column, start=START, end=END):
    filter_type, filter_op, filter_value = _SET_VALUED_COLUMNS[column]
    return [
        # A raw attribute filter is what makes this shape candidate-cursor safe
        # at all; on its own it emits no session HAVING, so the aggregate below
        # is the only set-valued predicate in the statement.
        *_membership_filters("attribute", start=start, end=end),
        {
            "column_id": column,
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": filter_type,
                "filter_op": filter_op,
                "filter_value": filter_value,
            },
        },
    ]


def _root_set_builder(column="traces_count", page_size=2):
    return SessionListQueryBuilderV2(
        project_id=PROJECT,
        filters=_root_set_filters(column),
        page_number=0,
        page_size=page_size,
        bounded_internal_scan=True,
    )


def _sessions_having(sql: str) -> str | None:
    """The HAVING of the ``sessions`` relation, not of a membership CTE."""
    tail = sql[re.search(r"\n\s*sessions AS \(", sql).end() :]
    found = re.search(r"HAVING [^\n]*", tail)
    return found.group(0).strip() if found else None


@pytest.mark.unit
@pytest.mark.parametrize("column", sorted(_SET_VALUED_COLUMNS))
def test_a_set_valued_predicate_keeps_the_root_ceiling_at_the_request_end(column):
    """The ceiling may not truncate a set the page predicate is computed from.

    Narrowing the root scan to the cursor drops the roots above it, which
    changes what ``uniqExact``/``sum``/``dateDiff``/``argMax`` return for a
    session whose rank is unaffected - so the session fails the HAVING and
    never enters the statement at all. The floor still moves: this withholds
    the ceiling, it does not switch slicing off.
    """

    builder = _root_set_builder(column)
    floor = END - timedelta(hours=SPARSE_HOURS * 2)
    sql, params = builder.build_candidate_cursor_page_query(
        before_start_time=ROOT_SET_CURSOR,
        before_session_id=_sid(0),
        scan_start_time=floor,
    )

    assert _sessions_having(sql) is not None, "this shape must carry the HAVING"
    for name in _ROOT_CTES:
        assert _ceiling_of(_cte(sql, name), params) == END, name
        assert _floor_of(_cte(sql, name), params) == floor, name
    assert params["candidate_root_scan_end_us"] == params["end_date_us"]
    assert builder.page_admission_reads_the_root_set() is True


@pytest.mark.unit
def test_an_org_scope_page_keeps_the_root_ceiling_at_the_request_end():
    """``uniqExact(project_id)`` is the same shape with its test in Python.

    The view refuses a page whose candidate reports more than one project,
    because a reused session UUID must never merge two projects' data. That
    count is an aggregate over the root set and the published row carries the
    SLICE's value, so a ceiling that hides the second project's roots turns a
    refusal into a published page.
    """

    builder = SessionListQueryBuilderV2(
        project_ids=[PROJECT, str(UUID(int=2))],
        filters=_filters(),
        page_number=0,
        page_size=2,
        bounded_internal_scan=True,
    )
    floor = END - timedelta(hours=SPARSE_HOURS * 2)
    sql, params = builder.build_candidate_cursor_page_query(
        before_start_time=ROOT_SET_CURSOR,
        before_session_id=_sid(0),
        scan_start_time=floor,
    )

    assert "candidate_session_project_counts" in sql
    for name in _ROOT_CTES:
        assert _ceiling_of(_cte(sql, name), params) == END, name
    assert params["candidate_root_scan_end_us"] == params["end_date_us"]
    assert builder.page_admission_reads_the_root_set() is True


@pytest.mark.unit
def test_a_page_that_only_ranks_by_roots_still_narrows_the_ceiling():
    """The withholding is conditional, and these shapes are the condition.

    Nothing in them reduces the root set to a value, so a root the ceiling
    hides changes no answer: the date-only page and the attribute page keep
    the narrowed ceiling and the cost it buys.
    """

    for builder in (_builder(), _membership_builder("attribute")):
        _sql, params = builder.build_candidate_cursor_page_query(
            before_start_time=ROOT_SET_CURSOR,
            before_session_id=_sid(0),
            scan_start_time=END - timedelta(hours=SPARSE_HOURS * 2),
        )
        assert params["candidate_root_scan_end_us"] == (
            params["cursor_before_start_us"] + 1
        )
        assert builder.page_admission_reads_the_root_set() is False


class _RootSetServer(_Server):
    """A CH double whose admission is computed from the roots it can SEE.

    Each session is a set of live root instants. This double reads the ROOT
    scan's own window out of the rendered statement - never a window the test
    assumes - keeps the roots inside it, and admits a session only when more
    than ``MIN_TRACES`` survive, which is the ``traces_count`` HAVING the
    statement carries. ``session_start`` is the minimum of the survivors and
    the keyset is applied to that, in that order, exactly as the statement
    does. Every session here satisfies the attribute filter: round two put
    membership evidence on the request window, and this case is about the set.

    ``build_filter_match_query`` re-applies the same HAVING over the whole
    request window, which ``_Server._match`` models by carrying only the
    sessions whose FULL root set admits them.
    """

    def __init__(self, *, roots, **kwargs):
        self.roots = roots
        self.root_window: tuple[datetime, datetime] | None = None
        admitted = {
            sid: min(instants)
            for sid, instants in roots.items()
            if len(instants) > MIN_TRACES
        }
        super().__init__(
            rows=[
                {"session_id": sid, "session_start": start}
                for sid, start in sorted(
                    admitted.items(), key=lambda item: item[1], reverse=True
                )
            ],
            full_state=admitted,
            **kwargs,
        )
        self._sql = ""

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "AS remaining_count" in query:
            self._sql = query
        return super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )

    def _slice(self, params):
        window = _cte(self._sql, "candidate_root_identities")
        floor, ceiling = _floor_of(window, params), _ceiling_of(window, params)
        self.root_window = (floor, ceiling)
        cursor = _moment(params["cursor_before_start_us"])
        found = []
        for sid, instants in self.roots.items():
            visible = [at for at in instants if floor <= at < ceiling]
            # The HAVING, over the root set this scan can see.
            if len(visible) <= MIN_TRACES:
                continue
            start = min(visible)
            # The keyset, on the start this scan computes.
            if start >= cursor:
                continue
            found.append({"session_id": sid, "session_start": start})
        found.sort(key=lambda row: row["session_start"], reverse=True)
        return SimpleNamespace(
            data=[{**row, "remaining_count": len(found)} for row in found],
            columns=["session_id", "session_start", "remaining_count"],
        )


@pytest.mark.unit
def test_a_session_whose_traces_straddle_the_cursor_is_not_displaced():
    """The page row a ceiling-bounded aggregate silently loses.

    ``_sid(0)`` has three live roots - one ten hours below the cursor and two
    above it - so it has three traces, passes ``traces_count > 2``, starts
    above the floor and below the cursor, and belongs at the TOP of this page.
    A root scan capped at the cursor sees one of those roots, counts one trace,
    fails the HAVING and never discovers it. Every candidate such a scan does
    discover survives the gate with an unchanged start, so the page publishes
    the two rows BELOW the one it lost.
    """

    below_cursor = ROOT_SET_CURSOR - timedelta(hours=10)
    roots = {
        _sid(0): [
            below_cursor,
            ROOT_SET_CURSOR + timedelta(hours=2),
            ROOT_SET_CURSOR + timedelta(hours=3),
        ],
        **{
            _sid(index): [END - timedelta(hours=base + offset) for offset in range(3)]
            for index, base in enumerate((120, 140, 160), start=1)
        },
    }
    server = _RootSetServer(roots=roots)
    page = _read(
        _root_set_builder(page_size=2),
        server,
        before_start_time=ROOT_SET_CURSOR,
        before_session_id=_sid(9),
    )

    floor, ceiling = server.root_window
    # Guard the guard: the floor must reach the below-cursor root, or this
    # would be pinning the floor rather than the ceiling.
    assert floor <= below_cursor, "the width search must reach the lost root"
    assert [row["session_id"] for row in page.rows] == [_sid(0), _sid(1)]
    assert page.has_more is True
    assert ceiling == END
