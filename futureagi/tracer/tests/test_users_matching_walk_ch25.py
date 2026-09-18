"""Two-page cursor proof of the matching-activity walk on live ClickHouse 25.

One seeded population carries every mutation the walk must survive - a witness
only on an older version, a latest version handed to another user, a
tombstone, a remap alias, a case-variant value and an uncurated user - plus
activity outside the newest slice, so the published totals can only be right
if they come from the whole-window replay. The manager is driven end to end
through a table-binding executor; slices are forced to two users so the page
walks several slices and the cursor is resumed twice.
"""

import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from clickhouse_driver import Client

from conftest import _require_safe_ch25_test_target
from tracer.services import users_matching_walk as walk
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.users_list_manager import UsersListManager

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_NATIVE_PORT = int(
    os.environ.get("CH25_NATIVE_PORT") or os.environ.get("CH25_TCP_PORT") or "19000"
)
CH_USER = os.environ.get("CH25_USER") or os.environ.get("CH_USERNAME") or "default"
CH_PASSWORD = os.environ.get("CH25_PASSWORD") or os.environ.get("CH_PASSWORD") or ""

PROJECT = str(uuid.UUID(int=71))
ORGANIZATION = str(uuid.UUID(int=72))
USER_A, USER_B, USER_C, USER_D = (str(uuid.UUID(int=n)) for n in (81, 82, 83, 84))
USER_E, USER_F, USER_G, USER_H = (str(uuid.UUID(int=n)) for n in (85, 86, 87, 88))
ALIAS_OF_E = str(uuid.UUID(int=89))
WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW_END = WINDOW_START + timedelta(days=3)
SERVICE = "tracer.services.users_list_manager.V2AnalyticsQueryService"


def _ch_client(*, database):
    return Client(
        host=CH_HOST,
        port=CH_NATIVE_PORT,
        user=CH_USER,
        password=CH_PASSWORD,
        database=database,
        connect_timeout=3,
    )


@pytest.fixture(scope="module")
def ch_database():
    database = f"test_users_matching_walk_{uuid.uuid4().hex}"
    _require_safe_ch25_test_target(host=CH_HOST, database=database)
    admin = _ch_client(database="default")
    created = False
    try:
        try:
            admin.execute("SELECT 1")
        except Exception as exc:
            pytest.skip(
                f"CH25 is not reachable on {CH_HOST}:{CH_NATIVE_PORT} ({exc!r})"
            )
        admin.execute(f"CREATE DATABASE {database}")
        created = True
        yield database
    finally:
        try:
            if created:
                admin.execute(f"DROP DATABASE IF EXISTS {database} SYNC")
        finally:
            admin.disconnect()


@pytest.fixture(scope="module")
def ch_client(ch_database):
    client = _ch_client(database=ch_database)
    try:
        yield client
    finally:
        client.disconnect()


@pytest.fixture(scope="module")
def seeded_tables(ch_client):
    suffix = uuid.uuid4().hex[:8]
    spans = f"_test_walk_spans_{suffix}"
    end_users = f"_test_walk_end_users_{suffix}"
    remap = f"_test_walk_remap_{suffix}"
    ch_client.execute(
        f"""
        CREATE TABLE {spans} (
            project_id UUID,
            observation_type String,
            service_name String,
            start_time DateTime64(6, 'UTC'),
            trace_id String,
            id String,
            end_user_id Nullable(UUID),
            end_time Nullable(DateTime64(6, 'UTC')),
            attrs_string Map(LowCardinality(String), String),
            attrs_number Map(LowCardinality(String), Float64),
            attrs_bool Map(LowCardinality(String), UInt8),
            attributes_extra String,
            cost Float64,
            total_tokens Int32,
            prompt_tokens Int32,
            completion_tokens Int32,
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, observation_type, service_name,
                  toStartOfHour(start_time), trace_id, id)
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {end_users} (
            project_id UUID,
            end_user_id UUID,
            organization_id UUID,
            user_id String,
            user_id_type String,
            user_id_hash String,
            first_seen DateTime64(6, 'UTC'),
            version UInt64,
            is_deleted UInt8
        ) ENGINE = ReplacingMergeTree(version)
        ORDER BY (project_id, end_user_id)
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {remap} (
            old_id UUID,
            new_id UUID,
            version UInt64
        ) ENGINE = ReplacingMergeTree(version)
        ORDER BY (old_id, new_id)
        """
    )
    # Every version must stay a physical row: the walk's witness is a raw
    # superset and the certification must see the stale ones.
    ch_client.execute(f"SYSTEM STOP MERGES {spans}")

    def span(trace, sid, user, tag, version, *, hours, deleted=0, cost=1.0):
        start = WINDOW_START + timedelta(hours=hours)
        return (
            uuid.UUID(PROJECT),
            "llm",
            "svc",
            start,
            trace,
            sid,
            uuid.UUID(user) if user else None,
            start + timedelta(seconds=5),
            {"tag": tag} if tag else {},
            {},
            {},
            "{}",
            cost,
            10,
            6,
            4,
            deleted,
            version,
        )

    columns = (
        "project_id, observation_type, service_name, start_time, trace_id, id, "
        "end_user_id, end_time, attrs_string, attrs_number, attrs_bool, "
        "attributes_extra, cost, total_tokens, prompt_tokens, completion_tokens, "
        "is_deleted, _version"
    )
    batches = [
        # A: old non-matching activity, then two matching spans; key = hour 30,
        # totals must include hour 2 (never in the newest slice).
        [span("t-a1", "a1", USER_A, "silver", 1, hours=2, cost=5.0)],
        [span("t-a2", "a2", USER_A, "gold", 1, hours=6)],
        [span("t-a3", "a3", USER_A, "gold", 1, hours=30, cost=2.0)],
        # B: newest witness is a stale version (v2 moved the value away);
        # the live matching span is at hour 10.
        [span("t-b1", "b1", USER_B, "gold", 1, hours=40)],
        [span("t-b1", "b1", USER_B, "silver", 2, hours=40)],
        [span("t-b2", "b2", USER_B, "gold", 1, hours=10)],
        # C -> D: the latest version belongs to D; C never matches.
        [span("t-c1", "c1", USER_C, "gold", 1, hours=35)],
        [span("t-c1", "c1", USER_D, "gold", 2, hours=35)],
        # E: matches only through an alias; E's own activity is older.
        [span("t-e1", "e1", ALIAS_OF_E, "gold", 1, hours=20)],
        [span("t-e2", "e2", USER_E, "gold", 1, hours=1, cost=3.0)],
        # F: a tombstone on its only witnessed identity.
        [span("t-f1", "f1", USER_F, "gold", 1, hours=45)],
        [span("t-f1", "f1", USER_F, "gold", 2, hours=45, deleted=1)],
        # G: the newest match of all, but not in the curated dimension.
        [span("t-g1", "g1", USER_G, "gold", 1, hours=50)],
        # H: a case variant; equals is case-insensitive.
        [span("t-h1", "h1", USER_H, "GOLD", 1, hours=25)],
    ]
    for batch in batches:
        ch_client.execute(f"INSERT INTO {spans} ({columns}) VALUES", batch)
    ch_client.execute(
        f"INSERT INTO {end_users} VALUES",
        [
            (
                uuid.UUID(PROJECT),
                uuid.UUID(user),
                uuid.UUID(ORGANIZATION),
                label,
                "string",
                label,
                WINDOW_START,
                1,
                0,
            )
            for user, label in (
                (USER_A, "alpha"),
                (USER_B, "bravo"),
                (USER_C, "charlie"),
                (USER_D, "delta"),
                (USER_E, "echo"),
                (ALIAS_OF_E, "echo-old"),
                (USER_F, "foxtrot"),
                (USER_H, "hotel"),
            )
        ],
    )
    ch_client.execute(
        f"INSERT INTO {remap} VALUES",
        [(uuid.UUID(ALIAS_OF_E), uuid.UUID(USER_E), 1)],
    )
    physical_versions = ch_client.execute(
        f"SELECT count() FROM {spans} WHERE id = 'b1'"
    )[0][0]
    assert physical_versions == 2, "both versions of the moved value must persist"
    return spans, end_users, remap


def _bind(sql, tables):
    spans, end_users, remap = tables
    sql = sql.replace("FROM end_users AS eu", f"FROM {end_users} AS eu")
    sql = sql.replace("FROM end_user_id_remap", f"FROM {remap}")
    return re.sub(r"\bspans\b", spans, sql)


class _LiveExecutor:
    """Runs the manager's statements as they are, on the seeded tables."""

    def __init__(self, client, tables):
        self.client = client
        self.tables = tables
        self.statements: list[str] = []

    def execute_ch_query(self, query, params=None, timeout_ms=None, settings=None):
        self.statements.append(query)
        rows, columns = self.client.execute(
            _bind(query, self.tables), params or {}, with_column_types=True
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            query_time_ms=1.0,
        )


def _manager():
    return UsersListManager(
        organization_id=ORGANIZATION,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        filters=[
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
        ],
        requested_columns=[],
        attribute_keys=[],
    )


def _never_seed(**kwargs):
    raise AssertionError("the whole-window candidate statement must never run")


def _read_page(ch_client, tables, *, page_size, cursor=None):
    manager = _manager()
    executor = _LiveExecutor(ch_client, tables)
    with (
        patch(SERVICE, return_value=executor),
        patch.object(manager, "_read_dimension_candidates", side_effect=_never_seed),
    ):
        read = manager.list_cursor_payload(page_size=page_size, cursor=cursor)
    return read, executor


def _cursor(read):
    assert read.has_more and read.checkpoint_order is not None
    return ListCursor(
        window_start=read.window_start,
        window_end=read.window_end,
        order=tuple(read.checkpoint_order),
        seen_rows=read.seen_rows,
    )


def test_two_page_cursor_publishes_every_member_once_in_matching_order(
    ch_client, seeded_tables
):
    pages = []
    cursor = None
    with patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 2):
        while True:
            read, executor = _read_page(
                ch_client, seeded_tables, page_size=2, cursor=cursor
            )
            pages.append([row["user_id"] for row in read.payload["table"]])
            assert read.payload["query_provenance"] == "matching_activity_walk"
            assert read.payload["ordering"] == "latest_matching_activity"
            assert read.payload["query_exact"] is True
            assert all(
                "scalar_witness_identities" not in s for s in executor.statements
            )
            if not read.has_more:
                break
            cursor = _cursor(read)
            assert len(pages) < 8

    # D (hour 35), A (30), H (25), E (20 via its alias), B (10: its hour-40
    # witness is stale). C moved, F tombstoned, G uncurated: never published.
    assert pages == [["delta", "alpha"], ["hotel", "echo-old"], ["bravo"]]
    assert read.seen_rows == 5


def test_published_totals_are_whole_window_not_slice(ch_client, seeded_tables):
    with patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 2):
        read, executor = _read_page(ch_client, seeded_tables, page_size=25)
    rows = {row["user_id"]: row for row in read.payload["table"]}
    assert list(rows) == ["delta", "alpha", "hotel", "echo-old", "bravo"]
    # A: hour-2 silver (5.0) + hour-6 gold (1.0) + hour-30 gold (2.0).
    assert rows["alpha"]["total_cost"] == 8.0
    assert rows["alpha"]["num_traces"] == 3
    # E's consolidation group is published under its survivor (the old id,
    # labelled echo-old): the alias's hour-20 match (1.0) plus E's own
    # hour-1 span (3.0), both sides of the remap.
    assert rows["echo-old"]["total_cost"] == 4.0
    assert rows["echo-old"]["num_traces"] == 2
    # B: the moved value's latest version still counts as activity (1.0 + 1.0).
    assert rows["bravo"]["total_cost"] == 2.0
    assert rows["delta"]["total_cost"] == 1.0
    # Every replay was issued for published users only.
    replays = [s for s in executor.statements if "candidate_users AS" in s]
    assert replays and all(
        "HAVING end_user_id IN %(candidate_end_user_ids)s" in s for s in replays
    )
    assert read.has_more is False
