"""Span-attribute-filtered Users pages, ordered by newest matching activity.

The seeded candidate statement decides an attribute-filtered page by
aggregating the whole window; on the largest tenants it materialises two
planning-time sets over the sorting key and dies before it starts. This walk
replaces it for plain-text ``equals``/``in`` filters:

* discover: one bounded statement per time slice, newest-first, through the
  deployed key and value blooms, grouped by survivor-resolved user
  (``build_matching_activity_slice_query``; a raw superset, never a result);
* certify: the page's existing attribute enrichment, which also returns the
  user's newest LIVE span whose LATEST value matches - the order key;
* materialise: the existing finite per-user replay, only for users that are
  attribute members AND publishable by position, which decides curated
  presence, search and every native/relation predicate over the whole window
  (``_row_matches_filters``, unchanged), and carries the set-valued totals.

Coverage floor. A truncated slice proves nothing at or below the newest
witness of its last user; a user is published only once its certified key lies
strictly above every undecided user's possible key (the slice floor, or the
newest witness of the next uncertified batch). Users whose key is at or below
that line are carried to a later slice, where their own newest matching row
rediscovers them.

Budget. The walk owns a wall (``USER_LIST_PAGE_WALL_MS``) and a statement
budget (``USER_LIST_WALK_MAX_STATEMENTS``). On exhaustion it returns the users
certified so far, in order, with a cursor; it never falls back to the
whole-window statement. A slice that fails on a read budget is retried
narrower, never wider.

Cursor. ``(marker, last_key, last_id, coverage)``: every user with a matching
row at or after ``coverage`` is decided; the keyset ``(key, id) < (last_key,
last_id)`` under ``(key DESC, id DESC)`` rejects a re-discovered published user
at the enrichment step, before any replay.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from django.conf import settings

from tracer.services.clickhouse.list_cursor import ListCursorError
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_read_budget_error,
)
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
USER_LIST_WALK_MAX_STATEMENTS = settings.USER_LIST_WALK_MAX_STATEMENTS
USER_LIST_WALK_INITIAL_SLICE = timedelta(
    seconds=settings.USER_LIST_WALK_INITIAL_SLICE_SECONDS
)
USER_LIST_WALK_MAX_SLICE = timedelta(seconds=settings.USER_LIST_WALK_MAX_SLICE_SECONDS)
USER_LIST_WALK_SLICE_USER_LIMIT = settings.USER_LIST_WALK_SLICE_USER_LIMIT
# A slice that fails on a read budget is retried at a quarter of its width
# down to this floor; below it the failure propagates as a retryable error.
USER_LIST_WALK_MIN_SLICE = timedelta(minutes=1)
# Users certified per enrichment statement and replayed per materialisation.
USER_LIST_WALK_CERTIFY_BATCH_SIZE = 25
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

    def remaining_ms(self) -> float:
        return max(self.deadline.total_ms - self.deadline.elapsed_ms(), 0.0)

    def statement_deadline(self) -> ReadDeadline | None:
        """The wall as a per-statement deadline while it still has room."""

        return self.deadline if self.remaining_ms() >= 25 else None


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
    certified: dict[str, _Certified] = field(default_factory=dict)
    published: list[dict[str, Any]] = field(default_factory=list)
    stopped: bool = False

    def keyset_admits(self, key: datetime, end_user_id: str) -> bool:
        if self.last_key is None:
            return True
        if key != self.last_key:
            return key < self.last_key
        return self.last_id is None or end_user_id < self.last_id

    def pending(self, boundary: datetime | None) -> list[_Certified]:
        """Certified members publishable now, newest first."""

        rows = [
            entry
            for entry in self.certified.values()
            if entry.order_key is not None
            and not entry.published
            and not (entry.materialised and not entry.member)
            and (boundary is None or entry.order_key > boundary)
            and self.keyset_admits(entry.order_key, entry.end_user_id)
        ]
        rows.sort(key=lambda entry: (entry.order_key, entry.end_user_id), reverse=True)
        return rows


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _enrichment_statement_count(manager: Any) -> int:
    from tracer.services.users_list_manager import _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE

    accelerated = sum(
        1
        for key in manager.attribute_keys
        if key in manager.attribute_exact_text_filters
    )
    ordinary = len(manager.attribute_keys) - accelerated
    return accelerated + -(-ordinary // _USER_LIST_ATTRIBUTE_KEY_BATCH_SIZE)


def _materialisation_statement_count(manager: Any) -> int:
    return (
        1
        + bool(manager.metric_keys)
        + bool(manager.needs_evals)
        + bool(manager.relation_filters)
    )


@dataclass
class _Slice:
    candidates: list[_Candidate]
    truncated: bool
    query_ms: float | None
    slice_start: datetime


def _read_slice(
    state: _WalkState,
    *,
    slice_start: datetime,
    slice_end: datetime,
    before: tuple[datetime, str] | None,
) -> _Slice | None:
    """One slice statement, retried narrower on a read-budget failure.

    Returns ``None`` when the walk's own budget stops it first.
    """
    from tracer.services import users_list_manager as ulm

    while True:
        if not state.budget.take(1):
            return None
        query, params = state.builder.build_matching_activity_slice_query(
            slice_start=slice_start,
            slice_end=slice_end,
            limit=USER_LIST_WALK_SLICE_USER_LIMIT,
            before=before,
        )
        started = time.monotonic()
        try:
            result = ulm.V2AnalyticsQueryService().execute_ch_query(
                query,
                params,
                timeout_ms=state.budget.deadline.remaining_ms(),
                settings=ulm._page_replay_read_settings(
                    max_result_rows=USER_LIST_WALK_SLICE_USER_LIMIT
                ),
            )
        except ReadDeadlineExceeded:
            state.budget.exhausted_by = "wall"
            return None
        except Exception as exc:
            width = slice_end - slice_start
            if not is_read_budget_error(exc) or width <= USER_LIST_WALK_MIN_SLICE:
                raise
            narrower = max(USER_LIST_WALK_MIN_SLICE, width / 4)
            logger.warning(
                "users_matching_walk_slice_narrowed",
                error_type=type(exc).__name__,
                width_seconds=width.total_seconds(),
                retry_width_seconds=narrower.total_seconds(),
            )
            slice_start = max(state.window_start, slice_end - narrower)
            continue
        candidates = []
        for row in result.data or ():
            end_user_id = str(row.get("end_user_id") or "")
            newest = _utc(row.get("newest_witness"))
            if not end_user_id or newest is None:
                continue
            aliases = tuple(
                dict.fromkeys(
                    [
                        *(str(alias) for alias in row.get("alias_end_user_ids") or ()),
                        end_user_id,
                    ]
                )
            )
            candidates.append(_Candidate(end_user_id, newest, aliases))
        candidates.sort(key=lambda c: (c.newest_witness, c.end_user_id), reverse=True)
        return _Slice(
            candidates=candidates,
            truncated=len(candidates) >= USER_LIST_WALK_SLICE_USER_LIMIT,
            # The statement's own server time when the transport reports it;
            # otherwise the client-observed time, so growth never waits on an
            # optional attribute (an executor without it would freeze the
            # slice width at its initial value).
            query_ms=(
                float(getattr(result, "query_time_ms", None) or 0.0)
                or (time.monotonic() - started) * 1000.0
            ),
            slice_start=slice_start,
        )


def _certify(state: _WalkState, batch: list[_Candidate]) -> bool:
    """Attribute enrichment for a batch: membership superset and order key."""

    manager = state.manager
    if not state.budget.take(_enrichment_statement_count(manager)):
        return False
    rows = [{"end_user_id": candidate.end_user_id} for candidate in batch]
    scan_ids = list(
        dict.fromkeys(alias for candidate in batch for alias in candidate.alias_ids)
    )
    alias_map = {
        alias: candidate.end_user_id
        for candidate in batch
        for alias in candidate.alias_ids
    }
    try:
        manager._read_span_attributes(
            rows,
            state.budget.deadline,
            start_date=state.window_start,
            end_date=state.window_end,
            candidate_scan_ids=scan_ids,
            candidate_end_user_id_map=alias_map,
        )
    except ReadDeadlineExceeded:
        state.budget.exhausted_by = "wall"
        return False
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
    return True


def _materialise(state: _WalkState, entries: list[_Certified]) -> bool:
    """Whole-window replay plus final membership for publishable users only."""

    manager = state.manager
    if not state.budget.take(_materialisation_statement_count(manager), finish=True):
        return False
    ids = [entry.end_user_id for entry in entries]
    scan_ids = list(
        dict.fromkeys(alias for entry in entries for alias in entry.alias_ids)
    )
    alias_map = {
        alias: entry.end_user_id for entry in entries for alias in entry.alias_ids
    }
    try:
        rows = manager._read_exact_candidate_rows(
            candidate_ids=ids,
            candidate_scan_ids=scan_ids,
            candidate_end_user_id_map=alias_map,
            frozen_filters=state.frozen_filters,
            window_start=state.window_start,
            window_end=state.window_end,
            # Finish mode: the wall does not govern these statements. Passing
            # the deadline would let the replay spend the last of it and the
            # metrics read that follows raise on the client clock, dropping a
            # user the page had already certified.
            deadline=None,
            enrich_rows=True,
            candidate_rows=None,
            skip_attribute_read=True,
        )
    except ReadDeadlineExceeded:
        state.budget.exhausted_by = "wall"
        return False
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
        room = state.page_size - len(state.published)
        if not _materialise(state, unmaterialised[:room]):
            return False
    return True


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
    if cursor_order is None:
        last_key, last_id, coverage = None, None, window_end
    else:
        if len(cursor_order) != 4 or cursor_order[0] != USER_LIST_MATCHING_CURSOR_ORDER:
            raise ListCursorError(
                "invalid_cursor", "User ordering changed; restart pagination."
            )
        last_key = _utc(cursor_order[1])
        last_id = str(cursor_order[2]) if cursor_order[2] is not None else None
        coverage = _utc(cursor_order[3])
        if coverage is None or (last_key is None) != (last_id is None):
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
            wall_ms=USER_LIST_PAGE_WALL_MS, max_statements=USER_LIST_WALK_MAX_STATEMENTS
        ),
        last_key=last_key,
        last_id=last_id,
    )
    slice_end = min(coverage, window_end)
    width = USER_LIST_WALK_INITIAL_SLICE
    before: tuple[datetime, str] | None = None
    # Every undecided user's newest matching row lies strictly below this line.
    boundary: datetime | None = slice_end
    exhausted = manager.empty_scope or slice_end <= window_start
    while not exhausted and not state.stopped and len(state.published) < page_size:
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
        floor = (
            min(c.newest_witness for c in candidates)
            if read.truncated
            else read.slice_start
        )
        batch_size = USER_LIST_WALK_CERTIFY_BATCH_SIZE
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start : start + batch_size]
            remaining = candidates[start + batch_size :]
            fresh = [c for c in batch if c.end_user_id not in state.certified]
            if fresh and not _certify(state, fresh):
                state.stopped = True
                break
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
            last = candidates[-1]
            before = (last.newest_witness, last.end_user_id)
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
        anchors = [*keys, *([boundary] if boundary is not None else [])]
        next_coverage = max(anchors) + _TICK if anchors else window_start
        checkpoint = (
            USER_LIST_MATCHING_CURSOR_ORDER,
            state.last_key,
            state.last_id,
            min(next_coverage, window_end),
        )
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
    payload = {
        "table": list(state.published),
        "total_count": lower_bound,
        "total_pages": (lower_bound + page_size - 1) // page_size,
        "count_is_lower_bound": has_more,
        "has_more": has_more,
        "query_complete": True,
        "query_status": "complete",
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
