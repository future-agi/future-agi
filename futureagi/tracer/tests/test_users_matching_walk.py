"""The span-attribute-filtered Users page walks newest matching activity.

A scripted world stands in for ClickHouse: raw witness rows (a superset that
includes stale versions), latest-state order keys, and whole-window totals are
three separate answers, so each guard can see which statement decided what.
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
    ) -> str:
        uid = str(uuid.UUID(int=1000 + ordinal))
        alias_ids = tuple(
            str(uuid.UUID(int=5000 + ordinal * 10 + n)) for n in range(aliases)
        )
        self.users[uid] = {
            "key": key,
            "cost": cost,
            "aliases": (uid, *alias_ids),
            "curated": curated,
            "name": f"user-{ordinal}",
        }
        for alias in (uid, *alias_ids):
            self.canonical[alias] = uid
        for index, moment in enumerate(raw):
            # Spread raw rows over the user's aliases so alias resolution is live.
            self.raw.append((moment, self.users[uid]["aliases"][index % (aliases + 1)]))
        return uid


class Engine:
    """Answers the walk's three statement shapes from the world."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.calls: list[str] = []
        self.enriched: list[tuple[str, ...]] = []
        self.replayed: list[tuple[str, ...]] = []

    def execute_ch_query(self, query, params=None, timeout_ms=None, settings=None):
        self.calls.append(query)
        params = params or {}
        if "witnessed AS" in query:
            return self._slice(params)
        if "latest_candidate_attribute_values" in query:
            return self._enrich(params)
        if "candidate_users AS" in query:
            return self._replay(params)
        raise AssertionError("unexpected statement: " + " ".join(query.split())[:120])

    def _slice(self, params):
        low, high = _from_us(params["slice_start_us"]), _from_us(params["slice_end_us"])
        groups: dict[str, datetime] = {}
        for moment, raw_id in self.world.raw:
            if low <= moment < high:
                canonical = self.world.canonical[raw_id]
                groups[canonical] = max(groups.get(canonical, moment), moment)
        rows = sorted(groups.items(), key=lambda item: (item[1], item[0]), reverse=True)
        if "slice_before_us" in params:
            before = (
                _from_us(params["slice_before_us"]),
                params["slice_before_end_user_id"],
            )
            rows = [(uid, moment) for uid, moment in rows if (moment, uid) < before]
        rows = rows[: params["slice_user_limit"]]
        return SimpleNamespace(
            data=[
                {
                    "end_user_id": uid,
                    "newest_witness": moment,
                    "alias_end_user_ids": list(self.world.users[uid]["aliases"]),
                }
                for uid, moment in rows
            ],
            query_time_ms=1.0,
        )

    def _enrich(self, params):
        ids = tuple(params["eu_ids"])
        self.enriched.append(ids)
        data = []
        for uid in ids:
            key = self.world.users[uid]["key"]
            if key is None:
                continue
            data.append(
                {
                    "end_user_id": uid,
                    "attribute_key": "tag",
                    "attribute_typed_values": [("string", '"Gold"')],
                    "latest_matching_start_time": key,
                }
            )
        return SimpleNamespace(data=data, query_time_ms=1.0)

    def _replay(self, params):
        ids = tuple(params["candidate_end_user_ids"])
        self.replayed.append(ids)
        data = []
        for uid in ids:
            user = self.world.users[uid]
            if not user["curated"]:
                continue
            data.append(
                {
                    "user_id": user["name"],
                    "total_cost": user["cost"],
                    "total_tokens": 10,
                    "input_tokens": 6,
                    "output_tokens": 4,
                    "num_traces": 1,
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


def _filters():
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
            },
        },
        {
            "column_id": "tag",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "gold",
            },
        },
    ]


def _manager() -> UsersListManager:
    return UsersListManager(
        organization_id=ORG,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        filters=_filters(),
        requested_columns=[],
        attribute_keys=[],
    )


def _never_seed(**kwargs):
    raise AssertionError("the whole-window candidate statement must never run")


def _page(world: World, *, page_size: int, cursor=None, engine: Engine | None = None):
    manager = _manager()
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
    assert all(
        "witnessed AS" in call
        or "latest_candidate_attribute_values" in call
        or "candidate_users AS" in call
        for call in engine.calls
    )


def test_floor_rule_carries_a_stale_witness_below_the_slice_floor():
    """A raw witness above the floor does not place a user whose key is below it.

    P's newest raw row is a stale version; its live matching span is older
    than H's. A slice truncated at two users returns P and A, so P must wait
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
    assert "slice_before_us" in engine.calls[0] or len(engine.calls) >= 4


def test_totals_come_from_the_whole_window_replay_for_published_users_only():
    world = World()
    published = world.user(
        1, key=minutes_before_end(5), raw=(minutes_before_end(5),), cost=6.0
    )
    carried = world.user(
        2, key=minutes_before_end(40), raw=(minutes_before_end(40),), cost=9.0
    )
    world.user(3, key=None, raw=(minutes_before_end(3),))

    read, engine = _page(world, page_size=1)

    assert _names(read) == ["user-1"]
    assert read.payload["table"][0]["total_cost"] == 6.0
    # One replay, for the published user alone: the carried member and the
    # stale witness were never replayed, and nothing was summed from a slice.
    assert engine.replayed == [(published,)]
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

    # One statement: the slice runs, nothing can be certified.
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 1):
        read, engine = _page(world, page_size=25)
    assert read.payload["table"] == []
    assert read.has_more is True
    assert len(engine.calls) == 1 and "witnessed AS" in engine.calls[0]
    assert read.checkpoint_order[0] == walk.USER_LIST_MATCHING_CURSOR_ORDER
    assert read.checkpoint_order[1] is None and read.checkpoint_order[2] is None

    # Two statements: certified but not materialised; the cursor carries the
    # certified keys so the next page starts above them.
    with patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 2):
        read, engine = _page(world, page_size=25)
    assert read.payload["table"] == []
    assert read.has_more is True
    assert engine.replayed == []
    assert read.checkpoint_order[3] > minutes_before_end(3)

    # The same cursor resumes under a full budget and publishes everyone once.
    resumed, _engine = _page(world, page_size=25, cursor=_cursor(read))
    assert _names(resumed) == ["user-1", "user-2", "user-3"]
    assert resumed.has_more is False


def test_wall_exhaustion_stops_between_statements():
    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))

    # The wall is checked between statements: the slice statement alone
    # outlives it, so certification never starts and the page is partial.
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
    assert len(engine.calls) == 1


def test_a_slice_that_fails_on_a_read_budget_is_retried_narrower_never_wider():
    from clickhouse_driver.errors import ServerException

    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    engine = Engine(world)
    original = engine.execute_ch_query
    widths: list[int] = []

    def flaky(query, params=None, timeout_ms=None, settings=None):
        if "witnessed AS" in query:
            widths.append(params["slice_end_us"] - params["slice_start_us"])
            if len(widths) == 1:
                raise ServerException("memory", code=241)
        return original(query, params, timeout_ms, settings)

    engine.execute_ch_query = flaky
    read, engine = _page(world, page_size=25, engine=engine)

    assert _names(read) == ["user-1"]
    assert len(widths) >= 2 and widths[1] == widths[0] // 4


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
    assert "HAVING newest_witness <" in compact
    assert params["slice_user_limit"] == 200
    assert params["project_ids"] == (PROJECT,)
    assert "attrs_string" in compact and "indexHint" in compact
    assert ") IN ( SELECT" not in compact and "IN (SELECT" not in compact
    assert compact.count("FROM spans") == 1
    assert "max_execution_time" not in compact and "max_memory_usage" not in compact
    tail = compact.rsplit("SETTINGS", 1)[1]
    for setting in (
        "optimize_aggregation_in_order = 0",
        "optimize_distinct_in_order = 0",
        "optimize_read_in_order = 0",
    ):
        assert setting in tail
    assert "optimize_aggregation_in_order = 1" not in tail


def test_certified_users_are_materialised_past_the_wall_but_the_search_stops():
    """The wall bounds the search; a user the page already certified is published.

    The slice and the enrichment consume the wall; the replay of the certified
    user still runs (one finite statement), and no further slice is read.
    """
    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    world.user(2, key=minutes_before_end(600), raw=(minutes_before_end(600),))
    engine = Engine(world)
    original = engine.execute_ch_query

    def slow_enrichment(query, params=None, timeout_ms=None, settings=None):
        import time

        if "latest_candidate_attribute_values" in query:
            time.sleep(0.08)
        return original(query, params, timeout_ms, settings)

    engine.execute_ch_query = slow_enrichment
    with patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60):
        read, engine = _page(world, page_size=25, engine=engine)

    assert _names(read) == ["user-1"]
    assert read.has_more is True
    kinds = [
        "slice"
        if "witnessed AS" in call
        else "enrich"
        if "latest_candidate_attribute_values" in call
        else "replay"
        for call in engine.calls
    ]
    assert kinds == ["slice", "enrich", "replay"]


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
    slices = [call for call in engine.calls if "witnessed AS" in call]
    assert 2 <= len(slices) <= 4


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
    kinds = [
        "slice"
        if "witnessed AS" in call
        else "enrich"
        if "latest_candidate_attribute_values" in call
        else "metrics"
        if "session_rows AS" in call
        else "replay"
        for call in engine.calls
    ]
    assert kinds == ["slice", "enrich", "replay", "metrics"]
    assert metric_timeouts == [None]
