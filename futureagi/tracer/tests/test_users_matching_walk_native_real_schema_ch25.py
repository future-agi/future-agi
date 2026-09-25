"""Live ClickHouse 25 on the DEPLOYED schema: Users pages walked on a native leaf.

Runs on the lane database provisioned with the repo's own DDL
(``provision-lane-ch-db.sh``, named by ``CH25_DATABASE``), so ``model``,
``status`` and ``provider`` are the real LowCardinality columns with their
real indexes and ``trace_name`` is MATERIALIZED from ``trace_dict`` at INSERT.
The population carries what a native walk must survive: a case variant, a
stale correction whose old version is the newest witnessed row (C), a
tombstone (D), a remap alias (E), a value only outside the window (G), a span
whose trace ``trace_dict`` did not know at INSERT (G), a keyset tie (K1/K2)
and whole-window totals that sit on spans the witness never sees.

* every native walk (status, model, a family-less negation, trace_name)
  publishes exactly the users graph's members, once, in ``(key DESC, id
  DESC)`` order, across a two-id slice limit and a cursor, with whole-window
  totals;
* the walk and the seeded page (the walk switched off) publish the same set
  with identical totals, for a native-only and a raw + native page;
* an empty 30-day tail on a native witness is proven by one costed existence
  statement.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from conftest import _ch_test_native_client
from tracer.services import users_matching_walk as walk
from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.users_list_manager import UsersListManager

pytestmark = pytest.mark.integration

PROJECT = str(uuid.uuid4())
ORGANIZATION = str(uuid.UUID(int=372))
LABELS = ["A", "B", "C", "D", "E", "F", "G", "H", "K1", "K2"]
USERS = {label: str(uuid.uuid4()) for label in LABELS}
ALIAS_OF_E = str(uuid.uuid4())
WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW_END = WINDOW_START + timedelta(days=3)
SERVICE = "tracer.services.users_list_manager.V2AnalyticsQueryService"


def _lane_database() -> str:
    database = (os.environ.get("CH25_DATABASE") or "").strip()
    if not database:
        pytest.skip("no lane database named: set CH25_DATABASE")
    if database == "test_tfc" or not database.startswith("test_"):
        pytest.fail(
            f"refusing {database!r}: this module writes to a lane database "
            "provisioned with provision-lane-ch-db.sh, never the shared test_tfc"
        )
    return database


@pytest.fixture(scope="module")
def ch_client():
    database = _lane_database()
    with _ch_test_native_client(database=database) as client:
        kind = client.execute(
            "SELECT default_kind FROM system.columns WHERE database = "
            "currentDatabase() AND table = 'spans' AND name = 'trace_name'"
        )
        if kind != [("MATERIALIZED",)]:
            pytest.fail(
                f"{database} does not carry the deployed spans schema; "
                "provision it with provision-lane-ch-db.sh"
            )
        client.database_name = database
        yield client


def _trace(sid: str) -> str:
    return str(uuid.uuid5(uuid.UUID(PROJECT), f"trace-{sid}"))


def _at(hours: float) -> datetime:
    return WINDOW_START + timedelta(hours=hours)


@pytest.fixture(scope="module")
def survivor_of_e(ch_client):
    database = ch_client.database_name
    project = uuid.UUID(PROJECT)

    def traces(names: dict[str, str]) -> None:
        ch_client.execute(
            "INSERT INTO traces (id, project_id, name, created_at) VALUES",
            [
                (uuid.UUID(_trace(sid)), project, name, WINDOW_START)
                for sid, name in names.items()
            ],
        )
        ch_client.execute(f"SYSTEM RELOAD DICTIONARY {database}.trace_dict")

    def span(
        user,
        sid,
        *,
        hours,
        model="",
        status="OK",
        tag=None,
        version=1,
        deleted=0,
        cost=1.0,
    ):
        start = _at(hours)
        row = {
            "project_id": project,
            "observation_type": "llm",
            "service_name": "svc",
            "start_time": start,
            "trace_id": _trace(sid),
            "id": sid,
            "name": sid,
            "end_user_id": uuid.UUID(user),
            "end_time": start + timedelta(seconds=1),
            "latency_ms": 10,
            "cost": cost,
            "total_tokens": 3,
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "status": status,
            "model": model,
            "provider": "openai" if model else "",
            "attrs_string": {"tag": tag} if tag else {},
            "is_deleted": deleted,
            "_version": version,
        }
        columns = list(row)
        # One INSERT a row: a block is deduplicated on insert.
        ch_client.execute(
            f"INSERT INTO spans ({', '.join(columns)}) VALUES",
            [tuple(row[column] for column in columns)],
        )

    u = USERS
    ch_client.execute("SYSTEM STOP MERGES spans")
    try:
        # g2's trace is never written: its spans store trace_name ''.
        traces(
            {
                "a1": "checkout",
                "a2": "browse",
                "b1": "Checkout",
                "c1": "checkout",
                "c2": "checkout",
                "d1": "checkout",
                "d2": "browse",
                "e1": "checkout",
                "e2": "browse",
                "f1": "browse",
                "g1": "checkout",
                "h1": "checkout",
                "k1": "checkout",
                "k2": "checkout",
            }
        )
        span(u["A"], "a1", hours=60, model="gpt-4o", status="ERROR", tag="gold")
        # A's whole-window cost sits on a span no native witness sees.
        span(u["A"], "a2", hours=10, model="claude", cost=5.0)
        span(u["B"], "b1", hours=50, model="GPT-4O", tag="gold")
        # C: the newest witnessed row is a stale version (h70); its live
        # match is c2 at h20.
        span(u["C"], "c1", hours=70, model="gpt-4o", status="ERROR")
        span(u["C"], "c2", hours=20, model="gpt-4o")
        # D: its match is tombstoned; d2 is live and not a match.
        span(u["D"], "d1", hours=65, model="gpt-4o", status="ERROR")
        span(
            u["D"], "d1", hours=65, model="gpt-4o", status="ERROR", version=2, deleted=1
        )
        span(u["D"], "d2", hours=5, model="claude")
        # E: its match is carried by a remap alias; e2 carries its cost.
        span(ALIAS_OF_E, "e1", hours=40, model="gpt-4o", status="ERROR", tag="gold")
        span(u["E"], "e2", hours=2, model="claude", cost=3.0)
        span(u["F"], "f1", hours=45, model="claude", tag="gold")
        # G: its match lies before the window.
        span(u["G"], "g1", hours=-5, model="gpt-4o", status="ERROR")
        span(u["G"], "g2", hours=30)
        span(u["H"], "h1", hours=30, model="gpt-4o", status="ERROR", tag="gold")
        span(u["K1"], "k1", hours=25, model="gpt-4o", status="ERROR", tag="gold")
        span(u["K2"], "k2", hours=25, model="gpt-4o", status="ERROR")
        # C's correction is written after its trace was renamed, as a
        # production correction would be: its stored trace_name changes too.
        ch_client.execute(
            "ALTER TABLE traces DELETE WHERE id = %(id)s SETTINGS mutations_sync = 2",
            {"id": uuid.UUID(_trace("c1"))},
        )
        traces({"c1": "browse"})
        span(u["C"], "c1", hours=70, model="claude", version=2)
        stored = dict(
            ch_client.execute(
                "SELECT concat(id, '/', toString(_version)), trace_name FROM spans "
                "WHERE project_id = %(project)s",
                {"project": project},
            )
        )
        assert stored["c1/1"] == "checkout" and stored["c1/2"] == "browse"
        assert stored["g2/1"] == "" and stored["b1/1"] == "Checkout"
        ch_client.execute(
            "INSERT INTO end_users (project_id, end_user_id, organization_id, "
            "user_id, user_id_type, first_seen) VALUES",
            [
                (
                    project,
                    uuid.UUID(user),
                    uuid.UUID(ORGANIZATION),
                    f"user-{label}",
                    "string",
                    WINDOW_START,
                )
                for label, user in [*USERS.items(), ("E-alias", ALIAS_OF_E)]
            ],
        )
        # A consolidation group survives as its least OLD id: E, the only one.
        ch_client.execute(
            "INSERT INTO end_user_id_remap (old_id, new_id) VALUES",
            [(uuid.UUID(USERS["E"]), uuid.UUID(ALIAS_OF_E))],
        )
        yield USERS["E"]
    finally:
        ch_client.execute("SYSTEM START MERGES spans")
        for table in ("spans", "traces", "end_users"):
            ch_client.execute(
                f"ALTER TABLE {table} DELETE WHERE project_id = %(project)s "
                "SETTINGS mutations_sync = 2",
                {"project": project},
            )
        ch_client.execute(
            "ALTER TABLE end_user_id_remap DELETE WHERE old_id IN %(ids)s "
            "OR new_id IN %(ids)s SETTINGS mutations_sync = 2",
            {"ids": [uuid.UUID(USERS["E"]), uuid.UUID(ALIAS_OF_E)]},
        )


class _LiveExecutor:
    def __init__(self, client):
        self.client = client
        self.statements: list[str] = []

    def execute_ch_query(
        self,
        query,
        params=None,
        timeout_ms=None,
        settings=None,
        *,
        server_execution_cap_ms=None,
    ):
        self.statements.append(query)
        rows, columns = self.client.execute(
            query, params or {}, with_column_types=True, settings=settings or {}
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            columns=names,
            query_time_ms=1.0,
        )


def _date_filter(window_start=WINDOW_START):
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [window_start.isoformat(), WINDOW_END.isoformat()],
        },
    }


def _native(column_id, operation, value, *, col_type="SYSTEM_METRIC"):
    config = {"filter_type": "text", "filter_op": operation, "filter_value": value}
    if col_type is not None:
        config["col_type"] = col_type
    return {
        "column_id": column_id,
        "property_id": f"system_attribute:traces:{column_id}",
        "filter_config": config,
    }


TAG_GOLD = {
    "column_id": "tag",
    "filter_config": {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "gold",
    },
}


def _manager(*items, window_start=WINDOW_START):
    return UsersListManager(
        organization_id=ORGANIZATION,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        filters=[_date_filter(window_start), *items],
        requested_columns=[],
        attribute_keys=[],
    )


def _never_seed(**kwargs):
    raise AssertionError("the whole-window candidate statement must never run")


def _walk_page(ch_client, items, *, page_size, cursor=None, window_start=WINDOW_START):
    manager = _manager(*items, window_start=window_start)
    executor = _LiveExecutor(ch_client)
    with (
        patch(SERVICE, return_value=executor),
        patch.object(manager, "_read_dimension_candidates", side_effect=_never_seed),
    ):
        read = manager.list_cursor_payload(page_size=page_size, cursor=cursor)
    return read, executor, manager


def _cursor(read):
    assert read.has_more and read.checkpoint_order is not None
    return ListCursor(
        window_start=read.window_start,
        window_end=read.window_end,
        order=tuple(read.checkpoint_order),
        seen_rows=read.seen_rows,
    )


def _walk_all(ch_client, items, *, page_size=2):
    rows: list[dict] = []
    statements: list[str] = []
    cursor = None
    pages = 0
    # Walls wide enough that a loaded host never degrades a page: this module
    # proves membership, order and totals, not timing.
    with (
        patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 2),
        patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 64),
        patch.object(walk, "USER_LIST_PAGE_WALL_MS", 120_000),
        patch.object(walk, "USER_LIST_WALK_FINISH_WALL_MS", 240_000),
    ):
        while True:
            read, executor, manager = _walk_page(
                ch_client, items, page_size=page_size, cursor=cursor
            )
            pages += 1
            assert read.payload["query_provenance"] == "matching_activity_walk"
            assert read.payload["query_status"] == "complete"
            statements.extend(executor.statements)
            rows.extend(read.payload["table"])
            if not read.has_more:
                break
            cursor = _cursor(read)
            assert pages < 40
    return rows, statements, manager, pages


def _seeded_all(ch_client, items):
    rows: dict[str, dict] = {}
    cursor = None
    for _ in range(10):
        manager = _manager(*items)
        executor = _LiveExecutor(ch_client)
        with (
            patch(SERVICE, return_value=executor),
            patch.object(manager, "matching_activity_walk_applies", return_value=False),
        ):
            read = manager.list_cursor_payload(page_size=25, cursor=cursor)
        assert read.payload["query_provenance"] != "matching_activity_walk"
        for row in read.payload["table"]:
            assert row["user_id"] not in rows
            rows[row["user_id"]] = row
        if not read.has_more:
            break
        cursor = _cursor(read)
    return rows


def _graph_members(ch_client, survivor, *items) -> set[str]:
    query, params, _needs_eval = exact_graph_reads._user_id_membership_sql(
        project_id=PROJECT,
        filters=[_date_filter(), *items],
        start_date=WINDOW_START,
        end_date=WINDOW_END,
        all_snapshot_users=True,
    )
    labels = {user: f"user-{label}" for label, user in USERS.items()}
    labels[survivor] = "user-E"
    return {labels[str(row[0])] for row in ch_client.execute(query, params)}


def _ordered(survivor, keys: dict[str, float]) -> list[str]:
    """``user-<label>`` in page order: key DESC, then resolved id DESC."""

    ids = {**USERS, "E": survivor}
    return [
        f"user-{label}"
        for label in sorted(
            keys, key=lambda label: (keys[label], ids[label]), reverse=True
        )
    ]


GPT_MEMBERS = {"A": 60, "B": 50, "E": 40, "H": 30, "K1": 25, "K2": 25, "C": 20}
NATIVE_WALKS = [
    (
        _native("status", "equals", "error"),
        {"A": 60, "E": 40, "H": 30, "K1": 25, "K2": 25},
    ),
    (_native("model", "equals", "gpt-4o"), GPT_MEMBERS),
    (_native("trace_name", "equals", "checkout"), GPT_MEMBERS),
    # Family-less: discovered on the presence flag; a gpt-4o span anywhere in
    # the window rejects the user (A, C, E) at certification.
    (
        _native("model", "not_equals", "gpt-4o", col_type=None),
        {"F": 45, "G": 30, "D": 5},
    ),
]
NATIVE_IDS = ["status", "model", "trace_name", "model-not_equals-no-family"]
# Whole-window totals: (total_cost, num_traces).
TOTALS = {
    "user-A": (6.0, 2),
    "user-E": (4.0, 2),
    "user-C": (2.0, 2),
    "user-D": (1.0, 1),
    "user-G": (1.0, 1),
}


@pytest.mark.parametrize(("item", "keys"), NATIVE_WALKS, ids=NATIVE_IDS)
def test_a_native_walk_publishes_the_graphs_members_once_in_order(
    ch_client, survivor_of_e, item, keys
):
    rows, statements, manager, pages = _walk_all(ch_client, [item])
    names = [row["user_id"] for row in rows]

    assert manager._walk_witness.family == "native"
    assert names == _ordered(survivor_of_e, keys)
    assert set(names) == _graph_members(ch_client, survivor_of_e, item)
    assert pages >= 2
    for row in rows:
        cost, traces = TOTALS.get(row["user_id"], (1.0, 1))
        assert (row["total_cost"], row["num_traces"]) == (cost, traces), row
    slices = [s for s in statements if "AS raw_end_user_id" in s]
    assert len(slices) > 2
    assert all("attrs_string" not in s for s in slices)
    # Only certified members were ever replayed.
    replays = [s for s in statements if "candidate_users AS" in s]
    assert replays


@pytest.mark.parametrize(
    "items",
    [
        [_native("model", "equals", "gpt-4o")],
        [_native("status", "equals", "error")],
        [TAG_GOLD, _native("status", "equals", "error")],
    ],
    ids=["model", "status", "tag-and-status"],
)
def test_the_walk_and_the_seeded_page_publish_the_same_set_and_totals(
    ch_client, survivor_of_e, items
):
    walked, _statements, manager, _pages = _walk_all(ch_client, items)
    seeded = _seeded_all(ch_client, items)
    by_name = {row["user_id"]: row for row in walked}
    assert set(by_name) == set(seeded)
    for name, row in by_name.items():
        for column in ("total_cost", "num_traces", "total_tokens"):
            assert row[column] == seeded[name][column], (name, column)
    if items[0] is TAG_GOLD:
        # The raw leaf is walkable, so it is the witness and the order key.
        assert manager._walk_witness.family == "raw"
        assert [row["user_id"] for row in walked] == _ordered(
            survivor_of_e, {"A": 60, "E": 40, "H": 30, "K1": 25}
        )


def test_an_empty_thirty_day_native_tail_is_proven_by_one_costed_existence_statement(
    ch_client, survivor_of_e
):
    read, executor, manager = _walk_page(
        ch_client,
        [_native("model", "equals", "no-such-model")],
        page_size=25,
        window_start=WINDOW_END - timedelta(days=30),
    )
    assert manager._walk_witness.family == "native"
    assert manager._walk_witness.index_pruned is False
    assert read.payload["table"] == [] and read.has_more is False
    statements = executor.statements
    assert len(statements) == 3, [s.split()[0] for s in statements]
    assert "AS raw_end_user_id" in statements[0]
    assert statements[1].lstrip().startswith("EXPLAIN ESTIMATE")
    assert "AS witnessed" in statements[2] and "LIMIT 1" in statements[2]
