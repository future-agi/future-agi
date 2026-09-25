"""Every sequence of Users walk hops ends, and publishes each user once, in order.

The walk's cursor promises that every user with a matching row at or after
its coverage is decided. Two ways of breaking that promise stalled the list:

* a user whose finishing replay always outlasts the server cap was retried,
  and stopped, on every hop, so the cursor never moved (B1);
* a user rejected by its replay above the coverage, re-witnessed by a lower
  row, was admitted again, and when the budget refused its replay the
  coverage moved back UP to it, so hops cycled (B2).

These tests drive the real walk and manager through the scripted ``World`` /
``Engine`` of ``test_users_matching_walk`` (and, for the server cap, through
the real ``V2AnalyticsQueryService`` and ``ClickHouseClient`` over a recording
native driver). The property test generates worlds from fixed seeds and
follows every cursor to the end. One stall is known and documented, not
fixed: a head-of-line split that outlasts the analytics wall
(``test_a_split_that_outlasts_the_analytics_wall_is_a_known_stall``).
"""

from __future__ import annotations

import json
import math
import random
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from tracer.services import users_list_manager as ulm
from tracer.services import users_matching_walk as walk
from tracer.services.clickhouse import read_budget
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.tests.test_users_matching_walk import (
    PROJECT,
    SERVICE,
    WINDOW_END,
    WINDOW_START,
    Engine,
    World,
    _filters,
    _from_us,
    _manager,
    _names,
    _native_status_leaf,
    _NativeDriver,
    _never_seed,
    _page,
    _real_service_page,
    _signed_cursor,
    _tied_world,
    kind_of,
    minutes_before_end,
)

pytestmark = pytest.mark.unit

TICK = timedelta(microseconds=1)


def _uid(n: int) -> str:
    return str(uuid.UUID(int=n))


def _add(
    world: World,
    uid_int: int,
    alias_ints: list[int],
    key: datetime | None,
    raw: list[tuple[datetime, int]],
    *,
    curated: bool = True,
    native: bool | None = None,
) -> str:
    """A user with explicit ids, so survivors and aliases interleave in id order.

    ``raw`` is ``((moment, identity index), ...)``: which of the user's ids
    carries each witnessed row.
    """
    uid = _uid(uid_int)
    ids = (uid, *(_uid(alias) for alias in alias_ints))
    world.users[uid] = {
        "key": key,
        "cost": 1.0,
        "aliases": ids,
        "curated": curated,
        "name": uid,
        "typed_values": [("string", '"Gold"')],
        # The native leaf's whole-window decision, in a world whose page
        # carries one (``_family_filters``); None: no native leaf.
        "native": native,
    }
    for identity in ids:
        world.canonical[identity] = uid
    for moment, index in raw:
        world.raw.append((moment, ids[index % len(ids)]))
    return uid


def _is_member(user: dict) -> bool:
    """A key (the witness leaf's newest live match), curated, and every native
    leaf decided true when the page carries one."""

    return user["key"] is not None and user["curated"] and user["native"] is not False


def _expected(world: World) -> list[str]:
    """Members in page order: newest matching activity, then id, descending."""

    members = [
        (user["key"], uid) for uid, user in world.users.items() if _is_member(user)
    ]
    return [uid for _key, uid in sorted(members, reverse=True)]


def _check_uncapped(
    replays: list[tuple[tuple[str, ...], bool, bool]],
    reasons: list[str],
    *,
    replay_info: list[tuple[float, int]] | None = None,
    finish_wall_ms: float | None = None,
) -> None:
    """At most one replay without a cap a request, for one user, and why.

    ``replays`` is one request's ``(ids, capped, stopped)``, in order, and
    ``reasons`` what the walk logged for its uncapped replay:
    ``own_stopped``, right after that user's own capped replay was stopped;
    ``batch_stopped``, right after a stopped batch the user led, when the
    budget could not afford the user's own attempt; ``refused``, when the
    search had spent the analytics wall and its capped attempt was never
    sent. With ``replay_info`` (when each replay was sent, and its rows)
    it also holds that no replay before it returned a row: an uncapped
    replay comes only while the page has published nothing.
    """
    uncapped = [i for i, (_ids, capped, _stopped) in enumerate(replays) if not capped]
    assert len(uncapped) == len(reasons) <= 1, (replays, reasons)
    for index, reason in zip(uncapped, reasons, strict=True):
        (user,) = replays[index][0]
        if replay_info is not None:
            assert not any(rows for _at, rows in replay_info[:index]), replay_info
        if reason == "own_stopped":
            assert index > 0 and replays[index - 1] == ((user,), True, True), replays
        elif reason == "batch_stopped":
            before, capped, stopped = replays[index - 1]
            assert capped and stopped and len(before) > 1, replays
            assert before[0] == user, replays
        else:
            assert reason == "refused", reason
            if replay_info is not None:
                assert replay_info[index][0] >= finish_wall_ms - 25, replay_info


def _check_singly(finishing: list[tuple[str, tuple[str, ...], bool, bool]]) -> None:
    """Once a finishing statement is stopped, every later replay is one user."""

    stops = [
        i for i, (_kind, _ids, _capped, stopped) in enumerate(finishing) if stopped
    ]
    if stops:
        later = finishing[stops[0] + 1 :]
        assert all(len(ids) == 1 for kind, ids, _c, _s in later if kind == "replay"), (
            finishing
        )


class _Clock:
    """The monotonic clock of the walls, advanced by each scripted statement."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def spend(self, ms: float) -> None:
        self.now += ms / 1000.0


@contextmanager
def _scripted_clock(clock: _Clock):
    """Run the page wall, the finish deadline and statement timing on ``clock``."""

    fake = SimpleNamespace(monotonic=clock.monotonic)
    with patch.object(read_budget, "time", fake), patch.object(walk, "time", fake):
        yield clock


@contextmanager
def _shipped_walls():
    with (
        patch.object(walk, "USER_LIST_PAGE_WALL_MS", 5_000),
        patch.object(walk, "USER_LIST_WALK_FINISH_WALL_MS", 30_000),
    ):
        yield


# The statements that finish a materialisation after the replay; each names
# the users it reads in ``candidate_end_user_ids``.
FINISHING = frozenset(
    {"replay", "relation", "session_metrics", "span_metrics", "evals"}
)


def _statement_kind(query: str, params: dict | None) -> str:
    """``kind_of``, told apart for every finishing statement."""

    params = params or {}
    if "native_span_flags" in query:
        # Certification, not a finishing statement; it names its users in
        # ``candidate_end_user_ids`` like one.
        return "native"
    if "latest_relation_candidate_spans AS" in query:
        return "relation"
    if "eval_eu_ids" in params:
        return "evals"
    if "session_rows AS" in query:
        return "session_metrics"
    if "candidate_users AS" in query:
        return "replay"
    if "candidate_end_user_ids" in params:
        return "span_metrics"
    return kind_of(query)


class _CappedEngine(Engine):
    """A scripted server on which a heavy user's replay outlasts any cap.

    A replay that carries a heavy user, or more than ``batch_limit`` users,
    and asks the server to stop at a cap is stopped there
    (``ReadDeadlineExceeded``, as the service maps code 159 under a cap); the
    same replay without a cap runs to completion; so do the relation,
    metrics and evals statements that finish it. Records every replay as
    ``(ids, capped, stopped)``, and every finishing statement as ``(kind,
    ids, capped, stopped)``. With a ``clock``, every statement spends its
    time on it: 1 ms, an instant read ``instant_ms``, an enrichment statement
    ``enrich_ms``, a stopped statement its cap, and a slice what
    ``slice_ms(width, returned_rows)`` says. A slice that costs more than the
    cap it was sent with is stopped there; one sent without a cap runs to
    the end whatever it costs. Records every slice as ``(width, cap,
    stopped)``.
    """

    def __init__(
        self,
        world: World,
        heavy: frozenset[str] = frozenset(),
        batch_limit: int | None = None,
        clock: _Clock | None = None,
        slice_ms: Callable[[timedelta, bool], float] | None = None,
        instant_ms: float = 1.0,
        enrich_ms: float = 1.0,
    ) -> None:
        super().__init__(world)
        self.heavy = heavy
        self.batch_limit = batch_limit
        self.clock = clock
        self.slice_ms = slice_ms
        self.instant_ms = instant_ms
        self.enrich_ms = enrich_ms
        self.replays: list[tuple[tuple[str, ...], bool, bool]] = []
        # Per replay: ms into the request when it was sent, rows it returned.
        self.replay_info: list[tuple[float, int]] = []
        self.finishing: list[tuple[str, tuple[str, ...], bool, bool]] = []
        self.slices: list[tuple[timedelta, float | None, bool]] = []
        # Per slice: ms into the request when it was sent.
        self.slice_at: list[float] = []
        # Every statement fails in the transport: the server is unreachable.
        self.outage = False
        # Every statement's kind, in the order sent.
        self.kinds: list[str] = []
        self.start = clock.now if clock is not None else 0.0

    def _elapsed_ms(self) -> float:
        return (self.clock.now - self.start) * 1000.0 if self.clock else 0.0

    def execute_ch_query(
        self,
        query,
        params=None,
        timeout_ms=None,
        settings=None,
        *,
        server_execution_cap_ms=None,
    ):
        kind = _statement_kind(query, params)
        self.kinds.append(kind)
        if self.outage:
            self.calls.append(query)
            raise ReadDeadlineExceeded("the server is unreachable")
        if kind in FINISHING:
            ids = tuple((params or {})["candidate_end_user_ids"])
            capped = server_execution_cap_ms is not None
            stopped = capped and (
                bool(self.heavy.intersection(ids))
                or (self.batch_limit is not None and len(ids) > self.batch_limit)
            )
            self.finishing.append((kind, ids, capped, stopped))
            if kind == "replay":
                self.replays.append((ids, capped, stopped))
                self.replay_info.append((self._elapsed_ms(), 0))
            if stopped:
                self.calls.append(query)
                self.settings.append(settings)
                self.timeouts.append(timeout_ms)
                self.caps.append(server_execution_cap_ms)
                if self.clock is not None:
                    self.clock.spend(server_execution_cap_ms)
                raise ReadDeadlineExceeded("ClickHouse statement exceeded its cap")
            if kind != "replay":
                self.calls.append(query)
                self.settings.append(settings)
                self.timeouts.append(timeout_ms)
                self.caps.append(server_execution_cap_ms)
                if self.clock is not None:
                    self.clock.spend(1.0)
                # Every user matches the relation filter; metrics and evals
                # carry nothing a filter reads.
                data = (
                    [{"end_user_id": uid} for uid in ids] if kind == "relation" else []
                )
                return SimpleNamespace(data=data, query_time_ms=1.0)
        if kind == "slice":
            self.slice_at.append(self._elapsed_ms())
        result = super().execute_ch_query(
            query,
            params,
            timeout_ms,
            settings,
            server_execution_cap_ms=server_execution_cap_ms,
        )
        if kind == "replay":
            self.replay_info[-1] = (self.replay_info[-1][0], len(result.data or ()))
        cost = {"instant": self.instant_ms, "enrich": self.enrich_ms}.get(kind, 1.0)
        if kind == "slice":
            width = TICK * (params["slice_end_us"] - params["slice_start_us"])
            if self.slice_ms is not None:
                cost = self.slice_ms(width, bool(result.data))
            cap = server_execution_cap_ms
            stopped = cap is not None and cost > cap
            self.slices.append((width, cap, stopped))
            if stopped:
                if self.clock is not None:
                    self.clock.spend(cap)
                raise ReadDeadlineExceeded("ClickHouse statement exceeded its cap")
        if self.clock is not None:
            result.query_time_ms = cost
            self.clock.spend(cost)
        return result


def _every_slice(ms: float) -> Callable[[timedelta, bool], float]:
    """Every slice that returns rows costs ``ms``; an empty one, 1 ms."""

    return lambda _width, rows: ms if rows else 1.0


def _any_per_hour(ms: float) -> Callable[[timedelta, bool], float]:
    """Every slice, rows or none, costs ``ms`` per hour of its width."""

    return lambda width, _rows: max(1.0, ms * width / timedelta(hours=1))


def _per_hour(ms: float) -> Callable[[timedelta, bool], float]:
    """A slice that returns rows costs ``ms`` per hour of its width."""

    return lambda width, rows: (
        max(1.0, ms * width / timedelta(hours=1)) if rows else 1.0
    )


def _follow(
    world: World,
    *,
    page_size: int,
    max_hops: int,
    max_statements: int | None = None,
    heavy: frozenset[str] = frozenset(),
    mutate=None,
    slice_ms: Callable[[timedelta, bool], float] | None = None,
    keys: int = 0,
    finish: int = 1,
    outage_every: int | None = None,
    fault: Callable[[int, int], bool] | None = None,
    fail_ms: float = 50.0,
    width: timedelta | None = None,
    family: str = "raw",
) -> tuple[list[str], int]:
    """Follow the cursor to the end; returns the published names and the hops.

    Every hop must keep the coverage where it was or lower it, send no more
    statements than one decision or the page's budget (``_one_decision``,
    every finishing statement included), and send at most one replay without
    a server cap, for one user, while the page has published nothing: right
    after its own capped replay was stopped, after a stopped batch it led
    when the budget could not afford its own attempt, or when the search had
    spent the analytics wall and its capped attempt was never sent. In a
    static world a repeated ``(cursor, seen rows)`` is a livelock, and no two
    hops in a row may both make no progress: publish a user, lower the
    coverage, or lower the decided position. The walls run on a scripted
    clock (``_CappedEngine``); the page shows ``keys`` attribute columns
    besides the filtered one, and the columns and filters that make
    ``finish`` finishing statements (``FINISH_SHAPES``). Every uncapped
    replay and slice must have the reason the walk logs for it
    (``_check_uncapped``, ``_check_uncapped_slices``), and once a finishing
    statement is stopped every later replay carries one user
    (``_check_singly``). With ``outage_every``, every such hop finds the
    server unreachable: it must not raise the coverage, and it is left out of
    the progress and livelock checks. ``fault(users, index)`` and ``width``
    say which enrichment statements run out of memory, each costing
    ``fail_ms`` (``_MemoryEngine``): at most one user's read a hop may be
    split in time, uncounted, inside the documented bound.
    ``mutate(world, published, cursor)`` runs between hops. ``family`` says
    which filters the page carries (``_family_filters``): the walk's witness
    is the raw attribute leaf, or a native leaf.
    """
    with _scripted_clock(_Clock()) as clock:
        return _follow_on(
            world,
            clock=clock,
            page_size=page_size,
            max_hops=max_hops,
            max_statements=max_statements,
            heavy=heavy,
            mutate=mutate,
            slice_ms=slice_ms,
            keys=keys,
            finish=finish,
            outage_every=outage_every,
            fault=fault,
            fail_ms=fail_ms,
            width=width,
            family=family,
        )


def _follow_on(
    world: World,
    *,
    clock: _Clock,
    page_size: int,
    max_hops: int,
    max_statements: int | None,
    heavy: frozenset[str],
    mutate,
    slice_ms: Callable[[timedelta, bool], float] | None,
    keys: int,
    finish: int,
    outage_every: int | None,
    fault: Callable[[int, int], bool] | None,
    fail_ms: float,
    width: timedelta | None,
    family: str = "raw",
) -> tuple[list[str], int]:
    budget = max(
        max_statements or walk.USER_LIST_WALK_MAX_STATEMENTS,
        _one_decision(keys, finish, family),
    )
    assert walk._statement_budget(_keyed_manager(keys, finish, family)) == budget
    names: list[str] = []
    seen_states: set = set()
    coverage = WINDOW_END
    position: tuple = (None, None)
    stalled = False
    # After a read_budget stop, the refused user's newest witness.
    refused_at: datetime | None = None
    cursor = None
    finish_wall = walk.USER_LIST_WALK_FINISH_WALL_MS
    enrichment = _enrichments(keys, family)
    least = _split_statements(ulm._USER_LIST_ATTRIBUTE_MIN_BUCKET)
    for hop in range(1, max_hops + 1):
        engine = _MemoryEngine(
            world,
            heavy,
            clock=clock,
            fault=fault,
            fail_ms=fail_ms,
            width=width,
            slice_ms=slice_ms,
        )
        engine.outage = outage_every is not None and hop % outage_every == 0
        with capture_logs() as logs:
            read = _keyed_page(
                page_size=page_size,
                cursor=cursor,
                engine=engine,
                keys=keys,
                finish=finish,
                family=family,
            )
        names.extend(_names(read))
        # One user's read split in time, at most, outside the budget.
        narrowed = sum(engine.split.values())
        assert len(engine.split) <= 1, (hop, engine.split)
        assert narrowed <= enrichment * (least - 1), (hop, narrowed)
        assert len(engine.calls) - narrowed <= budget, (hop, len(engine.calls))
        if not engine.outage:
            _check_uncapped(
                engine.replays,
                _logged(logs, "users_matching_walk_uncapped_finish"),
                replay_info=engine.replay_info,
                finish_wall_ms=finish_wall,
            )
            _check_singly(engine.finishing)
            _check_uncapped_slices(
                engine.slices,
                _logged(logs, "users_matching_walk_uncapped_slice"),
                slice_at=engine.slice_at,
                finish_wall_ms=finish_wall,
            )
        if not read.has_more:
            return names, hop
        order = tuple(read.checkpoint_order)
        assert order[3] <= coverage, f"coverage moved up at hop {hop}: {order}"
        if engine.outage:
            assert _names(read) == [], hop
            cursor = _signed_cursor(read)
            continue
        if mutate is None:
            state = (order, read.seen_rows)
            assert state not in seen_states, f"livelock at hop {hop}: {state}"
            seen_states.add(state)
            progressed = (
                bool(_names(read))
                or order[3] < coverage
                or _lower_position(order[1:3], position)
            )
            assert progressed or not stalled, f"two hops without progress at {hop}"
            # A read_budget stop leaves the coverage just above the refused
            # batch: the next request publishes, or moves the coverage down
            # to the refused user's witness.
            assert (
                refused_at is None or _names(read) or order[3] <= refused_at + TICK
            ), f"no progress past the refused user at {hop}: {order[3]}, {refused_at}"
            stalled = not progressed
            refused_at = None
            if _exhausted(logs) == ["read_budget"]:
                refused_at = max(
                    moment
                    for moment, raw_id in world.raw
                    if world.canonical.get(raw_id) == engine.refused
                    and moment < coverage
                )
        coverage, position = order[3], order[1:3]
        cursor = _signed_cursor(read)
        if mutate is not None:
            mutate(world, set(names), order)
    raise AssertionError(f"no end after {max_hops} hops; published {len(names)}")


ANNOTATION_FILTER = {
    "column_id": str(uuid.UUID(int=77)),
    "filter_config": {
        "filter_type": "number",
        "filter_op": "greater_than",
        "filter_value": 3,
        "col_type": "ANNOTATION",
    },
}
# Finishing statements per materialisation: the columns and relation filter
# that make them. A session metric, a span metric, a relation filter and an
# eval column each add one statement after the replay.
FINISH_SHAPES = {
    1: ((), False),
    2: (("num_sessions",), False),
    3: (("avg_session_duration", "avg_trace_latency"), False),
    4: (("avg_session_duration", "avg_trace_latency"), True),
    5: (("avg_session_duration", "avg_trace_latency", "eval_score"), True),
}


# The page's witness: the raw ``tag`` leaf alone; a native ``status`` leaf
# alone (the walk discovers on its flag, and no attribute is filtered); or
# both, where the raw leaf is the witness and the native leaf is decided at
# certification.
FAMILIES = ["raw", "native", "mixed"]


def _family_filters(family: str) -> list[dict]:
    date_and_tag = _filters()
    if family == "raw":
        return date_and_tag
    if family == "native":
        return [date_and_tag[0], _native_status_leaf()]
    assert family == "mixed", family
    return [*date_and_tag, _native_status_leaf()]


def _keyed_manager(keys: int, finish: int = 1, family: str = "raw"):
    """The page's manager: ``keys`` attribute columns besides the filters of
    ``family``, and the columns and filters that make ``finish`` finishing
    statements."""

    from tracer.services.users_list_manager import UsersListManager

    base = _manager(_family_filters(family))
    columns, relation = FINISH_SHAPES[finish]
    manager = UsersListManager(
        organization_id=base.organization_id,
        allowed_project_ids=list(base.scoped_project_ids),
        project_id=base.project_id,
        filters=[*base.filters, *([ANNOTATION_FILTER] if relation else [])],
        requested_columns=list(columns),
        attribute_keys=[f"k{n:03d}" for n in range(keys)],
    )
    assert walk._materialisation_statement_count(manager) == finish
    return manager


def _one_decision(keys: int, finish: int = 1, family: str = "raw") -> int:
    """The statements one decision takes, restated from its parts.

    Not read from ``walk._statement_budget``, so a budget that grows past
    what a decision needs is caught: the open instant; the first slice and
    its retries a quarter as wide down to the least width; the head-of-line
    slice read without a cap and its survivor statement, then the instant
    read and its survivor statement; a batch's enrichment, and the
    head-of-line user's alone after it fails (``_enrichments``); and a
    finish of ``finish`` statements with its uncapped retry.
    """
    width, retries = walk.USER_LIST_WALK_INITIAL_SLICE, 0
    while width > walk.USER_LIST_WALK_MIN_SLICE:
        width, retries = width / 4, retries + 1
    return 1 + 1 + retries + 2 + 2 + 2 * _enrichments(keys, family) + 2 * finish


def _enrichments(keys: int, family: str = "raw") -> int:
    """One certification's statements for a page of ``keys`` attribute
    columns: one for the filtered key (a raw witness), one per four of the
    others, and the native statement when the page carries a native leaf."""

    return (family != "native") + -(-keys // 4) + (family != "raw")


@contextmanager
def _eval_configs():
    """One eval config in the project, so an eval column sends its statement."""

    from unittest.mock import MagicMock

    configs = MagicMock()
    configs.filter.return_value.values_list.return_value = [
        (PROJECT, str(uuid.UUID(int=88)))
    ]
    with patch(
        "tracer.models.custom_eval_config.CustomEvalConfig.no_workspace_objects",
        configs,
    ):
        yield


def _keyed_page(
    *,
    page_size: int,
    cursor,
    engine: Engine,
    keys: int,
    finish: int = 1,
    family: str = "raw",
):
    manager = _keyed_manager(keys, finish, family)
    with (
        patch(SERVICE, return_value=engine),
        patch.object(manager, "_read_dimension_candidates", side_effect=_never_seed),
        _eval_configs(),
    ):
        return manager.list_cursor_payload(page_size=page_size, cursor=cursor)


def _check_uncapped_slices(
    slices: list[tuple[timedelta, float | None, bool]],
    reasons: list[str],
    *,
    slice_at: list[float] | None = None,
    finish_wall_ms: float | None = None,
) -> None:
    """At most one slice without a cap a request, no wider than the least, and why.

    ``reasons`` is what the walk logged: ``narrowest_stopped``, right after
    a capped slice of the least width was stopped; ``no_budget``, right
    after a wider one was stopped when the budget could not afford a
    narrower retry; ``wall_spent``, once the analytics wall had nothing
    left (``slice_at`` says when each slice was sent).
    """
    uncapped = [i for i, (_width, cap, _stopped) in enumerate(slices) if cap is None]
    assert len(uncapped) == len(reasons) <= 1, (slices, reasons)
    least = walk.USER_LIST_WALK_MIN_SLICE
    for index, reason in zip(uncapped, reasons, strict=True):
        assert slices[index][0] <= least, slices
        if reason == "wall_spent":
            if slice_at is not None:
                assert slice_at[index] >= finish_wall_ms - 25, slice_at
            continue
        width, cap, stopped = slices[index - 1]
        assert index > 0 and cap is not None and stopped, slices
        if reason == "narrowest_stopped":
            assert width <= least, slices
        else:
            assert reason == "no_budget" and width > least, (reason, slices)


def _logged(logs: list[dict], event: str) -> list[str]:
    return [entry["reason"] for entry in logs if entry["event"] == event]


def _exhausted(logs: list[dict]) -> list[str]:
    """Why each stopped request stopped, as the walk logged it."""

    return [
        entry["exhausted_by"]
        for entry in logs
        if entry["event"] == "users_matching_walk_budget_exhausted"
    ]


def _lower_position(new: tuple, old: tuple) -> bool:
    """Whether cursor position ``(last_key, last_id)`` ``new`` is below ``old``."""

    if new[0] is None:
        return False
    return old[0] is None or tuple(new) < tuple(old)


# --------------------------------------------------------------------------
# B1: a user whose finishing replay always outlasts the server cap.
# --------------------------------------------------------------------------


class _SlowUserDriver(_NativeDriver):
    """The server stops a replay carrying a heavy user whenever it is told to.

    Heavy, not broken: the same replay sent without ``max_execution_time``
    completes. Also stops, under a cap, any replay of more than
    ``batch_limit`` users. Records every replay as ``(ids, capped, stopped)``
    and each cap it was sent with.
    """

    def __init__(
        self, engine: Engine, heavy: set[str], batch_limit: int | None = None
    ) -> None:
        super().__init__(engine)
        self.heavy = frozenset(heavy)
        self.batch_limit = batch_limit
        self.replays: list[tuple[tuple[str, ...], bool, bool]] = []
        self.caps: list[float] = []

    def execute(self, query, params=None, *, with_column_types=False, settings=None):
        kind = _statement_kind(query, params)
        if kind == "replay":
            ids = tuple((params or {}).get("candidate_end_user_ids", ()))
            cap = float((settings or {}).get("max_execution_time") or 0)
            stopped = cap > 0 and (
                bool(self.heavy.intersection(ids))
                or (self.batch_limit is not None and len(ids) > self.batch_limit)
            )
            self.replays.append((ids, cap > 0, stopped))
            self.caps.append(cap)
            if stopped:
                from clickhouse_driver.errors import ErrorCodes, ServerException

                self.sent.append(("replay", settings))
                raise ServerException(
                    "Timeout exceeded: elapsed 8.0 seconds, maximum: 8",
                    code=ErrorCodes.TIMEOUT_EXCEEDED,
                )
        if kind not in FINISHING:
            return super().execute(
                query, params, with_column_types=with_column_types, settings=settings
            )
        self.sent.append((kind, settings))
        result = self.engine.execute_ch_query(query, params)
        data = list(result.data or ())
        columns = list(getattr(result, "columns", None) or (data[0] if data else ()))
        rows = [tuple(row.get(name) for name in columns) for row in data]
        return rows, [(name, "String") for name in columns]


def _real_service_hops(world: World, driver: _SlowUserDriver, max_hops: int):
    """Every page through the real service and client: ``(names, replays)``."""

    hops = []
    cursor = None
    coverage = WINDOW_END
    for _ in range(max_hops):
        before = len(driver.replays)
        with capture_logs() as logs:
            read = _real_service_page(world, driver, cursor=cursor)
        replays = driver.replays[before:]
        reasons = _logged(logs, "users_matching_walk_uncapped_finish")
        # The shipped budget always affords a user's own capped attempt.
        assert set(reasons) <= {"own_stopped", "refused"}, reasons
        _check_uncapped(replays, reasons)
        hops.append((_names(read), replays))
        if not read.has_more:
            return hops
        assert read.checkpoint_order[3] <= coverage, read.checkpoint_order
        coverage = read.checkpoint_order[3]
        cursor = _signed_cursor(read)
    raise AssertionError(f"no end after {max_hops} hops: {[h[0] for h in hops]}")


def test_a_sole_user_whose_replay_always_outlasts_the_cap_is_published():
    """The page decides its head-of-line user without the cap, once."""

    world = World()
    uid = world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    driver = _SlowUserDriver(_CappedEngine(world), {uid})

    hops = _real_service_hops(world, driver, max_hops=3)

    assert [names for names, _replays in hops] == [["user-1"]]
    # Stopped under the server cap, then decided with none.
    assert driver.replays == [((uid,), True, True), ((uid,), False, False)]
    assert 0 < driver.caps[0] <= 8.0 and driver.caps[1] == 0


@pytest.mark.parametrize(
    ("heavy_rank", "pages"),
    [
        (1, [[1, 2, 3, 4, 5]]),
        (3, [[1, 2], [3, 4, 5]]),
        (5, [[1, 2, 3, 4], [5]]),
    ],
)
def test_a_heavy_user_never_blocks_the_users_ranked_around_it(heavy_rank, pages):
    """The five users share one replay batch, and the server stops it.

    The page replays them one at a time, capped: the users ahead of the heavy
    user publish on the first page, the heavy user's own replay is stopped
    and ends that page, and the next page decides it without the cap (it has
    published nothing yet) and goes on to the users behind it.
    """
    world = World()
    ids = [
        world.user(n, key=minutes_before_end(n), raw=(minutes_before_end(n),))
        for n in range(1, 6)
    ]
    driver = _SlowUserDriver(_CappedEngine(world), {ids[heavy_rank - 1]})

    hops = _real_service_hops(world, driver, max_hops=4)

    assert [names for names, _replays in hops] == [
        [f"user-{n}" for n in page] for page in pages
    ]
    assert [ids for ids, capped, _stopped in driver.replays if not capped] == [
        (ids[heavy_rank - 1],)
    ]


def test_a_batch_that_outlasts_the_cap_publishes_its_users_one_at_a_time():
    """No user is heavy alone, but any replay of more than three users is.

    Every page's batch is stopped; its users then replay one at a time under
    the cap, until the statement budget ends the page. No statement is ever
    sent without the cap.
    """
    world = World()
    for n in range(1, 41):
        world.user(n, key=minutes_before_end(n), raw=(minutes_before_end(n),))
    driver = _SlowUserDriver(_CappedEngine(world), set(), batch_limit=3)

    hops = _real_service_hops(world, driver, max_hops=4)

    assert [name for names, _replays in hops for name in names] == [
        f"user-{n}" for n in range(1, 41)
    ]
    assert len(hops[0][0]) > 1
    assert all(capped for _ids, capped, _stopped in driver.replays)


def test_a_stopped_batch_the_budget_cannot_retry_singly_decides_its_head_uncapped():
    """``batch_stopped``: the budget cannot afford the head's own capped attempt.

    Any replay of more than one user is stopped at the cap. With five
    statements, the slice, its survivor statement, the batch's enrichment and
    its stopped replay leave one: a page that has published nothing replays
    its head-of-line user alone without the cap, once, and publishes it.
    """
    world, expected = _spread_world(3, 3)
    engine = _CappedEngine(world, batch_limit=1, clock=_Clock())
    with (
        _scripted_clock(engine.clock),
        patch.object(walk, "_statement_budget", return_value=5),
        capture_logs() as logs,
    ):
        read, _engine = _page(world, page_size=25, engine=engine)

    assert _names(read) == expected[:1]
    ids = tuple(world.users)
    assert engine.replays == [(ids, True, True), (ids[:1], False, False)]
    reasons = _logged(logs, "users_matching_walk_uncapped_finish")
    assert reasons == ["batch_stopped"]
    _check_uncapped(engine.replays, reasons, replay_info=engine.replay_info)
    assert len(engine.calls) == 5


def test_metric_columns_are_charged_one_statement_per_metric_group():
    """A session metric and a span metric column are two metrics statements.

    Through the real service and client. Every replay of more than three
    users is stopped, so the page materialises one user at a time, and each
    materialisation sends the replay and both metrics statements. They were
    charged as one, and a request sent 30-31 statements at a budget of 24;
    every request now stays within its budget.
    """
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.users_list_manager import UsersListManager

    world, expected = _spread_world(30, 5)
    driver = _SlowUserDriver(_CappedEngine(world), set(), batch_limit=3)
    base = _manager()

    def manager():
        return UsersListManager(
            organization_id=base.organization_id,
            allowed_project_ids=list(base.scoped_project_ids),
            project_id=base.project_id,
            filters=base.filters,
            requested_columns=["avg_session_duration", "avg_trace_latency"],
            attribute_keys=[],
        )

    assert walk._materialisation_statement_count(manager()) == 3
    budget = walk._statement_budget(manager())
    names, cursor, per_request = [], None, []
    for _hop in range(12):
        client = ClickHouseClient(host="localhost", port=39999, pool_size=1)
        page = manager()
        before = len(driver.sent)
        with (
            patch.object(client, "_get_client", return_value=driver),
            patch.object(client, "_return_client"),
            patch(
                "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
                return_value=client,
            ),
            patch.object(page, "_read_dimension_candidates", side_effect=_never_seed),
        ):
            read = page.list_cursor_payload(page_size=25, cursor=cursor)
        per_request.append(driver.sent[before:])
        names.extend(_names(read))
        if not read.has_more:
            break
        cursor = _signed_cursor(read)

    assert names == expected
    assert all(len(sent) <= budget for sent in per_request), [
        len(sent) for sent in per_request
    ]
    kinds = [kind for sent in per_request for kind, _settings in sent]
    assert kinds.count("session_metrics") == kinds.count("span_metrics") > 0


def test_a_heavy_user_inside_a_large_tie_does_not_stall_the_instant():
    world, expected = _tied_world(601)
    heavy = next(u for u, v in world.users.items() if v["name"] == expected[59])
    driver = _SlowUserDriver(_CappedEngine(world), {heavy})

    hops = _real_service_hops(world, driver, max_hops=30)

    assert [name for names, _replays in hops for name in names] == expected


def test_a_finish_the_server_stops_after_the_page_published_carries_the_rest():
    """Once the page has users to publish, a stopped finish ends it, bounded.

    User 2's replay always outlasts the cap. The batch of both is stopped;
    user 1 then replays alone, capped, and publishes; user 2's own capped
    replay is stopped, and the page is published as it stands, degraded,
    with user 2 carried in the cursor. Nothing is sent without the cap.
    """
    world = World()
    cheap = world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    heavy = world.user(2, key=minutes_before_end(5), raw=(minutes_before_end(5),))
    driver = _SlowUserDriver(_CappedEngine(world), {heavy})

    first = _real_service_page(world, driver)

    assert _names(first) == ["user-1"]
    assert first.payload["query_status"] == "degraded"
    assert first.has_more is True
    assert driver.replays == [
        ((cheap, heavy), True, True),
        ((cheap,), True, False),
        ((heavy,), True, True),
    ]
    assert first.checkpoint_order[3] > minutes_before_end(5)

    resumed = _real_service_page(world, driver, cursor=_signed_cursor(first))
    assert _names(resumed) == ["user-2"]
    assert driver.replays[3:] == [((heavy,), True, True), ((heavy,), False, False)]


def test_a_finish_the_analytics_wall_refuses_is_decided_without_the_cap():
    """The search spent the analytics wall, so the capped replay is never sent.

    Nothing is left of the finish deadline: the capped replay is refused at
    admission, on the client, before it reaches the server. That stalls the
    cursor just as a replay the server stops does, and it ends the same way:
    the page has published nothing, so it decides its head-of-line user
    without a cap.
    """
    world = World()
    uid = world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    driver = _SlowUserDriver(_CappedEngine(world), set())
    spent = ReadDeadline.start(1, enforce_on_server=True)

    with patch.object(walk, "_finish_deadline", return_value=spent):
        read = _real_service_page(world, driver)

    assert _names(read) == ["user-1"]
    assert driver.replays == [((uid,), False, False)]
    assert driver.caps == [0]
    assert read.has_more is False


# --------------------------------------------------------------------------
# B2: a replay-rejected user re-witnessed below the resume coverage.
# --------------------------------------------------------------------------


def _unique_time_world(rng: random.Random, n: int, reject_rate: float) -> World:
    """No ties: every witnessed row at its own microsecond."""

    world = World()
    span = int((WINDOW_END - WINDOW_START) / TICK)
    used: set[int] = set()

    def moment() -> datetime:
        while True:
            value = rng.randrange(span)
            if value not in used:
                used.add(value)
                return WINDOW_START + TICK * value

    for index in range(n):
        key = None if rng.random() < 0.15 else moment()
        raw = [key] if key is not None else []
        raw += [moment() for _ in range(rng.choice([0, 1, 2, 3]))]
        if not raw:
            raw = [moment()]
        _add(
            world,
            10 + index,
            [],
            key,
            [(m, 0) for m in raw],
            curated=rng.random() > reject_rate,
        )
    return world


def test_a_rejected_user_found_again_below_the_coverage_does_not_raise_it():
    """Small budget, no ties, 35% of members rejected by their replay."""

    rng = random.Random(36)
    world = _unique_time_world(rng, rng.choice([4, 8, 15, 30]), reject_rate=0.35)
    with (
        patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 2),
        patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 8),
        patch.object(walk, "USER_LIST_WALK_CERTIFY_BATCH_SIZE", 2),
    ):
        names, _hops = _follow(world, page_size=3, max_hops=60, max_statements=8)

    assert names == _expected(world)


def test_a_dense_rejecting_world_ends_at_shipped_limits():
    """2,000 users, no ties, most rejected by the replay, a metric column shown."""

    assert walk.USER_LIST_WALK_SLICE_USER_LIMIT == 200
    assert walk.USER_LIST_WALK_MAX_STATEMENTS == 24
    assert walk.USER_LIST_WALK_CERTIFY_BATCH_SIZE == 25
    rng = random.Random(1)
    n = rng.choice([2000, 4000])
    world = _unique_time_world(rng, n, reject_rate=rng.choice([0.7, 0.9, 0.97]))
    names, _hops = _follow(world, page_size=25, max_hops=400, finish=2)

    assert names == _expected(world)


# --------------------------------------------------------------------------
# P2: a populated slice whose own statement outlasts the page wall.
# --------------------------------------------------------------------------


def _spread_world(users: int, every_minutes: int) -> tuple[World, list[str]]:
    world = World()
    for n in range(1, users + 1):
        moment = minutes_before_end(every_minutes * n)
        world.user(n, key=moment, raw=(moment,))
    return world, [f"user-{n}" for n in range(1, users + 1)]


def test_a_slice_that_outlasts_the_page_wall_still_decides_its_first_batch():
    """Every slice that returns rows takes 6 s against the 5 s page wall.

    The slice statement is admitted inside the wall and runs past it. Its
    survivor statement was then refused, the slice discarded whole, and the
    next request read the same slice again, so the list never got past it.
    A request that has decided nothing yet finishes that slice's first batch
    against the analytics wall: every request publishes, and the list ends.
    """
    world, expected = _spread_world(60, 2)

    with _shipped_walls():
        names, hops = _follow(
            world, page_size=25, max_hops=8, slice_ms=_every_slice(6_000)
        )

    assert names == expected
    assert hops <= 6


def _stopped_slice_hops(world, slice_ms, max_hops, reasons=None, filters=None):
    """Every request, on the scripted clock: ``(names, slices)`` per hop.

    ``reasons``, when given, collects every uncapped slice's logged reason;
    ``filters`` replaces the page's (a narrower window, say).
    """

    clock = _Clock()
    hops = []
    cursor = None
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(max_hops):
            engine = _CappedEngine(world, clock=clock, slice_ms=slice_ms)
            with capture_logs() as logs:
                read, _engine = _page(
                    world, page_size=25, cursor=cursor, engine=engine, filters=filters
                )
            logged = _logged(logs, "users_matching_walk_uncapped_slice")
            _check_uncapped_slices(
                engine.slices,
                logged,
                slice_at=engine.slice_at,
                finish_wall_ms=walk.USER_LIST_WALK_FINISH_WALL_MS,
            )
            if reasons is not None:
                reasons.extend(logged)
            hops.append((_names(read), engine.slices))
            if not read.has_more:
                return hops
            cursor = _signed_cursor(read)
    raise AssertionError(f"no end after {max_hops} hops: {[h[0] for h in hops]}")


def test_a_slice_that_outlasts_every_cap_is_read_uncapped_at_its_narrowest():
    """Every slice that returns rows takes 40 s: longer than the whole request.

    Every capped attempt is stopped, however narrow, and the analytics wall
    (30 s) cannot hold even one of them. The request narrows while it can,
    then reads its head-of-line slice once more, at the least width, without
    a cap, and decides that slice's first batch with no deadline: the same
    minimal escape as a replay that outlasts every cap.
    """
    world, expected = _spread_world(12, 5)

    hops = _stopped_slice_hops(world, _every_slice(40_000), max_hops=14)

    # Only an uncapped slice can read rows here, and a request reads one:
    # every request decides one user (the last proves the rest empty).
    published = [names for names, _slices in hops if names]
    assert published == [[name] for name in expected]
    assert len(hops) <= len(expected) + 1
    first = hops[0][1]
    assert [w for w, _c, _s in first[:3]] == [
        walk.USER_LIST_WALK_INITIAL_SLICE / 4**n for n in range(3)
    ]
    assert [s for _w, _c, s in first[:2]] == [True, True]
    width, cap, stopped = first[-1]
    assert cap is None and not stopped and width == walk.USER_LIST_WALK_MIN_SLICE


def test_a_slice_that_outlasts_its_cap_only_when_wide_is_narrowed_not_uncapped():
    """A slice costs 40 s per hour of width: stopped when wide, fine when narrow.

    The request narrows the stopped slice a quarter at a time and reads the
    narrower one under a cap; no slice is ever sent without one.
    """
    world, expected = _spread_world(12, 5)

    hops = _stopped_slice_hops(world, _per_hour(40_000), max_hops=14)

    assert [name for names, _slices in hops for name in names] == expected
    assert all(names for names, _slices in hops[:-1])
    slices = [entry for _names_, hop in hops for entry in hop]
    assert any(stopped for _width, _cap, stopped in slices)
    assert all(cap is not None for _width, cap, _stopped in slices)


def test_slices_slow_even_when_empty_still_end():
    """Every slice costs 40 s an hour, empty or not; the users are 90 minutes back.

    A request that has decided nothing narrows its stopped slices and walks
    the empty ones below them against the analytics wall, until a slice of
    the least width is stopped; then it reads its head-of-line slice once
    without a cap and stops, having moved its coverage down past everything
    it read: about ten minutes a request here, the slow-slice cost left as
    a follow-up. In a two-hour window the list ends, each user once, in
    order.
    """
    world = World()
    for n, minutes in enumerate((90, 100, 110), start=1):
        moment = minutes_before_end(minutes)
        world.user(n, key=moment, raw=(moment,))
    reasons: list[str] = []
    two_hours = _filters(window_start=WINDOW_END - timedelta(hours=2))

    hops = _stopped_slice_hops(
        world, _any_per_hour(40_000), 40, reasons, filters=two_hours
    )

    assert [name for names, _slices in hops for name in names] == [
        "user-1",
        "user-2",
        "user-3",
    ]
    assert set(reasons) == {"narrowest_stopped"}, reasons


def test_a_search_that_spent_the_analytics_wall_reads_its_head_slice_uncapped():
    """The server was unreachable; then the open instant took the whole 30 s.

    The first request fails in the transport and leaves the cursor where it
    was, with its instant open. The next request's instant read takes the
    whole analytics wall before any slice, so no capped slice can be
    admitted: the request reads its head-of-line slice without a cap
    (``wall_spent``), one least width below the coverage, and decides the
    user there.
    """
    world = World()
    world.user(1, key=minutes_before_end(0.5), raw=(minutes_before_end(0.5),))
    expected = ["user-1"]
    clock = _Clock()
    with _shipped_walls(), _scripted_clock(clock):
        down = _CappedEngine(world, clock=clock)
        down.outage = True
        first, _engine = _page(world, page_size=25, engine=down)
        assert first.payload["table"] == [] and first.checkpoint_order[4] is True
        slow = _CappedEngine(world, clock=clock, instant_ms=30_000)
        with capture_logs() as logs:
            read, _engine = _page(
                world, page_size=25, cursor=_signed_cursor(first), engine=slow
            )

    reasons = _logged(logs, "users_matching_walk_uncapped_slice")
    assert reasons == ["wall_spent"]
    _check_uncapped_slices(
        slow.slices,
        reasons,
        slice_at=slow.slice_at,
        finish_wall_ms=walk.USER_LIST_WALK_FINISH_WALL_MS,
    )
    assert _names(read) == expected


def test_a_stopped_slice_at_the_window_start_is_read_at_what_is_left():
    """The slice at the window's start is narrower than the least width.

    It cannot be narrowed; when it is stopped the request reads that slice,
    clipped at the window start, without a cap.
    """
    world = World()
    ids = []
    for n, seconds in enumerate((20, 10, 5), start=1):
        moment = WINDOW_START + timedelta(seconds=seconds)
        ids.append(world.user(n, key=moment, raw=(moment,)))
    expected = ["user-1", "user-2", "user-3"]

    hops = _stopped_slice_hops(world, _every_slice(40_000), max_hops=12)

    assert [name for names, _slices in hops for name in names] == expected
    uncapped = [w for _n, hop in hops for w, cap, _s in hop if cap is None]
    assert uncapped and all(w < walk.USER_LIST_WALK_MIN_SLICE for w in uncapped)


class _SlowSliceDriver(_NativeDriver):
    """The server stops a capped slice wider than ``limit``, as code 159."""

    def __init__(self, engine: Engine, limit: timedelta) -> None:
        super().__init__(engine)
        self.limit = limit
        self.slices: list[tuple[timedelta, float]] = []

    def execute(self, query, params=None, *, with_column_types=False, settings=None):
        if kind_of(query) == "slice":
            width = TICK * (params["slice_end_us"] - params["slice_start_us"])
            cap = float((settings or {}).get("max_execution_time") or 0)
            self.slices.append((width, cap))
            if cap > 0 and width > self.limit:
                from clickhouse_driver.errors import ErrorCodes, ServerException

                raise ServerException(
                    "Timeout exceeded: elapsed 15.0 seconds, maximum: 15",
                    code=ErrorCodes.TIMEOUT_EXCEEDED,
                )
        return super().execute(
            query, params, with_column_types=with_column_types, settings=settings
        )


def test_a_slice_the_server_stops_is_narrowed_through_the_real_client():
    """Through ``V2AnalyticsQueryService`` and ``ClickHouseClient`` unmocked.

    Every slice reaches the driver with a positive ``max_execution_time``;
    the server stops the wide ones (code 159 under that cap), and the walk
    narrows them and publishes every user.
    """
    world, expected = _spread_world(6, 7)
    driver = _SlowSliceDriver(Engine(world), timedelta(minutes=20))

    read = _real_service_page(world, driver)

    assert _names(read) == expected
    assert all(cap > 0 for _width, cap in driver.slices)
    widths = [width for width, _cap in driver.slices]
    assert widths[:2] == [timedelta(hours=1), timedelta(minutes=15)]


# --------------------------------------------------------------------------
# Certification that runs out of a read budget.
# --------------------------------------------------------------------------


class _MemoryEngine(_CappedEngine):
    """An enrichment runs out of memory (code 241) when it holds too much.

    It fails when its bucket is wider than ``width``, whatever it carries,
    when its users times its bucket's hours exceed ``user_hours``, or when
    ``fault(users, index)`` says so, ``index`` counting the request's
    enrichment statements from 0; a failure costs ``fail_ms``. Records every
    enrichment as ``(users, bucket width, failed)``, and counts, per user,
    the statements of that user alone over less than the window (its read
    split in time).

    The native span-dimension statement is a certification statement too: it
    counts as an enrichment over the whole window, and ``fault`` applies to it
    when it carries more than one user. It has no time split, so a
    head-of-line user whose native statement fails raises; that documented
    limit has a test of its own
    (``test_a_head_of_line_native_statement_that_fails_raises``), and the
    generated worlds leave it out.
    """

    def __init__(
        self,
        world: World,
        heavy: frozenset[str] = frozenset(),
        *,
        clock: _Clock,
        width: timedelta | None = None,
        user_hours: float | None = None,
        fault: Callable[[int, int], bool] | None = None,
        fail_ms: float = 50.0,
        slice_ms: Callable[[timedelta, bool], float] | None = None,
    ) -> None:
        super().__init__(world, heavy, clock=clock, slice_ms=slice_ms)
        self.width = width
        self.user_hours = user_hours
        self.fault = fault
        self.fail_ms = fail_ms
        self.enrichments: list[tuple[int, timedelta, bool]] = []
        self.split: dict[str, int] = {}
        # The user whose whole-window read last failed alone.
        self.refused: str | None = None

    def execute_ch_query(
        self,
        query,
        params=None,
        timeout_ms=None,
        settings=None,
        *,
        server_execution_cap_ms=None,
    ):
        kind = _statement_kind(query, params)
        if kind in ("enrich", "native"):
            if kind == "enrich":
                bucket = _from_us(params["attr_end_us"]) - _from_us(
                    params["attr_start_us"]
                )
                users = len(params["eu_ids"])
            else:
                bucket = WINDOW
                users = len(params["candidate_end_user_ids"])
            failed = (
                kind == "enrich"
                and (
                    (self.width is not None and bucket > self.width)
                    or (
                        self.user_hours is not None
                        and users * (bucket / timedelta(hours=1)) > self.user_hours
                    )
                )
            ) or (
                self.fault is not None
                and (kind == "enrich" or users > 1)
                and self.fault(users, len(self.enrichments))
            )
            self.enrichments.append((users, bucket, failed))
            if users == 1 and bucket < WINDOW:
                (uid,) = params["eu_ids"]
                self.split[uid] = self.split.get(uid, 0) + 1
            if users == 1 and bucket >= WINDOW and failed:
                (self.refused,) = params[
                    "eu_ids" if kind == "enrich" else ("candidate_end_user_ids")
                ]
            if failed:
                from clickhouse_driver.errors import ErrorCodes, ServerException

                self.calls.append(query)
                self.clock.spend(self.fail_ms)
                raise ServerException(
                    "Memory limit exceeded", code=ErrorCodes.MEMORY_LIMIT_EXCEEDED
                )
        return super().execute_ch_query(
            query,
            params,
            timeout_ms,
            settings,
            server_execution_cap_ms=server_execution_cap_ms,
        )


def _memory_hops(world, *, max_hops, **engine):
    """Every request on the scripted clock: ``(names, statements, enrichments)``."""

    clock = _Clock()
    hops = []
    cursor = None
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(max_hops):
            memory = _MemoryEngine(world, clock=clock, **engine)
            read, _engine = _page(world, page_size=25, cursor=cursor, engine=memory)
            hops.append((_names(read), len(memory.calls), memory.enrichments))
            if not read.has_more:
                return hops, read
            cursor = _signed_cursor(read)
    raise AssertionError(f"no end after {max_hops} hops: {[h[0] for h in hops]}")


WINDOW = WINDOW_END - WINDOW_START
TEN_MINUTES = timedelta(minutes=10)


def _split_statements(least: timedelta) -> int:
    """The statements one enrichment statement over the window sends when every
    bucket wider than ``least`` runs out of memory: each failed bucket is
    halved until its halves are no wider than ``least``, so the tree has
    ``2 ** ceil(log2(window / least))`` leaves and one fewer failed buckets.
    At the least bucket the manager splits to (1 minute), 4,095 over 24 h.
    """
    return 2 ** (math.ceil(math.log2(WINDOW / least)) + 1) - 1


def test_a_batch_whose_enrichment_runs_out_of_memory_certifies_its_head_alone():
    """Memory grows with users times bucket: five users do not fit, one does.

    The batch's enrichment is tried once over the whole window and not split
    in time; when it runs out of a read budget the head-of-line user is
    certified alone, and the rest of the request certifies one user at a
    time. No enrichment is ever narrowed in time, and every request stays
    within its statement budget.
    """
    world, expected = _spread_world(5, 3)

    hops, last = _memory_hops(world, max_hops=4, user_hours=30.0)

    assert [name for names, _s, _e in hops for name in names] == expected
    enrichments = [entry for _n, _s, hop in hops for entry in hop]
    assert all(bucket == WINDOW for _users, bucket, _failed in enrichments)
    assert [users for users, _b, failed in enrichments if failed] == [5]
    assert all(statements <= 24 for _n, statements, _e in hops)
    # Nothing was narrowed in time, so nothing is marked inexact.
    assert last.payload["query_exact"] is True


def test_a_tied_instant_whose_batch_runs_out_of_memory_publishes_everyone():
    """A tie decided at its own instant, whose batches run out of memory.

    Twelve users share one instant and a slice returns five raw ids, so the
    walk decides the instant with its own statement (``_decide_instant``).
    Five users over the window do not fit in memory, one does: each batch
    falls back to its head-of-line user, and the instant moves on by the
    users certified, not by the batch it tried. Every user is published
    once, in order.
    """
    world, expected = _tied_world(12)
    clock = _Clock()
    names: list[str] = []
    cursor = None
    failed_batches = instant_reads = 0
    with (
        _shipped_walls(),
        _scripted_clock(clock),
        patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 5),
    ):
        for _hop in range(30):
            memory = _MemoryEngine(world, clock=clock, user_hours=30.0)
            read, _engine = _page(world, page_size=25, cursor=cursor, engine=memory)
            names.extend(_names(read))
            failed_batches += sum(1 for n, _b, f in memory.enrichments if n > 1 and f)
            instant_reads += sum(1 for q in memory.calls if kind_of(q) == "instant")
            if not read.has_more:
                break
            cursor = _signed_cursor(read)

    assert failed_batches > 0 and instant_reads > 0
    assert names == expected


@pytest.mark.parametrize("tied", [False, True], ids=["spread", "tied"])
@pytest.mark.parametrize("keys", [40, 44, 100])
def test_a_batch_that_runs_out_of_memory_at_many_keys_still_publishes(keys, tied):
    """The head-of-line user's retry is a second enrichment, and it fits.

    Five users over the window do not fit in memory, one does, whatever the
    page's attribute keys (``_MemoryEngine``). A batch's enrichment is
    charged in full before it is sent, and the head-of-line user alone is
    charged again after it fails. With one enrichment reserved, from 44 keys
    (12 statements) the budget refused that retry and no request published
    anyone; at 40 keys the retry fitted and the replay after it did not, so
    a request resuming inside a tie returned the cursor it was given. The
    tie here is 30 users at one instant, more than a request decides.
    """
    world, expected = _tied_world(30) if tied else _spread_world(5, 3)
    clock = _Clock()
    names: list[str] = []
    cursor = None
    per_hop: list[int] = []
    failed_batches = 0
    resumed_in_instant = 0
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(2 * len(expected) + 2):
            memory = _MemoryEngine(world, clock=clock, user_hours=30.0)
            read = _keyed_page(page_size=25, cursor=cursor, engine=memory, keys=keys)
            per_hop.append(len(_names(read)))
            names.extend(_names(read))
            failed_batches += sum(1 for n, _b, f in memory.enrichments if n > 1 and f)
            if not read.has_more:
                break
            order = tuple(read.checkpoint_order)
            resumed_in_instant += len(order) == 5 and order[4]
            cursor = _signed_cursor(read)

    # Users are published on every pair of consecutive requests.
    assert all(a or b for a, b in zip(per_hop, per_hop[1:], strict=False)), per_hop
    assert names == expected, per_hop
    assert failed_batches > 0
    if tied:
        assert resumed_in_instant > 0, per_hop


def test_only_the_head_of_line_decision_of_a_request_splits_in_time():
    """One user's read is split in time only while the request owes progress.

    Every enrichment wider than ten minutes runs out of memory, and a failure
    is cheap, so the page wall would admit several splits. The first batch
    fails, its head-of-line user alone is split down to buckets that fit and
    is published; the next user's own enrichment then fails over the whole
    window and ends the request, degraded, rather than split a second user's
    read uncounted. The request stops with its coverage just above that user,
    so the next request decides it as its head of line.
    """
    world, expected = _spread_world(5, 3)
    clock = _Clock()
    names: list[str] = []
    cursor = None
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(8):
            memory = _MemoryEngine(
                world, clock=clock, width=timedelta(minutes=10), fail_ms=5.0
            )
            with capture_logs() as logs:
                read, _engine = _page(world, page_size=25, cursor=cursor, engine=memory)
            names.extend(_names(read))
            if not read.has_more:
                break
            # One user split, the head of line, into the buckets that fit,
            # and it was published.
            split = {world.users[uid]["name"]: n for uid, n in memory.split.items()}
            assert split == {_names(read)[0]: _split_statements(TEN_MINUTES) - 1}, split
            # The next user was tried once, over the whole window, and ended
            # the request: its read is neither split nor retried.
            alone = [f for n, b, f in memory.enrichments if n == 1 and b == WINDOW]
            assert alone == [True, True], memory.enrichments[-1]
            assert memory.enrichments[-1] == (1, WINDOW, True)
            # A stop of its own kind, not a wall, and the page is degraded.
            assert _exhausted(logs) == ["read_budget"]
            assert read.payload["query_status"] == "degraded"
            cursor = _signed_cursor(read)

    assert names == expected


@pytest.mark.parametrize("keys", [0, 12])
def test_after_a_batch_runs_out_of_memory_the_request_goes_one_user_at_a_time(keys):
    """The fall back to one user at a time lasts the rest of the request.

    Five users over the window do not fit in memory, one does. Each request's
    first batch fails once; its head-of-line user is certified alone, and so
    is every user after it in that request: no later batch is tried and fails
    again. Twenty users take several requests, each with one failed batch.
    """
    world, expected = _spread_world(20, 3)
    clock = _Clock()
    names: list[str] = []
    cursor = None
    failed_batches = []
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(25):
            memory = _MemoryEngine(world, clock=clock, user_hours=30.0)
            read = _keyed_page(page_size=25, cursor=cursor, engine=memory, keys=keys)
            names.extend(_names(read))
            failed_batches.append(
                sum(1 for n, _b, f in memory.enrichments if n > 1 and f)
            )
            if not read.has_more:
                break
            cursor = _signed_cursor(read)

    assert names == expected
    assert len(failed_batches) > 2
    assert all(failed <= 1 for failed in failed_batches), failed_batches


@pytest.mark.parametrize("users", [5, 1])
def test_a_head_of_line_user_that_fails_at_every_width_raises(users):
    """Today's stance on a user no bucket can hold: the request raises.

    Every enrichment of the head-of-line user runs out of memory, down to the
    least bucket, whether it leads a batch that failed first or is the
    slice's only user. The request raises the server's error rather than
    publish a page, and the next request does the same. Whether it should
    instead mark that user undecidable and publish a degraded page is the
    owner's decision; this pins the current behaviour until then.
    """
    from clickhouse_driver.errors import ErrorCodes, ServerException

    world, _expected = _spread_world(users, 3)
    clock = _Clock()
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(2):
            memory = _MemoryEngine(world, clock=clock, width=timedelta(0), fail_ms=1.0)
            with pytest.raises(ServerException) as raised:
                _page(world, page_size=25, engine=memory)
            assert raised.value.code == ErrorCodes.MEMORY_LIMIT_EXCEEDED
            # It was split first, down to a bucket no wider than the least.
            assert min(b for _n, b, _f in memory.enrichments) <= timedelta(minutes=1)


class _FailingNativeEngine(_MemoryEngine):
    """Every native statement runs out of memory, whatever it carries."""

    def execute_ch_query(
        self,
        query,
        params=None,
        timeout_ms=None,
        settings=None,
        *,
        server_execution_cap_ms=None,
    ):
        if _statement_kind(query, params) == "native":
            from clickhouse_driver.errors import ErrorCodes, ServerException

            self.enrichments.append(
                (len(params["candidate_end_user_ids"]), WINDOW, True)
            )
            self.calls.append(query)
            raise ServerException(
                "Memory limit exceeded", code=ErrorCodes.MEMORY_LIMIT_EXCEEDED
            )
        return super().execute_ch_query(
            query,
            params,
            timeout_ms,
            settings,
            server_execution_cap_ms=server_execution_cap_ms,
        )


@pytest.mark.parametrize("users", [5, 1])
def test_a_head_of_line_native_statement_that_fails_raises(users):
    """The native certification has no time split (design O3): the request raises.

    A batch whose native statement runs out of memory falls back to its
    head-of-line user alone, as an enrichment does; when that user's own
    statement fails too there is nothing narrower to try, so the request
    raises the server's error (retryable) rather than publish a page, and the
    next request does the same. It does not livelock: nothing is published
    and no cursor is handed out.
    """
    from clickhouse_driver.errors import ErrorCodes, ServerException

    world, _expected = _spread_world(users, 3)
    for user in world.users.values():
        user["native"] = True
    filters = _family_filters("native")
    clock = _Clock()
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(2):
            memory = _FailingNativeEngine(world, clock=clock)
            with pytest.raises(ServerException) as raised:
                _page(world, page_size=25, engine=memory, filters=filters)
            assert raised.value.code == ErrorCodes.MEMORY_LIMIT_EXCEEDED
            # The batch first, then its head-of-line user alone; never split.
            assert [n for n, _b, _f in memory.enrichments] == (
                [users, 1] if users > 1 else [1]
            )
            assert "replay" not in memory.kinds


@pytest.mark.parametrize("size", [401, 601])
def test_a_native_tied_cohort_larger_than_one_request_publishes_everyone_once(size):
    """The tied-instant proof, walked on a native leaf.

    Hundreds of users share their newest matching instant: no user at a
    truncated floor is publishable until the whole instant is seen, and the
    continuation resumes inside the instant, in resolved-id order.
    """
    world, expected = _tied_world(size)
    for user in world.users.values():
        user["native"] = True
    filters = _family_filters("native")
    names: list[str] = []
    cursor = None
    for _hop in range(-(-size // 25) + 1):
        read, engine = _page(world, page_size=25, cursor=cursor, filters=filters)
        names.extend(_names(read))
        assert "enrich" not in [kind_of(call) for call in engine.calls]
        if not read.has_more:
            break
        cursor = _signed_cursor(read)
    assert names == expected
    assert len(set(names)) == size


@pytest.mark.parametrize("gap_hours", [4, 10, 20])
def test_a_user_refused_below_an_empty_slice_is_the_next_requests_head(gap_hours):
    """A user refused off the head path below an empty slice is decided next.

    Every enrichment wider than ten minutes runs out of memory, cheaply. Three
    users sit ``gap_hours`` and more below the window end, 90 minutes apart,
    so a request's first slice is empty: it decides no one, but the request
    no longer owes progress, and the user the widened slice below it meets is
    refused without a split. That request stops with its coverage just above
    the refused user, not at the empty slice's floor an hour down, so the
    next request decides it as its head of line: users are published on
    consecutive requests.
    """
    world = World()
    for n in range(3):
        moment = WINDOW_END - timedelta(hours=gap_hours, minutes=90 * n)
        world.user(n + 1, key=moment, raw=(moment,))
    expected = ["user-1", "user-2", "user-3"]
    clock = _Clock()
    per_hop: list[list[str]] = []
    cursor = None
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(8):
            memory = _MemoryEngine(world, clock=clock, width=TEN_MINUTES, fail_ms=5.0)
            with capture_logs() as logs:
                read, _engine = _page(world, page_size=25, cursor=cursor, engine=memory)
            per_hop.append(_names(read))
            if not read.has_more:
                break
            # Each request decides one user, published, and stops at the
            # next, refused unsplit: only the head of line splits.
            split = {world.users[uid]["name"] for uid in memory.split}
            assert split == set(_names(read)), (per_hop, split)
            assert _exhausted(logs) == ["read_budget"]
            assert read.payload["query_status"] == "degraded"
            cursor = _signed_cursor(read)

    assert [name for names in per_hop for name in names] == expected
    # The first request only finds the first user; every later one publishes.
    assert [len(names) for names in per_hop] == [0, 1, 1, 1], per_hop


@pytest.mark.parametrize("gap_hours", [4, 10, 20])
def test_a_budget_refusal_below_an_empty_slice_ends_without_a_crawl(gap_hours):
    """The statement budget, not a read, refuses a batch below an empty slice.

    Thirty users sit ``gap_hours`` below the window end, a minute apart, and
    the page shows 100 attribute keys, so a batch of five costs 27 statements
    to certify and publish, against a budget of 63. The first request's first
    slice is empty and the widened slice below it meets the users; after two
    batches what is left cannot certify and publish a third, and the request
    stops, its coverage just above the refused batch. (The first batch below
    an empty slice always fits the floor, so the count only refuses a later
    one, after the boundary has moved to it.) Each next request starts at
    that user: every request publishes, none crawls down the gap.
    """
    world = World()
    for n in range(30):
        moment = WINDOW_END - timedelta(hours=gap_hours, minutes=n)
        world.user(n + 1, key=moment, raw=(moment,))
    clock = _Clock()
    names: list[str] = []
    per_hop: list[int] = []
    cursor = None
    with (
        _shipped_walls(),
        _scripted_clock(clock),
        patch.object(walk, "USER_LIST_WALK_CERTIFY_BATCH_SIZE", 5),
    ):
        for hop in range(10):
            engine = _CappedEngine(world, clock=clock)
            with capture_logs() as logs:
                read = _keyed_page(page_size=25, cursor=cursor, engine=engine, keys=100)
            names.extend(_names(read))
            per_hop.append(len(_names(read)))
            if hop == 0:
                # The first slice is empty: no survivor statement follows it.
                assert engine.kinds[:2] == ["slice", "slice"], engine.kinds
            if not read.has_more:
                break
            assert _exhausted(logs) == ["statements"], _exhausted(logs)
            assert read.payload["query_status"] == "degraded"
            cursor = _signed_cursor(read)

    assert names == [f"user-{n}" for n in range(1, 31)]
    assert all(per_hop[:-1]), per_hop


@pytest.mark.parametrize(
    ("keys", "finish", "most_statements"), [(12, 3, 70), (21, 1, 75)]
)
def test_a_count_refusal_in_a_tie_world_keeps_its_boundary(
    keys, finish, most_statements
):
    """A refusal by the statement count does not move the boundary.

    Eighty users with ties and aliases, no faults. Moving the boundary to a
    refused batch's first candidate is right for a read or wall stop, but a
    count refusal of a slice's first batch lands it on an instant a
    published user shares: the cursor opens that instant, and the next
    request reads it and enriches again. The list takes 4 requests and
    opens at most 2 instants.
    """
    world = _world(random.Random(11), 80)
    clock = _Clock()
    names: list[str] = []
    cursor = None
    requests = statements = opened = 0
    stops: list[str] = []
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(20):
            engine = _CappedEngine(world, clock=clock)
            with capture_logs() as logs:
                read = _keyed_page(
                    page_size=25, cursor=cursor, engine=engine, keys=keys, finish=finish
                )
            names.extend(_names(read))
            stops.extend(_exhausted(logs))
            requests += 1
            statements += len(engine.calls)
            if not read.has_more:
                break
            order = tuple(read.checkpoint_order)
            opened += len(order) == 5 and bool(order[4])
            cursor = _signed_cursor(read)

    assert names == _expected(world)
    assert "statements" in stops, stops
    assert requests == 4 and opened <= 2, (requests, opened)
    assert statements <= most_statements, statements


@pytest.mark.parametrize(
    ("world_kind", "gap_hours"),
    [
        ("failure_spends_the_wall", 4),
        ("failure_spends_the_wall", 10),
        ("failure_spends_the_wall", 20),
        ("slow_enrichment", 3),
        ("slow_enrichment", 7),
    ],
)
def test_a_wall_stop_below_an_empty_slice_is_the_next_requests_head(
    world_kind, gap_hours
):
    """The page wall, not a read, refuses a batch below an empty first slice.

    Two ways a request whose first slice is empty (so it no longer owes
    progress) meets its users only after the 5 s page wall is spent: three
    users ``gap_hours`` below the end, 90 minutes apart, whose batch fails
    after 5.1 s (a user alone fits at six-hour buckets); or three clusters of
    five users ``gap_hours`` apart, 100 attribute keys, and every enrichment
    statement taking 200 ms, no faults, so a batch's 26 statements outlast
    the wall. Either way the request stops for ``wall`` with its coverage
    just above the refused batch, not at the empty slice's floor an hour
    down, and the next request decides it: no two requests in a row publish
    nobody, unless the first stopped for ``read_budget``.
    """
    world = World()
    if world_kind == "failure_spends_the_wall":
        for n in range(3):
            moment = WINDOW_END - timedelta(hours=gap_hours, minutes=90 * n)
            world.user(n + 1, key=moment, raw=(moment,))
        keys = 0
    else:
        for n in range(15):
            cluster, member = divmod(n, 5)
            moment = WINDOW_END - timedelta(
                hours=gap_hours * (cluster + 1), minutes=2 * member
            )
            world.user(n + 1, key=moment, raw=(moment,))
        keys = 100
    clock = _Clock()
    names: list[str] = []
    per_hop: list[int] = []
    stops: list[list[str]] = []
    cursor = None
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(20):
            if world_kind == "failure_spends_the_wall":
                engine = _MemoryEngine(
                    world, clock=clock, width=timedelta(hours=6), fail_ms=5_100.0
                )
            else:
                engine = _CappedEngine(world, clock=clock, enrich_ms=200.0)
            with capture_logs() as logs:
                read = _keyed_page(
                    page_size=25, cursor=cursor, engine=engine, keys=keys
                )
            names.extend(_names(read))
            per_hop.append(len(_names(read)))
            stops.append(_exhausted(logs))
            if not read.has_more:
                break
            cursor = _signed_cursor(read)

    assert names == [f"user-{n}" for n in range(1, len(world.users) + 1)]
    assert "wall" in [stop for hop in stops for stop in hop], stops
    for n in range(1, len(per_hop) - 1):
        assert per_hop[n - 1] or per_hop[n] or stops[n - 1] == ["read_budget"], (
            per_hop,
            stops,
        )


@pytest.mark.parametrize("slices", ["cheap", "every_capped_slice_stopped"])
def test_an_enrichment_that_fails_above_a_bucket_width_still_ends(slices):
    """Every enrichment wider than ten minutes runs out of memory, one user or five.

    Only the head-of-line user's enrichment is narrowed in time, one user a
    request, down to buckets that fit: the ten-minute tree, inside the
    remainder the walk documents at the least bucket, and every other
    statement inside the budget. The list ends, each user once and in
    order, whether the slices are cheap or every capped slice is stopped,
    because a split here (255 failures at 50 ms, 12.75 s) fits the analytics
    wall; one that does not is the known stall
    (``test_a_split_that_outlasts_the_analytics_wall_is_a_known_stall``).
    """
    world, expected = _spread_world(5, 3)
    slice_ms = _every_slice(40_000) if slices != "cheap" else None

    hops, _last = _memory_hops(
        world, max_hops=12, width=timedelta(minutes=10), slice_ms=slice_ms
    )

    assert [name for names, _s, _e in hops for name in names] == expected
    least = _split_statements(ulm._USER_LIST_ATTRIBUTE_MIN_BUCKET)
    assert least == 4_095
    for _, statements, enrichments in hops:
        # A batch of more than one user is never narrowed in time.
        assert all(
            bucket == WINDOW for users, bucket, _f in enrichments if users > 1
        ), enrichments
        narrowed = sum(1 for _u, bucket, _f in enrichments if bucket < WINDOW)
        assert narrowed in (0, _split_statements(TEN_MINUTES) - 1), narrowed
        assert narrowed <= least - 1
        assert statements - narrowed <= walk.USER_LIST_WALK_MAX_STATEMENTS


@pytest.mark.parametrize(
    ("slices", "fail_ms", "width"),
    [
        ("cheap", 120.0, TEN_MINUTES),
        ("every_capped_slice_stopped", 150.0, TEN_MINUTES),
        ("every_capped_slice_stopped", 50.0, timedelta(minutes=1)),
    ],
)
def test_a_split_that_outlasts_the_analytics_wall_is_a_known_stall(
    slices, fail_ms, width
):
    """The documented stall: a head-of-line split the analytics wall stops.

    Every enrichment wider than ``width`` runs out of memory and a failure
    costs ``fail_ms``, so splitting one user's read takes more than the 30 s
    analytics wall (255 failures at ten minutes, 2,047 at one). That split
    has no deadline after the request reads its head-of-line slice without a
    cap, or once the analytics wall is already spent: the first request with
    every capped slice stopped splits it whole, the documented bound at the
    least bucket, but cannot publish it (its key is that slice's floor). A
    split that starts with some of the wall left, on cheap slices or in the
    open instant the next request decides first, is stopped by the wall: the
    request decides no one, and the next request does the same. Each request
    still ends by that wall, degraded,
    without raising the coverage; the list does not move past the user. The
    walk documents this stall and does not retry it.
    """
    world, _expected = _spread_world(5, 3)
    slice_ms = _every_slice(40_000) if slices != "cheap" else None
    wall_ms = walk.USER_LIST_WALK_FINISH_WALL_MS
    clock = _Clock()
    cursor = None
    published: list[str] = []
    coverages = []
    stalled_ms = []
    with _shipped_walls(), _scripted_clock(clock):
        for hop in range(4):
            memory = _MemoryEngine(
                world,
                clock=clock,
                width=width,
                fail_ms=fail_ms,
                slice_ms=slice_ms,
            )
            started = clock.now
            with capture_logs() as logs:
                read, _engine = _page(world, page_size=25, cursor=cursor, engine=memory)
            published.extend(_names(read))
            assert read.has_more and read.payload["query_status"] == "degraded"
            assert _exhausted(logs) == ["wall"], _exhausted(logs)
            if slices == "cheap" or hop > 0:
                # Split under the wall, and stopped by it.
                stalled_ms.append((clock.now - started) * 1000)
                assert memory.split, hop
            else:
                # Split with no deadline: the whole tree, root included.
                assert list(memory.split.values()) == [_split_statements(width) - 1]
            coverages.append(read.checkpoint_order[3])
            cursor = _signed_cursor(read)

    assert published == []
    # Within a failure and the admission floor of the wall, either side.
    assert all(abs(ms - wall_ms) <= fail_ms + 25 for ms in stalled_ms), stalled_ms
    assert coverages == sorted(coverages, reverse=True)
    assert coverages[0] - coverages[-1] <= 4 * TICK, coverages


@pytest.mark.parametrize(
    ("keys", "finish", "most_requests", "most_statements"),
    [(21, 1, 24, 468), (13, 3, 24, 468)],
)
def test_a_request_certifies_only_what_it_can_also_publish(
    keys, finish, most_requests, most_statements
):
    """Off the head of line, a batch is certified only when its replay fits too.

    One decision reserves two enrichments and two finishes, so after two
    certified batches the budget left can hold a third batch's enrichment and
    not its replay: that request certified users it could not publish, ended
    in their instant, and the next request read the instant and enriched the
    same users again. No row here shares an instant, so a cursor opens one
    only for users it certified and could not publish: none may.
    """
    world = _unique_time_world(random.Random(3), 400, 0.0)
    clock = _Clock()
    names: list[str] = []
    cursor = None
    requests = statements = 0
    with _shipped_walls(), _scripted_clock(clock):
        for _hop in range(60):
            engine = _CappedEngine(world, clock=clock)
            read = _keyed_page(
                page_size=25, cursor=cursor, engine=engine, keys=keys, finish=finish
            )
            names.extend(_names(read))
            requests += 1
            statements += len(engine.calls)
            if not read.has_more:
                break
            order = tuple(read.checkpoint_order)
            assert len(order) == 4 or not order[4], (requests, engine.kinds)
            cursor = _signed_cursor(read)

    assert names == _expected(world)
    # The cost at 60dcf25ca.
    assert requests <= most_requests and statements <= most_statements, (
        requests,
        statements,
    )


# --------------------------------------------------------------------------
# The view's largest attribute key count.
# --------------------------------------------------------------------------


def test_the_views_largest_key_count_still_decides_every_user():
    """100 attribute keys, the most the Users view accepts, one of them filtered.

    Certifying a batch reads every key, four ordinary keys a statement: 26
    statements, more than the 24-statement budget, so every certification
    was refused and the list returned empty, degraded pages forever. A
    request may always spend one batch's decision: 63 statements here.
    """
    from tracer.serializers.trace import UsersQuerySerializer
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.users_list_manager import UsersListManager

    keys = [f"k{n:02d}" for n in range(99)] + ["tag"]
    assert UsersQuerySerializer(data={"attribute_keys": json.dumps(keys)}).is_valid()
    too_many = json.dumps([*keys, "k99"])
    assert not UsersQuerySerializer(data={"attribute_keys": too_many}).is_valid()

    world, expected = _spread_world(30, 3)
    driver = _NativeDriver(Engine(world))

    def page(cursor):
        client = ClickHouseClient(host="localhost", port=39999, pool_size=1)
        base = _manager()
        manager = UsersListManager(
            organization_id=base.organization_id,
            allowed_project_ids=list(base.scoped_project_ids),
            project_id=base.project_id,
            filters=base.filters,
            requested_columns=[],
            attribute_keys=keys,
        )
        assert walk._enrichment_statement_count(manager) == 26
        with (
            patch.object(client, "_get_client", return_value=driver),
            patch.object(client, "_return_client"),
            patch(
                "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
                return_value=client,
            ),
            patch.object(
                manager, "_read_dimension_candidates", side_effect=_never_seed
            ),
        ):
            before = len(driver.sent)
            read = manager.list_cursor_payload(page_size=25, cursor=cursor)
        return read, len(driver.sent) - before

    names = []
    cursor = None
    for _hop in range(6):
        read, statements = page(cursor)
        assert statements <= _one_decision(99) == 63, statements
        names.extend(_names(read))
        if not read.has_more:
            break
        cursor = _signed_cursor(read)
    assert names == expected


# --------------------------------------------------------------------------
# Property: generated worlds, from tiny budgets to the shipped limits.
# --------------------------------------------------------------------------


def _instants() -> list[datetime]:
    """Where ties are likeliest to hurt: the window's edges and slice floors."""

    first_floor = WINDOW_END - walk.USER_LIST_WALK_INITIAL_SLICE
    return [
        WINDOW_START,
        WINDOW_START + TICK,
        WINDOW_END - TICK,
        first_floor,
        first_floor - TICK,
        first_floor + TICK,
        minutes_before_end(3),
        minutes_before_end(61),
        minutes_before_end(90),
        WINDOW_END - timedelta(hours=5),
    ]


def _world(rng: random.Random, n_users: int) -> World:
    world = World()
    pool = rng.sample(range(1, 1 << 16), n_users * 4)
    instants = _instants()
    span = int((WINDOW_END - WINDOW_START) / TICK)
    tie_bias = rng.choice([0.0, 0.3, 0.7, 0.95])
    reject_rate = rng.choice([0.0, 0.12, 0.35])

    def moment() -> datetime:
        if rng.random() < tie_bias:
            return rng.choice(instants)
        return WINDOW_START + TICK * rng.randrange(span)

    for n in range(n_users):
        aliases = pool[n * 4 + 1 : n * 4 + 1 + rng.choice([0, 0, 0, 1, 2, 3])]
        # Witnessed but never matching live: no key, never a member.
        key = None if rng.random() < 0.12 else moment()
        raw = [(key, rng.randrange(4))] if key is not None else []
        # Other witnessed rows, above the key (stale versions) or below it.
        raw += [(moment(), rng.randrange(4)) for _ in range(rng.choice([0, 0, 1, 2]))]
        if not raw:
            raw = [(moment(), 0)]
        _add(
            world,
            pool[n * 4],
            aliases,
            key,
            raw,
            curated=rng.random() >= reject_rate,
        )
    return world


# (slice user limit, statement budget, certify batch, finishing statements):
# tiny budgets up to the shipped limits. A request never has fewer
# statements than one decision (``walk._statement_budget``), so the tiniest
# budgets here are lifted to that. The finishing statements are real ones:
# the page shows the columns and filters that make them (``FINISH_SHAPES``).
LIMITS = [
    (2, 6, 1, 1),
    (2, 8, 2, 2),
    (3, 7, 3, 1),
    (4, 10, 2, 2),
    (5, 12, 5, 3),
    (8, 24, 25, 4),
    (200, 24, 25, 1),
    (200, 24, 25, 5),
]
PAGE_SIZES = [1, 2, 3, 7, 25, 100]
STATIC_WORLDS = 124
CHANGING_WORLDS = 28
# What a slice that returns rows costs, per run of the limit grid: nothing
# much; 6 s, past the page wall; 40 s, past the whole request; or 40 s an
# hour, past its cap only while it is wide.
SLICE_MODELS = [
    None,
    _every_slice(6_000),
    None,
    _every_slice(40_000),
    _per_hour(40_000),
]


# A native witness has no skip index: its slice reads every row of its range,
# so an EMPTY one costs in proportion to its width too.
UNPRUNED_SLICE = _any_per_hour(2_000)


def _slice_model(
    seed: int, family: str = "raw"
) -> Callable[[timedelta, bool], float] | None:
    model = SLICE_MODELS[(seed // len(LIMITS)) % len(SLICE_MODELS)]
    if family == "native" and model is None and seed % 2:
        return UNPRUNED_SLICE
    return model


# Attribute columns the page shows besides the filtered key, up to the Users
# view's maximum of 100: one enrichment statement per four of them, so 1 to
# 26 enrichment statements per certified batch. None is listed twice: most
# pages show few columns, and every column costs statements to build.
KEY_COUNTS = [0, 0, 3, 12, 40, 100]


def _key_count(seed: int) -> int:
    # A generator of its own, so the key count varies independently of the
    # limit grid and the slice model, and the world's own draws stay put.
    return random.Random(7_919 * seed + 1).choice(KEY_COUNTS)


# Enrichment faults per world (``_MemoryEngine``): none, most often; every
# enrichment of more than one user runs out of memory, so the head-of-line
# user alone is certified again, at 40, 44 or 100 keys; one enrichment
# statement fails at the request's k-th; a failed batch costs more than the
# whole analytics wall, and what decides the head of line after it runs
# with no deadline; or every enrichment wider than an hour or six fails, one
# user or many, in a world with hours between users: the head of line
# splits, and the next user, met off the head path (often below an empty
# first slice), stops the request.
FAULTS = ["none", "none", "batch", "index", "batch_past_the_wall", "width"]


@dataclass
class _Faults:
    fault: Callable[[int, int], bool] | None = None
    fail_ms: float = 50.0
    width: timedelta | None = None
    gaps: bool = False


def _fault(seed: int, keys: int, kinds=tuple(FAULTS)) -> tuple[_Faults, int]:
    """The enrichment faults of ``seed``'s world, and the keys its page shows."""

    rng = random.Random(6_007 * seed + 5)
    kind = rng.choice(kinds)
    if kind == "none":
        return _Faults(), keys
    if kind == "index":
        k = rng.randrange(3 * _enrichments(keys))
        return _Faults(fault=lambda _users, index: index == k), keys
    if kind == "width":
        width = timedelta(hours=rng.choice([1, 6]))
        return _Faults(fail_ms=1.0, width=width, gaps=True), min(keys, 12)
    fail_ms = 40_000.0 if kind == "batch_past_the_wall" else 50.0
    return _Faults(fault=lambda users, _i: users > 1, fail_ms=fail_ms), rng.choice(
        [40, 44, 100]
    )


def _with_native(world: World, seed: int, family: str) -> World:
    """Give every user a native decision when the page carries a native leaf.

    From a generator of its own, so the raw world is the same one the raw
    family draws. A native witness keys on the leaf's newest latest live
    match (the world's ``key``); a user that has one can still fail the leaf
    (a family-less negation's forbidden value elsewhere in the window), and a
    user with none never passes it. A raw witness keys on the attribute, and
    the native leaf is then any user's independent decision.
    """
    if family == "raw":
        return world
    rng = random.Random(4_441 * seed + 17)
    forbid = rng.choice([0.0, 0.15, 0.4])
    for user in world.users.values():
        if family == "native":
            user["native"] = user["key"] is not None and rng.random() >= forbid
        else:
            user["native"] = rng.random() >= forbid
    return world


def _gap_world(rng: random.Random, n_users: int) -> World:
    """Users 20 minutes to 6 hours apart, one row each, as many as fit."""

    world = World()
    moment = WINDOW_END
    for n in range(n_users):
        moment -= timedelta(minutes=rng.randrange(20, 360))
        if moment < WINDOW_START:
            break
        _add(world, 30_000 + n, [], moment, [(moment, 0)])
    return world


def _slice_hops(world: World, seed: int, family: str = "raw") -> int:
    """Extra hops a world may take when slices are slow.

    When no capped slice can return rows, only the head-of-line slice read
    without a cap, one least width wide, returns rows, and a request reads
    one: every least-width window that holds a witnessed row may cost a
    request of its own. When every slice costs by its width, empty or not
    (``UNPRUNED_SLICE``, two seconds an hour), the page wall admits a few
    hours of window a request: at most one request an hour of the window.
    """
    model = _slice_model(seed, family)
    if model is UNPRUNED_SLICE:
        return int(WINDOW / timedelta(hours=1))
    if model is not SLICE_MODELS[3]:
        return 0
    return len({moment.replace(second=0, microsecond=0) for moment, _id in world.raw})


@contextmanager
def _limits(seed: int):
    """The walk's limits for ``seed``; yields its statement budget and the
    finishing statements its page makes."""

    slice_limit, max_statements, batch, finish = LIMITS[seed % len(LIMITS)]
    with (
        patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", slice_limit),
        patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", max_statements),
        patch.object(walk, "USER_LIST_WALK_CERTIFY_BATCH_SIZE", batch),
    ):
        yield max_statements, finish


def _hop_bound(n_users: int, page_size: int, heavy: int) -> int:
    """The hops a world may take.

    A hop publishes a user, decides one (rejected, or placed elsewhere), or
    lowers the coverage by at least a slice; a hop that does none of these
    opens the instant below the coverage, and the next one decides there.
    So two hops per user, the empty window's slices, and, for each heavy
    user, one-user pages for the users sharing its replay batch ahead of it.
    """

    return 30 + 2 * n_users + heavy * (min(page_size, n_users) + 2)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("seed", range(STATIC_WORLDS))
def test_every_hop_sequence_ends_exact_and_never_raises_the_coverage(seed, family):
    rng = random.Random(seed)
    n_users = rng.choice([1, 3, 8, 20, 45, 80])
    # A native statement has no time split: the width fault stays raw.
    kinds = FAULTS if family == "raw" else [k for k in FAULTS if k != "width"]
    faults, keys = _fault(seed, _key_count(seed), kinds)
    world = _world(rng, n_users)
    if faults.gaps:
        world = _gap_world(random.Random(3 * seed + 1), n_users)
        n_users = len(world.users)
    world = _with_native(world, seed, family)
    page_size = rng.choice(PAGE_SIZES)
    heavy = frozenset(
        rng.sample(sorted(world.users), min(n_users, rng.choice([0, 0, 0, 1, 2, 3])))
    )
    # A quarter of the worlds lose the server on every fifth request.
    outage = random.Random(31 * seed + 7).random() < 0.25
    bound = _hop_bound(n_users, page_size, len(heavy)) + _slice_hops(
        world, seed, family
    )
    with _limits(seed) as (max_statements, finish), _shipped_walls():
        names, _hops = _follow(
            world,
            page_size=page_size,
            max_hops=bound + (bound // 4 if outage else 0),
            max_statements=max_statements,
            heavy=heavy,
            slice_ms=_slice_model(seed, family),
            keys=keys,
            finish=finish,
            outage_every=5 if outage else None,
            fault=faults.fault,
            fail_ms=faults.fail_ms,
            width=faults.width,
            family=family,
        )

    assert len(names) == len(set(names)), "a user was published twice"
    assert names == _expected(world)


def _stop_matching(rng: random.Random, changed: set[str]):
    """Between hops, an unpublished member may stop matching or be rejected.

    On a page with a native leaf, stopping to match may also be the native
    leaf turning false (a forbidden value appears elsewhere in the window).
    """

    def mutate(world: World, published: set[str], _cursor: tuple) -> None:
        members = [
            uid
            for uid, user in world.users.items()
            if uid not in published and _is_member(user)
        ]
        if not members or rng.random() < 0.5:
            return
        uid = rng.choice(members)
        changed.add(uid)
        if rng.random() < 0.5:
            world.users[uid]["key"] = None
        elif world.users[uid]["native"] is not None and rng.random() < 0.5:
            world.users[uid]["native"] = False
        else:
            world.users[uid]["curated"] = False

    return mutate


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("seed", range(CHANGING_WORLDS))
def test_users_that_stop_matching_between_hops_never_stall_or_repeat(seed, family):
    rng = random.Random(10_000 + seed)
    n_users = rng.choice([8, 20, 45, 80])
    world = _with_native(_world(rng, n_users), 10_000 + seed, family)
    before = _expected(world)
    page_size = rng.choice([1, 3, 7, 25])
    heavy = frozenset(rng.sample(sorted(world.users), rng.choice([0, 0, 1, 2])))
    changed: set[str] = set()
    # The width fault needs its gap world, which only the static test draws.
    faults, keys = _fault(
        10_000 + seed, _key_count(seed), [kind for kind in FAULTS if kind != "width"]
    )
    with _limits(seed) as (max_statements, finish), _shipped_walls():
        names, _hops = _follow(
            world,
            page_size=page_size,
            max_hops=_hop_bound(n_users, page_size, len(heavy))
            + _slice_hops(world, seed, family),
            max_statements=max_statements,
            heavy=heavy,
            mutate=_stop_matching(rng, changed),
            slice_ms=_slice_model(seed, family),
            keys=keys,
            finish=finish,
            fault=faults.fault,
            fail_ms=faults.fail_ms,
            width=faults.width,
            family=family,
        )

    assert len(names) == len(set(names)), "a user was published twice"
    # Users that never changed are all published, in order; a changed user
    # appears only if it was published before it changed.
    assert [uid for uid in names if uid not in changed] == [
        uid for uid in before if uid not in changed
    ]
    assert set(names) <= set(before)


def _passed(key: datetime, uid: str, cursor: tuple) -> bool:
    """Whether ``cursor`` has already walked past the position ``(key, uid)``."""

    _marker, last_key, last_id, coverage = cursor[:4]
    return key >= coverage or (
        last_key is not None and (key, uid) >= (last_key, last_id)
    )


def _change(rng: random.Random, kind: str, changed: dict[str, bool]):
    """Between hops, an unpublished user starts matching or its key moves.

    ``start`` makes a non-member a member (a new live match, or no longer
    rejected); ``up`` and ``down`` move a member's newest live match, its
    old row staying behind as a witness. Each user changes at most once;
    ``changed`` records whether the cursor had already passed its new key.
    """

    def mutate(world: World, published: set[str], cursor: tuple) -> None:
        if rng.random() < 0.5:
            return
        uid = rng.choice(sorted(world.users))
        user = world.users[uid]
        if uid in published or uid in changed:
            return
        member = _is_member(user)
        if kind == "start":
            if member:
                return
            user["curated"] = True
            if user["native"] is False:
                user["native"] = True
            if user["key"] is None:
                user["key"] = WINDOW_START + TICK * rng.randrange(WINDOW // TICK)
        elif not member:
            return
        elif kind == "up":
            user["key"] = user["key"] + (WINDOW_END - TICK - user["key"]) * rng.random()
        else:
            user["key"] = WINDOW_START + (user["key"] - WINDOW_START) * rng.random()
        world.raw.append((user["key"], uid))
        changed[uid] = _passed(user["key"], uid, cursor)

    return mutate


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("kind", ["start", "up", "down"])
@pytest.mark.parametrize("seed", range(16))
def test_users_that_change_between_hops_follow_the_coverage_fence(kind, seed, family):
    """A change a cursor has walked past is not published by it; any other is.

    A user whose new key lies at or above the cursor's coverage, or behind
    its keyset, was decided before the change as the data stood then, so
    the cursor does not publish it (a new first page would). A user whose new
    key lies ahead of the cursor is published once, where it now sorts.
    Unchanged users are all published, once, in order.
    """
    rng = random.Random(20_000 + 97 * seed + len(kind))
    n_users = rng.choice([8, 20, 45])
    world = _with_native(_world(rng, n_users), 20_000 + 97 * seed, family)
    page_size = rng.choice([1, 3, 7, 25])
    changed: dict[str, bool] = {}
    with _limits(seed) as (max_statements, finish), _shipped_walls():
        names, _hops = _follow(
            world,
            page_size=page_size,
            max_hops=_hop_bound(n_users, page_size, 0)
            + _slice_hops(world, seed, family),
            max_statements=max_statements,
            mutate=_change(rng, kind, changed),
            slice_ms=_slice_model(seed, family),
            finish=finish,
            family=family,
        )

    assert len(names) == len(set(names)), "a user was published twice"
    members = set(_expected(world))
    for uid, passed in changed.items():
        assert (uid in names) == (not passed and uid in members), (uid, passed)
    # Everything published is in newest-matching-activity order as it stands.
    positions = {uid: (world.users[uid]["key"], uid) for uid in names}
    assert names == sorted(names, key=positions.__getitem__, reverse=True)
    assert [uid for uid in names if uid not in changed] == [
        uid for uid in _expected(world) if uid not in changed
    ]
