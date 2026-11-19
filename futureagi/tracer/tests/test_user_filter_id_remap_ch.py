"""Live ClickHouse check: the user filter follows the end-user ID remap.

The legacy ``tracer_enduser`` dimension stays keyed by a user's old ID while
spans written after the remap cutover carry the new ID, and a consolidated
duplicate's spans carry a sibling old ID. Filtering by that user must find all
of them, as the list and analytics readers do, and a negated filter must
exclude all of them.

Runs only against the test ClickHouse sidecar (see ``conftest``), inside a
uniquely named database that is dropped afterwards.
"""

from __future__ import annotations

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder

pytestmark = pytest.mark.integration

ALICE_OLD = "10000000-0000-4000-8000-000000000001"
ALICE_DUPLICATE_OLD = "10000000-0000-4000-8000-000000000002"
ALICE_NEW = "20000000-0000-5000-8000-000000000001"
BOB = "30000000-0000-4000-8000-000000000003"
CAROL = "40000000-0000-4000-8000-000000000004"

SPANS = [
    ("trace-alice-before", ALICE_OLD),
    ("trace-alice-after", ALICE_NEW),
    ("trace-alice-duplicate", ALICE_DUPLICATE_OLD),
    ("trace-bob", BOB),
    ("trace-carol", CAROL),
]
ALICE_TRACES = {"trace-alice-before", "trace-alice-after", "trace-alice-duplicate"}


@pytest.fixture(scope="module")
def ch_client():
    with (
        _ch_test_owned_database("_test_user_filter_remap_") as database,
        _ch_test_native_client(database=database) as client,
    ):
        client.execute(
            """
            CREATE TABLE tracer_enduser (
                id UUID,
                user_id String,
                user_id_type Nullable(String),
                _peerdb_is_deleted UInt8,
                deleted UInt8,
                _peerdb_version Int64
            ) ENGINE = ReplacingMergeTree(_peerdb_version) ORDER BY id
            """
        )
        client.execute(
            """
            CREATE TABLE end_user_id_remap (
                old_id UUID,
                new_id UUID,
                version DateTime64(6, 'UTC') DEFAULT now64(6, 'UTC')
            ) ENGINE = ReplacingMergeTree(version) ORDER BY old_id
            """
        )
        client.execute(
            """
            CREATE TABLE spans (
                trace_id String,
                end_user_id Nullable(UUID),
                _peerdb_is_deleted UInt8,
                _peerdb_version Int64
            ) ENGINE = ReplacingMergeTree(_peerdb_version) ORDER BY trace_id
            """
        )
        client.execute(
            "INSERT INTO tracer_enduser "
            "(id, user_id, user_id_type, _peerdb_is_deleted, deleted, _peerdb_version) "
            "VALUES",
            [
                (ALICE_OLD, "alice", None, 0, 0, 1),
                (BOB, "bob", None, 0, 0, 1),
                (CAROL, "carol", None, 0, 0, 1),
            ],
            types_check=True,
        )
        # Alice's duplicate shares her new ID; Bob has a self-mapping row;
        # Carol was created after the cutover and has none.
        client.execute(
            "INSERT INTO end_user_id_remap (old_id, new_id) VALUES",
            [
                (ALICE_OLD, ALICE_NEW),
                (ALICE_DUPLICATE_OLD, ALICE_NEW),
                (BOB, BOB),
            ],
            types_check=True,
        )
        client.execute(
            "INSERT INTO spans (trace_id, end_user_id, _peerdb_is_deleted, "
            "_peerdb_version) VALUES",
            [(trace_id, end_user_id, 0, 1) for trace_id, end_user_id in SPANS],
            types_check=True,
        )
        yield client


def _matching_traces(ch_client, filter_op: str, filter_value) -> set[str]:
    where, params = ClickHouseFilterBuilder(table="spans").translate(
        [
            {
                "column_id": "user",
                "filter_config": {
                    "col_type": ClickHouseFilterBuilder.SYSTEM_METRIC,
                    "filter_type": "text",
                    "filter_op": filter_op,
                    "filter_value": filter_value,
                },
            }
        ]
    )
    rows = ch_client.execute(
        f"SELECT DISTINCT trace_id FROM spans WHERE {where}", params
    )
    return {row[0] for row in rows}


def test_user_filter_finds_traces_under_every_remapped_id(ch_client):
    assert _matching_traces(ch_client, "equals", "alice") == ALICE_TRACES


def test_negated_user_filter_excludes_every_remapped_id(ch_client):
    assert _matching_traces(ch_client, "not_equals", "alice") == {
        "trace-bob",
        "trace-carol",
    }


def test_user_filter_keeps_users_with_or_without_a_remap_row(ch_client):
    assert _matching_traces(ch_client, "equals", "bob") == {"trace-bob"}
    assert _matching_traces(ch_client, "equals", "carol") == {"trace-carol"}
    assert _matching_traces(ch_client, "in", ["bob", "carol"]) == {
        "trace-bob",
        "trace-carol",
    }
