"""The span-attribute-filtered Users page walks newest matching activity.

A scripted world stands in for ClickHouse: raw witness rows (a superset that
includes stale versions, keyed by the RAW id a span carries), the survivor
map, latest-state order keys, and whole-window totals are separate answers,
so each guard can see which statement decided what.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services import users_matching_walk as walk
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager

pytestmark = pytest.mark.unit

PROJECT = str(uuid.UUID(int=201))
ORG = str(uuid.UUID(int=202))
WINDOW_START = datetime(2026, 9, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 9, 2, tzinfo=UTC)
SERVICE = "tracer.services.users_list_manager.V2AnalyticsQueryService"
# ``_USERS_ORIGIN_SHAPES["no_text_witness"]`` in
# scripts/qa/replay_observe_queries_readonly.py: the unfiltered cursor page.
UNFILTERED_PAGE_DIGEST = (
    "ba51ea62b5e2f3831b6d9d1e4ab345283c068af90f1795bb7b7082526cd4d4e5"
)


def minutes_before_end(minutes: float) -> datetime:
    return WINDOW_END - timedelta(minutes=minutes)


def _from_us(value: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=int(value))


class World:
    """Users with raw witness rows, a latest-state order key and totals."""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.raw: list[tuple[datetime, str]] = []
        self.canonical: dict[str, str] = {}

    def user(
        self,
        ordinal: int,
        *,
        key: datetime | None,
        raw: tuple[datetime, ...],
        cost: float = 1.0,
        aliases: int = 0,
        curated: bool = True,
        typed_values: list[tuple[str, str]] | None = None,
    ) -> str:
        uid = str(uuid.UUID(int=1000 + ordinal))
        alias_ids = tuple(
            str(uuid.UUID(int=5000 + ordinal * 10 + n)) for n in range(aliases)
        )
        self.users[uid] = {
            "key": key,
            # The user's whole-window cost sits on its OLDEST raw row, so a
            # replay narrowed to anything newer than that row loses it.
            "cost": cost,
            "aliases": (uid, *alias_ids),
            "curated": curated,
            "name": f"user-{ordinal}",
            "typed_values": typed_values or [("string", '"Gold"')],
        }
        for alias in (uid, *alias_ids):
            self.canonical[alias] = uid
        for index, moment in enumerate(raw):
            # Spread raw rows over the user's aliases so alias resolution is live.
            self.raw.append((moment, self.users[uid]["aliases"][index % (aliases + 1)]))
        return uid

    def replayed_totals(
        self, uid: str, low: datetime, high: datetime
    ) -> tuple[float, int]:
        """``(total_cost, num_traces)`` of ``uid`` over ``[low, high)``."""

        rows = sorted(
            moment
            for moment, raw_id in self.raw
            if self.canonical[raw_id] == uid and low <= moment < high
        )
        every = [moment for moment, raw_id in self.raw if self.canonical[raw_id] == uid]
        oldest = min(every) if every else None
        carries_cost = oldest is not None and low <= oldest < high
        return (self.users[uid]["cost"] if carries_cost else 0.0), len(rows)


def kind_of(query: str) -> str:
    if "AS raw_end_user_id" in query:
        return "slice"
    if query.lstrip().startswith("EXPLAIN ESTIMATE"):
        return "estimate"
    if "AS witnessed" in query:
        return "probe"
    if "dimension_candidate_ids" in query:
        return "remap"
    if "latest_candidate_attribute_values" in query:
        return "enrich"
    if "session_rows AS" in query:
        return "metrics"
    if "candidate_users AS" in query:
        return "replay"
    raise AssertionError("unexpected statement: " + " ".join(query.split())[:120])


class Engine:
    """Answers the walk's statement shapes from the world."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.calls: list[str] = []
        self.enriched: list[tuple[str, ...]] = []
        self.enrichment_scan_ids: list[tuple[str, ...]] = []
        self.replayed: list[tuple[str, ...]] = []
        self.remapped: list[tuple[str, ...]] = []
        self.slice_ranges: list[tuple[datetime, datetime]] = []
        self.probe_ranges: list[tuple[datetime, datetime]] = []
        self.estimate_ranges: list[tuple[datetime, datetime]] = []
        self.replay_ranges: list[tuple[datetime, datetime]] = []
        # The estimate table the scripted server returns for a tail: by
        # default the raw rows in range (a true index estimate); a test may
        # set an integer to over-report (bloom false positives with no real
        # row), an empty list for a plan the reducer cannot read, or a list
        # of rows for any other shape.
        self.estimate_override: int | list | None = None
        # The client-observed time the scripted server reports for the
        # estimate: what the walk charges the probe's wall.
        self.estimate_ms: float = 1.0
        # Per statement: the read settings and the timeout the walk sent.
        self.settings: list[dict | None] = []
        self.timeouts: list[float | None] = []

    def execute_ch_query(self, query, params=None, timeout_ms=None, settings=None):
        self.calls.append(query)
        self.settings.append(settings)
        self.timeouts.append(timeout_ms)
        params = params or {}
        kind = kind_of(query)
        if kind == "slice":
            return self._slice(params)
        if kind == "estimate":
            return self._estimate(params)
        if kind == "probe":
            return self._probe(params)
        if kind == "remap":
            return self._remap(params)
        if kind == "enrich":
            return self._enrich(params)
        return self._replay(params)

    def _witnessed(self, params, ranges):
        low, high = _from_us(params["slice_start_us"]), _from_us(params["slice_end_us"])
        ranges.append((low, high))
        groups: dict[str, datetime] = {}
        for moment, raw_id in self.world.raw:
            if low <= moment < high:
                groups[raw_id] = max(groups.get(raw_id, moment), moment)
        return groups

    def _slice(self, params):
        rows = sorted(
            self._witnessed(params, self.slice_ranges).items(),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )
        if "slice_before_us" in params:
            before = (
                _from_us(params["slice_before_us"]),
                params["slice_before_end_user_id"],
            )
            rows = [(rid, moment) for rid, moment in rows if (moment, rid) < before]
        rows = rows[: params["slice_user_limit"]]
        return SimpleNamespace(
            data=[
                {"raw_end_user_id": rid, "raw_newest": moment} for rid, moment in rows
            ],
            query_time_ms=1.0,
        )

    def _probe(self, params):
        found = bool(self._witnessed(params, self.probe_ranges))
        return SimpleNamespace(
            data=[{"witnessed": 1}] if found else [], query_time_ms=1.0
        )

    def _estimate(self, params):
        in_range = self._witnessed(params, self.estimate_ranges)
        columns = ["database", "table", "parts", "rows", "marks"]
        if isinstance(self.estimate_override, list):
            return SimpleNamespace(
                data=list(self.estimate_override), columns=columns, query_time_ms=1.0
            )
        rows = (
            self.estimate_override
            if self.estimate_override is not None
            else len(in_range)
        )
        return SimpleNamespace(
            data=[
                {
                    "database": "default",
                    "table": "spans",
                    "parts": int(bool(rows)),
                    "rows": rows,
                    "marks": rows,
                }
            ],
            columns=columns,
            query_time_ms=self.estimate_ms,
        )

    def _remap(self, params):
        ids = tuple(params["dimension_candidate_ids"])
        self.remapped.append(ids)
        rows = []
        for survivor in {self.world.canonical[rid] for rid in ids}:
            aliases = self.world.users[survivor]["aliases"]
            if len(aliases) > 1:
                rows.extend(
                    {"any_id": alias, "survivor_id": survivor} for alias in aliases
                )
        return SimpleNamespace(data=rows, query_time_ms=1.0)

    def _enrich(self, params):
        ids = tuple(params["eu_ids"])
        self.enriched.append(ids)
        self.enrichment_scan_ids.append(tuple(params["eu_scan_ids"]))
        data = []
        for uid in ids:
            key = self.world.users[uid]["key"]
            if key is None:
                continue
            data.append(
                {
                    "end_user_id": uid,
                    "attribute_key": params["requested_attribute_keys"][0],
                    "attribute_typed_values": self.world.users[uid]["typed_values"],
                    "latest_matching_start_time": key,
                }
            )
        return SimpleNamespace(data=data, query_time_ms=1.0)

    def _replay(self, params):
        ids = tuple(params["candidate_end_user_ids"])
        self.replayed.append(ids)
        # The replay answers over the window the statement carries, so a
        # replay narrowed to a slice returns that slice's totals, not the
        # window's: the totals guard can see it.
        low = _from_us(params["user_window_start_us"])
        high = _from_us(params["user_window_end_us"])
        self.replay_ranges.append((low, high))
        data = []
        for uid in ids:
            user = self.world.users[uid]
            if not user["curated"]:
                continue
            total_cost, num_traces = self.world.replayed_totals(uid, low, high)
            data.append(
                {
                    "user_id": user["name"],
                    "total_cost": total_cost,
                    "total_tokens": 10,
                    "input_tokens": 6,
                    "output_tokens": 4,
                    "num_traces": num_traces,
                    "num_sessions": 0,
                    "activated_at": WINDOW_START,
                    "last_active": user["key"],
                    "project_id": PROJECT,
                    "user_id_type": "custom",
                    "user_id_hash": "",
                    "end_user_id": uid,
                    "total_count": 0,
                }
            )
        return SimpleNamespace(data=data, query_time_ms=1.0)


def _attribute_filter(**config):
    return {
        "column_id": config.pop("column_id", "tag"),
        "filter_config": {"col_type": "SPAN_ATTRIBUTE", **config},
    }


def _filters(*, window_start=WINDOW_START, window_end=WINDOW_END, attribute=None):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        attribute
        or _attribute_filter(
            filter_type="text", filter_op="equals", filter_value="gold"
        ),
    ]


def _manager(filters=None) -> UsersListManager:
    return UsersListManager(
        organization_id=ORG,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        filters=filters or _filters(),
        requested_columns=[],
        attribute_keys=[],
    )


def _never_seed(**kwargs):
    raise AssertionError("the whole-window candidate statement must never run")


def _page(
    world: World,
    *,
    page_size: int,
    cursor=None,
    engine: Engine | None = None,
    filters=None,
):
    manager = _manager(filters)
    engine = engine or Engine(world)
    with (
        patch(SERVICE, return_value=engine),
        patch.object(manager, "_read_dimension_candidates", side_effect=_never_seed),
    ):
        read = manager.list_cursor_payload(page_size=page_size, cursor=cursor)
    return read, engine


def _cursor(read) -> ListCursor:
    assert read.has_more and read.checkpoint_order is not None
    return ListCursor(
        window_start=read.window_start,
        window_end=read.window_end,
        order=tuple(read.checkpoint_order),
        seen_rows=read.seen_rows,
    )


def _names(read) -> list[str]:
    return [row["user_id"] for row in read.payload["table"]]


def _kinds(engine: Engine) -> list[str]:
    return [kind_of(call) for call in engine.calls]


def test_filtered_page_orders_by_newest_matching_activity_and_never_seeds():
    world = World()
    world.user(1, key=minutes_before_end(30), raw=(minutes_before_end(30),))
    world.user(2, key=minutes_before_end(5), raw=(minutes_before_end(5),))
    world.user(3, key=minutes_before_end(90), raw=(minutes_before_end(90),))
    # Witnessed only on stale versions: no live matching span, never a member.
    world.user(4, key=None, raw=(minutes_before_end(1),))

    read, engine = _page(world, page_size=25)

    assert _names(read) == ["user-2", "user-1", "user-3"]
    assert read.has_more is False and read.checkpoint_order is None
    assert read.payload["query_provenance"] == "matching_activity_walk"
    assert read.payload["ordering"] == "latest_matching_activity"
    assert read.payload["query_exact"] is True
    assert all("scalar_witness_identities" not in call for call in engine.calls)
    assert set(_kinds(engine)) <= {"slice", "remap", "enrich", "replay", "probe"}


PICKED_JSON = '{"user":"u-1","stage":"final","note":"a long picked value"}'


@pytest.mark.parametrize(
    "picked",
    [[PICKED_JSON], [PICKED_JSON, "[1,2]"], ["true"]],
    ids=["json-object", "json-object-and-array", "boolean-word"],
)
def test_a_picked_json_looking_string_walks_through_the_text_witness(picked):
    """Picker provenance admits JSON- and boolean-looking strings to the walk.

    A picked string is the stored string and compares raw (the classifier's
    picker branch), so the exact-text lane's witness - the deployed value
    bloom over ``lower()`` of the stored values - is exhaustive for it: the
    page walks exactly as a plain-text filter does, narrows its certification
    on the picked values and never issues the whole-window statement. Typed
    JSON-looking text keeps the seeded page (below).
    """
    filters = _filters(
        attribute=_attribute_filter(
            filter_type="text",
            filter_op="in",
            filter_value=list(picked),
            attribute_value_types=["string"] * len(picked),
        )
    )
    world = World()
    world.user(
        1,
        key=minutes_before_end(30),
        raw=(minutes_before_end(30),),
        typed_values=[("string", '"' + picked[0].replace('"', '\\"') + '"')],
    )
    world.user(2, key=None, raw=(minutes_before_end(5),))
    manager = _manager(filters)
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=filters, empty_scope=False
    )
    assert manager.matching_activity_walk_applies(builder) is True
    assert manager._walked_typed_filter is None
    assert manager.attribute_exact_text_filters == {
        "tag": tuple(dict.fromkeys(value.lower() for value in picked))
    }

    read, engine = _page(world, page_size=25, filters=filters)

    assert _names(read) == ["user-1"]
    assert read.payload["query_provenance"] == "matching_activity_walk"
    slices = [call for call in engine.calls if kind_of(call) == "slice"]
    assert slices and all("attrs_string[" in call for call in slices)
    enrichments = [call for call in engine.calls if kind_of(call) == "enrich"]
    assert enrichments and all(
        "candidate_attribute_values_0" in call for call in enrichments
    )
    assert not any(
        hashlib.sha256(call.encode()).hexdigest() == UNFILTERED_PAGE_DIGEST
        for call in engine.calls
    )


def test_populated_slice_resolves_aliases_through_the_bounded_survivor_statement():
    """The slice reads spans alone; a populated slice is followed by one remap.

    The slice statement touches no other table and returns raw ids; the
    existing bounded survivor statement then runs over exactly those ids, and
    the certification scans every alias of the resolved user. An empty slice
    issues no remap at all.
    """
    world = World()
    walked = world.user(
        1, key=minutes_before_end(3), raw=(minutes_before_end(3),), aliases=2
    )
    read, engine = _page(world, page_size=25)

    assert _names(read) == ["user-1"]
    kinds = _kinds(engine)
    assert kinds[:2] == ["slice", "remap"]
    slice_sql = engine.calls[0]
    assert "end_user_id_remap" not in slice_sql and "JOIN" not in slice_sql
    assert slice_sql.count("FROM spans") == 1
    raw_ids = {raw_id for _moment, raw_id in world.raw}
    assert set(engine.remapped[0]) == raw_ids
    aliases = set(world.users[walked]["aliases"])
    assert set(engine.enrichment_scan_ids[0]) == aliases
    assert engine.enriched == [(walked,)]
    # The remap is the seeded page's own bounded survivor statement.
    assert "dimension_candidate_ids" in engine.calls[1]
    assert "end_user_id_remap FINAL" in engine.calls[1]

    empty = World()
    read, engine = _page(empty, page_size=25)
    assert _names(read) == [] and read.has_more is False
    assert "remap" not in _kinds(engine)


def test_floor_rule_carries_a_stale_witness_below_the_slice_floor():
    """A raw witness above the floor does not place a user whose key is below it.

    P's newest raw row is a stale version; its live matching span is older
    than H's. A slice truncated at two ids returns P and A, so P must wait
    until the walk has covered H's activity; publishing P on its raw position
    would put it ahead of H.
    """
    world = World()
    world.user(1, key=minutes_before_end(2), raw=(minutes_before_end(2),))  # A
    world.user(
        2,
        key=minutes_before_end(30),
        raw=(minutes_before_end(1), minutes_before_end(30)),
    )  # P
    world.user(3, key=minutes_before_end(15), raw=(minutes_before_end(15),))  # H

    with patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 2):
        read, engine = _page(world, page_size=25)

    assert _names(read) == ["user-1", "user-3", "user-2"]
    assert read.has_more is False
    # P and A came out of the first slice; the second slice continued past A.
    assert any("slice_before_us" in call for call in engine.calls)


def test_totals_come_from_the_whole_window_replay_for_published_users_only():
    """The scripted replay answers over the window its statement carries.

    The published user's cost sits on a raw row twelve hours below the
    one-hour slice that discovered it: a replay narrowed to that slice (or
    to anything newer than the window's start) returns zero, so this guard
    sees a narrowed replay, not only a missing one.
    """
    world = World()
    published = world.user(
        1,
        key=minutes_before_end(5),
        raw=(minutes_before_end(5), minutes_before_end(720)),
        cost=6.0,
    )
    carried = world.user(
        2, key=minutes_before_end(40), raw=(minutes_before_end(40),), cost=9.0
    )
    world.user(3, key=None, raw=(minutes_before_end(3),))

    read, engine = _page(world, page_size=1)

    assert _names(read) == ["user-1"]
    assert read.payload["table"][0]["total_cost"] == 6.0
    assert read.payload["table"][0]["num_traces"] == 2
    # One replay, for the published user alone, over the whole window: the
    # carried member and the stale witness were never replayed, and nothing
    # was summed from a slice.
    assert engine.replayed == [(published,)]
    assert engine.replay_ranges == [(WINDOW_START, WINDOW_END)]
    assert engine.slice_ranges[0][0] > minutes_before_end(720)
    assert carried not in {uid for ids in engine.replayed for uid in ids}
    assert read.has_more is True
    assert read.unseen_row_proven is False


def test_cursor_resumes_without_skipping_or_repeating_a_user():
    world = World()
    for ordinal, minutes in enumerate((3, 7, 11, 200, 260, 1000), start=1):
        world.user(
            ordinal,
            key=minutes_before_end(minutes),
            raw=(minutes_before_end(minutes), minutes_before_end(minutes + 500)),
            aliases=ordinal % 2,
        )
    expected = [f"user-{n}" for n in range(1, 7)]

    seen: list[str] = []
    cursor = None
    pages = 0
    with patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 2):
        while True:
            read, _engine = _page(world, page_size=2, cursor=cursor)
            pages += 1
            seen.extend(_names(read))
            if not read.has_more:
                break
            cursor = _cursor(read)
            assert cursor.order[0] == walk.USER_LIST_MATCHING_CURSOR_ORDER
            assert pages < 12

    assert seen == expected
    assert read.seen_rows == len(expected)


def test_budget_exhaustion_returns_partial_page_and_cursor_without_fallback():
    world = World()
    for ordinal, minutes in enumerate((3, 7, 11), start=1):
        world.user(
            ordinal, key=minutes_before_end(minutes), raw=(minutes_before_end(minutes),)
        )

    # One statement: the slice runs, its survivor statement is refused, the
    # slice is discarded whole and the cursor re-reads it.
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 1):
        read, engine = _page(world, page_size=25)
    assert read.payload["table"] == []
    assert read.has_more is True
    assert _kinds(engine) == ["slice"]
    assert read.checkpoint_order[0] == walk.USER_LIST_MATCHING_CURSOR_ORDER
    assert read.checkpoint_order[1] is None and read.checkpoint_order[2] is None
    assert read.checkpoint_order[3] == WINDOW_END

    # Two statements: resolved but not certified; nothing is enriched.
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 2):
        read, engine = _page(world, page_size=25)
    assert read.payload["table"] == [] and read.has_more is True
    assert _kinds(engine) == ["slice", "remap"] and engine.enriched == []

    # Three statements: certified but not materialised; the cursor carries the
    # certified keys so the next page starts above them.
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 3):
        read, engine = _page(world, page_size=25)
    assert read.payload["table"] == []
    assert read.has_more is True
    assert engine.replayed == []
    assert read.checkpoint_order[3] > minutes_before_end(3)

    # The same cursor resumes under a full budget and publishes everyone once.
    resumed, _engine = _page(world, page_size=25, cursor=_cursor(read))
    assert _names(resumed) == ["user-1", "user-2", "user-3"]
    assert resumed.has_more is False


def test_slice_read_exhaustion_returns_partial_page_and_cursor_without_fallback():
    """The budget can run out AT a slice read; that path issues no seed either.

    The first slice is populated and spends four statements (slice, survivor,
    certification, replay) publishing its user; the page is not full, so the
    walk wants a second slice. With four statements in the budget that read
    is refused before it is sent, inside ``_read_slice``: a tail probe is
    never asked below a populated slice, so this is the only budget refusal
    that reaches the slice read itself. With the wall spent inside the
    transport the read raises instead. Both end the page with a cursor at
    the first slice's start and never issue the whole-window statement.
    """
    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))

    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 4):
        read, engine = _page(world, page_size=25)
    assert _kinds(engine) == ["slice", "remap", "enrich", "replay"]
    assert _names(read) == ["user-1"] and read.has_more is True
    assert read.checkpoint_order[3] == engine.slice_ranges[0][0] + walk._TICK

    world = World()
    world.user(1, key=minutes_before_end(600), raw=(minutes_before_end(600),))
    engine = Engine(world)
    original = engine.execute_ch_query

    def wall_spent_in_transport(query, params=None, timeout_ms=None, settings=None):
        if len(engine.calls) == 1 and kind_of(query) == "slice":
            engine.calls.append(query)
            raise ReadDeadlineExceeded("read deadline exceeded")
        return original(query, params, timeout_ms, settings)

    engine.execute_ch_query = wall_spent_in_transport
    read, engine = _page(world, page_size=25, engine=engine)
    assert _kinds(engine) == ["slice", "slice"]
    assert read.payload["table"] == [] and read.has_more is True
    assert read.checkpoint_order[3] == engine.slice_ranges[0][0] + walk._TICK


def test_wall_exhaustion_stops_between_statements():
    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))

    # The wall is checked between statements: the slice statement alone
    # outlives it, so its survivor statement is refused and the page is
    # partial.
    with patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60):
        engine = Engine(world)
        original = engine.execute_ch_query

        def slow(query, params=None, timeout_ms=None, settings=None):
            import time

            time.sleep(0.08)
            return original(query, params, timeout_ms, settings)

        engine.execute_ch_query = slow
        read, engine = _page(world, page_size=25, engine=engine)

    assert read.payload["table"] == []
    assert read.has_more is True
    assert _kinds(engine) == ["slice"]


def test_a_slice_that_fails_on_a_read_budget_is_retried_narrower_never_wider():
    from clickhouse_driver.errors import ServerException

    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    engine = Engine(world)
    original = engine.execute_ch_query
    widths: list[int] = []

    def flaky(query, params=None, timeout_ms=None, settings=None):
        if kind_of(query) == "slice":
            widths.append(params["slice_end_us"] - params["slice_start_us"])
            if len(widths) == 1:
                raise ServerException("memory", code=241)
        return original(query, params, timeout_ms, settings)

    engine.execute_ch_query = flaky
    read, engine = _page(world, page_size=25, engine=engine)

    assert _names(read) == ["user-1"]
    assert len(widths) >= 2 and widths[1] == widths[0] // 4


def test_empty_default_window_is_proven_by_one_costed_existence_probe():
    """An empty thirty-day window is three statements, not thirty-one.

    After the first empty slice the tail needs more slices at the one-day
    cap than the statement budget holds, so the walk costs one existence
    statement over the whole tail (EXPLAIN ESTIMATE of the identical text,
    no column data) and, the estimate fitting the target, asks it whether
    any witnessed row lies below at all; none proves the window exhausted.
    The estimate alone never does: a planner's count is not a row read.
    """
    thirty_days = _filters(window_start=WINDOW_END - timedelta(days=30))
    read, engine = _page(World(), page_size=25, filters=thirty_days)

    assert _kinds(engine) == ["slice", "estimate", "probe"]
    assert read.payload["table"] == []
    assert read.has_more is False and read.checkpoint_order is None
    estimate, probe = engine.calls[1], engine.calls[2]
    assert estimate == "EXPLAIN ESTIMATE\n" + probe.lstrip()
    assert "LIMIT 1" in probe and "GROUP BY" not in probe
    tail = (WINDOW_END - timedelta(days=30), engine.slice_ranges[0][0])
    assert engine.estimate_ranges == [tail] and engine.probe_ranges == [tail]
    assert "AND 0 = 1" not in probe


def test_an_estimate_over_the_target_or_unreadable_licenses_no_wide_statement():
    """Nothing bounds a statement wider than the cap but its cost proof.

    Over the target (bloom false positives across a long tail), or a result
    the reducer cannot read (an empty estimate table, another table, no
    integer): the existence statement is not issued and the walk slices at
    the cap, partial + cursor when the budget runs out.
    """
    thirty_days = _filters(window_start=WINDOW_END - timedelta(days=30))
    for override in (
        walk.USER_LIST_WALK_PROBE_TARGET_READ_ROWS + 1,
        [],
        [{"database": "default", "table": "traces", "parts": 0, "rows": 0, "marks": 0}],
        [
            {
                "database": "default",
                "table": "spans",
                "parts": 0,
                "rows": None,
                "marks": 0,
            }
        ],
    ):
        engine = Engine(World())
        engine.estimate_override = override
        with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 4):
            read, engine = _page(
                World(), page_size=25, filters=thirty_days, engine=engine
            )
        assert _kinds(engine) == ["slice", "estimate", "slice", "slice"], override
        assert "probe" not in _kinds(engine)
        assert read.has_more is True and read.payload["table"] == []
        cap = walk.USER_LIST_WALK_MAX_SLICE
        assert max(high - low for low, high in engine.slice_ranges) <= cap

    # Exactly at the target the statement is issued.
    engine = Engine(World())
    engine.estimate_override = walk.USER_LIST_WALK_PROBE_TARGET_READ_ROWS
    read, engine = _page(World(), page_size=25, filters=thirty_days, engine=engine)
    assert _kinds(engine) == ["slice", "estimate", "probe"]
    assert read.has_more is False


def test_a_costed_probe_that_reads_only_false_positives_still_proves_the_tail():
    """The estimate over-reports (granules the blooms could not exclude, no
    witnessed row in them); the existence statement reads them and finds
    nothing: the tail is exhausted on the statement's answer, not the count.
    """
    thirty_days = _filters(window_start=WINDOW_END - timedelta(days=30))
    engine = Engine(World())
    engine.estimate_override = 12_345
    read, engine = _page(World(), page_size=25, filters=thirty_days, engine=engine)
    assert _kinds(engine) == ["slice", "estimate", "probe"]
    assert read.has_more is False and read.checkpoint_order is None


def test_a_probe_that_finds_a_row_leaves_the_walk_slicing_at_the_cap():
    """A row proves existence, never a position: the slices go on at the cap.

    The populated slice re-arms the probe, so the twenty empty days below it
    are proven by one more costed pair instead of spending the rest of the
    budget one day at a time.
    """

    thirty_days = _filters(window_start=WINDOW_END - timedelta(days=30))
    world = World()
    world.user(
        1,
        key=WINDOW_END - timedelta(days=10),
        raw=(WINDOW_END - timedelta(days=10),),
    )
    read, engine = _page(world, page_size=25, filters=thirty_days)

    kinds = _kinds(engine)
    assert _names(read) == ["user-1"] and read.has_more is False
    assert kinds[:3] == ["slice", "estimate", "probe"]
    assert kinds.count("estimate") == 2 and kinds.count("probe") == 2
    assert kinds[-2:] == ["estimate", "probe"]
    cap = walk.USER_LIST_WALK_MAX_SLICE
    assert max(high - low for low, high in engine.slice_ranges) <= cap
    assert kinds.count("slice") >= 3
    assert len(engine.calls) <= walk.USER_LIST_WALK_MAX_STATEMENTS


def test_a_probe_the_budget_refuses_or_that_fails_licenses_nothing():
    from clickhouse_driver.errors import ServerException

    thirty_days = _filters(window_start=WINDOW_END - timedelta(days=30))
    world = World()
    engine = Engine(world)
    original = engine.execute_ch_query

    def failing_probe(query, params=None, timeout_ms=None, settings=None):
        result = original(query, params, timeout_ms, settings)
        if kind_of(query) == "probe":
            raise ServerException("rows", code=158)
        return result

    engine.execute_ch_query = failing_probe
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 4):
        read, engine = _page(world, page_size=25, filters=thirty_days, engine=engine)
    # The failed probe changed nothing: the walk went on slicing at the cap
    # and stopped on its statement budget with a cursor.
    assert _kinds(engine) == ["slice", "estimate", "probe", "slice"]
    assert read.has_more is True and read.payload["table"] == []

    # A failing estimate is the same: no existence statement, slice on.
    engine = Engine(world)
    original = engine.execute_ch_query

    def failing_estimate(query, params=None, timeout_ms=None, settings=None):
        if kind_of(query) == "estimate":
            engine.calls.append(query)
            raise ServerException("memory", code=241)
        return original(query, params, timeout_ms, settings)

    engine.execute_ch_query = failing_estimate
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 4):
        read, engine = _page(world, page_size=25, filters=thirty_days, engine=engine)
    assert _kinds(engine) == ["slice", "estimate", "slice", "slice"]
    assert read.has_more is True and read.payload["table"] == []

    # With the budget spent on the estimate itself, the page ends there.
    engine = Engine(world)
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 2):
        read, engine = _page(world, page_size=25, filters=thirty_days, engine=engine)
    assert _kinds(engine) == ["slice", "estimate"]
    assert read.has_more is True and read.payload["table"] == []


def test_the_estimate_runs_under_the_existence_statements_settings_in_the_probe_wall():
    """The estimate is the existence statement's own index analysis.

    It goes out under the SAME read settings as the statement it costs -
    the parallel page-replay settings, eight threads, not the one-thread
    page settings - and both carry the probe's own deadline, never more
    than ``USER_LIST_WALK_PROBE_WALL_MS``, the existence statement getting
    what the estimate left of it.
    """
    from tracer.services import users_list_manager as ulm

    thirty_days = _filters(window_start=WINDOW_END - timedelta(days=30))
    read, engine = _page(World(), page_size=25, filters=thirty_days)

    assert _kinds(engine) == ["slice", "estimate", "probe"]
    assert read.has_more is False
    expected = ulm._page_replay_read_settings(max_result_rows=1)
    assert expected["max_threads"] == 8
    assert engine.settings[1] == expected and engine.settings[2] == expected
    assert engine.settings[1] != ulm._page_read_settings(max_result_rows=1)
    assert 0 < engine.timeouts[1] <= walk.USER_LIST_WALK_PROBE_WALL_MS
    assert 0 < engine.timeouts[2] <= engine.timeouts[1]


def test_an_estimate_over_its_time_budget_licenses_no_wide_statement():
    """A probe that cannot answer inside its budget is 'cannot answer'.

    The existence statement repeats the estimate's index analysis before it
    reads a row, so when the estimate's own observed time does not fit what
    is left of the probe wall the statement is not issued and the walk
    slices at the cap - and the PAGE goes on: the probe's budget is not the
    page's wall.
    """
    thirty_days = _filters(window_start=WINDOW_END - timedelta(days=30))
    engine = Engine(World())
    engine.estimate_ms = walk.USER_LIST_WALK_PROBE_WALL_MS / 2 + 1
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 4):
        read, engine = _page(World(), page_size=25, filters=thirty_days, engine=engine)
    assert _kinds(engine) == ["slice", "estimate", "slice", "slice"]
    assert read.has_more is True and read.payload["table"] == []
    cap = walk.USER_LIST_WALK_MAX_SLICE
    assert max(high - low for low, high in engine.slice_ranges) <= cap

    # Within the budget, the statement is issued.
    engine = Engine(World())
    engine.estimate_ms = walk.USER_LIST_WALK_PROBE_WALL_MS / 2 - 50
    read, engine = _page(World(), page_size=25, filters=thirty_days, engine=engine)
    assert _kinds(engine) == ["slice", "estimate", "probe"]
    assert read.has_more is False


def test_a_probe_deadline_raised_in_the_transport_ends_the_probe_not_the_page():
    """An executor that honours ``timeout_ms`` raises for the probe's deadline.

    That ends the probe (no existence statement, nothing licensed) and the
    walk slices on at the cap; only a page wall that is really spent stops
    the page.
    """
    thirty_days = _filters(window_start=WINDOW_END - timedelta(days=30))
    world = World()
    engine = Engine(world)
    original = engine.execute_ch_query

    def probe_deadline(query, params=None, timeout_ms=None, settings=None):
        if kind_of(query) == "estimate":
            engine.calls.append(query)
            raise ReadDeadlineExceeded("probe deadline exceeded")
        return original(query, params, timeout_ms, settings)

    engine.execute_ch_query = probe_deadline
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 4):
        read, engine = _page(world, page_size=25, filters=thirty_days, engine=engine)
    assert _kinds(engine) == ["slice", "estimate", "slice", "slice"]
    assert read.has_more is True and read.payload["table"] == []


def test_seeded_and_unfiltered_candidate_statements_are_byte_identical_to_the_pins():
    builder = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=[PROJECT],
        filters=_filters()[:1],
        search="",
        empty_scope=False,
    )
    sql, _ = builder.build_dimension_candidate_query(
        limit=26, window_start=WINDOW_START, window_end=WINDOW_END
    )
    digest = hashlib.sha256(sql.strip().rstrip(";").encode()).hexdigest()
    assert digest == UNFILTERED_PAGE_DIGEST
    # The seeded shape still renders for callers that ask for it; the walk
    # merely stops the cursor page from issuing it for plain-text filters.
    seeded, _ = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=[PROJECT],
        filters=_filters(),
        search="",
        empty_scope=False,
    ).build_dimension_candidate_query(
        limit=65, window_start=WINDOW_START, window_end=WINDOW_END
    )
    assert "scalar_witness_identities AS" in seeded
    # A boolean filter qualifies no seeded witness: that statement keeps its
    # reviewed shape and the walk alone carries the boolean witness.
    boolean, _ = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=[PROJECT],
        filters=_filters(
            attribute=_attribute_filter(
                filter_type="boolean", filter_op="equals", filter_value=True
            )
        ),
        search="",
        empty_scope=False,
    ).build_dimension_candidate_query(
        limit=26, window_start=WINDOW_START, window_end=WINDOW_END
    )
    assert "scalar_witness_identities AS" not in boolean


def test_slice_statement_is_bounded_by_the_slice_and_carries_no_sorting_key_in_set():
    builder = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=[PROJECT],
        filters=_filters(),
        search="",
        empty_scope=False,
    )
    sql, params = builder.build_matching_activity_slice_query(
        slice_start=minutes_before_end(60),
        slice_end=WINDOW_END,
        limit=200,
        before=(minutes_before_end(1), str(uuid.UUID(int=7))),
    )
    compact = " ".join(sql.split())
    assert (
        "start_time >= fromUnixTimestamp64Micro(%(slice_start_us)s, 'UTC')" in compact
    )
    assert "start_time < fromUnixTimestamp64Micro(%(slice_end_us)s, 'UTC')" in compact
    assert "toStartOfHour(start_time) >= toStartOfHour(" in compact
    assert "LIMIT %(slice_user_limit)s" in compact
    assert "HAVING raw_newest <" in compact
    assert "ORDER BY raw_newest DESC, raw_end_user_id DESC" in compact
    assert params["slice_user_limit"] == 200
    assert params["project_ids"] == (PROJECT,)
    assert "attrs_string" in compact and "indexHint" in compact
    assert ") IN ( SELECT" not in compact and "IN (SELECT" not in compact
    assert compact.count("FROM spans") == 1
    assert "end_user_id_remap" not in compact and "JOIN" not in compact
    assert "max_execution_time" not in compact and "max_memory_usage" not in compact
    tail = compact.rsplit("SETTINGS", 1)[1]
    for setting in (
        "optimize_aggregation_in_order = 0",
        "optimize_distinct_in_order = 0",
        "optimize_read_in_order = 0",
    ):
        assert setting in tail
    assert "optimize_aggregation_in_order = 1" not in tail

    probe, probe_params = builder.build_matching_activity_existence_query(
        range_start=WINDOW_START, range_end=minutes_before_end(60)
    )
    compact = " ".join(probe.split())
    assert compact.startswith("SELECT 1 AS witnessed FROM spans PREWHERE")
    assert "LIMIT 1" in compact and "GROUP BY" not in compact
    assert "end_user_id_remap" not in compact
    assert probe_params["slice_end_us"] == params["slice_start_us"]
    assert "slice_user_limit" not in probe_params

    # The estimate is EXPLAIN ESTIMATE of the identical text, settings clause
    # included, with the identical parameters: the cost proven is the cost
    # paid.
    estimate, estimate_params = (
        builder.build_matching_activity_existence_estimate_query(
            range_start=WINDOW_START, range_end=minutes_before_end(60)
        )
    )
    assert estimate == "EXPLAIN ESTIMATE\n" + probe.lstrip()
    assert estimate_params == probe_params
    assert estimate.count("SETTINGS") == 1
    reduce = builder.matching_activity_existence_estimate
    columns = ["database", "table", "parts", "rows", "marks"]
    assert (
        reduce([{"table": "spans", "rows": 7}, {"table": "spans", "rows": 5}], columns)
        == 12
    )
    assert reduce([{"table": "spans", "rows": 0, "parts": 0, "marks": 0}], columns) == 0
    assert reduce([], columns) is None
    assert reduce([{"table": "spans", "rows": 7}], ["rows"]) is None
    assert reduce([{"table": "traces", "rows": 7}], columns) is None
    assert reduce([{"table": "spans", "rows": "7"}], columns) is None


@pytest.mark.parametrize(
    "attribute, column, order_fragment",
    [
        (
            _attribute_filter(
                column_id="score",
                filter_type="number",
                filter_op="greater_than",
                filter_value=5,
            ),
            "attrs_number[",
            "toFloat64OrNull(latest_attribute_value_json) > ",
        ),
        (
            _attribute_filter(
                column_id="score",
                filter_type="number",
                filter_op="in",
                filter_value=[5, 7.5],
            ),
            "attrs_number[",
            "toFloat64OrNull(latest_attribute_value_json) IN ",
        ),
        (
            _attribute_filter(
                column_id="flag",
                filter_type="boolean",
                filter_op="equals",
                filter_value=True,
            ),
            "attrs_bool[",
            "JSONExtractBool(latest_attribute_value_json) = ",
        ),
        (
            _attribute_filter(
                column_id="flag",
                filter_type="boolean",
                filter_op="equals",
                filter_value=False,
            ),
            "attrs_bool[",
            "JSONExtractBool(latest_attribute_value_json) = ",
        ),
    ],
    ids=["number-greater_than", "number-in", "boolean-true", "boolean-false"],
)
def test_number_and_boolean_filters_walk_through_the_typed_map_witness(
    attribute, column, order_fragment
):
    """A typed filter walks exactly as a text one, on one typed-map predicate.

    The slice statement carries the compiler's typed witness; the enrichment
    narrows its physical seed by the same predicate and projects the order
    key on the LATEST typed value; the page never seeds.
    """
    config = attribute["filter_config"]
    typed = (
        [("boolean", "true" if config["filter_value"] else "false")]
        if config["filter_type"] == "boolean"
        else [("number", "7.5")]
    )
    world = World()
    world.user(
        1, key=minutes_before_end(3), raw=(minutes_before_end(3),), typed_values=typed
    )
    read, engine = _page(world, page_size=25, filters=_filters(attribute=attribute))

    assert _names(read) == ["user-1"] and read.has_more is False
    assert read.payload["ordering"] == "latest_matching_activity"
    kinds = _kinds(engine)
    # The page is not full, so the walk goes on slicing the window after
    # its first member; those later slices are empty and issue no remap.
    assert kinds[:4] == ["slice", "remap", "enrich", "replay"]
    assert set(kinds[4:]) <= {"slice"}
    assert column in engine.calls[0] and "attrs_string" not in engine.calls[0]
    enrichment = engine.calls[2]
    assert column in enrichment and order_fragment in enrichment
    assert "latest_matching_start_time" in enrichment
    assert "matching_activity_typed_key" in enrichment
    assert "candidate_attribute_values_0" not in enrichment


def test_a_typed_row_the_sql_matches_but_python_rejects_is_never_published():
    """Python membership stays the authority: an order key alone publishes nothing."""

    attribute = _attribute_filter(
        column_id="score",
        filter_type="number",
        filter_op="greater_than",
        filter_value=5,
    )
    world = World()
    # The enrichment reports an order key, but the latest value it carries is
    # string-typed: not a number match in Python.
    world.user(
        1,
        key=minutes_before_end(3),
        raw=(minutes_before_end(3),),
        typed_values=[("string", '"7"')],
    )
    read, engine = _page(world, page_size=25, filters=_filters(attribute=attribute))

    assert _names(read) == [] and read.has_more is False
    assert engine.replayed == []


@pytest.mark.parametrize(
    "filters",
    [
        # Two items on one key: the order key and the witness would be two
        # different predicates.
        [
            *_filters(),
            _attribute_filter(
                filter_type="text", filter_op="equals", filter_value="silver"
            ),
        ],
        # A number range: Python compares the raw pair, the compiler coerces.
        _filters(
            attribute=_attribute_filter(
                filter_type="number", filter_op="between", filter_value=[1, 9]
            )
        ),
        # A storage-type picker on a number.
        _filters(
            attribute=_attribute_filter(
                filter_type="number",
                filter_op="in",
                filter_value=[5],
                attribute_value_types=["number"],
            )
        ),
        # A boolean given as text.
        _filters(
            attribute=_attribute_filter(
                filter_type="boolean", filter_op="equals", filter_value="true"
            )
        ),
        # A negative predicate.
        _filters(
            attribute=_attribute_filter(
                filter_type="number", filter_op="not_equals", filter_value=5
            )
        ),
        # An ordering whose missing-key default satisfies it: the compiler
        # gives it no value witness.
        _filters(
            attribute=_attribute_filter(
                filter_type="number", filter_op="less_than", filter_value=5
            )
        ),
        # A non-ASCII text value.
        _filters(
            attribute=_attribute_filter(
                filter_type="text", filter_op="equals", filter_value="gôld"
            )
        ),
        # JSON-looking text a user TYPED: Users canonicalises it, and no raw
        # witness is exhaustive for that comparison.
        _filters(
            attribute=_attribute_filter(
                filter_type="text", filter_op="equals", filter_value='{"a":1,"b":2}'
            )
        ),
        # The same JSON-looking text in an ``in`` without picker provenance.
        _filters(
            attribute=_attribute_filter(
                filter_type="text", filter_op="in", filter_value=['{"a":1,"b":2}']
            )
        ),
        # A picked string whose script is not ASCII: raw, but Python-lowered.
        _filters(
            attribute=_attribute_filter(
                filter_type="text",
                filter_op="in",
                filter_value=['{"a":"gôld"}'],
                attribute_value_types=["string"],
            )
        ),
    ],
    ids=[
        "two-items-one-key",
        "number-between",
        "number-picker",
        "boolean-as-text",
        "number-not_equals",
        "number-less_than-default-matches",
        "non-ascii-text",
        "typed-json-looking-equals",
        "typed-json-looking-in",
        "picked-non-ascii-json-looking",
    ],
)
def test_shapes_the_walk_cannot_serve_keep_the_seeded_page(filters):
    manager = _manager(filters)
    builder = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=[PROJECT],
        filters=filters,
        empty_scope=False,
    )
    assert manager.matching_activity_walk_applies(builder) is False
    assert manager._walked_typed_filter is None


def test_a_sorted_request_keeps_the_seeded_page():
    manager = UsersListManager(
        organization_id=ORG,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        filters=_filters(),
        sort_params=[{"column_id": "total_cost", "order": "desc"}],
        requested_columns=[],
        attribute_keys=[],
    )
    builder = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=[PROJECT],
        filters=_filters(),
        empty_scope=False,
    )
    assert manager.matching_activity_walk_applies(builder) is False


def test_certified_users_are_materialised_past_the_wall_but_the_search_stops():
    """The wall bounds the search; a user the page already certified is published.

    The slice, its survivor statement and the enrichment consume the wall;
    the replay of the certified user still runs (one finite statement), and
    no further slice is read.
    """
    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    world.user(2, key=minutes_before_end(600), raw=(minutes_before_end(600),))
    engine = Engine(world)
    original = engine.execute_ch_query

    def slow_enrichment(query, params=None, timeout_ms=None, settings=None):
        import time

        if kind_of(query) == "enrich":
            time.sleep(0.08)
        return original(query, params, timeout_ms, settings)

    engine.execute_ch_query = slow_enrichment
    with patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60):
        read, engine = _page(world, page_size=25, engine=engine)

    assert _names(read) == ["user-1"]
    assert read.has_more is True
    assert _kinds(engine) == ["slice", "remap", "enrich", "replay"]


def test_slice_width_grows_without_a_server_time_report():
    """An executor that reports no statement time must not freeze the walk.

    The user's newest match is ten hours back; from a one-hour initial slice
    the walk must reach it in a handful of statements, growing on the
    client-observed time when the transport reports none.
    """
    world = World()
    world.user(1, key=minutes_before_end(600), raw=(minutes_before_end(600),))
    engine = Engine(world)
    original = engine.execute_ch_query

    def without_server_time(query, params=None, timeout_ms=None, settings=None):
        result = original(query, params, timeout_ms, settings)
        return SimpleNamespace(data=result.data)

    engine.execute_ch_query = without_server_time
    read, engine = _page(world, page_size=25, engine=engine)

    assert _names(read) == ["user-1"]
    assert 2 <= _kinds(engine).count("slice") <= 4


def test_wall_spent_during_materialisation_still_publishes_the_certified_user():
    """Finish mode does not hand the wall to the materialising statements.

    The replay of a certified user consumes the last of the wall; the metrics
    read that follows it must still run (the page has already committed to the
    user) instead of raising on the client clock and dropping the row.
    """
    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    engine = Engine(world)
    original = engine.execute_ch_query
    metric_timeouts: list[int | None] = []

    def slow_replay_then_metrics(query, params=None, timeout_ms=None, settings=None):
        import time

        if "session_rows AS" in query:
            metric_timeouts.append(timeout_ms)
            engine.calls.append(query)
            return SimpleNamespace(
                data=[{"end_user_id": uid, "num_sessions": 7} for uid in world.users],
                query_time_ms=1.0,
            )
        if "candidate_users AS" in query:
            time.sleep(0.08)
        return original(query, params, timeout_ms, settings)

    engine.execute_ch_query = slow_replay_then_metrics
    manager = UsersListManager(
        organization_id=ORG,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        filters=_filters(),
        requested_columns=["num_sessions"],
        attribute_keys=[],
    )
    with (
        patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60),
        patch(SERVICE, return_value=engine),
        patch.object(manager, "_read_dimension_candidates", side_effect=_never_seed),
    ):
        read = manager.list_cursor_payload(page_size=25, cursor=None)

    assert _names(read) == ["user-1"]
    assert read.payload["table"][0]["num_sessions"] == 7
    assert _kinds(engine) == ["slice", "remap", "enrich", "replay", "metrics"]
    assert metric_timeouts == [None]


def test_walk_limits_are_runtime_settings_not_module_constants():
    from django.conf import settings

    assert walk.USER_LIST_WALK_MIN_SLICE == timedelta(
        seconds=settings.USER_LIST_WALK_MIN_SLICE_SECONDS
    )
    assert walk.USER_LIST_WALK_CERTIFY_BATCH_SIZE == (
        settings.USER_LIST_WALK_CERTIFY_BATCH_SIZE
    )
    assert walk.USER_LIST_WALK_PROBE_TARGET_READ_ROWS == (
        settings.USER_LIST_WALK_PROBE_TARGET_READ_ROWS
    )
    assert walk.USER_LIST_WALK_PROBE_WALL_MS == settings.USER_LIST_WALK_PROBE_WALL_MS
