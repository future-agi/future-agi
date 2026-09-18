"""Hash and in-order execution of the seeded Users page publish the same rows.

Live ClickHouse 25 differential: the shipped seeded acquisition statement and
the same text with in-order reads restored run over one seeded population that
carries the mutations the statement exists to survive - a witness only on an
older version, a latest version handed to another user, a tombstone and a
remap alias - and must publish identical rows in identical order. EXPLAIN
PIPELINE pins the mechanism: the shipped statement carries no in-order
aggregation or sorted-distinct processor, the restored one does.
"""

import os
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from clickhouse_driver import Client

from conftest import _require_safe_ch25_test_target
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_NATIVE_PORT = int(
    os.environ.get("CH25_NATIVE_PORT") or os.environ.get("CH25_TCP_PORT") or "19000"
)
CH_USER = os.environ.get("CH25_USER") or os.environ.get("CH_USERNAME") or "default"
CH_PASSWORD = os.environ.get("CH25_PASSWORD") or os.environ.get("CH_PASSWORD") or ""

PROJECT = str(uuid.UUID(int=51))
ORGANIZATION = str(uuid.UUID(int=52))
USER_A, USER_B, USER_C, USER_D = (str(uuid.UUID(int=n)) for n in (61, 62, 63, 64))
ALIAS_OF_A = str(uuid.UUID(int=65))
WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW_END = WINDOW_START + timedelta(days=3)
SHIPPED_TAIL = (
    "optimize_aggregation_in_order = 0, optimize_distinct_in_order = 0, "
    "optimize_read_in_order = 0"
)
RESTORED_TAIL = (
    "optimize_aggregation_in_order = 1, optimize_distinct_in_order = 1, "
    "optimize_read_in_order = 1"
)
IN_ORDER_PROCESSORS = ("AggregatingInOrder", "DistinctSorted")


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
    database = f"test_users_seeded_read_{uuid.uuid4().hex}"
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
    spans = f"_test_seeded_spans_{suffix}"
    end_users = f"_test_seeded_end_users_{suffix}"
    remap = f"_test_seeded_remap_{suffix}"
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
    # Every version must stay a physical row: a background merge would collapse
    # the moved user and the tombstone before the statement replays them.
    ch_client.execute(f"SYSTEM STOP MERGES {spans}")
    t0 = WINDOW_START + timedelta(hours=6)

    def span(trace, sid, user, tag, version, *, hours=0, deleted=0, cost=1.0):
        start = t0 + timedelta(hours=hours)
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
            cost,
            10,
            6,
            4,
            deleted,
            version,
        )

    columns = (
        "project_id, observation_type, service_name, start_time, trace_id, id, "
        "end_user_id, end_time, attrs_string, cost, total_tokens, prompt_tokens, "
        "completion_tokens, is_deleted, _version"
    )
    # Every batch is its own part, so both execution shapes see several parts.
    batches = [
        # A: a plain match, re-versioned with the same value.
        [
            span("t-a", "a1", USER_A, "Gold", 1),
            span("t-a", "a2", USER_A, "gold", 1, hours=1),
        ],
        [span("t-a", "a1", USER_A, "gold", 2, cost=2.0)],
        # B: witnessed on version 1 only; the latest version belongs to C.
        [span("t-b", "b1", USER_B, "gold", 1, hours=2)],
        [span("t-b", "b1", USER_C, "gold", 2, hours=2)],
        # A tombstone on a witnessed identity.
        [span("t-a", "a2", USER_A, "gold", 2, hours=1, deleted=1)],
        # An alias of A carries a match on an older physical id.
        [span("t-x", "x1", ALIAS_OF_A, "gold", 1, hours=3)],
        # D never matches.
        [span("t-d", "d1", USER_D, "silver", 1, hours=4)],
        # C has more activity outside the witness; its metrics must count it.
        [span("t-c", "c1", USER_C, "", 1, hours=5, cost=3.0)],
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
                (ALIAS_OF_A, "alpha-old"),
            )
        ],
    )
    ch_client.execute(
        f"INSERT INTO {remap} VALUES",
        [(uuid.UUID(ALIAS_OF_A), uuid.UUID(USER_A), 1)],
    )
    parts = ch_client.execute(
        "SELECT count() FROM system.parts WHERE database = currentDatabase() "
        "AND table = %(t)s AND active",
        {"t": spans},
    )[0][0]
    assert parts >= 2, "the fixture must span several parts"
    physical_versions = ch_client.execute(
        f"SELECT count() FROM {spans} WHERE id = 'b1'"
    )[0][0]
    assert physical_versions == 2, "both versions of the moved span must persist"
    return spans, end_users, remap


def _bind(sql, tables):
    spans, end_users, remap = tables
    sql = sql.replace("FROM end_users AS eu", f"FROM {end_users} AS eu")
    sql = sql.replace("FROM end_user_id_remap", f"FROM {remap}")
    return re.sub(r"\bspans\b", spans, sql)


def _seeded_statement(tables):
    builder = UserListQueryBuilderV2(
        organization_id=ORGANIZATION,
        project_ids=[PROJECT],
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
        search="",
        empty_scope=False,
    )
    sql, params = builder.build_dimension_candidate_query(
        limit=65, window_start=WINDOW_START, window_end=WINDOW_END
    )
    assert "scalar_witness_identities AS" in sql
    assert sql.count(SHIPPED_TAIL) == 1
    return _bind(sql, tables), params


def _restored(sql):
    return sql.replace(SHIPPED_TAIL, RESTORED_TAIL)


def _rows(ch_client, sql, params):
    rows, columns = ch_client.execute(sql, params, with_column_types=True)
    names = [name for name, _type in columns]
    return [dict(zip(names, row, strict=True)) for row in rows]


def _pipeline(ch_client, sql, params):
    return "\n".join(
        row[0] for row in ch_client.execute("EXPLAIN PIPELINE " + sql, params)
    )


def test_hash_and_in_order_execution_publish_identical_pages(ch_client, seeded_tables):
    sql, params = _seeded_statement(seeded_tables)
    shipped = _rows(ch_client, sql, params)
    restored = _rows(ch_client, _restored(sql), params)
    assert shipped == restored
    published = [row["user_id"] for row in shipped]
    # The witness set is a superset the manager narrows later; the page itself
    # must still carry the users whose latest live spans it replayed.
    assert "charlie" in published and "delta" not in published
    # B's only span moved to C in its latest version: only a replay of every
    # version can drop B, and only from the latest one can C's page be right.
    assert "bravo" not in published
    assert len(published) >= 2
    by_label = {row["user_id"]: row for row in shipped}
    # C's usage counts the un-witnessed span too: metrics are set-valued.
    assert by_label["charlie"]["num_traces"] == 2
    assert by_label["charlie"]["total_cost"] == pytest.approx(4.0)


def test_shipped_statement_carries_no_in_order_processor(ch_client, seeded_tables):
    sql, params = _seeded_statement(seeded_tables)
    shipped = _pipeline(ch_client, sql, params)
    restored = _pipeline(ch_client, _restored(sql), params)
    assert not any(name in shipped for name in IN_ORDER_PROCESSORS), shipped
    assert any(name in restored for name in IN_ORDER_PROCESSORS), restored


def test_witness_scan_alone_drops_sorted_distinct(ch_client, seeded_tables):
    sql, params = _seeded_statement(seeded_tables)
    start = re.search(r"scalar_witness_identities AS \(", sql).end()
    depth, end = 1, start
    while depth:
        depth += (sql[end] == "(") - (sql[end] == ")")
        end += 1
    witness = sql[start : end - 1]
    shipped = _pipeline(ch_client, f"{witness} SETTINGS {SHIPPED_TAIL}", params)
    restored = _pipeline(ch_client, f"{witness} SETTINGS {RESTORED_TAIL}", params)
    assert "DistinctSorted" not in shipped, shipped
    assert "DistinctSorted" in restored, restored
