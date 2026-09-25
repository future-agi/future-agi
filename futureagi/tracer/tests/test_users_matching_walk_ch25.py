"""Two-page cursor proof of the matching-activity walk on live ClickHouse 25.

One seeded population carries every mutation the walk must survive - a witness
only on an older version, a latest version handed to another user, a
tombstone, a remap alias, a case-variant value and an uncurated user - plus
activity outside the newest slice, so the published totals can only be right
if they come from the whole-window replay. The manager is driven end to end
through a table-binding executor; slices are forced to two users so the page
walks several slices and the cursor is resumed twice.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services import users_matching_walk as walk
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager

PROJECT = str(uuid.UUID(int=71))
ORGANIZATION = str(uuid.UUID(int=72))
USER_A, USER_B, USER_C, USER_D = (str(uuid.UUID(int=n)) for n in (81, 82, 83, 84))
USER_E, USER_F, USER_G, USER_H = (str(uuid.UUID(int=n)) for n in (85, 86, 87, 88))
ALIAS_OF_E = str(uuid.UUID(int=89))
WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW_END = WINDOW_START + timedelta(days=3)
SERVICE = "tracer.services.users_list_manager.V2AnalyticsQueryService"


@pytest.fixture(scope="module")
def ch_database():
    with _ch_test_owned_database("test_users_matching_walk_") as database:
        yield database


@pytest.fixture(scope="module")
def ch_client(ch_database):
    with _ch_test_native_client(database=ch_database) as client:
        yield client


@pytest.fixture(scope="module")
def seeded_tables(ch_client):
    # The production names, inside this module's own throwaway database: the
    # walk's EXPLAIN ESTIMATE reducer accepts the ``spans`` table alone, so
    # the statements must reach the server naming it.
    spans = "spans"
    end_users = "end_users"
    remap = "end_user_id_remap"
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
            _version UInt64,
            INDEX idx_attrs_str_keys mapKeys(attrs_string) TYPE bloom_filter(0.01) GRANULARITY 1,
            INDEX idx_attrs_num_keys mapKeys(attrs_number) TYPE bloom_filter(0.01) GRANULARITY 1,
            INDEX idx_attrs_bool_keys mapKeys(attrs_bool) TYPE bloom_filter(0.01) GRANULARITY 1,
            INDEX idx_attrs_str_values arrayMap(x -> lower(x), mapValues(attrs_string))
                TYPE bloom_filter(0.01) GRANULARITY 1,
            INDEX idx_attrs_num_values mapValues(attrs_number) TYPE bloom_filter(0.01) GRANULARITY 1
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, observation_type, service_name,
                  toStartOfHour(start_time), trace_id, id)
        SETTINGS index_granularity = 2
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
            _bind(query, self.tables), params or {}, with_column_types=True
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            columns=names,
            query_time_ms=1.0,
        )


def _manager(*, value="gold", window_start=WINDOW_START, project=PROJECT):
    return UsersListManager(
        organization_id=ORGANIZATION,
        allowed_project_ids=[project],
        project_id=project,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [window_start.isoformat(), WINDOW_END.isoformat()],
                },
            },
            {
                "column_id": "tag",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": value,
                },
            },
        ],
        requested_columns=[],
        attribute_keys=[],
    )


def _never_seed(**kwargs):
    raise AssertionError("the whole-window candidate statement must never run")


def _read_page(
    ch_client,
    tables,
    *,
    page_size,
    cursor=None,
    value="gold",
    window_start=WINDOW_START,
    project=PROJECT,
):
    manager = _manager(value=value, window_start=window_start, project=project)
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


def test_a_tied_instant_pages_by_resolved_id_on_live_clickhouse(
    ch_client, seeded_tables
):
    """Live: the instant statement resolves aliases and pages inside one tie.

    A separate project holds five users and one consolidation group whose
    span at the instant carries the group's NEW id (sorting above every
    survivor) while the group resolves to its oldest id (sorting below them
    all), plus a user whose only row at the instant is a stale version and
    one user above the instant. Two-id slices make the tie fill a slice, so
    the instant is decided through the resolved-order statement, across
    two-row pages and their cursors.
    """
    spans, end_users, remap = seeded_tables
    project = str(uuid.UUID(int=300))
    instant = WINDOW_START + timedelta(hours=12)
    tied = [str(uuid.UUID(int=n)) for n in range(302, 307)]
    survivor, new_id = str(uuid.UUID(int=301)), str(uuid.UUID(int=0x3FF))
    stale, above = str(uuid.UUID(int=0x3FE)), str(uuid.UUID(int=0x3FD))

    def span(sid, user, tag, version, start):
        return (
            uuid.UUID(project), "llm", "svc", start, f"t-{sid}", sid,
            uuid.UUID(user), start, {"tag": tag}, {}, {}, "{}",
            1.0, 10, 6, 4, 0, version,
        )  # fmt: skip

    rows = [span(f"tie-{n}", user, "gold", 1, instant) for n, user in enumerate(tied)]
    rows += [
        span("tie-alias", new_id, "gold", 1, instant),
        span("tie-stale", stale, "gold", 1, instant),
        span("tie-stale", stale, "silver", 2, instant),
        span("tie-stale-live", stale, "gold", 1, instant - timedelta(hours=1)),
        span("tie-above", above, "gold", 1, instant + timedelta(hours=1)),
    ]
    ch_client.execute(
        f"INSERT INTO {spans} (project_id, observation_type, service_name, "
        "start_time, trace_id, id, end_user_id, end_time, attrs_string, "
        "attrs_number, attrs_bool, attributes_extra, cost, total_tokens, "
        "prompt_tokens, completion_tokens, is_deleted, _version) VALUES",
        rows,
    )
    labels = {user: f"tie-{user[-3:]}" for user in [*tied, survivor, stale, above]}
    ch_client.execute(
        f"INSERT INTO {end_users} VALUES",
        [
            (uuid.UUID(project), uuid.UUID(user), uuid.UUID(ORGANIZATION), label,
             "string", label, WINDOW_START, 1, 0)
            for user, label in labels.items()
        ],
    )  # fmt: skip
    ch_client.execute(
        f"INSERT INTO {remap} VALUES", [(uuid.UUID(survivor), uuid.UUID(new_id), 1)]
    )

    names, statements, cursor = [], [], None
    with patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 2):
        while True:
            read, executor = _read_page(
                ch_client, seeded_tables, page_size=2, cursor=cursor, project=project
            )
            names += [row["user_id"] for row in read.payload["table"]]
            statements += executor.statements
            if not read.has_more:
                break
            cursor = _cursor(read)
            assert len(names) < 20

    expected = [above, *sorted(tied, reverse=True), survivor, stale]
    assert names == [labels[user] for user in expected]
    assert any("AS instant_end_user_id" in s for s in statements)


def test_published_totals_are_whole_window_not_slice(ch_client, seeded_tables):
    # Two-id slices make this three-day window a dozen slices, each populated
    # one followed by its survivor statement; give the page the statements
    # that shape needs so it finishes instead of returning partial + cursor.
    with (
        patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", 2),
        patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 48),
    ):
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


def test_empty_thirty_day_tail_is_proven_by_one_costed_existence_statement(
    ch_client, seeded_tables
):
    """Live: the estimate is a row of zeros for a value the value bloom
    excludes, the existence statement then reads nothing, and the page says
    empty in three statements instead of one slice per day. The estimate of
    a present value is smaller than the table: the blooms are applied to it,
    so it is the cost of the statement it wraps and not the window's size.
    """
    read, executor = _read_page(
        ch_client,
        seeded_tables,
        page_size=25,
        value="platinum",
        window_start=WINDOW_END - timedelta(days=30),
    )
    assert read.payload["table"] == [] and read.has_more is False
    statements = executor.statements
    assert len(statements) == 3, [s.split()[0] for s in statements]
    assert "AS raw_end_user_id" in statements[0]
    assert statements[1].lstrip().startswith("EXPLAIN ESTIMATE")
    assert "AS witnessed" in statements[2] and "LIMIT 1" in statements[2]

    builder = UserListQueryBuilderV2(
        organization_id=ORGANIZATION,
        project_ids=[PROJECT],
        filters=_manager(value="gold").filters,
        search="",
        empty_scope=False,
    )
    spans = seeded_tables[0]
    total_rows = ch_client.execute(f"SELECT count() FROM {spans}")[0][0]

    def estimate_for(value):
        candidate = UserListQueryBuilderV2(
            organization_id=ORGANIZATION,
            project_ids=[PROJECT],
            filters=_manager(value=value).filters,
            search="",
            empty_scope=False,
        )
        candidate.walk_witness = candidate.matching_activity_witness()
        sql, params = candidate.build_matching_activity_existence_estimate_query(
            range_start=WINDOW_START, range_end=WINDOW_END
        )
        rows, columns = ch_client.execute(
            _bind(sql, seeded_tables), params, with_column_types=True
        )
        names = [name for name, _type in columns]
        data = [dict(zip(names, row, strict=True)) for row in rows]
        return data, builder.matching_activity_existence_estimate(data, names)

    absent_rows, absent = estimate_for("platinum")
    assert absent == 0
    assert absent_rows and all(row["parts"] == 0 for row in absent_rows), (
        "ClickHouse 25.3 reports a selection of no part as a row of zeros"
    )
    _present_rows, present = estimate_for("gold")
    assert 0 < present < total_rows
