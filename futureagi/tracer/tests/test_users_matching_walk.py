"""The span-attribute-filtered Users page walks newest matching activity.

A scripted world stands in for ClickHouse: raw witness rows (a superset that
includes stale versions, keyed by the RAW id a span carries), the survivor
map, latest-state order keys, and whole-window totals are separate answers,
so each guard can see which statement decided what.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from tracer.services import users_list_manager as ulm
from tracer.services import users_matching_walk as walk
from tracer.services.clickhouse.list_cursor import (
    ListCursor,
    ListCursorError,
    canonical_filter_leaf,
    decode_list_cursor,
    encode_list_cursor,
)
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
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
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
        # Native leaves with answers of their own, keyed by the value their
        # compiled predicate binds (lower-cased): the physical rows their flag
        # holds, each user's newest latest live match and whole-window
        # decision (``native_leaf``). A native leaf with no entry answers from
        # ``raw``, ``key`` and ``native`` above, as every leaf did before.
        self.leaves: dict[str, dict] = {}

    def native_leaf(
        self,
        value: str,
        members: dict[str, tuple[datetime | None, bool]],
        rows: list[tuple[datetime, str]] | None = None,
    ) -> None:
        """A native leaf of its own: ``members`` maps a user to ``(key, decision)``.

        ``rows`` are the physical rows its flag holds (stale versions
        included), by the raw id that carries each; by default one row at each
        user's key.
        """

        self.leaves[value.lower()] = {
            "keys": {uid: key for uid, (key, _decided) in members.items()},
            "decisions": {uid: decided for uid, (_key, decided) in members.items()},
            "raw": list(
                rows
                if rows is not None
                else [(key, uid) for uid, (key, _d) in members.items() if key]
            ),
        }

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
        native: bool | None = None,
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
            # A native span-dimension leaf's decision; None: no live span.
            "native": native,
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
    if "native_span_flags" in query:
        return "native"
    if "AS raw_end_user_id" in query:
        return "slice"
    if "AS instant_end_user_id" in query:
        return "instant"
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
        self.native_ids: list[tuple[str, ...]] = []
        self.remapped: list[tuple[str, ...]] = []
        self.slice_ranges: list[tuple[datetime, datetime]] = []
        self.instant_ranges: list[tuple[datetime, datetime]] = []
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
        # Per statement: the read settings, the timeout the walk sent, and the
        # server execution cap it asked for (None: admission only).
        self.settings: list[dict | None] = []
        self.timeouts: list[float | None] = []
        self.caps: list[int | None] = []

    def execute_ch_query(
        self,
        query,
        params=None,
        timeout_ms=None,
        settings=None,
        *,
        server_execution_cap_ms=None,
    ):
        self.calls.append(query)
        self.settings.append(settings)
        self.timeouts.append(timeout_ms)
        self.caps.append(server_execution_cap_ms)
        params = params or {}
        kind = kind_of(query)
        if kind == "slice":
            return self._slice(params)
        if kind == "instant":
            return self._instant(params)
        if kind == "estimate":
            return self._estimate(params)
        if kind == "probe":
            return self._probe(params)
        if kind == "remap":
            return self._remap(params)
        if kind == "enrich":
            return self._enrich(params)
        if kind == "native":
            return self._native(query, params)
        return self._replay(params)

    def _leaf(self, params, prefix: str = "native_leaf_") -> dict | None:
        """The world's own leaf whose value a statement binds under ``prefix``."""

        for name, value in params.items():
            if (
                name.startswith(prefix)
                and isinstance(value, str)
                and value.lower() in self.world.leaves
            ):
                return self.world.leaves[value.lower()]
        return None

    def _witnessed(self, params, ranges):
        low, high = _from_us(params["slice_start_us"]), _from_us(params["slice_end_us"])
        ranges.append((low, high))
        groups: dict[str, datetime] = {}
        leaf = self._leaf(params)
        for moment, raw_id in leaf["raw"] if leaf is not None else self.world.raw:
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

    def _instant(self, params):
        """Resolved ids witnessed in the instant, through the survivor map."""

        resolved = {
            self.world.canonical[raw_id]
            for raw_id in self._witnessed(params, self.instant_ranges)
        }
        before = params.get("instant_before_end_user_id")
        rows = sorted(
            (uid for uid in resolved if before is None or uid < before), reverse=True
        )[: params["slice_user_limit"]]
        return SimpleNamespace(
            data=[{"instant_end_user_id": uid} for uid in rows], query_time_ms=1.0
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

    def _native(self, query, params):
        """Each native leaf's decision: the user's ``native`` answer.

        A native walk's order key (``native_leaf_<i>_newest``) is the user's
        ``key``: in a world walked on a native leaf, ``raw`` holds the
        physical rows satisfying its flag and ``key`` the newest latest live
        one; no key reads as the epoch, as ``maxIf`` over nothing does.
        """

        # ``(?![_\d])``: never a prefix of ``native_leaf_13`` or of a
        # ``_newest`` column.
        leaves = re.findall(r"AS (native_leaf_\d+)(?![_\d])", query)
        newest = re.findall(r"AS (native_leaf_\d+_newest)(?![_\d])", query)
        self.native_ids.append(tuple(params["candidate_end_user_ids"]))
        # A leaf of its own answers from its own decisions and keys.
        own = {
            alias: self._leaf(params, prefix=alias.removesuffix("_newest") + "_")
            for alias in (*leaves, *newest)
        }
        data = []
        for uid in params["candidate_end_user_ids"]:
            user = self.world.users[uid]
            if user["native"] is None:
                continue
            row = {"end_user_id": uid}
            for alias in leaves:
                leaf = own[alias]
                row[alias] = int(
                    leaf["decisions"].get(uid, False) if leaf else user["native"]
                )
            for alias in newest:
                leaf = own[alias]
                key = leaf["keys"].get(uid) if leaf else user["key"]
                row[alias] = key or EPOCH
            data.append(row)
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


# The statement count alone ends a request, as it did before an empty page's
# count could grow to its wall (``_WalkBudget.affords``): for tests of what a
# request does when its count, not its wall, runs out.
_plain_count = patch.object(walk, "USER_LIST_WALK_EMPTY_PAGE_BUDGETS", 1)


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


def _native_status_leaf(value="ERROR"):
    return {
        "column_id": "status",
        "property_id": "system_attribute:traces:status",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def test_a_raw_leaf_plus_a_native_leaf_publishes_only_native_members():
    # The walk discovers on the raw text leaf; the native leaf (status) is
    # decided at certification by the native statement, the users graph's own
    # SQL. A native leaf certified as a raw attribute rejected every user: an
    # empty exact page.
    world = World()
    world.user(
        1, key=minutes_before_end(30), raw=(minutes_before_end(30),), native=True
    )
    world.user(2, key=minutes_before_end(5), raw=(minutes_before_end(5),), native=False)
    world.user(
        3, key=minutes_before_end(90), raw=(minutes_before_end(90),), native=True
    )
    world.user(4, key=minutes_before_end(60), raw=(minutes_before_end(60),))

    read, engine = _page(
        world, page_size=25, filters=[*_filters(), _native_status_leaf()]
    )

    assert _names(read) == ["user-1", "user-3"]
    assert read.payload["query_provenance"] == "matching_activity_walk"
    assert read.payload["query_exact"] is True
    assert "native" in _kinds(engine)


def _native_member_ids(world: World) -> set[str]:
    return {uid for uid, user in world.users.items() if user["native"]}


def test_certification_rejects_a_native_non_member_before_any_replay():
    # Problem 3: the native leaf was decided only after the replay, so every
    # native non-member ranked ahead of a member cost a replay of its own. It
    # is decided at certification now: only native members are replayed.
    world = World()
    world.user(
        1, key=minutes_before_end(30), raw=(minutes_before_end(30),), native=True
    )
    world.user(2, key=minutes_before_end(5), raw=(minutes_before_end(5),), native=False)
    world.user(3, key=minutes_before_end(90), raw=(minutes_before_end(90),))

    read, engine = _page(
        world, page_size=25, filters=[*_filters(), _native_status_leaf()]
    )

    assert _names(read) == ["user-1"]
    replayed = {uid for ids in engine.replayed for uid in ids}
    assert replayed == _native_member_ids(world)
    kinds = _kinds(engine)
    assert kinds.index("native") < kinds.index("replay")
    # One native statement per certified batch, beside its enrichment.
    assert kinds.count("native") == kinds.count("enrich")


def test_native_non_members_ahead_of_a_member_do_not_degrade_the_page():
    # Thirty native non-members rank ahead of the one member. Replaying each
    # of them to learn it fails the native leaf spent the statement budget
    # before the member was reached, and the page came back degraded.
    world = World()
    for ordinal in range(1, 31):
        world.user(
            ordinal,
            key=minutes_before_end(ordinal),
            raw=(minutes_before_end(ordinal),),
            native=False,
        )
    member = world.user(
        31, key=minutes_before_end(45), raw=(minutes_before_end(45),), native=True
    )

    read, engine = _page(
        world, page_size=1, filters=[*_filters(), _native_status_leaf()]
    )

    assert _names(read) == ["user-31"]
    assert read.payload["query_status"] == "complete"
    assert engine.replayed == [(member,)]


def test_the_native_statement_is_a_certification_statement():
    # The native read decides membership at certification, before any
    # replay: it is admitted by the search's deadline under the enrichment
    # cap and, a whole-window replay like the finish's statements, asks the
    # server to stop it there too; it runs with the page-replay read
    # settings. The finish never sends it.
    world = World()
    for ordinal, minutes in enumerate((3, 7), start=1):
        world.user(
            ordinal,
            key=minutes_before_end(minutes),
            raw=(minutes_before_end(minutes),),
            native=True,
        )

    read, engine = _page(
        world, page_size=25, filters=[*_filters(), _native_status_leaf()]
    )

    assert _names(read) == ["user-1", "user-2"]
    kinds = _kinds(engine)
    native = [i for i, kind in enumerate(kinds) if kind == "native"]
    assert native and max(native) < kinds.index("replay")
    for index in native:
        assert engine.timeouts[index] is not None
        assert engine.timeouts[index] <= ulm.USER_LIST_ENRICHMENT_TIMEOUT_MS
        assert engine.caps[index] == engine.timeouts[index]
        assert engine.settings[index]["max_threads"] == 8
    replay = kinds.index("replay")
    assert "native" not in kinds[replay:]


def test_the_statement_budget_counts_the_native_statement_certification_sends():
    # A raw + native page's certification sends the attribute enrichment and
    # the native statement; one materialisation sends the replay alone. The
    # budget reserves exactly what each sends, and the head-of-line decision
    # costs what it did when the native statement finished the page.
    world = World()
    for ordinal, minutes in enumerate((3, 7), start=1):
        world.user(
            ordinal,
            key=minutes_before_end(minutes),
            raw=(minutes_before_end(minutes),),
            native=True,
        )
    filters = [*_filters(), _native_status_leaf()]

    read, engine = _page(world, page_size=25, filters=filters)

    assert _names(read) == ["user-1", "user-2"]
    kinds = _kinds(engine)
    assert kinds.count("replay") == 1
    assert kinds.count("enrich") == kinds.count("native") == 1
    native_page = _manager(filters)
    assert walk._materialisation_statement_count(native_page) == 1
    assert walk._enrichment_statement_count(native_page) == 2
    assert walk._materialisation_statement_count(_manager(_filters())) == 1
    assert walk._enrichment_statement_count(_manager(_filters())) == 1
    assert walk._head_statements(native_page) == 4 + 2 * 2 + 2 * 1


def _date_only():
    return _filters()[:1]


def _native_leaf(column_id="model", operation="equals", value="gpt-4o", col_type=None):
    config = {"filter_type": "text", "filter_op": operation}
    if col_type is not None:
        config["col_type"] = col_type
    if value is not None:
        config["filter_value"] = value
    return {
        "column_id": column_id,
        "property_id": f"system_attribute:traces:{column_id}",
        "filter_config": config,
    }


def test_a_native_only_page_walks_on_the_native_leaf_and_never_seeds():
    # ``raw`` rows are the physical rows satisfying the native flag (stale
    # versions included); ``key`` is the newest latest live match; ``native``
    # the leaf's whole-window decision.
    world = World()
    world.user(
        1, key=minutes_before_end(30), raw=(minutes_before_end(30),), native=True
    )
    world.user(2, key=minutes_before_end(5), raw=(minutes_before_end(5),), native=True)
    # Witnessed only on a stale version: its live spans do not match.
    world.user(3, key=None, raw=(minutes_before_end(1),), native=False)
    world.user(
        4,
        key=minutes_before_end(90),
        raw=(minutes_before_end(2), minutes_before_end(90)),
        native=True,
        aliases=1,
    )
    filters = [*_date_only(), _native_status_leaf()]

    read, engine = _page(world, page_size=25, filters=filters)

    assert _names(read) == ["user-2", "user-1", "user-4"]
    assert read.has_more is False
    assert read.payload["query_provenance"] == "matching_activity_walk"
    assert read.payload["ordering"] == "latest_matching_activity"
    assert read.payload["query_status"] == "complete"
    kinds = _kinds(engine)
    assert "enrich" not in kinds
    assert set(kinds) <= {"slice", "remap", "native", "replay", "probe", "estimate"}
    slices = [call for call in engine.calls if kind_of(call) == "slice"]
    assert all(
        "lowerUTF8(toString(status)) = %(native_leaf_1_0_col_1)s" in call
        for call in slices
    )
    natives = [call for call in engine.calls if kind_of(call) == "native"]
    assert all("AS native_leaf_1_newest" in call for call in natives)
    # The stale witness is certified out before any replay.
    assert all(str(uuid.UUID(int=1003)) not in ids for ids in engine.replayed)


def test_a_native_witness_certifies_its_negation_over_the_whole_window():
    # ``model not_equals gpt-4o`` without a family: discovered on the presence
    # flag, the forbidden value anywhere in the window rejects the user at
    # certification, before any replay.
    world = World()
    world.user(
        1, key=minutes_before_end(10), raw=(minutes_before_end(10),), native=True
    )
    forbidden = world.user(
        2, key=minutes_before_end(3), raw=(minutes_before_end(3),), native=False
    )
    filters = [*_date_only(), _native_leaf(operation="not_equals")]

    read, engine = _page(world, page_size=25, filters=filters)

    assert _names(read) == ["user-1"]
    assert all(forbidden not in ids for ids in engine.replayed)
    slices = [call for call in engine.calls if kind_of(call) == "slice"]
    assert slices and all("forbidden" not in call for call in slices)
    assert any(
        "forbidden" in call for call in engine.calls if kind_of(call) == "native"
    )


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        # A raw witness the walk accepts wins over any native leaf.
        (
            [
                _native_status_leaf(),
                _attribute_filter(
                    filter_type="text", filter_op="equals", filter_value="gold"
                ),
            ],
            ("raw", "tag", None),
        ),
        # Two items on the raw key: the walk declines the raw witness.
        (
            [
                _attribute_filter(
                    filter_type="text", filter_op="equals", filter_value="gold"
                ),
                _attribute_filter(
                    filter_type="text", filter_op="equals", filter_value="GOLD"
                ),
                _native_status_leaf(),
            ],
            ("native", "status", 3),
        ),
        # A raw leaf with no walkable witness at all.
        (
            [
                _attribute_filter(
                    filter_type="text", filter_op="contains", filter_value="go"
                ),
                _native_leaf(),
            ],
            ("native", "model", 2),
        ),
        # Native leaves only: the most selective (ERROR over a model), not
        # the lowest index.
        (
            [
                _native_leaf("model", "is_null", None),
                _native_leaf(),
                _native_status_leaf(),
            ],
            ("native", "status", 3),
        ),
        # An is_null without a family, alone: its absence flag.
        ([_native_leaf("model", "is_null", None)], ("native", "model", 1)),
        # Nothing to walk on: a raw leaf with no walkable witness alone.
        (
            [
                _attribute_filter(
                    filter_type="text", filter_op="contains", filter_value="go"
                )
            ],
            None,
        ),
    ],
    ids=[
        "raw-wins",
        "raw-declined",
        "raw-unwalkable",
        "most-selective-native",
        "absence-only",
        "no-witness",
    ],
)
def test_the_walk_witness_precedence(filters, expected):
    manager = _manager([*_date_only(), *filters])
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=manager.filters
    )
    applies = manager.matching_activity_walk_applies(builder)
    if expected is None:
        assert applies is False and manager._walk_witness is None
        return
    family, key, leaf_index = expected
    witness = manager._walk_witness
    assert applies is True
    assert (witness.family, witness.key, witness.leaf_index) == (
        family,
        key,
        leaf_index,
    )
    assert witness.index_pruned is (family == "raw")


def _raw_leaf(key, operation, value=None):
    config = {"filter_type": "text", "filter_op": operation}
    if value is not None:
        config["filter_value"] = value
    return _attribute_filter(column_id=key, **config)


def _chosen_witness(filters):
    manager = _manager([*_date_only(), *filters])
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=manager.filters
    )
    assert manager.matching_activity_walk_applies(builder) is True
    return manager, manager._walk_witness


@pytest.mark.parametrize(
    "leaves",
    [
        [_native_leaf(), _native_status_leaf(), _native_leaf("model", "is_null", None)],
        # The review's case: the first raw leaf in one order is declined (two
        # items on its key), the other order leads with an accepted one.
        [
            _raw_leaf("tag", "equals", "gold"),
            _raw_leaf("tag", "is_not_null"),
            _raw_leaf("plan", "equals", "pro"),
            _native_status_leaf(),
        ],
        [_raw_leaf("tag", "equals", "gold"), _raw_leaf("plan", "equals", "pro")],
    ],
    ids=["native+native", "raw-declined+raw+native", "raw+raw"],
)
def test_the_witness_is_one_leaf_whatever_the_request_order(leaves):
    # The signed cursor binds the filters without their order, so every
    # order it admits must discover on, and key by, the same leaf.
    chosen = set()
    for order in itertools.permutations(leaves):
        manager, witness = _chosen_witness(list(order))
        chosen.add(
            (
                witness.family,
                witness.key,
                witness.identity,
                walk.witness_fingerprint(witness),
            )
        )
        assert witness.identity
        if witness.family == "native":
            # ``leaf_index`` is only where the leaf sits in this request.
            leaf = manager.filters[witness.leaf_index]
            assert canonical_filter_leaf(leaf) == witness.identity
    assert len(chosen) == 1, chosen


def test_a_repeated_raw_leaf_is_the_one_leaf_the_cursor_binds():
    # The signed cursor deduplicates identical leaves
    # (``normalize_filter_conjunction``), so ``[A, A]`` binds as ``[A]`` and
    # must choose the same witness. It was declined as two items on its key,
    # so the two requests walked different leaves and the continuation
    # failed closed (invalid_cursor) after one user.
    tag = _raw_leaf("tag", "in", ["gold", "silver"])
    _manager_once, once = _chosen_witness([tag, _native_status_leaf()])
    _manager_twice, twice = _chosen_witness(
        [tag, copy.deepcopy(tag), _native_status_leaf()]
    )
    assert (once.family, once.key) == ("raw", "tag")
    assert walk.witness_fingerprint(twice) == walk.witness_fingerprint(once)
    # Two different items on one key are still two: the key is declined.
    _manager_two, two = _chosen_witness(
        [tag, _raw_leaf("tag", "is_not_null"), _native_status_leaf()]
    )
    assert two.family == "native"


def test_a_cursor_followed_with_a_repeated_raw_leaf_publishes_each_user_once():
    # Review r2765: the same filters with the raw leaf sent once on even
    # requests and twice on odd ones.
    world = World()
    for n in range(1, 6):
        moment = minutes_before_end(10 * n)
        world.user(n, key=moment, raw=(moment,), native=True)
    tag = _raw_leaf("tag", "in", ["gold", "silver"])
    once = [*_date_only(), tag, _native_status_leaf()]
    twice = [*_date_only(), tag, copy.deepcopy(tag), _native_status_leaf()]
    names, cursor = [], None
    for hop in range(10):
        read, _engine = _page(
            world, page_size=1, cursor=cursor, filters=twice if hop % 2 else once
        )
        names.extend(_names(read))
        if not read.has_more:
            break
        cursor = _signed_cursor(read)
    assert names == [f"user-{n}" for n in range(1, 6)]


def _observation_leaf():
    return _native_leaf("observation_type", "equals", "llm", col_type="SYSTEM_METRIC")


def _provider_leaf():
    return _native_leaf("provider", "equals", "openai", col_type="SYSTEM_METRIC")


def _two_leaf_world() -> tuple[World, list[str]]:
    """Four users matching ``observation_type = llm`` AND ``provider = openai``.

    The two leaves rank alike (``witness_selectivity_rank``), so the leaf as
    the cursor binds it decides: ``observation_type``. Each leaf has its own
    newest match per user, in different orders: by observation type U1 > U2 >
    U3 > U4, by provider U4 > U2 > U3 > U1 (the review's counterexample, in
    minutes into the window).
    """

    world = World()
    observation = {1: 60, 2: 50, 3: 40, 4: 20}
    provider = {1: 10, 2: 55, 3: 40, 4: 58}
    uids = {}
    for ordinal in (1, 2, 3, 4):
        key = WINDOW_START + timedelta(minutes=observation[ordinal])
        uids[ordinal] = world.user(ordinal, key=key, raw=(key,), native=True)
    world.native_leaf(
        "openai",
        {uids[n]: (WINDOW_START + timedelta(minutes=provider[n]), True) for n in uids},
    )
    return world, ["user-1", "user-2", "user-3", "user-4"]


def test_a_cursor_followed_with_the_filters_reordered_publishes_each_user_once():
    # The review's reproduction: page 1 asked with one leaf first, the rest
    # with the other first. Before the witness was ranked on the leaf as the
    # cursor binds it, the second order walked the other leaf with the first
    # one's keys and coverage: ['user-1', 'user-3', 'user-1'], user-2 and
    # user-4 never.
    world, expected = _two_leaf_world()
    one = [*_date_only(), _observation_leaf(), _provider_leaf()]
    other = [*_date_only(), _provider_leaf(), _observation_leaf()]
    read, _engine = _page(world, page_size=1, filters=one)
    names = _names(read)
    for _hop in range(8):
        if not read.has_more:
            break
        read, _engine = _page(
            world, page_size=1, filters=other, cursor=_signed_cursor(read)
        )
        names.extend(_names(read))
    assert names == expected


def test_a_cursor_minted_on_another_witness_is_refused_not_misread():
    # A pod whose precedence chose the provider leaf minted this cursor. This
    # pod chooses the observation-type leaf for the same filters: the
    # cursor's keys and coverage are the provider's, so it restarts instead
    # of misreading them.
    world, _expected = _two_leaf_world()
    filters = [*_date_only(), _observation_leaf(), _provider_leaf()]
    witnesses = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=filters
    )._native_user_witnesses()
    provider = next(w for w in witnesses if w.key == "provider")
    with patch.object(
        UserListQueryBuilderV2, "matching_activity_witnesses", return_value=[provider]
    ):
        read, _engine = _page(world, page_size=1, filters=filters)
    assert _names(read) == ["user-4"]
    with pytest.raises(ListCursorError) as raised:
        _page(world, page_size=1, filters=filters, cursor=_signed_cursor(read))
    assert raised.value.code == "invalid_cursor"


def test_a_v1_matching_cursor_restarts():
    # v1 cursors named no witness and chose native ones by request position.
    world, _expected = _two_leaf_world()
    filters = [*_date_only(), _native_leaf(), _native_status_leaf("error")]
    cursor = ListCursor(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        order=("matching_activity_users_v1", None, None, WINDOW_END),
        seen_rows=0,
    )
    with pytest.raises(ListCursorError) as raised:
        _page(world, page_size=1, filters=filters, cursor=cursor)
    assert raised.value.code == "invalid_cursor"


def _trace_name_leaf(value="checkout"):
    return _native_leaf("trace_name", "equals", value, col_type="SYSTEM_METRIC")


def _model_leaf(operation="equals", value="gpt-4o"):
    return _native_leaf("model", operation, value, col_type="SYSTEM_METRIC")


@pytest.mark.parametrize(
    ("leaves", "expected"),
    [
        # What almost every span is loses to a model.
        ([_native_status_leaf("OK"), _model_leaf()], ("native", "model", "equals")),
        # A trace name (one per operation) over a model (a handful).
        ([_model_leaf(), _trace_name_leaf()], ("native", "trace_name", "equals")),
        # Errors are the exception: ERROR over a model.
        ([_model_leaf(), _native_status_leaf()], ("native", "status", "equals")),
        # A negation (every span without the value) loses to an equality.
        (
            [
                _native_leaf("model", "not_equals", "gpt-4o"),
                _native_leaf("provider", "equals", "openai", col_type="SYSTEM_METRIC"),
            ],
            ("native", "provider", "equals"),
        ),
        # A pattern over "any value at all".
        (
            [_model_leaf("is_not_null", None), _model_leaf("contains", "gpt")],
            ("native", "model", "contains"),
        ),
        # A raw text value, served by the blooms, over any native leaf.
        (
            [
                _native_status_leaf(),
                _raw_leaf("tag", "equals", "gold"),
            ],
            ("raw", "tag", "equals"),
        ),
        # A raw boolean (one of two values) loses to a model.
        (
            [
                _attribute_filter(
                    column_id="flag",
                    filter_type="boolean",
                    filter_op="equals",
                    filter_value=True,
                ),
                _model_leaf(),
            ],
            ("native", "model", "equals"),
        ),
        # A raw number ranks with a provider; the blooms serve the raw one.
        (
            [
                _native_leaf("provider", "equals", "openai", col_type="SYSTEM_METRIC"),
                _attribute_filter(
                    column_id="score",
                    filter_type="number",
                    filter_op="greater_than",
                    filter_value=3,
                ),
            ],
            ("raw", "score", "greater_than"),
        ),
        # A raw leaf the walk declines (two items on its key) gives way to
        # the next raw leaf it accepts, before any native leaf.
        (
            [
                _raw_leaf("tag", "equals", "gold"),
                _raw_leaf("tag", "is_not_null"),
                _raw_leaf("plan", "equals", "pro"),
                _native_status_leaf(),
            ],
            ("raw", "plan", "equals"),
        ),
    ],
    ids=[
        "ok-status-vs-model",
        "model-vs-trace-name",
        "model-vs-error-status",
        "negation-vs-provider",
        "not-null-vs-pattern",
        "native-vs-raw-text",
        "raw-boolean-vs-model",
        "provider-vs-raw-number",
        "declined-raw-vs-raw-vs-native",
    ],
)
def test_the_most_selective_leaf_is_the_witness(leaves, expected):
    for order in (leaves, leaves[::-1]):
        manager, witness = _chosen_witness(list(order))
        item = next(
            leaf
            for leaf in manager.filters
            if canonical_filter_leaf(leaf) == witness.identity
        )
        assert (
            witness.family,
            witness.key,
            item["filter_config"]["filter_op"],
        ) == expected


def _dense_world(users: int, members: int) -> tuple[World, list[str]]:
    """Every user calls gpt-4o every hour; only ``members`` ran the checkout trace.

    ``model = gpt-4o`` is a leaf of its own that every user matches, with a
    row per user per hour; ``trace_name = checkout`` answers from the
    world's ``raw``, ``key`` and ``native``: the first ``members`` users,
    newest first.
    """

    world = World()
    dense: dict[str, tuple[datetime | None, bool]] = {}
    rows: list[tuple[datetime, str]] = []
    for n in range(1, users + 1):
        member = n <= members
        key = minutes_before_end(90 + n) if member else None
        uid = world.user(n, key=key, raw=(key,) if member else (), native=bool(member))
        offset = timedelta(minutes=n % 60, microseconds=n)
        hours = [WINDOW_START + timedelta(hours=h) + offset for h in range(24)]
        rows.extend((moment, uid) for moment in hours)
        dense[uid] = (hours[-1], True)
    world.native_leaf("gpt-4o", dense, rows)
    return world, [f"user-{n}" for n in range(1, members + 1)]


def _hops(world, filters, *, max_hops: int) -> tuple[list[str], list[int]]:
    """Follow the cursor to the end: every name, and each request's page size."""

    names, sizes, cursor = [], [], None
    for _hop in range(max_hops):
        read, _engine = _page(world, page_size=25, filters=filters, cursor=cursor)
        names.extend(_names(read))
        sizes.append(len(_names(read)))
        if not read.has_more:
            return names, sizes
        cursor = _signed_cursor(read)
    raise AssertionError(f"no end after {max_hops} requests: {sizes}")


def _dense_first(original):
    """The review's choice: the model leaf first, whatever it costs."""

    def ranked(self):
        return sorted(original(self), key=lambda witness: witness.key != "model")

    return ranked


def test_a_dense_witness_with_rare_matches_no_longer_pages_empty():
    # 600 users on gpt-4o every hour, 3 of them on the checkout trace. On the
    # model leaf every request finds the 600 users again below its coverage,
    # certifies and rejects them and publishes nothing: at the statement
    # count it had, 20 or more empty pages. The trace name is the more
    # selective leaf: one request publishes all three and ends.
    world, expected = _dense_world(600, 3)
    filters = [*_date_only(), _model_leaf(), _trace_name_leaf()]

    names, sizes = _hops(world, filters, max_hops=3)
    assert names == expected
    assert sizes == [3]

    original = UserListQueryBuilderV2.matching_activity_witnesses
    with (
        _plain_count,
        patch.object(
            UserListQueryBuilderV2,
            "matching_activity_witnesses",
            _dense_first(original),
        ),
    ):
        dense_names, dense_sizes = _hops(world, filters, max_hops=200)
    assert sorted(dense_names) == sorted(expected)
    assert dense_sizes.count(0) >= 20, dense_sizes


def test_an_empty_page_keeps_deciding_until_its_count_has_grown_to_the_ceiling():
    # One native leaf, discovered on its presence flag, whose forbidden value
    # rejects all but the oldest user at certification: no other witness can
    # narrow the page. 100 users are active in each hour, each in one hour
    # only. A request that has published nothing is not ended by the
    # statement count while its wall lasts; the count grows one budget at a
    # time up to its ceiling, so each empty page decides four budgets' worth
    # of users instead of one.
    world = World()
    for n in range(1, 2401):
        moment = WINDOW_END - timedelta(
            hours=(n - 1) // 100, minutes=30, microseconds=n
        )
        world.user(n, key=moment, raw=(moment,), native=n == 2400)
    filters = [*_date_only(), _native_leaf(operation="not_equals")]
    manager = _manager(filters)
    budget = walk._statement_budget(manager)
    ceiling = budget * walk.USER_LIST_WALK_EMPTY_PAGE_BUDGETS
    assert ceiling > budget

    read, engine = _page(world, page_size=25, filters=filters)

    assert _names(read) == [] and read.has_more is True
    assert read.payload["query_status"] == "degraded"
    # It went on past the plain count and stopped only at the ceiling.
    assert budget < len(engine.calls) <= ceiling
    certification = walk._enrichment_statement_count(manager)
    finish = walk._materialisation_statement_count(manager)
    assert len(engine.calls) + certification + finish > ceiling

    names, sizes = _hops(world, filters, max_hops=40)
    assert names == ["user-2400"]
    with _plain_count:
        plain_names, plain_sizes = _hops(world, filters, max_hops=200)
    assert plain_names == ["user-2400"]
    assert 3 * len(sizes) <= len(plain_sizes), (sizes, plain_sizes)


def test_a_family_less_is_null_page_walks_on_its_absence_flag_and_never_seeds():
    # ``model is_null`` without a family is one absence term,
    # ``countIf(present) = 0``. It sent the whole-window candidate statement
    # with no server cap on every refill; it now walks on ``NOT present``
    # (``raw`` holds the spans with no model), keyed by each member's newest
    # latest live span, and the certification decides the absence over the
    # whole window: a user with a model on any live span is rejected.
    world = World()
    world.user(
        1, key=minutes_before_end(10), raw=(minutes_before_end(10),), native=True
    )
    world.user(2, key=minutes_before_end(4), raw=(minutes_before_end(4),), native=True)
    has_a_model = world.user(
        3, key=minutes_before_end(2), raw=(minutes_before_end(2),), native=False
    )
    filters = [*_date_only(), _native_leaf("model", "is_null", None)]

    read, engine = _page(world, page_size=25, filters=filters)

    assert _names(read) == ["user-2", "user-1"]
    assert read.payload["query_provenance"] == "matching_activity_walk"
    slices = [call for call in engine.calls if kind_of(call) == "slice"]
    assert slices and all("NOT ifNull(" in call for call in slices)
    natives = [call for call in engine.calls if kind_of(call) == "native"]
    assert all("AS native_leaf_1_newest" in call for call in natives)
    assert all(has_a_model not in ids for ids in engine.replayed)


def test_sort_params_never_walk_a_native_leaf():
    manager = UsersListManager(
        organization_id=ORG,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        filters=[*_date_only(), _native_status_leaf()],
        sort_params=[{"column_id": "total_cost", "direction": "desc"}],
        requested_columns=[],
        attribute_keys=[],
    )
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=manager.filters
    )
    assert manager.matching_activity_walk_applies(builder) is False


@pytest.mark.parametrize(
    ("filters", "second_width"),
    [
        # An empty native slice read every row of its hour: the next is four
        # hours wide, not sixteen.
        ([*_filters()[:1], _native_status_leaf()], timedelta(hours=4)),
        # An empty raw slice cost only its fixed overhead: sixteen hours.
        (None, timedelta(hours=16)),
    ],
    ids=["native", "raw"],
)
def test_an_empty_slice_widens_by_what_its_witness_costs(filters, second_width):
    world = World()
    world.user(
        1,
        key=minutes_before_end(22 * 60),
        raw=(minutes_before_end(22 * 60),),
        native=True,
    )

    read, engine = _page(world, page_size=25, filters=filters)

    assert _names(read) == ["user-1"]
    first, second = engine.slice_ranges[:2]
    assert (
        first[1] - first[0] == walk.USER_LIST_WALK_INITIAL_SLICE == timedelta(hours=1)
    )
    assert second[1] - second[0] == second_width


def test_a_seeded_cursor_on_a_native_only_page_continues_seeded():
    # A cursor minted by the seeded page before native leaves walked never
    # reaches the walk: its pagination ends where it began.
    manager = _manager([*_date_only(), _native_status_leaf()])
    before = str(uuid.UUID(int=1234))
    cursor = ListCursor(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        order=("physical_latest_users_v1", WINDOW_START.isoformat(), before),
        seen_rows=25,
    )
    with (
        patch.object(manager, "_read_dimension_candidates", return_value=[]) as seeded,
        patch(
            "tracer.services.users_list_manager.walk_matching_activity_page",
            side_effect=AssertionError("a seeded cursor never walks"),
        ),
    ):
        read = manager.list_cursor_payload(page_size=25, cursor=cursor)
    assert read.payload["table"] == [] and read.has_more is False
    assert seeded.call_args.kwargs["before_end_user_id"] == before


def test_a_native_matching_cursor_an_older_pod_cannot_walk_restarts():
    world = World()
    for ordinal in range(1, 4):
        world.user(
            ordinal,
            key=minutes_before_end(ordinal),
            raw=(minutes_before_end(ordinal),),
            native=True,
        )
    filters = [*_date_only(), _native_status_leaf()]
    read, _engine = _page(world, page_size=1, filters=filters)
    assert _names(read) == ["user-1"]
    cursor = _signed_cursor(read)
    assert cursor.order[0] == walk.USER_LIST_MATCHING_CURSOR_ORDER
    # An older pod: the walk does not apply to a native-only page there.
    with (
        patch.object(
            UsersListManager, "matching_activity_walk_applies", return_value=False
        ),
        pytest.raises(ListCursorError) as raised,
    ):
        _page(world, page_size=1, filters=filters, cursor=cursor)
    assert raised.value.code == "invalid_cursor"
    # The same pod resumes it: every member exactly once, in order.
    names = _names(read)
    while read.has_more:
        read, _engine = _page(
            world, page_size=1, filters=filters, cursor=_signed_cursor(read)
        )
        names.extend(_names(read))
    assert names == ["user-1", "user-2", "user-3"]


def test_a_raw_plus_native_cursor_still_walks_the_raw_witness():
    world = World()
    for ordinal in range(1, 4):
        world.user(
            ordinal,
            key=minutes_before_end(ordinal),
            raw=(minutes_before_end(ordinal),),
            native=ordinal != 2,
        )
    filters = [*_filters(), _native_status_leaf()]
    read, engine = _page(world, page_size=1, filters=filters)
    names = _names(read)
    assert "enrich" in _kinds(engine)
    while read.has_more:
        read, engine = _page(
            world, page_size=1, filters=filters, cursor=_signed_cursor(read)
        )
        names.extend(_names(read))
        slices = [call for call in engine.calls if kind_of(call) == "slice"]
        assert all("attrs_string" in call for call in slices)
    assert names == ["user-1", "user-3"]


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


def _signed_cursor(read) -> ListCursor:
    """The continuation exactly as the Users view hands it to the next request."""

    assert read.has_more and read.checkpoint_order is not None
    binding = {"resource": "observe_users", "scope": {}, "query": {}, "page_size": 25}
    token = encode_list_cursor(
        **binding,
        window_start=read.window_start,
        window_end=read.window_end,
        order=read.checkpoint_order,
        seen_rows=read.seen_rows,
    )
    return decode_list_cursor(token, **binding)


def _walk_every_page(world: World, *, max_hops: int, cursor=None, page_size=25):
    """Follow the cursor to the end; returns the names, per-hop counts, engines."""

    names: list[str] = []
    counts: list[int] = []
    engines: list[Engine] = []
    while True:
        read, engine = _page(world, page_size=page_size, cursor=cursor)
        names.extend(_names(read))
        counts.append(len(read.payload["table"]))
        engines.append(engine)
        assert len(counts) <= max_hops, f"no end after {max_hops} hops: {counts}"
        if not read.has_more:
            return names, counts, engines
        cursor = _signed_cursor(read)


def _tied_world(size: int) -> tuple[World, list[str]]:
    world = World()
    stamp = minutes_before_end(3)
    for ordinal in range(1, size + 1):
        world.user(ordinal, key=stamp, raw=(stamp,))
    # One timestamp: the page's tie order is the resolved id, descending.
    return world, [f"user-{ordinal}" for ordinal in range(size, 0, -1)]


@pytest.mark.parametrize("size", [401, 601])
def test_a_tied_cohort_larger_than_one_request_publishes_everyone_once_in_order(
    size,
):
    """Hundreds of users share their newest matching instant, at shipped limits.

    No user at a truncated floor is publishable until the whole instant is
    seen, and one request cannot see 401 or 601 raw ids and certify them. The
    continuation must resume INSIDE the instant, not start it again: every
    user exactly once, in order, one full page per request.
    """
    assert walk.USER_LIST_WALK_SLICE_USER_LIMIT == 200
    assert walk.USER_LIST_WALK_MAX_STATEMENTS == 24
    assert walk.USER_LIST_WALK_CERTIFY_BATCH_SIZE == 25
    world, expected = _tied_world(size)

    names, counts, engines = _walk_every_page(world, max_hops=-(-size // 25) + 1)

    assert names == expected
    assert len(set(names)) == size
    assert all(count == 25 for count in counts[:-1])
    assert all(engine.calls for engine in engines)
    # The instant is decided through the resolved-order statement, and no
    # request re-certifies users an earlier request already published.
    enriched = [uid for engine in engines for batch in engine.enriched for uid in batch]
    assert len(enriched) == len(set(enriched)) == size


@pytest.mark.parametrize("page_size", [30, 100])
def test_a_tied_cohort_pages_exactly_when_pages_and_batches_do_not_align(page_size):
    """A page that ends inside a certified batch leaves certified users behind.

    The cursor resumes at the last published user, not at the end of the
    batch, so those users are certified again by the next request and
    published exactly once.
    """
    world, expected = _tied_world(601)

    names, counts, _engines = _walk_every_page(
        world, max_hops=-(-601 // page_size) + 1, page_size=page_size
    )

    assert names == expected
    assert all(count == page_size for count in counts[:-1])


@_plain_count
def test_a_tie_that_outlasts_the_budget_below_one_slice_resumes_in_the_instant():
    """Fewer tied raw ids than a slice holds, but more certification than a request.

    The slice is not all one instant, so it never opens the instant itself;
    certification at four statements a batch spends the budget before the
    tie is decided. The stopped request's cursor opens the instant, so the
    next one decides it in resolved order instead of starting the slice again.
    """
    world, expected = _tied_world(150)
    world.user(151, key=minutes_before_end(30), raw=(minutes_before_end(30),))
    expected = [*expected, "user-151"]

    with patch.object(walk, "_enrichment_statement_count", return_value=4):
        first, _engine = _page(world, page_size=25)
        assert first.payload["table"] == [] and first.has_more is True
        assert first.checkpoint_order[5] is True
        names, _counts, _engines = _walk_every_page(
            world, max_hops=8, cursor=_signed_cursor(first)
        )

    assert names == expected


def test_a_tied_cohort_progresses_under_a_tiny_budget():
    """The review's reproducer: six tied users, slices of two, six statements."""

    world, expected = _tied_world(6)
    with (
        patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 2),
        patch.object(walk, "_statement_budget", return_value=6),
    ):
        names, counts, _engines = _walk_every_page(world, max_hops=8)

    assert names == expected
    assert sum(counts[:3]) == 6


def test_a_tied_cohort_inside_one_request_keeps_the_raw_slice_path():
    """199 tied users fit one request: a full first page, no instant statement."""

    world, expected = _tied_world(199)

    read, engine = _page(world, page_size=25)
    assert _names(read) == expected[:25]
    assert "instant" not in _kinds(engine)
    assert len(read.checkpoint_order) == 5

    names, _counts, _engines = _walk_every_page(world, max_hops=9)
    assert names == expected


def test_a_tied_instant_orders_by_resolved_id_when_aliases_carry_the_rows():
    """Aliases sort above their survivors; the instant still publishes by survivor.

    Every third tied user's row at the instant is carried by an alias whose id
    sorts above every survivor, so the raw slice meets those users first. A
    user above the instant and users below it bracket the cohort.
    """
    world = World()
    stamp = minutes_before_end(3)
    for ordinal in range(1, 451):
        if ordinal % 3 == 0:
            # Index 0 lands on the survivor, index 1 (the instant) on the alias.
            world.user(
                ordinal,
                key=stamp,
                raw=(minutes_before_end(600), stamp),
                aliases=1,
            )
        else:
            world.user(ordinal, key=stamp, raw=(stamp,))
    world.user(900, key=minutes_before_end(1), raw=(minutes_before_end(1),))
    world.user(901, key=minutes_before_end(30), raw=(minutes_before_end(30),))
    world.user(902, key=minutes_before_end(31), raw=(stamp, minutes_before_end(31)))
    tied_ids = sorted(
        (uid for uid, user in world.users.items() if user["key"] == stamp),
        reverse=True,
    )
    expected = [
        "user-900",
        *(world.users[uid]["name"] for uid in tied_ids),
        "user-901",
        "user-902",
    ]
    aliased = [
        raw_id
        for moment, raw_id in world.raw
        if moment == stamp and raw_id not in world.users
    ]
    assert aliased and min(aliased) > max(tied_ids)

    names, _counts, engines = _walk_every_page(world, max_hops=21)

    assert names == expected
    assert any("instant" in _kinds(engine) for engine in engines)


def test_stale_witnesses_at_a_tied_instant_publish_at_their_own_key_or_never():
    """Raw rows at the instant that are stale versions place nobody there.

    Every fifth user's live match is ten minutes older than its stale row at
    the instant, and every seventh never matches live at all. The first are
    published below the cohort at their own key; the second never.
    """
    world = World()
    stamp = minutes_before_end(3)
    older = minutes_before_end(13)
    at_stamp, below, never = [], [], []
    for ordinal in range(1, 421):
        if ordinal % 7 == 0:
            world.user(ordinal, key=None, raw=(stamp,))
            never.append(ordinal)
        elif ordinal % 5 == 0:
            world.user(ordinal, key=older, raw=(stamp, older))
            below.append(ordinal)
        else:
            world.user(ordinal, key=stamp, raw=(stamp,))
            at_stamp.append(ordinal)
    expected = [
        *(f"user-{n}" for n in reversed(at_stamp)),
        *(f"user-{n}" for n in reversed(below)),
    ]

    names, _counts, _engines = _walk_every_page(world, max_hops=20)

    assert names == expected
    assert not {f"user-{n}" for n in never} & set(names)


def test_a_cursor_that_leaves_the_instant_closed_inside_a_tie_still_resumes_exactly():
    """A cursor without the open-instant flag decodes and behaves as before.

    It names only coverage, the last published user and the witness, so the
    request starts with a raw slice from coverage as before; that slice's tie
    then opens the instant below the last published user, and the walk
    finishes the cohort.
    """
    world, expected = _tied_world(601)
    first, _engine = _page(world, page_size=25)
    assert _names(first) == expected[:25]
    legacy = first.checkpoint_order[:5]
    assert legacy[1:3] == (
        world.users[str(uuid.UUID(int=1000 + 577))]["key"],
        str(uuid.UUID(int=1000 + 577)),
    )

    cursor = ListCursor(
        window_start=first.window_start,
        window_end=first.window_end,
        order=tuple(legacy),
        seen_rows=first.seen_rows,
    )
    names, _counts, _engines = _walk_every_page(world, max_hops=27, cursor=cursor)

    assert _names(first) + names == expected


def _fingerprint(filters=None) -> str:
    """The fingerprint of the witness the page of ``filters`` walks on."""

    manager = _manager(filters)
    builder = UserListQueryBuilderV2(
        organization_id=ORG, project_ids=[PROJECT], filters=manager.filters
    )
    assert manager.matching_activity_walk_applies(builder) is True
    return walk.witness_fingerprint(manager._walk_witness)


@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_a_six_element_cursor_must_say_the_instant_is_open(flag):
    world, _expected = _tied_world(3)
    order = (
        walk.USER_LIST_MATCHING_CURSOR_ORDER,
        None,
        None,
        WINDOW_END,
        _fingerprint(),
        flag,
    )
    cursor = ListCursor(
        window_start=WINDOW_START, window_end=WINDOW_END, order=order, seen_rows=0
    )
    with pytest.raises(ListCursorError):
        _page(world, page_size=25, cursor=cursor)


@_plain_count
def test_budget_exhaustion_returns_partial_page_and_cursor_without_fallback():
    world = World()
    for ordinal, minutes in enumerate((3, 7, 11), start=1):
        world.user(
            ordinal, key=minutes_before_end(minutes), raw=(minutes_before_end(minutes),)
        )

    # A request never has fewer statements than one batch's decision
    # (``_statement_budget``); these budgets are forced below that to reach
    # each exhaustion point. One statement: the slice runs, its survivor
    # statement is refused, the slice is discarded whole and the cursor
    # re-reads it.
    with patch.object(walk, "_statement_budget", return_value=1):
        read, engine = _page(world, page_size=25)
    assert read.payload["table"] == []
    assert read.has_more is True
    assert _kinds(engine) == ["slice"]
    assert read.checkpoint_order[0] == walk.USER_LIST_MATCHING_CURSOR_ORDER
    assert read.checkpoint_order[1] is None and read.checkpoint_order[2] is None
    assert read.checkpoint_order[3] == WINDOW_END

    # Two statements: resolved but not certified; nothing is enriched.
    with patch.object(walk, "_statement_budget", return_value=2):
        read, engine = _page(world, page_size=25)
    assert read.payload["table"] == [] and read.has_more is True
    assert _kinds(engine) == ["slice", "remap"] and engine.enriched == []

    # Three statements: certified but not materialised; the cursor carries the
    # certified keys so the next page starts above them.
    with patch.object(walk, "_statement_budget", return_value=3):
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

    with patch.object(walk, "_statement_budget", return_value=4):
        read, engine = _page(world, page_size=25)
    assert _kinds(engine) == ["slice", "remap", "enrich", "replay"]
    assert _names(read) == ["user-1"] and read.has_more is True
    assert read.checkpoint_order[3] == engine.slice_ranges[0][0] + walk._TICK

    world = World()
    world.user(1, key=minutes_before_end(600), raw=(minutes_before_end(600),))
    engine = Engine(world)
    original = engine.execute_ch_query

    def wall_spent_in_transport(
        query, params=None, timeout_ms=None, settings=None, **caps
    ):
        if len(engine.calls) == 1 and kind_of(query) == "slice":
            import time

            # The statement outlives the page wall, and then fails: the
            # narrower retry it would earn is refused by that wall.
            time.sleep(0.08)
            engine.calls.append(query)
            raise ReadDeadlineExceeded("read deadline exceeded")
        return original(query, params, timeout_ms, settings, **caps)

    engine.execute_ch_query = wall_spent_in_transport
    with patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60):
        read, engine = _page(world, page_size=25, engine=engine)
    assert _kinds(engine) == ["slice", "slice"]
    assert read.payload["table"] == [] and read.has_more is True
    assert read.checkpoint_order[3] == engine.slice_ranges[0][0] + walk._TICK


def test_wall_exhaustion_stops_between_statements():
    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))

    # The wall is checked between statements, and the slice statement alone
    # outlives it. The request has decided nobody yet, so the survivor and
    # enrichment statements of that slice's first batch are admitted against
    # the analytics wall and its user is published: discarding the slice
    # here had every later request read it again, and the list never got
    # past it. The next slice is refused, and the page is partial.
    with patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60):
        engine = Engine(world)
        original = engine.execute_ch_query

        def slow(query, params=None, timeout_ms=None, settings=None, **caps):
            import time

            time.sleep(0.08)
            return original(query, params, timeout_ms, settings, **caps)

        engine.execute_ch_query = slow
        read, engine = _page(world, page_size=25, engine=engine)

    assert _names(read) == ["user-1"]
    assert read.has_more is True
    assert read.payload["query_status"] == "degraded"
    assert _kinds(engine) == ["slice", "remap", "enrich", "replay"]


@_plain_count
def test_an_exhausted_walk_publishes_degraded_and_incomplete_not_complete():
    """A short page must not look like a finished one.

    The seeded lane on this same endpoint publishes ``degraded`` and
    ``query_complete`` false when its wall stopped it, so a caller can tell
    an exhausted read from an empty answer. The walk owes the same contract:
    without it, an exhausted page arrives as ``complete`` with no rows and
    ``has_more`` true, which reads as "no matches" to anything that trusts
    the status.
    """

    world = World()
    for ordinal, minutes in enumerate((3, 7, 11), start=1):
        world.user(
            ordinal, key=minutes_before_end(minutes), raw=(minutes_before_end(minutes),)
        )

    # Exhausted by the statement budget.
    with patch.object(walk, "_statement_budget", return_value=1):
        read, _engine = _page(world, page_size=25)
    assert read.payload["table"] == []
    assert read.has_more is True
    assert read.payload["query_complete"] is False
    assert read.payload["query_status"] == "degraded"

    # Exhausted by the wall, on the same page shape.
    with patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60):
        engine = Engine(world)
        original = engine.execute_ch_query

        def slow(query, params=None, timeout_ms=None, settings=None, **caps):
            import time

            time.sleep(0.08)
            return original(query, params, timeout_ms, settings, **caps)

        engine.execute_ch_query = slow
        read, _engine = _page(world, page_size=25, engine=engine)
    assert read.has_more is True
    assert read.payload["query_complete"] is False
    assert read.payload["query_status"] == "degraded"

    # A walk that finished is still complete; the contract only changes for
    # the page that was cut short.
    finished, _engine = _page(world, page_size=25)
    assert _names(finished) == ["user-1", "user-2", "user-3"]
    assert finished.has_more is False
    assert finished.payload["query_complete"] is True
    assert finished.payload["query_status"] == "complete"


def test_finish_mode_statements_ask_the_server_to_enforce_their_deadline():
    """Exempt from the page wall is not exempt from every deadline.

    Application reads carry no server time cap, so ``timeout_ms`` alone only
    decides whether a statement is admitted. The walk's finish-mode
    statements -- the whole-window replay and the enrichment that follows it
    -- are deliberately not governed by the page wall, which is what lets a
    page publish users it has already certified; they ask the service for a
    server execution cap from the request's analytics wall instead. Search
    statements keep the application policy, admitted and never capped, except
    a slice: it asks the server to stop it at half of what is left of the
    analytics wall, so that a slice too dense for that is narrowed instead of
    read again by every request (``_read_slice``).
    (``test_finish_mode_reaches_the_native_driver_as_max_execution_time``
    follows the caps through the real service and client.)
    """

    world = World()
    for ordinal, minutes in enumerate((3, 7), start=1):
        world.user(
            ordinal, key=minutes_before_end(minutes), raw=(minutes_before_end(minutes),)
        )

    read, engine = _page(world, page_size=25)

    assert _names(read) == ["user-1", "user-2"]
    assert engine.timeouts, "the walk issued no statement at all"
    assert all(t is not None for t in engine.timeouts), engine.timeouts
    kinds = _kinds(engine)
    finish = [i for i, kind in enumerate(kinds) if kind in {"replay", "metrics"}]
    assert finish, kinds
    for index, kind in enumerate(kinds):
        if index in finish:
            assert engine.caps[index] == engine.timeouts[index], kind
        elif kind == "slice":
            cap = engine.caps[index]
            assert 0 < cap <= walk.USER_LIST_WALK_FINISH_WALL_MS / 2, kind
        else:
            assert engine.caps[index] is None, kind
    # The finish-mode budget is the analytics wall LESS what the search
    # already spent, so at least one statement is allowed more than the page
    # wall, and the page as a whole cannot exceed the analytics wall.
    assert max(engine.timeouts) > walk.USER_LIST_PAGE_WALL_MS
    assert max(engine.timeouts) <= walk.USER_LIST_WALK_FINISH_WALL_MS


class _NativeDriver:
    """A clickhouse-driver stand-in under the REAL service and client.

    It answers each statement from the scripted engine and records the
    settings the native client actually sent, so the assertion is on what
    ClickHouse would receive, not on what the walk asked for. A statement of
    ``fail_kind`` sent with a ``max_execution_time`` is stopped there, as the
    server stops it at its cap; the same statement sent without one runs.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.sent: list[tuple[str, dict | None]] = []
        self.fail_kind: str | None = None

    def execute(self, query, params=None, *, with_column_types=False, settings=None):
        kind = kind_of(query)
        self.sent.append((kind, settings))
        capped = float((settings or {}).get("max_execution_time") or 0) > 0
        if kind == self.fail_kind and capped:
            from clickhouse_driver.errors import ErrorCodes, ServerException

            raise ServerException(
                "Timeout exceeded: elapsed 8.0 seconds, maximum: 8",
                code=ErrorCodes.TIMEOUT_EXCEEDED,
            )
        result = self.engine.execute_ch_query(query, params)
        data = list(result.data or ())
        columns = list(getattr(result, "columns", None) or (data[0] if data else ()))
        rows = [tuple(row.get(name) for name in columns) for row in data]
        return rows, [(name, "String") for name in columns]


def _real_service_page(
    world: World, driver: _NativeDriver, *, cursor=None, filters=None
):
    from tracer.services.clickhouse.client import ClickHouseClient

    client = ClickHouseClient(host="localhost", port=39999, pool_size=1)
    manager = _manager(filters)
    with (
        patch.object(client, "_get_client", return_value=driver),
        patch.object(client, "_return_client"),
        patch(
            "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
            return_value=client,
        ),
        patch.object(manager, "_read_dimension_candidates", side_effect=_never_seed),
    ):
        return manager.list_cursor_payload(page_size=25, cursor=cursor)


def test_finish_mode_reaches_the_native_driver_as_max_execution_time():
    """Through ``V2AnalyticsQueryService`` and ``ClickHouseClient`` unmocked.

    The finish-mode replay arrives at the driver with a real, positive
    ``max_execution_time`` no larger than its 8,000 ms statement cap, and a
    slice with half of the analytics wall; every other search statement
    still arrives with the application policy's 0.
    """

    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    driver = _NativeDriver(Engine(world))

    read = _real_service_page(world, driver)

    assert _names(read) == ["user-1"]
    sent = dict(driver.sent)
    replay_cap = sent["replay"]["max_execution_time"]
    assert 0 < replay_cap <= 8.0
    assert sent["replay"]["timeout_overflow_mode"] == "throw"
    assert sent["replay"]["max_rows_to_read"] == 0
    slice_cap = sent["slice"]["max_execution_time"]
    assert 0 < slice_cap <= walk.USER_LIST_WALK_FINISH_WALL_MS / 2000
    for kind in ("remap", "enrich"):
        assert sent[kind]["max_execution_time"] == 0, kind


def test_the_native_certification_reaches_the_driver_with_a_cap():
    """The native statement arrives with a real ``max_execution_time``.

    A whole-window replay of the batch's identities, it is stopped by the
    server at the enrichment cap (8,000 ms) or what admits it, like the
    finish's replay; it used to arrive with the application policy's 0.
    """

    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),), native=True)
    driver = _NativeDriver(Engine(world))

    read = _real_service_page(
        world, driver, filters=[*_date_only(), _native_status_leaf()]
    )

    assert _names(read) == ["user-1"]
    sent = dict(driver.sent)
    assert 0 < sent["native"]["max_execution_time"] <= 8.0
    assert sent["native"]["timeout_overflow_mode"] == "throw"


@pytest.mark.parametrize("users", [3, 1])
def test_a_native_certification_the_server_stops_for_its_head_is_retried_without_the_cap(
    users,
):
    """Stopped at its cap for the head-of-line user alone: sent once more, uncapped.

    A batch the server stops is certified again for its head-of-line user
    alone; when that statement is stopped too, nothing narrower exists (the
    native statement has no time split), so the request sends that one
    user's statement once more with no cap, as the finish decides its
    head-of-line user's replay, and publishes the user. It used to raise, and
    the next request raised at the same user: every request, forever.
    """

    world = World()
    for n in range(1, users + 1):
        world.user(
            n, key=minutes_before_end(n), raw=(minutes_before_end(n),), native=True
        )
    driver = _NativeDriver(Engine(world))
    driver.fail_kind = "native"

    with capture_logs() as logs:
        read = _real_service_page(
            world, driver, filters=[*_date_only(), _native_status_leaf()]
        )

    assert _names(read)[:1] == ["user-1"]
    caps = [
        float(settings["max_execution_time"])
        for kind, settings in driver.sent
        if kind == "native"
    ]
    # The batch (when there is one), the user's own capped statement, then
    # the same user's without a cap: once.
    head = 3 if users > 1 else 2
    assert all(cap > 0 for cap in caps[: head - 1]), caps
    assert caps[head - 1] == 0, caps
    assert caps.count(0) == 1, caps
    uncapped = [
        entry
        for entry in logs
        if entry["event"] == "users_matching_walk_uncapped_native"
    ]
    assert [(e["reason"], e["after_batch"]) for e in uncapped] == [
        ("own_stopped", users > 1)
    ]


class _HeavyNativeDriver(_NativeDriver):
    """A heavy user's native statement outlasts any cap; uncapped, it runs.

    Every native statement that carries a heavy user and arrives with a
    ``max_execution_time`` is stopped there (code 159, as the server stops it
    at its cap); records each native statement as ``(ids, cap seconds)``.
    """

    def __init__(self, engine: Engine, heavy: set[str]) -> None:
        super().__init__(engine)
        self.heavy = frozenset(heavy)
        self.natives: list[tuple[tuple[str, ...], float]] = []

    def execute(self, query, params=None, *, with_column_types=False, settings=None):
        if kind_of(query) == "native":
            ids = tuple((params or {}).get("candidate_end_user_ids", ()))
            cap = float((settings or {}).get("max_execution_time") or 0)
            self.natives.append((ids, cap))
            if cap > 0 and self.heavy.intersection(ids):
                from clickhouse_driver.errors import ErrorCodes, ServerException

                self.sent.append(("native", settings))
                raise ServerException(
                    "Timeout exceeded: elapsed 8.0 seconds, maximum: 8",
                    code=ErrorCodes.TIMEOUT_EXCEEDED,
                )
        return super().execute(
            query, params, with_column_types=with_column_types, settings=settings
        )


def _follow_heavy_native(users: int, heavy_rank: int, max_hops: int):
    """Follow the cursor over ``users`` native users, one of them heavy.

    Returns every request's outcome (``"raise"``, or the names it published;
    a raised request is retried on the same cursor, as the client does),
    whether the list ended, and the driver.
    """
    world = World()
    ids = [
        world.user(
            n,
            key=minutes_before_end(5 * n),
            raw=(minutes_before_end(5 * n),),
            native=True,
        )
        for n in range(1, users + 1)
    ]
    driver = _HeavyNativeDriver(Engine(world), {ids[heavy_rank - 1]})
    filters = [*_date_only(), _native_status_leaf()]
    outcomes, cursor = [], None
    for _ in range(max_hops):
        try:
            read = _real_service_page(world, driver, cursor=cursor, filters=filters)
        except ReadDeadlineExceeded:
            outcomes.append("raise")
            continue
        outcomes.append(_names(read))
        if not read.has_more:
            return outcomes, True, driver
        cursor = _signed_cursor(read)
    return outcomes, False, driver


@pytest.mark.parametrize(("users", "heavy_rank"), [(1, 1), (5, 1), (5, 3), (5, 5)])
def test_a_heavy_native_user_never_blocks_the_users_ranked_behind_it(users, heavy_rank):
    """A user whose native statement always outlasts the cap is still decided.

    Review r2765: the server cap on the native certification made every
    request raise at such a user once it reached the head of line; nobody
    ranked behind it was ever shown. The walk decides that user once without
    the cap, and every user is published once, in order.
    """
    outcomes, ended, driver = _follow_heavy_native(users, heavy_rank, users + 3)

    assert "raise" not in outcomes, outcomes
    published = [name for names in outcomes for name in names]
    assert ended and published == [f"user-{n}" for n in range(1, users + 1)], outcomes
    uncapped = [ids for ids, cap in driver.natives if cap == 0]
    # Only the heavy user, alone, and only once it led a request.
    heavy = driver.heavy
    assert uncapped and all(len(ids) == 1 and heavy >= set(ids) for ids in uncapped)
    assert len(uncapped) <= len([o for o in outcomes if o]), (uncapped, outcomes)


def test_a_finish_the_server_stops_on_an_empty_page_is_retried_without_the_cap():
    """The server's timeout is the finishing deadline, not a failed request.

    The page has published nothing when the server stops its capped replay,
    so it decides its head-of-line user alone, once, with no cap, instead of
    returning empty with the user carried: for a user whose replay always
    outlasts the cap, that empty page came back on every request
    (``test_users_matching_walk_liveness`` follows those to the end, and the
    page that has already published something, which the cap still ends).
    """

    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    driver = _NativeDriver(Engine(world))
    driver.fail_kind = "replay"

    read = _real_service_page(world, driver)

    assert _names(read) == ["user-1"]
    caps = [
        settings["max_execution_time"]
        for kind, settings in driver.sent
        if kind == "replay"
    ]
    assert len(caps) == 2 and 0 < caps[0] <= 8.0 and caps[1] == 0
    assert read.has_more is False
    assert read.payload["query_status"] == "complete"


def test_finish_mode_does_not_start_a_second_full_wall_after_the_page_wall():
    """Search plus finish add up to the analytics wall, not past it.

    This asserts on the DEADLINE the walk builds, not on the timeout a
    statement ends up carrying, and the difference is the whole point.
    Enrichment and the candidate replay each cap their statement timeout at
    8,000 ms and take the smaller of cap and remaining, so a statement
    receives 8,000 whichever way finish mode is bound. A test that watched
    the statements could not tell a restarted wall from this one, and would
    pass against the very thing it exists to forbid.
    """

    import dataclasses
    from types import SimpleNamespace

    def _state(spent_ms: float):
        budget = walk._WalkBudget(
            wall_ms=walk.USER_LIST_PAGE_WALL_MS,
            max_statements=walk.USER_LIST_WALK_MAX_STATEMENTS,
        )
        # Rewind the start rather than sleeping: the walk reads elapsed time,
        # and the deadline is frozen, so this replaces it with an older one.
        budget.deadline = dataclasses.replace(
            budget.deadline, started=budget.deadline.started - spent_ms / 1000.0
        )
        return SimpleNamespace(budget=budget)

    # Nothing spent: the finish budget is the whole analytics wall.
    assert (
        abs(
            walk._finish_deadline(_state(0)).total_ms
            - walk.USER_LIST_WALK_FINISH_WALL_MS
        )
        <= 50
    )

    # Spent 1.2 s of search, and the finish budget must be shorter by that.
    spent = 1_200
    got = walk._finish_deadline(_state(spent)).total_ms
    expected = walk.USER_LIST_WALK_FINISH_WALL_MS - spent
    assert abs(got - expected) <= 100, (
        f"finish budget {got} should be about {expected}; a wall restarted at "
        f"materialisation reports the full {walk.USER_LIST_WALK_FINISH_WALL_MS}"
    )

    # The whole page is bounded by one analytics wall, not by the page wall
    # plus another: spending the entire page wall still leaves less than the
    # analytics wall for the finish.
    exhausted = walk._finish_deadline(_state(walk.USER_LIST_PAGE_WALL_MS)).total_ms
    assert (
        exhausted
        <= walk.USER_LIST_WALK_FINISH_WALL_MS - walk.USER_LIST_PAGE_WALL_MS + 100
    )

    # And it never reaches zero, however long the search ran.
    assert (
        walk._finish_deadline(_state(walk.USER_LIST_WALK_FINISH_WALL_MS * 2)).total_ms
        > 0
    )


def test_a_slice_that_fails_on_a_read_budget_is_retried_narrower_never_wider():
    from clickhouse_driver.errors import ServerException

    world = World()
    world.user(1, key=minutes_before_end(3), raw=(minutes_before_end(3),))
    engine = Engine(world)
    original = engine.execute_ch_query
    widths: list[int] = []

    def flaky(query, params=None, timeout_ms=None, settings=None, **caps):
        if kind_of(query) == "slice":
            widths.append(params["slice_end_us"] - params["slice_start_us"])
            if len(widths) == 1:
                raise ServerException("memory", code=241)
        return original(query, params, timeout_ms, settings, **caps)

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


@_plain_count
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
        with patch.object(walk, "_statement_budget", return_value=4):
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


@_plain_count
def test_a_probe_the_budget_refuses_or_that_fails_licenses_nothing():
    from clickhouse_driver.errors import ServerException

    thirty_days = _filters(window_start=WINDOW_END - timedelta(days=30))
    world = World()
    engine = Engine(world)
    original = engine.execute_ch_query

    def failing_probe(query, params=None, timeout_ms=None, settings=None, **caps):
        result = original(query, params, timeout_ms, settings, **caps)
        if kind_of(query) == "probe":
            raise ServerException("rows", code=158)
        return result

    engine.execute_ch_query = failing_probe
    with patch.object(walk, "_statement_budget", return_value=4):
        read, engine = _page(world, page_size=25, filters=thirty_days, engine=engine)
    # The failed probe changed nothing: the walk went on slicing at the cap
    # and stopped on its statement budget with a cursor.
    assert _kinds(engine) == ["slice", "estimate", "probe", "slice"]
    assert read.has_more is True and read.payload["table"] == []

    # A failing estimate is the same: no existence statement, slice on.
    engine = Engine(world)
    original = engine.execute_ch_query

    def failing_estimate(query, params=None, timeout_ms=None, settings=None, **caps):
        if kind_of(query) == "estimate":
            engine.calls.append(query)
            raise ServerException("memory", code=241)
        return original(query, params, timeout_ms, settings, **caps)

    engine.execute_ch_query = failing_estimate
    with patch.object(walk, "_statement_budget", return_value=4):
        read, engine = _page(world, page_size=25, filters=thirty_days, engine=engine)
    assert _kinds(engine) == ["slice", "estimate", "slice", "slice"]
    assert read.has_more is True and read.payload["table"] == []

    # With the budget spent on the estimate itself, the page ends there.
    engine = Engine(world)
    with patch.object(walk, "_statement_budget", return_value=2):
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


@_plain_count
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
    with patch.object(walk, "_statement_budget", return_value=4):
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


@_plain_count
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

    def probe_deadline(query, params=None, timeout_ms=None, settings=None, **caps):
        if kind_of(query) == "estimate":
            engine.calls.append(query)
            raise ReadDeadlineExceeded("probe deadline exceeded")
        return original(query, params, timeout_ms, settings, **caps)

    engine.execute_ch_query = probe_deadline
    with patch.object(walk, "_statement_budget", return_value=4):
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
    builder.walk_witness = builder.matching_activity_witness()
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
    ],
    ids=[
        "two-items-one-key",
        "number-between",
        "number-picker",
        "boolean-as-text",
        "number-not_equals",
        "number-less_than-default-matches",
        "non-ascii-text",
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

    def slow_enrichment(query, params=None, timeout_ms=None, settings=None, **caps):
        import time

        if kind_of(query) == "enrich":
            time.sleep(0.08)
        return original(query, params, timeout_ms, settings, **caps)

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

    def without_server_time(query, params=None, timeout_ms=None, settings=None, **caps):
        result = original(query, params, timeout_ms, settings, **caps)
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

    def slow_replay_then_metrics(
        query, params=None, timeout_ms=None, settings=None, **caps
    ):
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
        return original(query, params, timeout_ms, settings, **caps)

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
    # What this test is about is that the metrics read does NOT inherit the
    # spent page wall, which would raise on the client clock and drop a user
    # the page had already committed to. It gets a deadline of its own, from
    # the request's analytics wall, rather than none at all: a statement with
    # no timeout runs unbounded on a stack whose reads carry no server
    # deadline of their own.
    assert len(metric_timeouts) == 1
    assert metric_timeouts[0] is not None
    assert metric_timeouts[0] > walk.USER_LIST_PAGE_WALL_MS
    assert metric_timeouts[0] <= walk.USER_LIST_WALK_FINISH_WALL_MS


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
    assert walk.USER_LIST_WALK_EMPTY_PAGE_BUDGETS == (
        settings.USER_LIST_WALK_EMPTY_PAGE_BUDGETS
    )
