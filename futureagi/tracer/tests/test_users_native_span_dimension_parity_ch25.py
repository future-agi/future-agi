"""Live ClickHouse 25: the Users list decides native leaves as the users graph.

One seeded population carries what a native span-dimension leaf must survive:
the empty string a non-nullable column stores for "no value", a user whose
spans disagree, a latest version that changed the value, a tombstone, a remap
alias, a case variant, activity outside the window and a curated user with no
span in it. For every native column and every text operator, the list's page
statement (``build_native_span_dimension_query`` read through
``UsersListManager``) must select exactly the users the users graph's own
membership statement (``_user_id_membership_sql``) selects, and for ``model``
exactly the any-span answer written out below.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.query_builders.user_list import (
    USER_NATIVE_SPAN_DIMENSIONS,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager

pytestmark = pytest.mark.integration

PROJECT = str(uuid.UUID(int=171))
ORGANIZATION = str(uuid.UUID(int=172))
USERS = {name: str(uuid.UUID(int=180 + n)) for n, name in enumerate("ABCDEFGH")}
ALIAS_OF_F = str(uuid.UUID(int=199))
WINDOW_START = datetime(2026, 8, 1)
WINDOW_END = WINDOW_START + timedelta(days=2)
SERVICE = "tracer.services.users_list_manager.V2AnalyticsQueryService"

# The latest live in-window values of every text column, per user:
#   A ('gpt-4o',)             B ('',)            C ('', 'gpt-4o')
#   D ('claude',) - an older version said 'gpt-4o'
#   E ('Claude-3',) - a tombstoned span said 'gpt-4o'
#   F ('GPT-4O',) - only through a remap alias
#   G ('',) - its 'gpt-4o' span is outside the window
#   H () - curated, no span in the window
MODEL_EXPECTED = [
    ("equals", "gpt-4o", "ACF"),
    ("not_equals", "gpt-4o", "BCDEG"),
    ("in", ["GPT-4o", "claude"], "ACDF"),
    ("not_in", ["gpt-4o", "claude"], "BCEG"),
    ("contains", "GPT", "ACF"),
    ("not_contains", "gpt", "BCDEG"),
    ("starts_with", "claude", "DE"),
    ("ends_with", "4O", "ACF"),
    ("is_null", None, "BCG"),
    ("is_not_null", None, "ACDEF"),
]
OPERATIONS = [(operation, value) for operation, value, _expected in MODEL_EXPECTED]
OPERATION_IDS = [f"{operation}-{value}" for operation, value in OPERATIONS]


@pytest.fixture(scope="module")
def ch_database():
    with _ch_test_owned_database("test_users_native_parity_") as database:
        yield database


@pytest.fixture(scope="module")
def ch_client(ch_database):
    with _ch_test_native_client(database=ch_database) as client:
        yield client


@pytest.fixture(scope="module")
def seeded(ch_client):
    # Production table names inside this module's own throwaway database, so
    # both statements run exactly as they are built.
    ch_client.execute(
        """
        CREATE TABLE spans (
            project_id UUID,
            observation_type LowCardinality(String),
            service_name String,
            start_time DateTime64(6, 'UTC'),
            trace_id String,
            id String,
            name String,
            end_user_id Nullable(UUID),
            trace_session_id Nullable(UUID),
            end_time Nullable(DateTime64(6, 'UTC')),
            latency_ms Int32,
            cost Float64,
            total_tokens Int32,
            prompt_tokens Int32,
            completion_tokens Int32,
            status LowCardinality(String),
            model LowCardinality(String),
            provider LowCardinality(String),
            trace_name String,
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, observation_type, service_name,
                  toStartOfHour(start_time), trace_id, id)
        """
    )
    ch_client.execute(
        """
        CREATE TABLE end_users (
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
    for table in ("end_user_id_remap", "trace_session_id_remap"):
        ch_client.execute(
            f"""
            CREATE TABLE {table} (
                old_id UUID,
                new_id UUID,
                version UInt64
            ) ENGINE = ReplacingMergeTree(version)
            ORDER BY (old_id, new_id)
            """
        )
    # Keep every version a physical row: the replay must pick the winner.
    ch_client.execute("SYSTEM STOP MERGES spans")

    def span(user, sid, value, *, hours, version=1, deleted=0, kind=None):
        start = WINDOW_START.replace(tzinfo=UTC) + timedelta(hours=hours)
        return (
            uuid.UUID(PROJECT),
            # observation_type is part of the replay identity, so a span with
            # several versions keeps one; every other span carries the value.
            value if kind is None else kind,
            "svc",
            start,
            f"t-{sid}",
            sid,
            value,
            uuid.UUID(user),
            None,
            start + timedelta(seconds=1),
            10,
            1.0,
            3,
            2,
            1,
            value,
            value,
            value,
            value,
            deleted,
            version,
        )

    rows = [
        span(USERS["A"], "a1", "gpt-4o", hours=1),
        span(USERS["B"], "b1", "", hours=2),
        span(USERS["C"], "c1", "", hours=3),
        span(USERS["C"], "c2", "gpt-4o", hours=4),
        span(USERS["D"], "d1", "gpt-4o", hours=5, kind="llm"),
        span(USERS["D"], "d1", "claude", hours=5, version=2, kind="llm"),
        span(USERS["E"], "e1", "gpt-4o", hours=6, kind="llm"),
        span(USERS["E"], "e1", "gpt-4o", hours=6, version=2, deleted=1, kind="llm"),
        span(USERS["E"], "e2", "Claude-3", hours=7),
        span(ALIAS_OF_F, "f1", "GPT-4O", hours=8),
        span(USERS["G"], "g1", "gpt-4o", hours=-30),
        span(USERS["G"], "g2", "", hours=9),
        span(USERS["H"], "h1", "gpt-4o", hours=60),
    ]
    columns = (
        "project_id, observation_type, service_name, start_time, trace_id, id, "
        "name, end_user_id, trace_session_id, end_time, latency_ms, cost, "
        "total_tokens, prompt_tokens, completion_tokens, status, model, provider, "
        "trace_name, is_deleted, _version"
    )
    for row in rows:
        ch_client.execute(f"INSERT INTO spans ({columns}) VALUES", [row])
    ch_client.execute(
        "INSERT INTO end_users VALUES",
        [
            (
                uuid.UUID(PROJECT),
                uuid.UUID(user),
                uuid.UUID(ORGANIZATION),
                f"user-{label}",
                "string",
                f"user-{label}",
                WINDOW_START.replace(tzinfo=UTC),
                1,
                0,
            )
            for label, user in [*USERS.items(), ("alias-f", ALIAS_OF_F)]
        ],
    )
    ch_client.execute(
        "INSERT INTO end_user_id_remap VALUES",
        # A consolidation group survives as its least old id: F.
        [(uuid.UUID(USERS["F"]), uuid.UUID(ALIAS_OF_F), 1)],
    )
    assert ch_client.execute("SELECT count() FROM spans WHERE id = 'd1'")[0][0] == 2


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


def _date_filter():
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        },
    }


def _leaf(column_id, operation, value):
    config = {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "text",
        "filter_op": operation,
    }
    if value is not None:
        config["filter_value"] = value
    return {
        "column_id": column_id,
        "property_id": f"system_attribute:traces:{column_id}",
        "filter_config": config,
    }


def _list_members(ch_client, item):
    filters = [_date_filter(), item]
    manager = UsersListManager(
        organization_id=ORGANIZATION,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        requested_columns=[],
        filters=filters,
    )
    builder = UserListQueryBuilderV2(
        organization_id=ORGANIZATION, project_ids=[PROJECT], filters=filters
    )
    rows = [{"end_user_id": user} for user in USERS.values()]
    executor = _LiveExecutor(ch_client)
    with patch(SERVICE, return_value=executor):
        manager._read_native_span_dimensions(rows, builder, None)
    assert len(executor.statements) == 1
    return {
        label
        for label, user in USERS.items()
        if manager._row_matches_filters({"end_user_id": user})
    }


def _graph_members(ch_client, *items):
    query, params, needs_eval = exact_graph_reads._user_id_membership_sql(
        project_id=PROJECT,
        filters=[_date_filter(), *items],
        start_date=WINDOW_START,
        end_date=WINDOW_END,
        all_snapshot_users=True,
    )
    assert needs_eval is False
    labels = {user: label for label, user in USERS.items()}
    return {labels[str(row[0])] for row in ch_client.execute(query, params)}


@pytest.mark.parametrize(
    ("operation", "value", "expected_labels"), MODEL_EXPECTED, ids=OPERATION_IDS
)
def test_model_leaf_is_the_any_span_answer(
    ch_client, seeded, operation, value, expected_labels
):
    expected = set(expected_labels)
    item = _leaf("model", operation, value)
    assert _graph_members(ch_client, item) == expected
    assert _list_members(ch_client, item) == expected


@pytest.mark.parametrize("column_id", sorted(USER_NATIVE_SPAN_DIMENSIONS))
@pytest.mark.parametrize(("operation", "value"), OPERATIONS, ids=OPERATION_IDS)
def test_every_native_leaf_matches_the_users_graph(
    ch_client, seeded, column_id, operation, value
):
    item = _leaf(column_id, operation, value)
    assert _list_members(ch_client, item) == _graph_members(ch_client, item)


def test_two_leaves_on_one_column_are_decided_independently(ch_client, seeded):
    # "model contains gpt AND model is_null": only C has both a gpt span and
    # an empty one. Each leaf reads its own any-span decision from the same
    # statement, as each is its own countIf in the graph.
    filters = [
        _date_filter(),
        _leaf("model", "contains", "gpt"),
        _leaf("model", "is_null", None),
    ]
    manager = UsersListManager(
        organization_id=ORGANIZATION,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        requested_columns=[],
        filters=filters,
    )
    builder = UserListQueryBuilderV2(
        organization_id=ORGANIZATION, project_ids=[PROJECT], filters=filters
    )
    rows = [{"end_user_id": user} for user in USERS.values()]
    with patch(SERVICE, return_value=_LiveExecutor(ch_client)):
        manager._read_native_span_dimensions(rows, builder, None)
    members = {
        label
        for label, user in USERS.items()
        if manager._row_matches_filters({"end_user_id": user})
    }
    assert members == {"C"}
    assert _graph_members(ch_client, *filters[1:]) == {"C"}
