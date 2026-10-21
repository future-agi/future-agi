"""Exact Session-list cursor pages from a bounded slice of the request window.

WHY THIS EXISTS. The candidate cursor statement replays latest physical state
across the WHOLE requested window: it builds the window's root identity set,
then probes every row in the window against it and aggregates the survivors.
Measured on production against one tenant's thirty-day window, that statement
did not return - it was still reading at 35 s, having covered 200.3M rows /
20.9 GB - while the product's request wall is 30 s, so the page is a 503 rather
than a slow page. The scan is not the cost: a bare count over the same window
is 2.85 s. The latest-state replay is, and the only lever that moves it is the
number of rows it considers. The identical statement with its scanned window
narrowed to the newest nine days of the same request returned a full page in
605 ms.

WHY A SLICE IS NOT EXACT ON ITS OWN. ``session_start`` is the earliest live
root a session has inside the scanned window. Raise that window's floor to T
and a session whose true first root lies below T keeps only the roots at or
above it, so its start is INFLATED - and an inflated start can rank anywhere,
including above rows that genuinely belong on the page. Production shows this
is not a corner case: of the 26 candidates the nine-day slice returned, the
NEWEST one truly started twenty-two days earlier. Checking the rows that came
back cannot detect this, because the row that is wrong is the row you are
looking at, and the row it displaced is one you never saw.

WHAT THE SLICE MAY BOUND. Discovery turns on two pieces of evidence and only
one of them is a root: a live root fixes where a session RANKS, and a
membership span - an end-user id, a scalar attribute, the raw witness - proves
it matches the FILTER at all, and any span in the session may carry that. The
floor is therefore raised under the root scan alone
(``candidate_root_scan_start_us``); membership evidence is gathered over the
whole request window. That asymmetry is not tidiness, it is what rule (2)
below rests on. A reviewer found the earlier version of this module raising the
floor under both: on the ``end_user_id`` and scalar-attribute shapes a session
whose root sat INSIDE the slice went undiscovered because its membership span
sat outside it, rule (2) then vouched for a page that had silently lost that
row, and the row below it was published in its place. Root evidence is safe to
bound by exactly the inference that fails for membership evidence: a root the
slice cannot see is a root below T.

WHEN THERE IS NO ROOT SCAN TO BOUND, THE SLICE IS NOT ISSUED. The user-detail
page with a scalar-attribute filter proves membership and root-ness from one
all-span replay seeded by the user's own sessions; its builder emits no root
CTE and its statement never reads the floor. Sliced or not, it is the same
text over the same rows, so this module asks the builder first
(``candidate_slice_narrows_root_scan``) and issues that statement once,
unsliced - which is exact by construction - instead of costing and widening
a root scan that does not exist.

WHAT THE SLICE MAY BOUND AT THE CEILING IS NARROWER STILL. A continuation also
caps the root scan at the cursor instant, and a second reviewer found the
sibling defect there. Some page predicates are a function of the root SET
rather than of root existence - ``traces_count``, ``duration``, ``total_cost``,
``total_tokens``, the ``first_message``/``last_message`` argMin/argMax, and the
org-scope project-count collision guard. Truncating the set ABOVE the cursor
changes such a predicate's VALUE while leaving the session's rank alone: roots
at cursor-10h, cursor+2h and cursor+3h are three traces to ``traces_count > 2``
and one to the capped relation, so the session fails the HAVING, never enters
the statement, and its true start sits above T and below the cursor - exactly
where rule (2) says nothing can hide. The FLOOR is immune to this by the same
inference as before: a set the floor truncates belongs to a session with a root
below T. So the builder withholds the ceiling whenever such a predicate is
present (``page_admission_reads_the_root_set``), and the ceiling is a cost
lever, not a premise - a session it hides has no root in [T, cursor] at all,
hence a root below T, hence rule (2).

THE RULE THIS MODULE IMPLEMENTS. Discovery in the slice, then re-resolution of
those candidates against their full state over the whole request window, then a
gate:

    a candidate whose re-resolved start EQUALS its slice start has no root
    below T, so its slice key is already its true key;

    every session the slice did NOT discover has no LIVE ROOT inside it -
    membership is decided over the whole window, and so is every predicate
    computed from the root SET, so nothing else can keep a session out of the
    slice - and its true start is therefore below T, hence below every such
    candidate;

    every discovered session outside the fetched prefix has a slice key below
    the prefix's lowest, and a true key no higher than its own slice key, hence
    also below every member of the page;

    so once ``page_size`` candidates survive with an unchanged start, the first
    ``page_size`` of them IN THE SLICE'S OWN ORDER are exactly the page.

Fewer than ``page_size`` survivors proves nothing about what lies below the
floor, so the floor is lowered and the whole thing runs again; at the request
start the statement is the unsliced one and is exact by construction. This page
therefore degrades to today's statement in the worst case and is never worse
than it.

The gate compares starts rather than re-sorting in Python on purpose: the
published order's tie-break is ClickHouse's own ``session_id`` collation, and a
survivor's slice key already IS its true key, so the server's ordering is
carried through untouched.

WIDTH. A slice's cost tracks the rows it contains, and on a real tenant that
varies by two orders of magnitude between adjacent days, so the floor cannot be
a duration. Every candidate width is costed first against an index-only
``EXPLAIN ESTIMATE`` - one row, 54 bytes measured - and the widest width inside
the row budget is the one issued.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from tracer.selectors.filter_seed_width import (
    EMPTY_DENSITY_ESTIMATE,
    EmptyDensityEstimate,
)
from tracer.services.clickhouse.query_builders.filter_seed_witness import floor_hour
from tracer.services.clickhouse.read_budget import ReadDeadline

# The rows one candidate slice may knowingly select, stated on the DENSITY
# PROBE's scale rather than the statement's. Production, one tenant, one
# request: the probe estimated 649,044 rows for the width whose statement then
# read 2,245,046 and returned a full page in 605 ms - the statement inlines its
# CTEs and touches the window about four times. One million index rows is
# therefore roughly three and a half million statement rows, a slice the
# measured replay rate finishes inside a second.
#
# This budget bounds the ROOT scan and nothing else. On a shape whose filter
# membership is proved by spans rather than roots, the same statement also
# reads the request window for that evidence, so its cost is the unsliced
# membership scan plus a bounded root scan - never more than the unsliced
# statement, but the widening loop re-issues it once per attempt, so a page
# that widens pays that membership scan again.
#
# One shape has no root scan for the floor to bound at all: the user-detail
# page with a scalar-attribute filter, whose builder fuses root-ness into the
# all-span replay its user's sessions seed. Measured on the high-volume tenant
# through the view's entry point at three, six and twelve months, that page
# issued eleven or twelve density probes and two or three candidate
# statements - every "slice" byte-identical to the unsliced statement and
# reading exactly its rows, 7.1-7.4 M per statement - for 4.3-6.9 s of page
# wall, where the unsliced statement alone took 1.3-1.8 s warm and 1.4-2.8 s
# as the first statement of a page. ``_slicing_is_available`` now asks the
# builder whether the floor narrows anything before the first probe
# (``candidate_slice_narrows_root_scan``) and reads the window whole when it
# does not.
#
# The same reservation applies to a continuation whose page predicate reads the
# root SET: the probe below costs [floor, cursor] while the statement then
# reads [floor, request end], so the approved width is optimistic for those
# shapes by however much of the window sits above the cursor. Still bounded
# above by the unsliced statement, so it is a cost gap and not a correctness
# one, and it is unmeasured.
_CANDIDATE_SLICE_TARGET_INDEX_ROWS = 1_000_000
# The narrowest width the search considers, and the unit the primary key's
# ``toStartOfHour(start_time)`` component prunes on.
_MIN_SLICE_HOURS = 1
# Probes per width decision. The search is geometric on whole hours, and the
# crossing it hunts can be a few hours wide: measured on one tenant, the page's
# whole answer sat inside a forty-minute burst, and a width three hours short of
# it returned one session. Bracketing a YEAR-wide request down to that costs
# about six halvings of the log range, so the cap is set where the search
# actually converges rather than where it merely gets close. Each probe reads
# the primary index - one row, 54 bytes measured - so the cost of the cap is
# round trips, not server work.
_MAX_DENSITY_PROBES = 7
# Slice statements before this lane stops narrowing and issues the unsliced
# statement, which is exactly what ships today.
_MAX_SLICE_ATTEMPTS = 4
_DENSITY_PROBE_MAX_RESULT_ROWS = 256
# ``build_filter_match_query`` refuses a batch wider than this, and a page this
# lane cannot verify is a page it must not narrow.
_MAX_VERIFIABLE_CANDIDATES = 200


class _QueryExecutor(Protocol):
    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> Any: ...


class CandidateSliceBuilder(Protocol):
    """The Session-list builder methods this lane issues its statements from.

    ``SessionListQueryBuilderV2`` is the one implementation. Some members are
    still looked up defensively with ``getattr``:

    * the density pair and ``supports_bounded_filter_scan``, in
      ``_slicing_is_available``: a builder that cannot answer them is read
      whole;
    * ``candidate_slice_narrows_root_scan() -> bool``, also in
      ``_slicing_is_available``, is optional and so not declared below: a
      builder that does not answer it is taken to narrow;
    * ``recommended_filter_classify_batch_size``, in ``_classify_batch_size``:
      without it the candidates are classified in one batch.
    """

    page_size: int
    filters: list[dict]

    def parse_time_range(
        self, filters: list[dict]
    ) -> tuple[datetime | None, datetime | None]: ...

    def build_candidate_cursor_page_query(
        self,
        *,
        before_start_time: datetime | None = None,
        before_session_id: str | None = None,
        scan_start_time: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]: ...

    def build_filter_match_query(
        self, candidate_ids: list[str]
    ) -> tuple[str, dict[str, Any]]: ...

    def build_candidate_slice_density_probe_query(
        self, *, slice_start: datetime, slice_end: datetime
    ) -> tuple[str, dict[str, Any]]: ...

    def candidate_slice_density_estimate(
        self,
        rows: Iterable[Mapping[str, Any]],
        columns: Iterable[str] | None = None,
    ) -> int | EmptyDensityEstimate | None: ...

    def supports_bounded_filter_scan(self) -> bool: ...

    def recommended_filter_classify_batch_size(self) -> int: ...


@dataclass(frozen=True)
class SessionCandidateSlicePage:
    """One exact cursor page, plus what it cost to prove it.

    ``rows`` are the published candidates in the server's own order.
    ``has_more`` follows today's rule: the discovery statement returned more
    candidates than the page holds. When ``slice_start`` is ``None``,
    ``remaining_count`` is the unsliced statement's own ``count() OVER()`` and
    is exact. Otherwise it is a LOWER BOUND, the same contract the bounded
    filter route publishes, and it counts only the candidates the full-window
    verifier proved: a sliced statement's ``count() OVER()`` counts sessions
    whose TRUNCATED root set passes a set-valued predicate such as
    ``traces_count = 1``, and full state can reject any number of them, so
    that count is not a bound on anything.
    """

    rows: list[dict[str, Any]]
    has_more: bool
    remaining_count: int
    slice_start: datetime | None
    statement_count: int


@dataclass(frozen=True)
class _Survivors:
    rows: list[dict[str, Any]]
    deepest_displaced: datetime | None
    statement_count: int


def read_candidate_slice_page(
    *,
    builder: CandidateSliceBuilder,
    analytics: _QueryExecutor,
    deadline: ReadDeadline,
    read_settings: Callable[[int], dict[str, Any]],
    query_timeout_ms: int,
    before_start_time: datetime | None = None,
    before_session_id: str | None = None,
) -> SessionCandidateSlicePage:
    """Return one exact candidate cursor page, narrowing the scan when it can.

    Every statement issued here is the builder's own: discovery is
    ``build_candidate_cursor_page_query`` with a raised floor and otherwise
    identical text, the verifier is ``build_filter_match_query``, and the width
    search reads only the primary index. Nothing here decides membership; the
    gate in this module's docstring does.
    """

    page_size = int(builder.page_size)
    request_start, request_end = builder.parse_time_range(builder.filters)
    if request_start is None or request_end is None:
        # The Session-list builder always resolves a finite window; this names
        # the assumption every width decision below already makes.
        raise ValueError("candidate slice page requires a bounded request window")
    # The cursor's keyset instant and the parsed request bounds do not have to
    # agree about tzinfo, and every width decision below is arithmetic on both.
    scan_end = _aligned(before_start_time, request_start) or request_end
    reader = _SliceReader(
        builder=builder,
        analytics=analytics,
        deadline=deadline,
        read_settings=read_settings,
        query_timeout_ms=query_timeout_ms,
        page_size=page_size,
        before_start_time=before_start_time,
        before_session_id=before_session_id,
        request_start=request_start,
        scan_end=scan_end,
    )
    floor = (
        reader.initial_floor() if _slicing_is_available(builder, page_size) else None
    )

    attempt = 0
    while True:
        attempt += 1
        rows, count = reader.slice_page(floor)
        if floor is None:
            # The unsliced statement. ``session_start`` is already the whole
            # window's minimum, so the server's order is the published order.
            return SessionCandidateSlicePage(
                rows=rows[:page_size],
                has_more=len(rows) > page_size,
                remaining_count=count,
                slice_start=None,
                statement_count=reader.statements,
            )
        # A slice holding no more candidates than a page cannot settle the page
        # whatever they resolve to - what is below the floor is unseen - so it
        # widens without paying for a full-window verifier that cannot help.
        survivors = (
            reader.resolve(rows)
            if len(rows) > page_size
            else _Survivors(rows=[], deepest_displaced=None, statement_count=0)
        )
        if len(survivors.rows) >= page_size:
            return SessionCandidateSlicePage(
                rows=survivors.rows[:page_size],
                has_more=len(rows) > page_size,
                remaining_count=len(survivors.rows),
                slice_start=floor,
                statement_count=reader.statements,
            )
        # Fewer survivors than a page. Sessions below the floor are unseen and
        # unordered against each other, so nothing here can be published yet.
        floor = (
            None
            if attempt >= _MAX_SLICE_ATTEMPTS
            else reader.widened_floor(floor, survivors.deepest_displaced)
        )


class _SliceReader:
    """The statements one candidate page may issue, and their bookkeeping."""

    def __init__(
        self,
        *,
        builder: CandidateSliceBuilder,
        analytics: _QueryExecutor,
        deadline: ReadDeadline,
        read_settings: Callable[[int], dict[str, Any]],
        query_timeout_ms: int,
        page_size: int,
        before_start_time: datetime | None,
        before_session_id: str | None,
        request_start: datetime,
        scan_end: datetime,
    ) -> None:
        self._builder = builder
        self._analytics = analytics
        self._deadline = deadline
        self._read_settings = read_settings
        self._query_timeout_ms = query_timeout_ms
        self._page_size = page_size
        self._before_start_time = before_start_time
        self._before_session_id = before_session_id
        self._request_start = request_start
        self._scan_end = scan_end
        self.statements = 0
        # One width's estimate never changes inside one page read, and the
        # widening search revisits the interval the first search bracketed.
        self._probed: dict[int, int | None] = {}
        # The narrowest width this read has seen exceed the budget. Every later
        # search is bounded by it, so a short slice re-searches a bracket that
        # is already tight instead of starting again from a fresh doubling.
        self._refused_hours: int | None = None

    # -- statements ------------------------------------------------------

    def _execute(self, query: str, params: dict[str, Any], max_result_rows: int) -> Any:
        self.statements += 1
        return self._analytics.execute_ch_query(
            query,
            params,
            timeout_ms=self._deadline.remaining_ms(self._query_timeout_ms),
            settings=self._read_settings(max_result_rows),
        )

    def slice_page(self, floor: datetime | None) -> tuple[list[dict[str, Any]], int]:
        query, params = self._builder.build_candidate_cursor_page_query(
            before_start_time=self._before_start_time,
            before_session_id=self._before_session_id,
            scan_start_time=floor,
        )
        result = self._execute(query, params, self._page_size + 1)
        rows = list(result.data or [])
        count = int(rows[0].get("remaining_count", 0) or 0) if rows else 0
        return rows, count

    def probe_hours(self, hours: int) -> int | None:
        """Estimated rows in the newest ``hours``; ``None`` when unreadable."""
        if hours in self._probed:
            return self._probed[hours]
        counted = self._probe_rows(self._floor_for_hours(hours))
        self._probed[hours] = counted
        if counted is not None and counted > _CANDIDATE_SLICE_TARGET_INDEX_ROWS:
            self._refused_hours = min(self._refused_hours or hours, hours)
        return counted

    def _probe_rows(self, floor: datetime) -> int | None:
        query, params = self._builder.build_candidate_slice_density_probe_query(
            slice_start=floor, slice_end=self._scan_end
        )
        result = self._execute(query, params, _DENSITY_PROBE_MAX_RESULT_ROWS)
        estimate = self._builder.candidate_slice_density_estimate(
            result.data or (), getattr(result, "columns", None)
        )
        if estimate is EMPTY_DENSITY_ESTIMATE:
            # An empty estimate reads as a genuine zero only because the
            # full-window probe ran first and proved this statement's result
            # shape readable for this request; a narrower key condition of the
            # identical statement naming no part is then real emptiness.
            return 0
        return estimate if isinstance(estimate, int) else None

    def resolve(self, rows: list[dict[str, Any]]) -> _Survivors:
        """Re-resolve the slice's candidates against the whole request window.

        Returns the candidates whose start is unchanged, in the slice's own
        order, and the earliest true start among those that moved - the moment
        a wider slice has to reach before those candidates can be trusted.
        """

        identities = [
            str(row.get("session_id") or "") for row in rows if row.get("session_id")
        ]
        if not identities:
            return _Survivors(rows=[], deepest_displaced=None, statement_count=0)
        before = self.statements
        batch = _classify_batch_size(self._builder, len(identities))
        resolved: dict[str, Any] = {}
        for offset in range(0, len(identities), batch):
            chunk = identities[offset : offset + batch]
            query, params = self._builder.build_filter_match_query(chunk)
            if not query:
                continue
            result = self._execute(query, params, len(chunk))
            for row in result.data or []:
                key = str(row.get("session_id") or "")
                if key:
                    resolved[key] = row.get("start_time")
        unchanged: list[dict[str, Any]] = []
        deepest: datetime | None = None
        for row in rows:
            true_start = resolved.get(str(row.get("session_id") or ""))
            if true_start is not None and true_start == row.get("session_start"):
                unchanged.append(row)
                continue
            # A candidate that moved, or that full state no longer places in
            # the window at all, is not publishable from this slice. Only a
            # moved one tells the widener where it has to reach.
            if true_start is not None and (deepest is None or true_start < deepest):
                deepest = true_start
        return _Survivors(
            rows=unchanged,
            deepest_displaced=deepest,
            statement_count=self.statements - before,
        )

    # -- width -----------------------------------------------------------

    def _window_hours(self) -> int:
        span = self._scan_end - self._request_start
        return max(_MIN_SLICE_HOURS, int(span / timedelta(hours=1)))

    def _floor_for_hours(self, hours: int) -> datetime:
        return max(
            self._request_start,
            floor_hour(self._scan_end - timedelta(hours=hours)),
        )

    def initial_floor(self) -> datetime | None:
        """The widest newest-anchored slice this request's rows fit inside.

        ``None`` means do not narrow at all - the whole window already fits the
        budget, or the probe could not answer and this lane refuses to guess.
        """

        window_hours = self._window_hours()
        if window_hours <= _MIN_SLICE_HOURS:
            return None
        return self._approved_floor(lo_hours=0, hi_hours=window_hours)

    def _approved_floor(self, *, lo_hours: int, hi_hours: int) -> datetime | None:
        """Bracket the row budget geometrically between two widths.

        ``lo_hours`` is a width already known cheap (zero at the start of a
        read); ``hi_hours`` is the proposal. ``None`` means either the whole
        proposal fits - so there is nothing to narrow - or the probe could not
        answer, which this lane reads as "do not narrow blind".
        """

        if self._refused_hours is not None:
            # Nothing at or above this width fits, and the estimate is monotone
            # in the width, so there is nothing to learn above it.
            hi_hours = min(hi_hours, self._refused_hours)
            if hi_hours <= lo_hours:
                return self._floor_for_hours(lo_hours) if lo_hours else None
        probed = self.probe_hours(hi_hours)
        if probed is None:
            return None
        if probed <= _CANDIDATE_SLICE_TARGET_INDEX_ROWS:
            return (
                None
                if hi_hours >= self._window_hours()
                else self._floor_for_hours(hi_hours)
            )
        for _ in range(_MAX_DENSITY_PROBES - 1):
            mid = int(math.isqrt(max(1, lo_hours) * hi_hours))
            if mid <= lo_hours or mid >= hi_hours:
                break
            probed = self.probe_hours(mid)
            if probed is None:
                break
            if probed <= _CANDIDATE_SLICE_TARGET_INDEX_ROWS:
                lo_hours = mid
            else:
                hi_hours = mid
        if lo_hours <= 0:
            # Nothing narrower than the proposal fits the budget. Issue the
            # proposal: it is still a subset of what the unsliced statement
            # reads, so it cannot be the worse of the two, and the request's
            # own wall deadline remains the ceiling.
            lo_hours = hi_hours
        return (
            None
            if lo_hours >= self._window_hours()
            else self._floor_for_hours(lo_hours)
        )

    def widened_floor(
        self, floor: datetime, deepest: datetime | None
    ) -> datetime | None:
        """Lower the floor for another round; ``None`` to read the window whole.

        Doubling alone bounds the number of rounds. A displaced candidate says
        something stronger - exactly how far back the slice must reach before
        THAT candidate resolves inside it - so the proposal takes whichever of
        the two is older, and the row budget then costs it before it is issued.
        """

        doubled = self._scan_end - 2 * (self._scan_end - floor)
        deepest = _aligned(deepest, self._scan_end)
        proposed = min(doubled, floor_hour(deepest)) if deepest else doubled
        if proposed <= self._request_start:
            return None
        proposed_hours = max(
            _MIN_SLICE_HOURS, int((self._scan_end - proposed) / timedelta(hours=1))
        )
        current_hours = max(
            _MIN_SLICE_HOURS, int((self._scan_end - floor) / timedelta(hours=1))
        )
        widened = self._approved_floor(lo_hours=current_hours, hi_hours=proposed_hours)
        if widened is not None and widened >= floor:
            # The budget approves nothing OLDER than the slice just read, so
            # another round would issue the same statement and learn the same
            # thing. Stop narrowing and read the window whole - today's
            # behaviour - instead of spending the attempt budget on repeats.
            return None
        return widened


def _aligned(moment: datetime | None, reference: datetime) -> datetime | None:
    """Put ``moment`` on the same naive/aware footing as ``reference``.

    Request bounds are parsed from the filter payload while resolved starts
    come back from the driver, and the two do not have to agree about tzinfo.
    Comparing them without this raises rather than widening.
    """

    if moment is None:
        return None
    if (moment.tzinfo is None) == (reference.tzinfo is None):
        return moment
    if reference.tzinfo is None:
        offset = moment.utcoffset() or timedelta(0)
        return (moment - offset).replace(tzinfo=None)
    return moment.replace(tzinfo=reference.tzinfo)


def _classify_batch_size(builder: CandidateSliceBuilder, candidates: int) -> int:
    recommended = getattr(builder, "recommended_filter_classify_batch_size", None)
    size = recommended() if callable(recommended) else None
    return max(1, int(size)) if size else max(1, candidates)


def _slicing_is_available(builder: CandidateSliceBuilder, page_size: int) -> bool:
    """Whether this request can be narrowed, verified, and narrowed to effect.

    All three are required. Without the full-state verifier a narrowed scan
    would publish inflated starts, so a builder that cannot run one reads the
    window whole, exactly as it does today. And a statement the floor does not
    reach - the builder says so through ``candidate_slice_narrows_root_scan``
    - would make every probe a cost for a root scan the statement never
    issues and every slice the unsliced statement under another name, so it
    reads the window whole too, once. A builder that does not answer the
    question is taken to narrow, which is every builder this lane had before
    the question existed.
    """

    probe = getattr(builder, "build_candidate_slice_density_probe_query", None)
    estimate = getattr(builder, "candidate_slice_density_estimate", None)
    supports_scan = getattr(builder, "supports_bounded_filter_scan", None)
    narrows = getattr(builder, "candidate_slice_narrows_root_scan", None)
    return bool(
        callable(probe)
        and callable(estimate)
        and callable(supports_scan)
        and supports_scan()
        and page_size + 1 <= _MAX_VERIFIABLE_CANDIDATES
        and (not callable(narrows) or narrows())
    )


__all__ = [
    "CandidateSliceBuilder",
    "SessionCandidateSlicePage",
    "read_candidate_slice_page",
]
