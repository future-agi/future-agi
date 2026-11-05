"""Which users a user question reads: the Users view's own population.

A user question is asked about the Users view: its window, its filters and
its search text. ``count_users`` and ``pick_users`` resolve that population
with the Users list's own query (``UsersListManager`` and
``UserListQueryBuilderV2``), so a run reads exactly the users the view lists.

- Filters the list decides in SQL (the window, the user id, traces, cost,
  tokens, first and last active, the search text) are counted in one
  statement, and picked in ``cityHash64(user id)`` order in another.
- Filters the list decides per user (sessions, latency, LLM calls, the eval
  pass rate, span attributes, eval and annotation results) are decided here
  the same way, by the manager's own exact replay, over the view's users in
  ``cityHash64`` order. A pick stops at its ``n``-th match, so a sample reads
  only as many users as it needs. A count reads users for a time budget per
  call and the next call carries on; until it has read them all, the users
  matched so far are a lower bound.

Within a budget every statement carries ClickHouse's ``max_execution_time``
for the time left, users are decided in batches sized so one fits, and the
budget is checked between batches. A statement that can't finish in an
estimate's budget (the view's candidate users, or its count) is read once
off the request, so the next estimate carries on from it.

The hash order is the same uniform random sample, and extends the same way,
as the traces, spans and sessions a run picks (``instant_eval_rows``).
``count_view`` and ``pick_view`` resolve any unit's view, for Instant eval
and Insights alike.

``user_sessions`` reads each user's latest sessions inside the run's window.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import structlog
from django.conf import settings
from django.core.cache import cache

from tracer.services.clickhouse.read_budget import ReadDeadline, is_read_budget_error
from tracer.services.instant_eval_rows import (
    SESSIONS_PER_USER,
    Population,
    PopulationUnavailable,
    _aware,
    _frozen_filters,
)

logger = structlog.get_logger(__name__)

# Users decided per statement on the per-user path: their ids are bound into
# the replay about four times over, at some 40 bytes each, and ClickHouse
# parses at most 256 KiB of a statement.
WALK_BATCH = 1000
# Within a budget a batch starts this small, doubles while one takes under a
# quarter of the budget, and halves (to MIN_BATCH) when one runs out of time
# or memory.
BUDGET_BATCH = 250
MIN_BATCH = 50
# Candidate users read per statement, in hash order.
CANDIDATE_PAGE = 5000
# A count is kept this long, and a count in progress resumes within it.
COUNT_CACHE_TTL = 600
# Statements read off the request, for the next estimate.
_LATER = ThreadPoolExecutor(max_workers=2, thread_name_prefix="instant-eval-users")


def estimate_budget_s() -> float:
    # How long an estimate (asked again on every filter edit) may spend
    # counting users the list decides one by one before it reports the users
    # found so far as a lower bound.
    return float(getattr(settings, "INSTANT_EVAL_USERS_ESTIMATE_BUDGET_S", 3))


def run_budget_s() -> float:
    # How long starting a run may spend finishing that count, or picking a
    # report's sample, before it gives up and says so.
    return float(getattr(settings, "INSTANT_EVAL_USERS_RUN_BUDGET_S", 60))


class UsersCountIncomplete(PopulationUnavailable):
    """Counting the users took longer than a run may wait. What was counted
    is kept, so asking again carries on from there."""

    def __init__(self, counted: int):
        self.counted = counted
        super().__init__(
            f"Still counting these users ({counted:,} so far). Try again in a "
            "moment, or narrow the filters or the date range."
        )


def unsupported_filter_columns(filters: list[dict]) -> list[str]:
    """System fields a Users filter names that the Users list has no column
    for. The list would read each as a span attribute no span carries, and
    match no user (or every one) without saying so. A custom attribute, an
    eval or annotation result, a date, a per-user column (traces, cost,
    sessions, latency, ...) and a span dimension it answers from the user's
    spans (status, model, name, ...) are all supported."""
    from tracer.services.clickhouse.v2.query_builders.user_list import (
        UserListQueryBuilderV2 as Builder,
    )

    unsupported: list[str] = []
    for item in filters or []:
        if Builder._is_date_filter(item) or Builder._is_relation_filter(item):
            continue
        column = str(item.get("column_id") or item.get("columnId") or "")
        property_id = str(item.get("property_id") or item.get("propertyId") or "")
        system = Builder._filter_col_type(item) in (
            "SYSTEM_METRIC",
            "NORMAL",
        ) or property_id.startswith("system_attribute:")
        if (
            not system
            or column in Builder.OUTPUT_FILTER_MAP
            or column == "eval_score"
            or Builder.native_span_dimension(item)
        ):
            continue
        unsupported.append(column)
    return list(dict.fromkeys(unsupported))


def unsupported_filter_message(filters: list[dict]) -> str | None:
    """The sentence a Users scope with unsupported system fields is refused
    with, or None."""
    unsupported = unsupported_filter_columns(filters)
    if not unsupported:
        return None
    names = ", ".join(f'"{column}"' for column in unsupported)
    return f"The Users view can't filter on {names}."


def _deadline(budget_s: float | None) -> ReadDeadline | None:
    if budget_s is None:
        return None
    return ReadDeadline.start(max(1, int(budget_s * 1000)), enforce_on_server=True)


def _expired(deadline: ReadDeadline | None) -> bool:
    # Too little left to send a statement (``remaining_ms``'s floor).
    return deadline is not None and deadline.elapsed_ms() > deadline.total_ms - 25


def _read(sql: str, params: dict, deadline: ReadDeadline | None = None) -> list[dict]:
    """One statement, stopped by the server at the deadline if there is one
    (``ReadDeadlineExceeded``)."""
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    cap = deadline.remaining_ms() if deadline is not None else None
    return (
        V2AnalyticsQueryService()
        .execute_ch_query(
            sql, params, timeout_ms=cap, settings={}, server_execution_cap_ms=cap
        )
        .data
    )


def _read_later(key: str, sql: str, params: dict, keep) -> None:
    """Read a statement too slow for an estimate off the request, once at a
    time, and keep ``keep(rows)`` under ``key`` for the next estimate."""
    reading = f"{key}:reading"
    if not cache.add(reading, 1, timeout=int(run_budget_s()) + 30):
        return

    def work():
        try:
            rows = _read(sql, params, _deadline(run_budget_s()))
            cache.set(key, keep(rows), COUNT_CACHE_TTL)
        except Exception:
            logger.warning("instant_eval_users_read_later_failed", exc_info=True)
        finally:
            cache.delete(reading)

    _LATER.submit(work)


class _UsersView:
    """The Users view a question is asked about, pinned to its window."""

    def __init__(self, project_id: str, filters: list[dict], search: str | None):
        from tracer.models.project import Project
        from tracer.services.clickhouse.v2.query_builders.user_list import (
            UserListQueryBuilderV2,
        )
        from tracer.services.users_list_manager import UsersListManager

        self.project_id = str(project_id)
        self.search = (search or "").strip() or None
        self.pinned, self.start, self.end = _frozen_filters(filters, "sessions")
        org_id = (
            Project.objects.filter(id=self.project_id)
            .values_list("organization_id", flat=True)
            .first()
        )
        if org_id is None:
            raise PopulationUnavailable("Project not found.")
        self.organization_id = str(org_id)
        try:
            # The page's projection is irrelevant here: only what the filters
            # need is read.
            self.manager = UsersListManager(
                organization_id=self.organization_id,
                allowed_project_ids=[self.project_id],
                project_id=self.project_id,
                search=self.search,
                filters=self.pinned,
                requested_columns=[],
            )
        except ValueError as exc:
            raise PopulationUnavailable(str(exc)) from exc
        builder = self._builder(self.pinned)
        self.in_sql = builder.supports_candidate_first_page()
        if self.in_sql:
            self.candidates = builder
        else:
            # The user ids to decide one by one: the window, the search text,
            # and the filters the list's statement decides exactly. Every
            # filter is decided again on each user's replayed row.
            sql_filters = [
                item
                for item in self.pinned
                if UserListQueryBuilderV2._is_date_filter(item)
                or (
                    UserListQueryBuilderV2._is_output_filter(item)
                    and (item.get("column_id") or item.get("columnId"))
                    in UserListQueryBuilderV2._CANDIDATE_FIRST_COLUMNS
                )
            ]
            self.candidates = self._builder(sql_filters)
        self.frozen = self.manager._frozen_filters(
            self.pinned, window_start=self.start, window_end=self.end
        )

    def _builder(self, filters: list[dict]):
        from tracer.services.clickhouse.v2.query_builders.user_list import (
            UserListQueryBuilderV2,
        )

        return UserListQueryBuilderV2(
            organization_id=self.organization_id,
            project_ids=[self.project_id],
            search=self.search,
            filters=filters,
        )

    def statement(self) -> tuple[str, dict]:
        """Every candidate user of the view, as the Users list reads them."""
        sql, params = self.candidates.build_candidate_page_query()
        return sql, dict(params)

    def page_statement(self, after: int | None) -> tuple[str, dict]:
        """The next candidate users after ``after``, in hash order."""
        inner, params = self.statement()
        where = "" if after is None else "WHERE ie_h > %(ie_after)s"
        return (
            "SELECT *, cityHash64(toString(end_user_id)) AS ie_h "
            f"FROM ({inner}) {where} ORDER BY ie_h, end_user_id LIMIT %(ie_n)s",
            {**params, "ie_after": after, "ie_n": CANDIDATE_PAGE},
        )

    def cache_key(self) -> str:
        raw = json.dumps(
            [self.project_id, self.pinned, self.search], sort_keys=True, default=str
        )
        return "ie-users-count:v3:" + hashlib.sha1(raw.encode()).hexdigest()

    def decide(
        self, raw_rows: list[dict], deadline: ReadDeadline | None = None
    ) -> set[str]:
        """Which of these candidate users the view lists: the manager's exact
        replay of each, then every filter, as a Users cursor page decides them."""
        manager = self.manager
        candidates = manager._format_candidate_rows(self.candidates, raw_rows)
        ids = [str(c["end_user_id"]) for c in candidates if c.get("end_user_id")]
        if not ids:
            return set()
        sql, params = self.candidates.build_dimension_survivor_query(ids)
        aliases: dict[str, set[str]] = defaultdict(set)
        for row in _read(sql, params, deadline) if sql else []:
            if row.get("any_id") and row.get("survivor_id"):
                aliases[str(row["survivor_id"])].add(str(row["any_id"]))
        scan_map: dict[str, str] = {}
        for candidate in candidates:
            canonical = str(candidate["end_user_id"])
            scan = sorted(aliases.get(canonical, set()) | {canonical})
            candidate["_candidate_scan_end_user_ids"] = tuple(scan)
            scan_map.update((alias, canonical) for alias in scan)
        try:
            rows = manager._read_exact_candidate_rows(
                candidate_ids=ids,
                candidate_scan_ids=list(scan_map),
                candidate_end_user_id_map=scan_map,
                frozen_filters=self.frozen,
                window_start=self.start,
                window_end=self.end,
                deadline=deadline,
                enrich_rows=manager.filters_need_enrichment,
                candidate_rows=candidates,
            )
            return {
                str(row["end_user_id"])
                for row in rows
                if row.get("end_user_id") and manager._row_matches_filters(row)
            }
        finally:
            # Decisions are per batch; don't keep every batch's attributes.
            manager._attribute_values_by_user.clear()
            manager._attribute_value_types_by_user.clear()
            manager._native_dimension_matches_by_user.clear()
            manager._native_filter_values_by_user.clear()
            manager._relation_matching_user_ids.clear()

    def walk(
        self, *, after: int | None, want: int, deadline: ReadDeadline | None
    ) -> list[tuple[str, int]]:
        """The first ``want`` of the view's users after ``after`` in hash
        order, decided a batch at a time. Past the deadline (or a statement
        that runs out of time or memory under it), the users found so far:
        still the first in hash order."""
        matched: list[tuple[str, int]] = []
        size = WALK_BATCH if deadline is None else BUDGET_BATCH
        cursor = after
        try:
            while True:
                page = _read(*self.page_statement(cursor), deadline)
                for start in range(0, len(page), size):
                    if _expired(deadline):
                        return matched
                    batch = page[start : start + size]
                    kept = self.decide(batch, deadline)
                    for row in batch:
                        rid, h = str(row["end_user_id"]), int(row["ie_h"])
                        if rid in kept:
                            matched.append((rid, h))
                            if len(matched) >= want:
                                return matched
                if len(page) < CANDIDATE_PAGE:
                    return matched
                cursor = int(page[-1]["ie_h"])
        except Exception as exc:
            if deadline is None or not is_read_budget_error(exc):
                raise
            return matched


def _count_walk(
    view: _UsersView, key: str, state: dict, deadline: ReadDeadline, *, finish: bool
) -> dict:
    """Decide users after the state's cursor until every one is decided or the
    deadline passes, keeping the progress after each batch. Each candidate
    page is read once and kept until it has been decided."""
    budget_ms = deadline.total_ms
    while not state["done"]:
        page_key = f"{key}:page:{state['page_from']}"
        page = cache.get(page_key)
        if page is None:
            sql, params = view.page_statement(state["page_from"])
            try:
                page = _read(sql, params, deadline)
            except Exception as exc:
                if not is_read_budget_error(exc):
                    raise
                if finish:
                    raise UsersCountIncomplete(state["total"]) from exc
                _read_later(page_key, sql, params, list)
                return state
            cache.set(page_key, page, COUNT_CACHE_TTL)
        after = state["after"]
        rows = [r for r in page if after is None or int(r["ie_h"]) > after]
        while rows:
            if _expired(deadline):
                return state
            size = state["batch"]
            batch, started = rows[:size], time.monotonic()
            try:
                kept = view.decide(batch, deadline)
            except Exception as exc:
                if not is_read_budget_error(exc):
                    raise
                # A smaller batch may fit the next time (or the time left).
                state["batch"] = max(MIN_BATCH, size // 2)
                cache.set(key, state, COUNT_CACHE_TTL)
                if finish and size > MIN_BATCH and not _expired(deadline):
                    continue
                if finish:
                    raise UsersCountIncomplete(state["total"]) from exc
                return state
            took_ms = (time.monotonic() - started) * 1000
            if took_ms * 4 < budget_ms:
                state["batch"] = min(WALK_BATCH, size * 2)
            state["total"] += sum(1 for r in batch if str(r["end_user_id"]) in kept)
            state["after"] = int(batch[-1]["ie_h"])
            rows = rows[len(batch) :]
            cache.set(key, state, COUNT_CACHE_TTL)
        if len(page) < CANDIDATE_PAGE:
            state["done"] = True
        else:
            state["page_from"] = state["after"]
        cache.set(key, state, COUNT_CACHE_TTL)
    return state


def count_users(
    project_id: str,
    filters: list[dict],
    *,
    search: str | None = None,
    finish: bool = False,
) -> Population:
    """How many users the Users view lists for these filters and search.

    Users the list decides one by one are counted within a time budget per
    call, and each call carries on where the last one stopped: until every
    user is decided, the count so far is a lower bound. ``finish`` (a run
    about to start) counts to the end within the run's budget, or raises
    ``UsersCountIncomplete``."""
    view = _UsersView(project_id, filters, search)
    key = view.cache_key()
    state = cache.get(key) or {
        "after": None,
        "page_from": None,
        "total": 0,
        "done": False,
        "batch": BUDGET_BATCH,
    }
    if not state["done"]:
        started = time.monotonic()
        deadline = _deadline(run_budget_s() if finish else estimate_budget_s())
        if view.in_sql:
            inner, params = view.statement()
            sql = f"SELECT count() AS total FROM ({inner})"
            try:
                rows = _read(sql, params, deadline)
                total = int(rows[0]["total"]) if rows else 0
                state = {**state, "total": total, "done": True}
                cache.set(key, state, COUNT_CACHE_TTL)
            except Exception as exc:
                if not is_read_budget_error(exc):
                    raise
                if finish:
                    raise UsersCountIncomplete(0) from exc
                _read_later(
                    key,
                    sql,
                    params,
                    lambda rows: {
                        **state,
                        "total": int(rows[0]["total"]) if rows else 0,
                        "done": True,
                    },
                )
        else:
            # Keeps its progress as it goes.
            state = _count_walk(view, key, dict(state), deadline, finish=finish)
        logger.info(
            "instant_eval_users_counted",
            project_id=view.project_id,
            in_sql=view.in_sql,
            total=state["total"],
            done=state["done"],
            ms=int((time.monotonic() - started) * 1000),
        )
    if finish and not state["done"]:
        raise UsersCountIncomplete(state["total"])
    return Population(
        state["total"], not state["done"], view.start, view.end, view.pinned
    )


def pick_users(
    project_id: str,
    filters: list[dict],
    *,
    n: int,
    after: int | None,
    search: str | None = None,
    budget_s: float | None = None,
) -> list[tuple[str, int]]:
    """The view's next ``n`` users in hash order, as (end user id, hash).
    Within ``budget_s`` a pick may stop short: the users it found are still
    the first in hash order (none, when its one statement runs out)."""
    if n <= 0:
        return []
    view = _UsersView(project_id, filters, search)
    if not view.in_sql:
        return view.walk(after=after, want=n, deadline=_deadline(budget_s))
    inner, params = view.statement()
    params["ie_n"] = int(n)
    where = ""
    if after is not None:
        params["ie_after"] = int(after)
        where = "WHERE h > %(ie_after)s"
    sql = (
        "SELECT rid, h FROM ("
        "SELECT toString(end_user_id) AS rid, cityHash64(toString(end_user_id)) AS h "
        f"FROM ({inner})) {where} ORDER BY h, rid LIMIT %(ie_n)s"
    )
    try:
        rows = _read(sql, params, _deadline(budget_s))
    except Exception as exc:
        if budget_s is None or not is_read_budget_error(exc):
            raise
        return []
    return [(str(r["rid"]), int(r["h"])) for r in rows if r.get("rid")]


def user_sessions(
    project_id: str, user_ids: list[str], start: datetime, end: datetime
) -> list[str]:
    """Each user's latest sessions inside the window, newest first. A merged
    user's sessions under its old ids count as its own."""
    from tracer.services.clickhouse.v2 import get_reader

    if not user_ids:
        return []
    with get_reader() as reader:
        roots = reader.roots_by_end_user_ids(
            [str(u) for u in user_ids],
            project_id=str(project_id),
            since=_aware(start),
            until=_aware(end),
        )
    first: dict[tuple[str, str], Any] = {}
    for span in roots:
        if not span.trace_session_id or not span.end_user_id:
            continue
        key = (str(span.end_user_id), str(span.trace_session_id))
        if key not in first or span.start_time < first[key]:
            first[key] = span.start_time
    by_user: dict[str, list[tuple[Any, str]]] = defaultdict(list)
    for (user, session), started in first.items():
        by_user[user].append((started, session))
    out: list[str] = []
    seen: set[str] = set()
    for user in [str(u) for u in user_ids]:
        latest = sorted(by_user.get(user, []), reverse=True)[:SESSIONS_PER_USER]
        for _, session in latest:
            if session not in seen:
                seen.add(session)
                out.append(session)
    return out


def count_view(
    project_id: str,
    unit: str,
    filters: list[dict],
    *,
    search: str | None = None,
    finish: bool = False,
) -> Population:
    """How many rows any unit's view holds: users here, the other units from
    their grid's own count (``instant_eval_rows``)."""
    from tracer.services.instant_eval_rows import count_population

    if unit == "users":
        return count_users(project_id, filters, search=search, finish=finish)
    return count_population(project_id, unit, filters)


def pick_view(
    project_id: str,
    unit: str,
    filters: list[dict],
    *,
    n: int,
    after: int | None,
    search: str | None = None,
    budget_s: float | None = None,
) -> list[tuple[str, int]]:
    """The next ``n`` rows of any unit's view in hash order."""
    from tracer.services.instant_eval_rows import pick_rows

    if unit == "users":
        return pick_users(
            project_id, filters, n=n, after=after, search=search, budget_s=budget_s
        )
    return pick_rows(project_id, unit, filters, n=n, after=after)
