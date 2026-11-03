"""Differential: the walk versus the seeded page on one live CH25 world.

The PR's live fixture plus the review's counter-cases:

* I: the only match is at exactly WINDOW_START (member on both paths);
* J: the only match is at exactly WINDOW_END (excluded on both paths);
* K1/K2: identical newest witness and identical certified key (keyset tie);
* L: the latest version carries the key in ``attributes_extra`` as well as
  ``attrs_string`` (the enrichment's multiIf picks ``json``; both paths must
  agree);
* M: a raw witness above the floor on a stale version whose live match is
  older than every other live match (floor rule across a cursor);

and, on the same tables, the typed populations the walk now serves: a number
comparison and a boolean equality, each carried by ``attrs_number`` /
``attrs_bool`` with a stale version, a string-typed decoy, a zero / false
value and a value the raw witness admits but the latest state rejects.

The seeded page is produced by the same manager with the walk switched off
(``matching_activity_walk_applies`` patched False), so the whole-window
candidate statement, the physical witness prune (text) or the plain
whole-window statement (typed) and the replay all run on the same tables.
The published SET and each published user's whole-window totals must be
identical; only the order may differ.
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
from tracer.services.users_list_manager import UsersListManager

PROJECT = str(uuid.UUID(int=71))
ORGANIZATION = str(uuid.UUID(int=72))
USER_A, USER_B, USER_C, USER_D = (str(uuid.UUID(int=n)) for n in (81, 82, 83, 84))
USER_E, USER_F, USER_G, USER_H = (str(uuid.UUID(int=n)) for n in (85, 86, 87, 88))
ALIAS_OF_E = str(uuid.UUID(int=89))
USER_I, USER_J, USER_K1, USER_K2 = (str(uuid.UUID(int=n)) for n in (90, 91, 92, 93))
USER_L, USER_M = (str(uuid.UUID(int=n)) for n in (94, 95))
# Typed populations: N* carry a number, Q* a boolean.
USER_N1, USER_N2, USER_N3, USER_N4, USER_N5, USER_N6 = (
    str(uuid.UUID(int=n)) for n in (101, 102, 103, 104, 105, 106)
)
USER_Q1, USER_Q2, USER_Q3, USER_Q4 = (
    str(uuid.UUID(int=n)) for n in (111, 112, 113, 114)
)
WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW_END = WINDOW_START + timedelta(days=3)
SERVICE = "tracer.services.users_list_manager.V2AnalyticsQueryService"


@pytest.fixture(scope="module")
def ch_database():
    with _ch_test_owned_database("test_walk_differential_") as database:
        yield database


@pytest.fixture(scope="module")
def ch_client(ch_database):
    with _ch_test_native_client(database=ch_database) as client:
        yield client


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
    ch_client.execute(f"SYSTEM STOP MERGES {spans}")

    def span(
        trace,
        sid,
        user,
        tag,
        version,
        *,
        hours,
        deleted=0,
        cost=1.0,
        extra="{}",
        number=None,
        flag=None,
    ):
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
            {"score": float(number)} if number is not None else {},
            {"flag": int(flag)} if flag is not None else {},
            extra,
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
        [span("t-a1", "a1", USER_A, "silver", 1, hours=2, cost=5.0)],
        [span("t-a2", "a2", USER_A, "gold", 1, hours=6)],
        [span("t-a3", "a3", USER_A, "gold", 1, hours=30, cost=2.0)],
        [span("t-b1", "b1", USER_B, "gold", 1, hours=40)],
        [span("t-b1", "b1", USER_B, "silver", 2, hours=40)],
        [span("t-b2", "b2", USER_B, "gold", 1, hours=10)],
        [span("t-c1", "c1", USER_C, "gold", 1, hours=35)],
        [span("t-c1", "c1", USER_D, "gold", 2, hours=35)],
        [span("t-e1", "e1", ALIAS_OF_E, "gold", 1, hours=20)],
        [span("t-e2", "e2", USER_E, "gold", 1, hours=1, cost=3.0)],
        [span("t-f1", "f1", USER_F, "gold", 1, hours=45)],
        [span("t-f1", "f1", USER_F, "gold", 2, hours=45, deleted=1)],
        [span("t-g1", "g1", USER_G, "gold", 1, hours=50)],
        [span("t-h1", "h1", USER_H, "GOLD", 1, hours=25)],
        # I: only match exactly at the window start (inclusive on both paths).
        [span("t-i1", "i1", USER_I, "gold", 1, hours=0, cost=7.0)],
        # J: only match exactly at the window end (exclusive on both paths).
        [span("t-j1", "j1", USER_J, "gold", 1, hours=72)],
        # K1/K2: identical newest witness and identical certified key.
        [span("t-k1", "k1", USER_K1, "gold", 1, hours=28)],
        [span("t-k2", "k2", USER_K2, "gold", 1, hours=28)],
        # L: latest version carries the key in attributes_extra too; the
        # enrichment's multiIf reports the json value, so neither path admits.
        [span("t-l1", "l1", USER_L, "gold", 1, hours=33)],
        [span("t-l1", "l1", USER_L, "gold", 2, hours=33, extra='{"tag": "gold"}')],
        # M: stale raw witness at hour 60 (v2 moved the value away), live
        # match at hour 0.5: the oldest live key of every member.
        [span("t-m1", "m1", USER_M, "gold", 1, hours=60)],
        [span("t-m1", "m1", USER_M, "silver", 2, hours=60)],
        [span("t-m2", "m2", USER_M, "gold", 1, hours=0.5, cost=4.0)],
        # Numbers, filter ``score > 5``. N1: 9 at hour 12 and 3 at hour 40
        # (key = hour 12, totals include hour 40). N2: stale 9 at hour 50 that
        # v2 lowered to 2; live 7 at hour 8. N3: the value 5 exactly (not
        # greater). N4: "9" stored as a string only: no number match. N5: 6
        # on a tombstoned identity only.
        [span("t-n1a", "n1a", USER_N1, None, 1, hours=12, number=9)],
        [span("t-n1b", "n1b", USER_N1, None, 1, hours=40, number=3, cost=2.0)],
        [span("t-n2a", "n2a", USER_N2, None, 1, hours=50, number=9)],
        [span("t-n2a", "n2a", USER_N2, None, 2, hours=50, number=2)],
        [span("t-n2b", "n2b", USER_N2, None, 1, hours=8, number=7)],
        [span("t-n3", "n3", USER_N3, None, 1, hours=20, number=5)],
        [span("t-n4", "n4", USER_N4, "9", 1, hours=22)],
        [span("t-n5", "n5", USER_N5, None, 1, hours=24, number=6)],
        [span("t-n5", "n5", USER_N5, None, 2, hours=24, number=6, deleted=1)],
        # N6: the value zero, the missing-key default the graph witness
        # declines; the walk discovers it on the compiler's raw witness.
        [span("t-n6", "n6", USER_N6, None, 1, hours=18, number=0, cost=2.5)],
        # Booleans, filter ``flag = false``. Q1: false at hour 15 (and true at
        # hour 44: key = hour 15... no, the newest FALSE decides the key; the
        # true row still counts as activity). Q2: stale false at hour 55 that
        # v2 flipped to true; live false at hour 3. Q3: true only. Q4: false
        # only on a tombstoned identity.
        [span("t-q1a", "q1a", USER_Q1, None, 1, hours=15, flag=False)],
        [span("t-q1b", "q1b", USER_Q1, None, 1, hours=44, flag=True, cost=3.0)],
        [span("t-q2a", "q2a", USER_Q2, None, 1, hours=55, flag=False)],
        [span("t-q2a", "q2a", USER_Q2, None, 2, hours=55, flag=True)],
        [span("t-q2b", "q2b", USER_Q2, None, 1, hours=3, flag=False)],
        [span("t-q3", "q3", USER_Q3, None, 1, hours=30, flag=True)],
        [span("t-q4", "q4", USER_Q4, None, 1, hours=31, flag=False)],
        [span("t-q4", "q4", USER_Q4, None, 2, hours=31, flag=False, deleted=1)],
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
                (USER_I, "india"),
                (USER_J, "juliet"),
                (USER_K1, "kilo-1"),
                (USER_K2, "kilo-2"),
                (USER_L, "lima"),
                (USER_M, "mike"),
                (USER_N1, "november-1"),
                (USER_N2, "november-2"),
                (USER_N3, "november-3"),
                (USER_N4, "november-4"),
                (USER_N5, "november-5"),
                (USER_N6, "november-6"),
                (USER_Q1, "quebec-1"),
                (USER_Q2, "quebec-2"),
                (USER_Q3, "quebec-3"),
                (USER_Q4, "quebec-4"),
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
    assert physical_versions == 2
    return spans, end_users, remap


def _bind(sql, tables):
    spans, end_users, remap = tables
    sql = sql.replace("FROM end_users AS eu", f"FROM {end_users} AS eu")
    sql = sql.replace("FROM end_user_id_remap", f"FROM {remap}")
    return re.sub(r"\bspans\b", spans, sql)


class _LiveExecutor:
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


TEXT = {
    "column_id": "tag",
    "filter_config": {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "gold",
    },
}
NUMBER = {
    "column_id": "score",
    "filter_config": {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "number",
        "filter_op": "greater_than",
        "filter_value": 5,
    },
}
ZERO = {
    "column_id": "score",
    "filter_config": {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "number",
        "filter_op": "equals",
        "filter_value": 0,
    },
}
BOOLEAN = {
    "column_id": "flag",
    "filter_config": {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "boolean",
        "filter_op": "equals",
        "filter_value": False,
    },
}


def _manager(attribute):
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
            attribute,
        ],
        requested_columns=[],
        attribute_keys=[],
    )


def _never_seed(**kwargs):
    raise AssertionError("the whole-window candidate statement must never run")


def _walk_page(ch_client, tables, attribute, *, page_size, cursor=None):
    manager = _manager(attribute)
    executor = _LiveExecutor(ch_client, tables)
    with (
        patch(SERVICE, return_value=executor),
        patch.object(manager, "_read_dimension_candidates", side_effect=_never_seed),
    ):
        read = manager.list_cursor_payload(page_size=page_size, cursor=cursor)
    return read, executor


def _seeded_page(ch_client, tables, attribute, *, page_size, cursor=None):
    manager = _manager(attribute)
    executor = _LiveExecutor(ch_client, tables)
    with (
        patch(SERVICE, return_value=executor),
        patch.object(manager, "matching_activity_walk_applies", return_value=False),
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


def _walk_all(ch_client, tables, attribute, *, page_size, slice_limit=2):
    pages = []
    statements = []
    cursor = None
    with (
        patch.object(walk, "USER_LIST_WALK_SLICE_USER_LIMIT", slice_limit),
        patch.object(walk, "USER_LIST_WALK_MAX_STATEMENTS", 64),
    ):
        while True:
            read, executor = _walk_page(
                ch_client, tables, attribute, page_size=page_size, cursor=cursor
            )
            assert read.payload["query_provenance"] == "matching_activity_walk"
            statements.extend(executor.statements)
            pages.append(list(read.payload["table"]))
            if not read.has_more:
                break
            cursor = _cursor(read)
            assert len(pages) < 40
    return pages, statements


def _seeded_all(ch_client, tables, attribute, *, page_size=25):
    rows = {}
    statements = []
    cursor = None
    for _ in range(10):
        read, executor = _seeded_page(
            ch_client, tables, attribute, page_size=page_size, cursor=cursor
        )
        statements.extend(executor.statements)
        assert read.payload["query_provenance"] != "matching_activity_walk"
        for row in read.payload["table"]:
            assert row["user_id"] not in rows
            rows[row["user_id"]] = row
        if not read.has_more:
            break
        cursor = _cursor(read)
    return rows, statements


def test_walk_order_and_membership_with_review_population(ch_client, seeded_tables):
    pages, statements = _walk_all(ch_client, seeded_tables, TEXT, page_size=2)
    order = [row["user_id"] for page in pages for row in page]
    # D 35, A 30, K2/K1 28 (tie broken by end_user_id DESC: K2 > K1), H 25,
    # E via alias 20, B 10 (hour-40 witness is stale), I at exactly the window
    # start, M at hour 0.5 (its hour-60 witness is stale). C moved, F
    # tombstoned, G uncurated, J at the exclusive window end, L json-typed
    # latest value: never published.
    assert order == [
        "delta",
        "alpha",
        "kilo-2",
        "kilo-1",
        "hotel",
        "echo-old",
        "bravo",
        "mike",
        "india",
    ]
    assert len(order) == len(set(order)), "no user is repeated across pages"
    slices = [s for s in statements if "AS raw_end_user_id" in s]
    assert slices and all("end_user_id_remap" not in s for s in slices)
    assert any("dimension_candidate_ids" in s for s in statements)


def test_walk_page_size_one_keyset_tie_publishes_both_once(ch_client, seeded_tables):
    pages, _statements = _walk_all(ch_client, seeded_tables, TEXT, page_size=1)
    order = [row["user_id"] for page in pages for row in page]
    assert order.count("kilo-1") == 1 and order.count("kilo-2") == 1
    assert order.index("kilo-2") < order.index("kilo-1")
    assert len(order) == len(set(order)) == 9


def test_seeded_page_publishes_the_same_set_and_totals(ch_client, seeded_tables):
    walk_pages, _statements = _walk_all(ch_client, seeded_tables, TEXT, page_size=25)
    walk_rows = {row["user_id"]: row for page in walk_pages for row in page}

    seeded_rows, seeded_statements = _seeded_all(ch_client, seeded_tables, TEXT)
    assert any("scalar_witness_identities" in s for s in seeded_statements), (
        "the seeded path must have issued the whole-window witnessed statement"
    )

    assert set(seeded_rows) == set(walk_rows), sorted(set(seeded_rows) ^ set(walk_rows))
    for user_id, seeded in seeded_rows.items():
        walked = walk_rows[user_id]
        for field in ("total_cost", "num_traces", "total_tokens", "last_active"):
            assert seeded.get(field) == walked.get(field), (
                user_id,
                field,
                seeded.get(field),
                walked.get(field),
            )
    # Whole-window totals, not slice totals.
    assert (
        walk_rows["alpha"]["total_cost"] == 8.0
        and walk_rows["alpha"]["num_traces"] == 3
    )
    assert (
        walk_rows["mike"]["total_cost"] == 5.0 and walk_rows["mike"]["num_traces"] == 2
    )
    assert walk_rows["india"]["total_cost"] == 7.0
    assert walk_rows["echo-old"]["total_cost"] == 4.0


@pytest.mark.parametrize(
    "attribute, expected_order, totals, witness_column, seeded_witnessed",
    [
        (
            NUMBER,
            # N1 key hour 12 (its 9), N2 key hour 8 (live 7; the hour-50 nine
            # is stale). N3 equals the bound, N4 is string-typed, N5 is
            # tombstoned, N6 is zero: never published.
            ["november-1", "november-2"],
            {"november-1": (3.0, 2), "november-2": (2.0, 2)},
            "attrs_number[",
            True,
        ),
        (
            ZERO,
            # Only N6 carries the value zero on a live row; the seeded page
            # has no witness for it (the graph witness declines the missing
            # key default) and reads the whole window.
            ["november-6"],
            {"november-6": (2.5, 1)},
            "attrs_number[",
            False,
        ),
        (
            BOOLEAN,
            # Q1 key hour 15 (the false; the hour-44 true is activity only),
            # Q2 key hour 3 (the hour-55 false is stale). Q3 true only, Q4
            # tombstoned: never published.
            ["quebec-1", "quebec-2"],
            {"quebec-1": (4.0, 2), "quebec-2": (2.0, 2)},
            "attrs_bool[",
            False,
        ),
    ],
    ids=["number-greater_than", "number-equals-zero", "boolean-equals-false"],
)
def test_typed_walk_matches_the_seeded_page_set_and_totals(
    ch_client,
    seeded_tables,
    attribute,
    expected_order,
    totals,
    witness_column,
    seeded_witnessed,
):
    walk_pages, statements = _walk_all(ch_client, seeded_tables, attribute, page_size=1)
    order = [row["user_id"] for page in walk_pages for row in page]
    assert order == expected_order
    slices = [s for s in statements if "AS raw_end_user_id" in s]
    assert slices and all(witness_column in s for s in slices)
    enrichments = [s for s in statements if "latest_candidate_attribute_values" in s]
    assert enrichments and all(
        "matching_activity_typed_key" in s and witness_column in s for s in enrichments
    )

    walk_rows = {row["user_id"]: row for page in walk_pages for row in page}
    seeded_rows, seeded_statements = _seeded_all(ch_client, seeded_tables, attribute)
    # The seeded page keeps the witnesses it always had: the number comparison
    # seeds on the numeric scalar witness, the boolean and the number zero
    # have none and read the whole window; Python membership decides all.
    assert (
        any("scalar_witness_identities" in s for s in seeded_statements)
        is seeded_witnessed
    )
    assert set(seeded_rows) == set(walk_rows), sorted(set(seeded_rows) ^ set(walk_rows))
    for user_id, (cost, traces) in totals.items():
        for rows in (walk_rows, seeded_rows):
            assert rows[user_id]["total_cost"] == cost, user_id
            assert rows[user_id]["num_traces"] == traces, user_id
        for field in ("total_cost", "num_traces", "total_tokens", "last_active"):
            assert seeded_rows[user_id].get(field) == walk_rows[user_id].get(field)
