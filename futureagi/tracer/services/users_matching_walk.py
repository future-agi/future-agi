"""Span-attribute-filtered Users pages, ordered by newest matching activity.

The seeded candidate statement decides an attribute-filtered page by
aggregating the whole window; on the largest tenants it materialises two
planning-time sets over the sorting key and dies before it starts. This walk
replaces it for one scalar span-attribute filter - plain-text
``equals``/``in``, boolean ``equals``/``in``, or a number comparison - when
that filter is the only item on its key:

* discover: one bounded statement per time slice, newest-first, through the
  deployed key and value blooms, grouped by the RAW user id the span carries
  (``build_matching_activity_slice_query``; a raw superset, never a result),
  then, only for a populated slice, one bounded survivor statement over
  exactly the ids returned (``build_dimension_survivor_query``) that resolves
  each raw id to its user and attaches every alias of that user;
* certify: the page's existing attribute enrichment, which also returns the
  user's newest LIVE span whose LATEST value matches - the order key;
* materialise: the existing finite per-user replay, only for users that are
  attribute members AND publishable by position, which decides curated
  presence, search and every native/relation predicate over the whole window
  (``_row_matches_filters``, unchanged), and carries the set-valued totals.

Coverage floor. A truncated slice proves nothing at or below the newest
witness of its last raw id; a user is published only once its certified key
lies strictly above every undecided user's possible key (the slice floor, or
the newest witness of the next uncertified batch). Users whose key is at or
below that line are carried to a later slice, where their own newest matching
row rediscovers them. Moving the LIMIT from resolved users to raw ids keeps
that rule: every raw id with a witnessed row above the floor is returned, so
every user with such a row is represented and its newest witness is exact.

Empty tail, costed. After an untruncated empty slice, when the rest of the
window needs more slices at the cap than the statement budget has left, the
walk may prove the whole tail empty in one existence statement
(``build_matching_activity_existence_query``, the newest row through the same
blooms) instead of one slice per day. That statement is wider than the slice
cap, and nothing on the application read path bounds a statement's rows,
bytes or time, so it is COSTED FIRST: ``EXPLAIN ESTIMATE`` of the identical
text (``build_matching_activity_existence_estimate_query``, index marks only,
no column data) reports the rows the blooms leave in the tail, and the
existence statement is issued only when that count fits
``USER_LIST_WALK_PROBE_TARGET_READ_ROWS``. The estimate is not free: it is the
existence statement's own index analysis, whose cost is the index granules
it reads (parts times marks, times cache state), so it runs under the SAME
read settings as the statement it costs - threads included - and the pair
shares one budget, ``USER_LIST_WALK_PROBE_WALL_MS`` within the page wall:
the existence statement, which repeats that analysis before it reads a row,
is issued only when the estimate's observed time fits what the probe budget
has left. An estimate over the target, over its time, or one the walk cannot
read, licenses nothing: the walk slices at the cap. The estimate never
decides coverage - only the existence statement's own answer does: none
proves the tail exhausted by the same rule an empty slice uses; a row is the
tail's newest witnessed row, so the same rule proves the range above it empty,
and the walk resumes just above it instead of slicing down to it a day at a
time (a six-month window whose newest match lay 79 days back spent four
requests, each an empty checkpoint, before its first rows). The pair is asked
at most once per page and once more after each populated slice, never twice
in a row.

Budget. The walk owns a wall (``USER_LIST_PAGE_WALL_MS``) and a statement
budget (``USER_LIST_WALK_MAX_STATEMENTS``, never less than one batch's
decision: ``_statement_budget``). On exhaustion it returns the users certified
so far, in order, with a cursor; it never falls back to the whole-window
statement. A slice that fails on a read budget is retried narrower, never
wider. Until a request decides something, its search is admitted against the
analytics wall rather than the page wall (``_admission_deadline``), a slice
the server stops at its cap is retried narrower, and when it cannot be, the
head-of-line slice is read once without a cap and its first batch decided
with no deadline (``_read_slice``). The finishing statements run past the
page wall under a server cap (``_finish_deadline``); a batch the server stops
is replayed one user at a time, and a page that has published nothing decides
a user whose replay the server stopped without the cap, once
(``_materialise``).

Time splits. A batch's attribute read (``_certify``) is never split in time.
When the server stops it on a read budget (``is_read_budget_error`` other than
the request's own wall: memory, row and time limits, cancellation, overload, a
socket timeout), its head-of-line user is read alone, charged as a second
enrichment (``_head_statements`` reserves both), and the rest of the request
certifies one user at a time; the request's wall (or a transport timeout the
service reports as one) stops it outright. Only the
head-of-line user of a request that has decided nothing may have its read
split in time (``UsersListManager._read_span_attributes``): one user a
request, up to
``2 ** (ceil(log2(window / _USER_LIST_ATTRIBUTE_MIN_BUCKET)) + 1) - 1``
statements (4,095 over 24 h) per enrichment statement, outside the statement
budget. The split has no deadline after the uncapped slice, or once
the analytics wall is already spent, and otherwise runs against what is left
of it (``_admission_deadline``). One stall is known and left open: a split
that starts with some of the wall left and outlasts it stops every request at
that user, having decided no one. Any other user whose read runs out of a read
budget stops the request (``read_budget``), and so does a batch the page wall
refuses (``wall``), with the coverage just above the refused batch's first
candidate: the refused user, unless an already-decided user precedes it. The
next request decides that first candidate first, and the refused user after
it. A refusal by the statement count keeps the boundary it had.
A request that has decided something certifies a batch only when the
statements left also pay one materialisation.

Tied instant. Inside one timestamp the slice's raw id order is not the
page's resolved id order (an alias may resolve to a survivor on either side of
it), so no user at a truncated floor is publishable until every raw id at that
instant has been seen. When a truncated slice returns only rows of its floor's
instant, or a cursor says an instant is open, the walk decides that instant on
its own: ``build_matching_activity_instant_query`` returns the RESOLVED users
witnessed at exactly that instant in descending id order, so each certified
batch settles a closed id range ``[last returned, before)`` and its members
publish at once in ``(key, id)`` order. A cohort larger than one request
resumes inside the instant instead of starting it again.

Cursor. ``(marker, last_key, last_id, coverage[, open_instant])``: every user
with a matching row at or after ``coverage`` is decided; the keyset ``(key,
id) < (last_key, last_id)`` under ``(key DESC, id DESC)`` rejects a
re-discovered published user at the enrichment step, before any replay, and
inside an instant it names the lowest decided position, published or not.
``open_instant`` (present only when true; a four-element cursor is read as
false) tells the next request to decide the instant just below ``coverage``
first, from ``last_id`` when ``last_key`` is that instant; a request sets it
when it ends inside an instant, or stops where a raw restart would find the
same users again (the checkpoint's comment says when). A user whose key
is at or above the coverage a request resumed from was decided before it,
published or rejected by its replay, so it is never pending again however a
lower row witnesses it, and no cursor's coverage rises above the one it
resumed from. That holds for the data as each request saw it. Between
requests: a user whose newest match moves to a key at or above a cursor's
coverage is not published by that cursor (a new first page shows it); one
whose key moves below the coverage and ahead of the keyset is published where
it now sorts; and a published user whose key moves below the coverage is
published again, the usual limit of a keyset over changing data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from django.conf import settings

from tracer.services.clickhouse.list_cursor import ListCursorError
from tracer.services.clickhouse.query_builders.user_list import (
    REQUESTED_PAGE_SESSION_METRIC_FIELDS,
    REQUESTED_PAGE_SPAN_METRIC_FIELDS,
)
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.id_remap_sql import NIL_UUID
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, the manager imports us.
    from tracer.services.users_list_manager import UserCursorRead, UsersListManager

logger = structlog.get_logger(__name__)

USER_LIST_MATCHING_CURSOR_ORDER = "matching_activity_users_v1"
USER_LIST_MATCHING_ORDERING = "latest_matching_activity"
USER_LIST_MATCHING_PROVENANCE = "matching_activity_walk"
USER_LIST_PAGE_WALL_MS = settings.USER_LIST_PAGE_WALL_MS
# Finish mode is exempt from the PAGE wall by design: a page that has already
# certified its users should publish them rather than return empty because the
# search spent the wall. Exempt from the page wall is not the same as exempt
# from every deadline -- application reads carry no server time cap unless the
# caller asks for one -- so they run under the analytics wall, measured from
# the START of the walk rather than restarted at materialisation, and ask the
# server to enforce it. Restarting it would let one page spend the page wall
# AND a whole analytics wall after it; measuring from the walk's start ends
# the finish by the analytics wall.
USER_LIST_WALK_FINISH_WALL_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
USER_LIST_WALK_MAX_STATEMENTS = settings.USER_LIST_WALK_MAX_STATEMENTS
USER_LIST_WALK_INITIAL_SLICE = timedelta(
    seconds=settings.USER_LIST_WALK_INITIAL_SLICE_SECONDS
)
USER_LIST_WALK_MAX_SLICE = timedelta(seconds=settings.USER_LIST_WALK_MAX_SLICE_SECONDS)
USER_LIST_WALK_SLICE_USER_LIMIT = settings.USER_LIST_WALK_SLICE_USER_LIMIT
# A slice that fails on a read budget is retried at a quarter of its width
# down to this floor; below it the failure propagates as a retryable error.
USER_LIST_WALK_MIN_SLICE = timedelta(seconds=settings.USER_LIST_WALK_MIN_SLICE_SECONDS)
# Users certified per enrichment statement and replayed per materialisation.
USER_LIST_WALK_CERTIFY_BATCH_SIZE = settings.USER_LIST_WALK_CERTIFY_BATCH_SIZE
# The rows a tail existence statement may knowingly read (its EXPLAIN
# ESTIMATE must fit here before it is issued).
USER_LIST_WALK_PROBE_TARGET_READ_ROWS = settings.USER_LIST_WALK_PROBE_TARGET_READ_ROWS
# The wall the estimate and the existence statement share, within the page
# wall: the estimate's own time must fit what is left of it before the
# existence statement, which repeats that index analysis, is issued.
USER_LIST_WALK_PROBE_WALL_MS = settings.USER_LIST_WALK_PROBE_WALL_MS
_TICK = timedelta(microseconds=1)


@dataclass
class _Candidate:
    end_user_id: str
    newest_witness: datetime
    alias_ids: tuple[str, ...]


@dataclass
class _Certified:
    end_user_id: str
    alias_ids: tuple[str, ...]
    # Newest live span whose latest value matches; None when the user is not
    # an attribute member (or matches only on stale versions).
    order_key: datetime | None
    materialised: bool = False
    member: bool = False
    row: dict[str, Any] | None = None
    published: bool = False


class _WalkBudget:
    """One wall and one statement budget; exhaustion is a result, not an error."""

    def __init__(self, *, wall_ms: int, max_statements: int) -> None:
        self.deadline = ReadDeadline.start(wall_ms)
        self.max_statements = int(max_statements)
        self.statements = 0
        # "statements" (sticky), "wall", or "read_budget" (``_certify``).
        self.exhausted_by: str | None = None

    def take(self, statements: int, *, finish: bool = False) -> bool:
        """Spend ``statements``; ``finish`` spends past the wall, never past the count.

        The wall bounds the search (slices and certification). Materialising
        users that are already certified and next in line is one finite replay
        per page, and a page that has found its users should publish them
        rather than return empty because the search used the whole wall.
        """
        if self.exhausted_by == "statements":
            return False
        if self.statements + statements > self.max_statements:
            self.exhausted_by = "statements"
            return False
        if not finish:
            if self.exhausted_by == "wall":
                return False
            try:
                self.deadline.remaining_ms()
            except ReadDeadlineExceeded:
                self.exhausted_by = "wall"
                return False
        self.statements += statements
        return True

    def remaining_statements(self) -> int:
        return max(self.max_statements - self.statements, 0)

    def remaining_ms(self) -> float:
        return max(self.deadline.total_ms - self.deadline.elapsed_ms(), 0.0)


@dataclass
class _WalkState:
    manager: Any
    builder: UserListQueryBuilderV2
    walked_key: str
    page_size: int
    window_start: datetime
    window_end: datetime
    frozen_filters: list[dict]
    budget: _WalkBudget
    last_key: datetime | None
    last_id: str | None
    # The coverage this request resumed from. Every user whose key is at or
    # above it was decided by an earlier request, published or rejected by
    # its replay, as the data stood then; nothing there is pending now,
    # however a lower row witnesses it, so a user whose key has since moved
    # into that range is not published by this cursor. The cursor never
    # moves back above it.
    decided_from: datetime
    certified: dict[str, _Certified] = field(default_factory=dict)
    published: list[dict[str, Any]] = field(default_factory=list)
    stopped: bool = False
    # A capped finish was stopped: later ones replay one user at a time
    # (``_materialise``), and one user whose own replay was stopped may be
    # decided without the cap, once per request.
    finish_singly: bool = False
    uncapped_finish: bool = False
    # Until this request decides something (certifies a batch, or reads a
    # slice that proves its range empty before any slice was stopped), its
    # search is admitted against the analytics wall rather than the page
    # wall, a slice the server stops is narrowed, and one head-of-line slice
    # may be read without a cap (``_admission_deadline``, ``_read_slice``).
    progress_owed: bool = True
    slice_stopped: bool = False
    slice_uncapped: bool = False
    # A batch's enrichment ran out of a read budget: later certifications
    # read one user at a time (``_certify``).
    certify_singly: bool = False
    # The tied instant being decided, the id below which it was entered, and
    # the resolved ids it returned (certified), in descending order.
    instant: datetime | None = None
    instant_after: str | None = None
    instant_ids: list[str] = field(default_factory=list)

    def keyset_admits(self, key: datetime, end_user_id: str) -> bool:
        if self.last_key is None:
            return True
        if key != self.last_key:
            return key < self.last_key
        return self.last_id is None or end_user_id < self.last_id

    def pending(
        self, boundary: datetime | tuple[datetime, str] | None
    ) -> list[_Certified]:
        """Certified members publishable now, newest first.

        A time ``boundary`` leaves undecided users at or below it; a tuple
        ``(instant, id)`` leaves them strictly below that position.
        """

        rows = [
            entry
            for entry in self.certified.values()
            if entry.order_key is not None
            and not entry.published
            and not (entry.materialised and not entry.member)
            and _clears(entry, boundary)
            and entry.order_key < self.decided_from
            and self.keyset_admits(entry.order_key, entry.end_user_id)
        ]
        rows.sort(key=lambda entry: (entry.order_key, entry.end_user_id), reverse=True)
        return rows


def _clears(
    entry: _Certified, boundary: datetime | tuple[datetime, str] | None
) -> bool:
    if boundary is None:
        return True
    if isinstance(boundary, tuple):
        return (entry.order_key, entry.end_user_id) >= boundary
    return entry.order_key > boundary


def _boundary_time(boundary: datetime | tuple[datetime, str] | None) -> datetime | None:
    return boundary[0] if isinstance(boundary, tuple) else boundary


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _enrichment_statement_count(manager: Any) -> int:
    from tracer.services.users_list_manager import _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE

    walked = getattr(manager, "_walked_typed_filter", None)
    walked_key = walked.key if walked is not None else None
    accelerated = sum(
        1
        for key in manager.attribute_keys
        if key in manager.attribute_exact_text_filters or key == walked_key
    )
    ordinary = len(manager.attribute_keys) - accelerated
    return accelerated + -(-ordinary // _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE)


def _materialisation_statement_count(manager: Any) -> int:
    """The statements one materialisation sends, whatever its batch.

    The replay; the relation statement, when relation filters are set; one
    metrics statement per group with a requested field, sessions and spans
    (``build_requested_page_metric_queries``); and the evals statement.
    """
    metrics = manager.metric_keys
    return (
        1
        + bool(manager.relation_filters)
        + bool(metrics & set(REQUESTED_PAGE_SESSION_METRIC_FIELDS))
        + bool(metrics & set(REQUESTED_PAGE_SPAN_METRIC_FIELDS))
        + bool(manager.needs_evals)
    )


def _statement_budget(manager: Any) -> int:
    """The statements a request may spend: its budget, or one decision.

    One decision is what a request needs to decide its first batch: the open
    instant, a slice and its retries a quarter as wide down to the least
    width, and the head-of-line decision (``_head_statements``), 63
    statements at the view's 100 keys. Both numbers are known before the
    first statement; only time splits fall outside them (module docstring).
    """
    return max(
        USER_LIST_WALK_MAX_STATEMENTS,
        2 + _narrowings() + _head_statements(manager),
    )


def _narrowings() -> int:
    """Retries a quarter as wide that take the first slice to the least width."""

    width, count = USER_LIST_WALK_INITIAL_SLICE, 0
    while width > USER_LIST_WALK_MIN_SLICE:
        width, count = max(USER_LIST_WALK_MIN_SLICE, width / 4), count + 1
    return count


def _head_statements(manager: Any) -> int:
    """What deciding the head-of-line slice's first batch costs, uncapped.

    The slice itself and its survivor statement; when it comes back tied at
    one instant, the instant read and its survivor statement; one batch's
    enrichment and, when that runs out of a read budget, the head-of-line
    user's own (``_certify``); and a finish with its uncapped retry.
    """
    return (
        4
        + 2 * _enrichment_statement_count(manager)
        + 2 * _materialisation_statement_count(manager)
    )


@dataclass
class _Slice:
    candidates: list[_Candidate]
    truncated: bool
    # Client-observed time of the slice statement and, for a populated slice,
    # its survivor statement: what a slice like it charges the wall.
    query_ms: float
    slice_start: datetime
    # The last RAW id returned, in ``(raw_newest DESC, raw_end_user_id DESC)``
    # order: the keyset a truncated slice continues from, and its floor.
    raw_last: tuple[datetime, str] | None
    # Every returned row shares one instant (a truncated tie).
    tied: bool = False


def _statement_ms(result: Any, started: float) -> float:
    # ``QueryResult.query_time_ms`` is the transport's client-observed time
    # around the native call; an executor without it reports nothing, so the
    # walk clocks the call itself. Either way the schedule runs on the client
    # clock: the wall it is measured against runs on that clock, and the round
    # trip is part of what a statement costs the page.
    return float(getattr(result, "query_time_ms", None) or 0.0) or (
        (time.monotonic() - started) * 1000.0
    )


def _resolve_raw_witnesses(
    raw_rows: list[tuple[str, datetime]], remap_rows: list[dict[str, Any]]
) -> list[_Candidate]:
    """Group raw witnessed ids by survivor, exactly as ``resolved_id_expr`` does.

    A raw id the survivor map does not know, or maps to the nil uuid, is its
    own user. Every alias the map holds for a returned user travels with it,
    so the certification scans all of that user's identities.
    """
    survivor_of: dict[str, str] = {}
    aliases_of: dict[str, set[str]] = {}
    for row in remap_rows:
        any_id = str(row.get("any_id") or "")
        survivor = str(row.get("survivor_id") or "")
        if not any_id or not survivor or survivor == NIL_UUID:
            continue
        survivor_of[any_id] = survivor
        aliases_of.setdefault(survivor, set()).add(any_id)
    newest: dict[str, datetime] = {}
    aliases: dict[str, set[str]] = {}
    for raw_id, raw_newest in raw_rows:
        canonical = survivor_of.get(raw_id, raw_id)
        previous = newest.get(canonical)
        if previous is None or raw_newest > previous:
            newest[canonical] = raw_newest
        aliases.setdefault(canonical, set()).add(raw_id)
    candidates = [
        _Candidate(
            canonical,
            moment,
            tuple(
                sorted(
                    aliases[canonical] | aliases_of.get(canonical, set()) | {canonical}
                )
            ),
        )
        for canonical, moment in newest.items()
    ]
    candidates.sort(key=lambda c: (c.newest_witness, c.end_user_id), reverse=True)
    return candidates


def _read_slice(
    state: _WalkState,
    *,
    slice_start: datetime,
    slice_end: datetime,
    before: tuple[datetime, str] | None,
) -> _Slice | None:
    """One slice statement, under a server cap, retried narrower when stopped.

    A populated slice is followed by the bounded survivor statement over the
    raw ids it returned. Returns ``None`` when the walk's own budget stops it
    first; a slice whose survivor statement the budget refuses is discarded
    whole, so the cursor stays at the slice's end and re-reads it.

    Every slice asks the server to stop it at half of what is left of the
    analytics wall (``_slice_cap``), and one the server stops is retried a
    quarter as wide, down to ``USER_LIST_WALK_MIN_SLICE``, if its wall
    admits the retry. A slice denser than every cap was read again by every
    request and stopped at the same place (before the cap it ran past the
    walls and was discarded). So while the request has decided nothing
    (``progress_owed``), a retry is admitted against the analytics wall and
    only while the budget still affords the head-of-line decision after it.
    When the slice cannot be narrowed or retried, or that wall is spent,
    the request reads its head-of-line slice (the least width below the
    coverage, clipped at the window start) once more without a cap, and
    decides that slice's first batch with no deadline (``_admission_deadline``):
    an exact list can neither skip that slice nor publish anyone below it
    first, so no bounded retry keeps the list both exact and moving. Once the
    request has decided something, a stopped slice it cannot narrow, or
    whose retry the page wall refuses, ends it.

    What stays unbounded. Survivor, instant, enrichment and tail-probe
    statements never carry a server cap (the application's no-abort policy):
    a wall only decides whether they start. The escape lifts even that, once
    per request, for the head-of-line decision: the uncapped slice, its
    survivor statement, the instant read and its survivor statement when the
    slice comes back tied at one instant, and one batch's enrichment (and its
    head-of-line user's alone when that fails) start with no wall at all.
    The finish's uncapped replay is not part of it: ``_materialise`` sends it
    when a page that has published nothing had its head-of-line user's
    capped replay stopped or refused, whether or not a slice was uncapped.
    The head-of-line user's read may also split in time (module docstring,
    Time splits). On a lane whose ClickHouse profile is read-only
    (``CH*_SERVER_ENFORCED_READONLY``) no setting reaches the server, so the
    slice cap is not sent there either and slices are admitted only.
    """
    from tracer.services import users_list_manager as ulm

    manager = state.manager
    # Why the next read is the head-of-line slice without a cap: the least
    # width was stopped, or the budget cannot afford a narrower retry.
    escape: str | None = None
    while True:
        owed = state.progress_owed
        if not state.budget.take(1, finish=owed):
            return None
        deadline = _admission_deadline(state)
        head = owed and (escape is not None or deadline is None)
        if head:
            if state.slice_uncapped:
                state.budget.exhausted_by = "wall"
                return None
            state.slice_uncapped = True
            slice_start = max(state.window_start, slice_end - USER_LIST_WALK_MIN_SLICE)
            logger.info(
                "users_matching_walk_uncapped_slice",
                reason=escape or "wall_spent",
                width_seconds=(slice_end - slice_start).total_seconds(),
            )
        width = slice_end - slice_start
        query, params = state.builder.build_matching_activity_slice_query(
            slice_start=slice_start,
            slice_end=slice_end,
            limit=USER_LIST_WALK_SLICE_USER_LIMIT,
            before=before,
        )
        started = time.monotonic()
        try:
            timeout_ms = None if head else deadline.remaining_ms()
        except ReadDeadlineExceeded:
            state.budget.exhausted_by = "wall"
            return None
        try:
            result = ulm.V2AnalyticsQueryService().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=ulm._page_replay_read_settings(
                    max_result_rows=USER_LIST_WALK_SLICE_USER_LIMIT
                ),
                server_execution_cap_ms=None if head else _slice_cap(state),
            )
        except ReadDeadlineExceeded:
            if head:
                state.budget.exhausted_by = "wall"
                return None
            state.slice_stopped = True
            room = not owed or (
                state.budget.remaining_statements() >= 1 + _head_statements(manager)
            )
            if width > USER_LIST_WALK_MIN_SLICE and room:
                slice_start = _narrower_start(state, slice_end, width)
            elif owed:
                escape = (
                    "no_budget"
                    if width > USER_LIST_WALK_MIN_SLICE
                    else "narrowest_stopped"
                )
            else:
                state.budget.exhausted_by = "wall"
                return None
            logger.info(
                "users_matching_walk_slice_stopped",
                width_seconds=width.total_seconds(),
                retry_width_seconds=(slice_end - slice_start).total_seconds(),
            )
            continue
        except Exception as exc:
            if not is_read_budget_error(exc) or width <= USER_LIST_WALK_MIN_SLICE:
                raise
            slice_start = _narrower_start(state, slice_end, width)
            logger.warning(
                "users_matching_walk_slice_narrowed",
                error_type=type(exc).__name__,
                width_seconds=width.total_seconds(),
                retry_width_seconds=(slice_end - slice_start).total_seconds(),
            )
            continue
        # Kept in the statement's own order: the survivor statement is bound
        # to exactly the ids returned, as returned.
        raw_rows: list[tuple[str, datetime]] = []
        for row in result.data or ():
            raw_id = str(row.get("raw_end_user_id") or "")
            newest = _utc(row.get("raw_newest"))
            if raw_id and newest is not None:
                raw_rows.append((raw_id, newest))
        ordered = sorted(raw_rows, key=lambda item: (item[1], item[0]), reverse=True)
        if not ordered and not state.slice_stopped:
            # Nothing witnessed in the range: the coverage moves past it. After
            # a stopped slice, the narrow empty ranges it leaves are too small
            # to count, and the request goes on until it decides a user.
            state.progress_owed = False
        query_ms = _statement_ms(result, started)
        remap = _read_survivors(state, [raw_id for raw_id, _newest in raw_rows])
        if remap is None:
            return None
        remap_rows, remap_ms = remap
        return _Slice(
            candidates=_resolve_raw_witnesses(ordered, remap_rows),
            truncated=len(ordered) >= USER_LIST_WALK_SLICE_USER_LIMIT,
            query_ms=query_ms + remap_ms,
            slice_start=slice_start,
            raw_last=ordered[-1][::-1] if ordered else None,
            tied=bool(ordered) and ordered[0][1] == ordered[-1][1],
        )


def _narrower_start(
    state: _WalkState, slice_end: datetime, width: timedelta
) -> datetime:
    """Where a retry a quarter as wide starts: never below the least width,
    never before the window."""

    return max(state.window_start, slice_end - max(USER_LIST_WALK_MIN_SLICE, width / 4))


def _read_survivors(
    state: _WalkState, ids: list[str]
) -> tuple[list[dict[str, Any]], float] | None:
    """The bounded survivor statement over exactly ``ids``; none for no ids."""

    from tracer.services import users_list_manager as ulm

    if not ids:
        return [], 0.0
    if not state.budget.take(1, finish=state.progress_owed):
        return None
    query, params = state.builder.build_dimension_survivor_query(ids)
    started = time.monotonic()
    try:
        result = ulm.V2AnalyticsQueryService().execute_ch_query(
            query,
            params,
            timeout_ms=_timeout_ms(_admission_deadline(state)),
            settings=ulm._page_read_settings(
                max_result_rows=ulm._USER_LIST_ATTR_RESULT_ROWS
            ),
        )
    except ReadDeadlineExceeded:
        state.budget.exhausted_by = "wall"
        return None
    return list(result.data or ()), _statement_ms(result, started)


def _read_instant(
    state: _WalkState, instant: datetime, before_id: str | None
) -> _Slice | None:
    """Resolved users witnessed at ``instant`` below ``before_id``, id DESC.

    The same bounded survivor statement as a slice then attaches every alias
    of each returned user, so certification scans all of its identities.
    """
    from tracer.services import users_list_manager as ulm

    if not state.budget.take(1, finish=state.progress_owed):
        return None
    query, params = state.builder.build_matching_activity_instant_query(
        instant=instant,
        limit=USER_LIST_WALK_SLICE_USER_LIMIT,
        before_end_user_id=before_id,
    )
    started = time.monotonic()
    try:
        result = ulm.V2AnalyticsQueryService().execute_ch_query(
            query,
            params,
            timeout_ms=_timeout_ms(_admission_deadline(state)),
            settings=ulm._page_replay_read_settings(
                max_result_rows=USER_LIST_WALK_SLICE_USER_LIMIT
            ),
        )
    except ReadDeadlineExceeded:
        state.budget.exhausted_by = "wall"
        return None
    ids = [
        str(row.get("instant_end_user_id"))
        for row in result.data or ()
        if row.get("instant_end_user_id")
    ]
    query_ms = _statement_ms(result, started)
    remap = _read_survivors(state, ids)
    if remap is None:
        return None
    remap_rows, remap_ms = remap
    return _Slice(
        candidates=_resolve_raw_witnesses(
            [(user_id, instant) for user_id in ids], remap_rows
        ),
        truncated=len(ids) >= USER_LIST_WALK_SLICE_USER_LIMIT,
        query_ms=query_ms + remap_ms,
        slice_start=instant,
        raw_last=None,
        tied=True,
    )


def _probe_wall_spent(state: _WalkState) -> None:
    """A deadline raised inside a probe statement: the page's wall, or the probe's.

    An executor that honours ``timeout_ms`` raises for the probe's own
    deadline; that ends the probe, never the page. Only a page wall that is
    really spent stops the walk.
    """
    try:
        state.budget.deadline.remaining_ms()
    except ReadDeadlineExceeded:
        state.budget.exhausted_by = "wall"


@dataclass(frozen=True)
class _Tail:
    """The probe's answer: the newest witnessed row below, ``None`` for none."""

    newest: datetime | None


def _probe_tail(state: _WalkState, *, below: datetime) -> _Tail | None:
    """The newest witnessed row in ``[window_start, below)``, or proof of none.

    Two statements under ONE budget, ``USER_LIST_WALK_PROBE_WALL_MS`` or what
    is left of the page wall, whichever is smaller: the estimate, which costs
    the existence statement and refuses it when the blooms leave more than
    ``USER_LIST_WALK_PROBE_TARGET_READ_ROWS`` rows in the tail (or when the
    estimate cannot be read), then the existence statement itself, whose
    answer alone decides. Both run under the existence statement's read
    settings (threads included): the estimate is that statement's own index
    analysis, and its observed time is what the existence statement pays
    again before it reads its first row, so the existence statement is
    issued only when that time fits what the probe budget has left. ``None``
    when the walk's budget stops either, when the estimate refuses on rows
    or on time, when either statement fails on a read budget, or when the
    row's time cannot be read inside the tail: a probe that cannot answer
    inside its budget licenses nothing, and the walk goes on slicing at the
    cap exactly as it would have without it.
    """
    from tracer.services import users_list_manager as ulm

    if not state.budget.take(1):
        return None
    # The probe's budget runs on the clock the walk schedules on: the
    # statement's own client-observed time (``_statement_ms``), the clock the
    # page wall is measured on too.
    probe_wall_ms = max(
        25, min(USER_LIST_WALK_PROBE_WALL_MS, int(state.budget.remaining_ms()))
    )
    settings = ulm._page_replay_read_settings(max_result_rows=1)
    query, params = state.builder.build_matching_activity_existence_estimate_query(
        range_start=state.window_start, range_end=below
    )
    started = time.monotonic()
    try:
        estimate = ulm.V2AnalyticsQueryService().execute_ch_query(
            query, params, timeout_ms=probe_wall_ms, settings=settings
        )
    except ReadDeadlineExceeded:
        _probe_wall_spent(state)
        return None
    except Exception as exc:
        if not is_read_budget_error(exc):
            raise
        logger.warning(
            "users_matching_walk_tail_estimate_failed", error_type=type(exc).__name__
        )
        return None
    estimate_ms = _statement_ms(estimate, started)
    rows = state.builder.matching_activity_existence_estimate(
        list(estimate.data or ()), getattr(estimate, "columns", None)
    )
    if rows is None or rows > USER_LIST_WALK_PROBE_TARGET_READ_ROWS:
        logger.info(
            "users_matching_walk_tail_probe_refused",
            estimated_rows=rows,
            target_rows=USER_LIST_WALK_PROBE_TARGET_READ_ROWS,
            estimate_ms=round(estimate_ms, 1),
        )
        return None
    # What the estimate left of the probe's budget; the existence statement
    # repeats the estimate's index analysis before it reads a row, so it is
    # issued only when a statement of the estimate's own time fits there.
    probe_left_ms = probe_wall_ms - estimate_ms
    if estimate_ms > probe_left_ms:
        logger.info(
            "users_matching_walk_tail_probe_over_budget",
            estimate_ms=round(estimate_ms, 1),
            probe_wall_ms=probe_wall_ms,
        )
        return None
    if not state.budget.take(1):
        return None
    query, params = state.builder.build_matching_activity_existence_query(
        range_start=state.window_start, range_end=below
    )
    try:
        result = ulm.V2AnalyticsQueryService().execute_ch_query(
            query, params, timeout_ms=max(25, int(probe_left_ms)), settings=settings
        )
    except ReadDeadlineExceeded:
        _probe_wall_spent(state)
        return None
    except Exception as exc:
        if not is_read_budget_error(exc):
            raise
        logger.warning(
            "users_matching_walk_tail_probe_failed", error_type=type(exc).__name__
        )
        return None
    found = list(result.data or ())
    if not found:
        return _Tail(newest=None)
    newest = _utc(found[0].get("witnessed"))
    if newest is None or not state.window_start <= newest < below:
        return None
    return _Tail(newest=newest)


def _certify(state: _WalkState, batch: list[_Candidate]) -> int:
    """Attribute enrichment for a batch: membership superset and order key.

    Returns how many of ``batch``, from its head, are certified; ``0`` when
    the budget or the wall refused, or one user's read ran out of a read
    budget off the head of line. The fallback to the head-of-line user, who
    alone may split, and each stop follow the module docstring's Time splits.
    """

    manager = state.manager
    if state.certify_singly:
        batch = batch[:1]
    statements = _enrichment_statement_count(manager)
    if not state.progress_owed and state.budget.remaining_statements() < (
        statements + _materialisation_statement_count(manager)
    ):
        # Off the head of line, only when one materialisation is paid too. On
        # the head path the floor leaves room, unless a stopped slice was
        # followed by a run of empty slices; there the head still certifies
        # what it may not publish.
        state.budget.exhausted_by = "statements"
        return 0
    if not state.budget.take(statements, finish=state.progress_owed):
        return 0
    rows = [{"end_user_id": candidate.end_user_id} for candidate in batch]
    scan_ids = list(
        dict.fromkeys(alias for candidate in batch for alias in candidate.alias_ids)
    )
    alias_map = {
        alias: candidate.end_user_id
        for candidate in batch
        for alias in candidate.alias_ids
    }
    head = len(batch) == 1 and state.progress_owed
    try:
        manager._read_span_attributes(
            rows,
            _admission_deadline(state),
            start_date=state.window_start,
            end_date=state.window_end,
            candidate_scan_ids=scan_ids,
            candidate_end_user_id_map=alias_map,
            split_buckets=head,
        )
    except ReadDeadlineExceeded:
        state.budget.exhausted_by = "wall"
        return 0
    except Exception as exc:
        if head or not is_read_budget_error(exc):
            raise
        if len(batch) == 1:
            # Not the head of line: the request stops above this batch.
            state.budget.exhausted_by = "read_budget"
            return 0
        state.certify_singly = True
        logger.info(
            "users_matching_walk_certify_singly",
            users=len(batch),
            error_type=type(exc).__name__,
        )
        return _certify(state, batch)
    state.progress_owed = False
    for candidate in batch:
        uid = candidate.end_user_id
        order_key = manager._matching_activity_by_user.get(uid, {}).get(
            state.walked_key
        )
        member = order_key is not None and manager._attribute_filters_match(
            {"end_user_id": uid}
        )
        state.certified[uid] = _Certified(
            end_user_id=uid,
            alias_ids=candidate.alias_ids,
            order_key=order_key if member else None,
        )
    return len(batch)


def _certified_prefix(
    state: _WalkState, batch: list[_Candidate]
) -> list[_Candidate] | None:
    """Certify ``batch``; returns its decided prefix, or ``None`` when refused.

    The prefix runs from the batch's head through the last user now
    certified: all of it when the whole batch was, less when the enrichment
    fell back to one user (``_certify``).
    """

    fresh = [c for c in batch if c.end_user_id not in state.certified]
    if not fresh:
        return batch
    done = _certify(state, fresh)
    if not done:
        return None
    if done == len(fresh):
        return batch
    return batch[: batch.index(fresh[done])]


def _admission_deadline(state: _WalkState) -> ReadDeadline | None:
    """What admits a search statement now; ``None`` admits it with no deadline.

    A request that stops before it decides anyone returns about the cursor
    it was given, and the next request does the same work again. A slice
    that returns rows and whose own statement outlasts the page wall is such
    a stop: its survivor statement was refused, the slice discarded whole,
    and every later request read it again. So until the request decides
    something (``progress_owed``), its search statements are admitted
    against the analytics wall measured from the walk's start instead of
    the page wall. For a read of one statement (a slice, a survivor
    statement, an instant read) the deadline returned has at least 25 ms
    left or is ``None``, and the application service sends no timeout for
    it; one returned with less than 26 ms left can still fall below the
    25 ms floor before it is read, and then stops the request. Otherwise it
    can stop only a read of several statements, a batch's enrichment between
    its key statements and time buckets: a head-of-line split that outlasts
    the wall is stopped there, the known stall (module docstring). Once
    that wall is spent, or the request has read its head-of-line slice
    without a cap (``_read_slice``), the statements that decide that slice's
    first batch are admitted with no deadline, once per request: its
    survivor statement, the instant read and its survivor statement when the
    slice is tied at one instant, and one batch's enrichment with any time
    split (module docstring).
    """

    if not state.progress_owed:
        return state.budget.deadline
    left = USER_LIST_WALK_FINISH_WALL_MS - state.budget.deadline.elapsed_ms()
    if state.slice_uncapped or left < 25:
        return None
    return ReadDeadline.start(int(left))


def _timeout_ms(deadline: ReadDeadline | None) -> int | None:
    return deadline.remaining_ms() if deadline is not None else None


def _slice_cap(state: _WalkState) -> int:
    """Half of what is left of the analytics wall: a slice's server cap.

    Half, so that a slice the server stops leaves room for a narrower one.
    """
    left = USER_LIST_WALK_FINISH_WALL_MS - state.budget.deadline.elapsed_ms()
    return max(25, int(left / 2))


def _finish_deadline(state: _WalkState) -> ReadDeadline:
    """The analytics wall less what the search already spent.

    The users route starts no request-level deadline of its own, so the
    walk's own start is the earliest timestamp this can be measured from
    without threading a new parameter through the view and the manager. A
    real request deadline would start EARLIER and so have LESS left by the
    time materialisation runs; measuring from the walk gives a budget at
    least as large as that, never smaller. What it is smaller than is the
    fresh wall it replaces, which is the point: the finish ends by the
    analytics wall measured from the walk's start instead of a further full
    wall after the page wall. The search before it is bounded at admission,
    by the page wall or, while the request has decided nothing, by the
    analytics wall (``_admission_deadline``); its capped slices also carry a
    server cap (``_slice_cap``), while the head-of-line slice read without one
    (``_read_slice``) and its other statements carry none, so one of those
    admitted in time may still run past the wall; a finish that then has less
    than a statement's floor left is refused, not started.

    The deadline is enforced on the server (``enforce_on_server``): each
    finishing statement sends the smaller of its own cap
    (``USER_LIST_QUERY_TIMEOUT_MS`` / ``USER_LIST_ENRICHMENT_TIMEOUT_MS``) and
    what is left as ``max_execution_time``, so a running replay, metrics,
    evals or relation statement is stopped there, not only refused admission
    after it. A statement the server stops raises ``ReadDeadlineExceeded``;
    ``_materialise`` then replays one user at a time, publishes the page as
    degraded with the rest carried in the cursor once a user's own replay is
    stopped, and decides that user without the cap only when the page has
    published nothing. On a server profile locked at ``readonly=1`` no query
    setting reaches ClickHouse and only the profile's limits apply.
    """

    spent = state.budget.deadline.elapsed_ms()
    return ReadDeadline.start(
        max(1.0, USER_LIST_WALK_FINISH_WALL_MS - spent), enforce_on_server=True
    )


def _replay(
    state: _WalkState, entries: list[_Certified], deadline: ReadDeadline | None
) -> list[dict[str, Any]]:
    """The whole-window replay of ``entries`` and its finishing statements."""

    scan_ids = list(
        dict.fromkeys(alias for entry in entries for alias in entry.alias_ids)
    )
    alias_map = {
        alias: entry.end_user_id for entry in entries for alias in entry.alias_ids
    }
    return state.manager._read_exact_candidate_rows(
        candidate_ids=[entry.end_user_id for entry in entries],
        candidate_scan_ids=scan_ids,
        candidate_end_user_id_map=alias_map,
        frozen_filters=state.frozen_filters,
        window_start=state.window_start,
        window_end=state.window_end,
        deadline=deadline,
        enrich_rows=True,
        candidate_rows=None,
        skip_attribute_read=True,
    )


def _materialise(state: _WalkState, entries: list[_Certified]) -> bool:
    """Whole-window replay plus final membership for publishable users only.

    Returns ``False`` when the page must stop here, and ``True`` without
    deciding anyone when the server stopped a batch: from then on the caller
    replays one user at a time, so a heavy user no longer stops the users
    ahead of it, and a user whose own replay is stopped is known to be heavy.
    """

    manager = state.manager
    statements = _materialisation_statement_count(manager)
    if not state.budget.take(statements, finish=True):
        return False
    finish = _finish_deadline(state)
    # Refused before it is sent: the search spent the analytics wall.
    refused = finish.total_ms - finish.elapsed_ms() < 25
    try:
        # Finish mode: the PAGE wall does not govern these statements.
        # Passing the walk's own deadline would let the replay spend the last
        # of it and the metrics read that follows raise on the client clock,
        # dropping a user the page had already certified. The analytics wall
        # MINUS what the walk has already spent keeps that property, reaches
        # ClickHouse as each statement's ``max_execution_time``, and ends the
        # finish by the analytics wall instead of granting a second full wall
        # here.
        rows = _replay(state, entries, finish)
    except ReadDeadlineExceeded:
        state.finish_singly = True
        must_decide = not state.published and not state.uncapped_finish
        if len(entries) > 1 and (
            not must_decide or state.budget.remaining_statements() >= 2 * statements
        ):
            logger.info("users_matching_walk_finish_stopped", users=len(entries))
            return True
        if not must_decide or not state.budget.take(statements, finish=True):
            state.budget.exhausted_by = "wall"
            return False
        # A page that has published nothing decides its head-of-line user
        # alone, with no deadline at all: no server cap and no admission
        # check. A user whose replay outlasts every cap would otherwise stop
        # every request at the same place, and an exact list can neither skip
        # it nor publish anyone ranked behind it first, so no bounded retry
        # keeps the list both exact and moving. The capped attempt before it
        # was the user's own replay, stopped; or a batch it led, stopped,
        # when the budget cannot afford the user's own attempt first; or it
        # was refused before it started because the search had spent the
        # analytics wall, and then the user was never tried and is not known
        # to be heavy. These statements (the replay and its relation, metrics
        # and evals reads) start with no wall, for one user, once per request
        # (``_read_slice`` lists the head-of-line slice's). Admitting them
        # against the analytics wall, as the finish did before the cap
        # existed, would not do: a replay that spent the wall would refuse the
        # metrics read after it, and the same user would stall there instead.
        state.uncapped_finish = True
        reason = (
            "refused"
            if refused
            else "own_stopped"
            if len(entries) == 1
            else "batch_stopped"
        )
        entries = entries[:1]
        try:
            rows = _replay(state, entries, None)
        except ReadDeadlineExceeded:
            state.budget.exhausted_by = "wall"
            return False
        logger.info(
            "users_matching_walk_uncapped_finish",
            reason=reason,
            statements=state.budget.statements,
        )
    by_id = {str(row.get("end_user_id")): row for row in rows if row.get("end_user_id")}
    for entry in entries:
        row = by_id.get(entry.end_user_id)
        entry.materialised = True
        entry.member = row is not None and manager._row_matches_filters(row)
        entry.row = row if entry.member else None
    return True


def _publish(state: _WalkState, boundary: datetime | None) -> bool:
    """Publish certified members above ``boundary``, in order, until the page fills.

    Materialisation happens lazily and in order: only the users that are next
    in line are replayed, so a carried or rejected user never costs a replay.
    """

    while len(state.published) < state.page_size:
        pending = state.pending(boundary)
        if not pending:
            return True
        for entry in pending:
            if len(state.published) == state.page_size or not entry.materialised:
                break
            entry.published = True
            state.published.append(entry.row)
            state.last_key, state.last_id = entry.order_key, entry.end_user_id
        if len(state.published) == state.page_size:
            return True
        unmaterialised = [
            entry for entry in state.pending(boundary) if not entry.materialised
        ]
        if not unmaterialised:
            return True
        room = 1 if state.finish_singly else state.page_size - len(state.published)
        if not _materialise(state, unmaterialised[:room]):
            return False
    return True


def _decide_instant(
    state: _WalkState, instant: datetime
) -> tuple[bool, datetime | tuple[datetime, str]]:
    """Decide the users of one tied instant, publishing in ``(key, id)`` order.

    Enters below ``last_id`` when ``last_key`` is this instant (everything at
    or above that position is decided). Each certified batch of the instant
    statement settles every resolved user at the instant from its last id up,
    so its members publish at once. Returns ``(True, instant - 1us)`` once
    the instant is exhausted; otherwise ``(False, boundary)`` with what is
    decided so far, having set ``state.stopped`` if the budget ended it.
    """
    state.instant = instant
    state.instant_after = state.last_id if state.last_key == instant else None
    state.instant_ids = []
    after = state.instant_after
    boundary: datetime | tuple[datetime, str] = instant
    batch_size = USER_LIST_WALK_CERTIFY_BATCH_SIZE
    while len(state.published) < state.page_size:
        read = _read_instant(state, instant, after)
        if read is None:
            state.stopped = True
            return False, boundary
        start = 0
        while start < len(read.candidates):
            batch = _certified_prefix(
                state, read.candidates[start : start + batch_size]
            )
            if batch is None:
                state.stopped = True
                return False, boundary
            start += len(batch)
            state.instant_ids.extend(c.end_user_id for c in batch)
            boundary = (instant, batch[-1].end_user_id)
            if not _publish(state, boundary):
                state.stopped = True
                return False, boundary
            if len(state.published) == state.page_size:
                return False, boundary
        if not read.truncated:
            state.instant = None
            return True, instant - _TICK
        after = read.candidates[-1].end_user_id
    return False, boundary


def _instant_position(state: _WalkState) -> str | None:
    """The lowest id of the open instant's decided prefix.

    Every user at the instant with an id at or above it is published, or
    certified as not placed there (no live match, a key elsewhere, or
    rejected by the replay), so a cursor may resume strictly below it.
    """
    position = state.instant_after
    for user_id in state.instant_ids:
        entry = state.certified.get(user_id)
        if entry is None or not (
            entry.published
            or entry.order_key != state.instant
            or (entry.materialised and not entry.member)
        ):
            break
        position = user_id
    return position


def _slices_needed(width: timedelta) -> int:
    """Slices at the cap that ``width`` of window still needs."""

    cap = USER_LIST_WALK_MAX_SLICE
    return max(-(-width // cap), 0)


def walk_matching_activity_page(
    manager: UsersListManager,
    *,
    page_size: int,
    window_start: datetime,
    window_end: datetime,
    frozen_filters: list[dict],
    cursor_order: tuple[Any, ...] | None,
    seen_before: int,
) -> UserCursorRead:
    from tracer.services.users_list_manager import UserCursorRead

    # The parsed window may be naive UTC; every key the walk compares against
    # it comes back from the driver timezone-aware. The transport keeps the
    # window exactly as parsed.
    parsed_window = (window_start, window_end)
    window_start, window_end = _utc(window_start), _utc(window_end)
    builder = UserListQueryBuilderV2(
        organization_id=manager.organization_id,
        project_ids=manager.scoped_project_ids,
        search=manager.search,
        filters=manager.filters,
        empty_scope=manager.empty_scope,
    )
    witness = builder.matching_activity_witness()
    if witness is None:
        raise ListCursorError(
            "invalid_cursor", "User ordering changed; restart pagination."
        )
    open_instant = False
    if cursor_order is None:
        last_key, last_id, coverage = None, None, window_end
    else:
        if (
            len(cursor_order) not in (4, 5)
            or cursor_order[0] != USER_LIST_MATCHING_CURSOR_ORDER
        ):
            raise ListCursorError(
                "invalid_cursor", "User ordering changed; restart pagination."
            )
        last_key = _utc(cursor_order[1])
        last_id = str(cursor_order[2]) if cursor_order[2] is not None else None
        coverage = _utc(cursor_order[3])
        open_instant = len(cursor_order) == 5 and cursor_order[4] is True
        if (
            coverage is None
            or (last_key is None) != (last_id is None)
            or (len(cursor_order) == 5 and not open_instant)
        ):
            raise ListCursorError(
                "invalid_cursor", "User ordering changed; restart pagination."
            )
    state = _WalkState(
        manager=manager,
        builder=builder,
        walked_key=witness[0],
        page_size=page_size,
        window_start=window_start,
        window_end=window_end,
        frozen_filters=frozen_filters,
        budget=_WalkBudget(
            wall_ms=USER_LIST_PAGE_WALL_MS, max_statements=_statement_budget(manager)
        ),
        last_key=last_key,
        last_id=last_id,
        decided_from=min(coverage, window_end),
    )
    slice_end = state.decided_from
    width = USER_LIST_WALK_INITIAL_SLICE
    before: tuple[datetime, str] | None = None
    # Every undecided user's newest matching row lies at or below this line
    # (a time), or strictly below this position inside a tied instant.
    boundary: datetime | tuple[datetime, str] | None = slice_end
    exhausted = manager.empty_scope or slice_end <= window_start
    probed = False
    # An instant to decide before the next slice: the one the cursor left
    # open, or the floor of a slice that returned nothing else.
    next_instant = slice_end - _TICK if open_instant else None
    while not exhausted and not state.stopped and len(state.published) < page_size:
        if next_instant is not None:
            decided, boundary = _decide_instant(state, next_instant)
            if not decided:
                break
            slice_end, before, next_instant = next_instant, None, None
            if slice_end <= window_start:
                exhausted = True
                boundary = None
                if not _publish(state, boundary):
                    state.stopped = True
                break
            continue
        read = _read_slice(
            state,
            slice_start=max(window_start, slice_end - width),
            slice_end=slice_end,
            before=before,
        )
        if read is None:
            state.stopped = True
            break
        candidates = read.candidates
        width = slice_end - read.slice_start
        floor = read.raw_last[0] if read.truncated else read.slice_start
        if candidates:
            # A populated slice re-arms the tail probe: the tail below it is a
            # new question, and one probe per populated region is the bound.
            probed = False
        if read.truncated and read.tied:
            # A whole slice of raw ids at one instant: certifying them here
            # could publish none of them (the tie is undecided until the
            # instant is exhausted), so decide the instant in resolved order.
            boundary = floor
            if not _publish(state, boundary):
                state.stopped = True
                break
            next_instant = floor
            width = max(USER_LIST_WALK_MIN_SLICE, width / 4)
            continue
        batch_size = USER_LIST_WALK_CERTIFY_BATCH_SIZE
        start = 0
        while start < len(candidates):
            batch = _certified_prefix(state, candidates[start : start + batch_size])
            if batch is None:
                state.stopped = True
                if state.budget.exhausted_by in ("read_budget", "wall"):
                    # Just above the refused batch's first candidate. A count
                    # refusal keeps its boundary: in a tie, moving it would
                    # re-open the instant.
                    boundary = candidates[start].newest_witness
                break
            start += len(batch)
            remaining = candidates[start:]
            boundary = remaining[0].newest_witness if remaining else floor
            if not _publish(state, boundary):
                state.stopped = True
                break
            if len(state.published) == page_size:
                break
        if state.stopped or len(state.published) == page_size:
            break
        if not candidates:
            boundary = floor
            if not _publish(state, boundary):
                state.stopped = True
                break
        if read.truncated:
            before = read.raw_last
            slice_end = floor + _TICK
            width = max(USER_LIST_WALK_MIN_SLICE, width / 4)
            continue
        before = None
        slice_end = read.slice_start
        if slice_end <= window_start:
            exhausted = True
            boundary = None
            if not _publish(state, boundary):
                state.stopped = True
            break
        if (
            not candidates
            and not probed
            and _slices_needed(slice_end - window_start)
            > state.budget.remaining_statements()
        ):
            # The tail below this empty slice does not fit the statements
            # left at the cap: cost one existence statement over it and, if
            # it fits, ask once for the newest witnessed row down there.
            # Nothing means the window is exhausted. A row means nothing any
            # slice reads lies above it, as a run of empty slices down to it
            # would have proven, so the walk resumes just above it. An
            # estimate over the target, or a statement the budget refuses or
            # that fails, changes nothing: the walk slices on at the cap.
            probed = True
            tail = _probe_tail(state, below=slice_end)
            if tail is None and state.budget.exhausted_by is not None:
                state.stopped = True
                break
            if tail is not None and tail.newest is None:
                exhausted = True
                boundary = None
                if not _publish(state, boundary):
                    state.stopped = True
                break
            if tail is not None and tail.newest + _TICK < slice_end:
                logger.info(
                    "users_matching_walk_tail_resumed",
                    skipped_seconds=(slice_end - tail.newest).total_seconds(),
                )
                boundary = tail.newest
                if not _publish(state, boundary):
                    state.stopped = True
                    break
                slice_end = tail.newest + _TICK
                continue
        # An empty slice cost only its fixed overhead (the bloom pruned every
        # granule), so its time says nothing about a wider one: widen hard as
        # long as another statement like it fits the wall. A populated slice
        # that came back untruncated widens by four only if four of it would
        # fit, since its cost grows with its width.
        remaining_ms = state.budget.remaining_ms()
        if not candidates and read.query_ms * 2 <= remaining_ms:
            width = min(USER_LIST_WALK_MAX_SLICE, width * 16)
        elif candidates and read.query_ms * 4 <= remaining_ms:
            width = min(USER_LIST_WALK_MAX_SLICE, width * 4)

    leftover = state.pending(boundary)
    has_more = bool(leftover) or not exhausted
    unseen_row_proven = any(entry.materialised and entry.member for entry in leftover)
    checkpoint = None
    if has_more:
        # Everything with a matching row at or after ``coverage`` is decided
        # and published; a carried member's own newest matching row and every
        # undecided user's rows lie below it.
        keys = [entry.order_key for entry in leftover if entry.order_key is not None]
        boundary_time = _boundary_time(boundary)
        anchors = [*keys, *([boundary_time] if boundary_time is not None else [])]
        next_coverage = max(anchors) + _TICK if anchors else window_start
        # Never above the coverage this request resumed from: everything at
        # or after it was decided before, so restating it is exact, and a
        # cursor that moved back up would repeat its hops.
        next_coverage = min(next_coverage, state.decided_from)
        last_key, last_id = state.last_key, state.last_id
        position = _instant_position(state) if state.instant is not None else None
        if position is not None:
            last_key, last_id = state.instant, position
        checkpoint = (USER_LIST_MATCHING_CURSOR_ORDER, last_key, last_id, next_coverage)
        # An instant left undecided resumes by deciding the instant below
        # ``coverage`` in resolved order, and so does a walk its budget
        # stopped where a raw restart would find the same users again: at
        # the coverage it resumed from, or with users it certified at the new
        # coverage's instant (a tie there larger than a request would stall
        # it). Any other stop moved the coverage, and resumes with a raw
        # slice, which costs no instant statement; were that request to stop
        # without moving the coverage, it would open the instant then. A
        # request that opens the instant decides a user there, exhausts it,
        # or publishes, so in a fixed world no two requests in a row make no
        # progress, except in the known stall (module docstring): a head split
        # that outlasts the analytics wall repeats the same cursor.
        certified_there = any(
            entry.order_key == next_coverage - _TICK
            for entry in state.certified.values()
        )
        if state.instant is not None or (
            state.stopped and (next_coverage == state.decided_from or certified_there)
        ):
            checkpoint = (*checkpoint, True)
    if state.stopped:
        logger.info(
            "users_matching_walk_budget_exhausted",
            exhausted_by=state.budget.exhausted_by,
            statements=state.budget.statements,
            published=len(state.published),
        )
    qualified_exact = not manager._unqualified_attribute_fallback_used
    seen_rows = seen_before + len(state.published)
    lower_bound = seen_rows + (1 if has_more and unseen_row_proven else 0)
    # A page the wall or the statement budget cut short is published as
    # degraded and incomplete, never as a complete page that happens to be
    # short: a caller must be able to tell an exhausted walk from an empty
    # answer. This is the seeded lane's contract on the same endpoint
    # (``UsersListManager._cursor_payload``), and the walk owes the same one.
    exhausted = state.budget.exhausted_by is not None
    payload = {
        "table": list(state.published),
        "total_count": lower_bound,
        "total_pages": (lower_bound + page_size - 1) // page_size,
        "count_is_lower_bound": has_more,
        "has_more": has_more,
        "query_complete": not exhausted,
        "query_status": "degraded" if exhausted else "complete",
        "query_exact": qualified_exact,
        "query_provenance": USER_LIST_MATCHING_PROVENANCE,
        "ordering_exact": qualified_exact,
        "ordering": USER_LIST_MATCHING_ORDERING,
        "approximate_fields": [],
    }
    return UserCursorRead(
        payload=payload,
        window_start=parsed_window[0],
        window_end=parsed_window[1],
        checkpoint_order=checkpoint,
        seen_rows=seen_rows,
        has_more=has_more,
        unseen_row_proven=unseen_row_proven,
    )
