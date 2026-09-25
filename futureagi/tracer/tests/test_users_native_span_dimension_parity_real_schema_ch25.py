"""Live ClickHouse 25 on the DEPLOYED schema: native Users leaves, list = graph.

The hand-built parity module (``test_users_native_span_dimension_parity_ch25``)
declares ``trace_name`` as a plain column. On the deployed schema it is
MATERIALIZED from ``trace_dict`` at INSERT, and ``SELECT *`` omits it, so the
users graph's latest-row snapshot lost it and every ``trace_name`` leaf failed
with code 47 there. This module runs on the lane database provisioned with the
repo's own DDL (``provision-lane-ch-db.sh``, named by ``CH25_DATABASE``) and
seeds ``trace_name`` the way production does: traces first, the dictionary
reloaded, then spans, whose stored ``trace_name`` is asserted before any
statement runs.

For every native column and operator, with and without ``col_type``:

* the list's native decision equals the graph's membership statement
  (``_user_id_membership_sql``);
* the real graph composition (the membership reused inside
  ``UserTimeSeriesQueryBuilderV2``) executes, and its ``active_users`` over a
  one-bucket window equals the list's member count.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from conftest import _ch_test_native_client
from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.query_builders.user_list import (
    USER_NATIVE_SPAN_DIMENSIONS,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserTimeSeriesQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager

pytestmark = pytest.mark.integration

# A fresh project per module run: every row this module writes is scoped to it.
PROJECT = str(uuid.uuid4())
ORGANIZATION = str(uuid.UUID(int=272))
USERS = {name: str(uuid.uuid4()) for name in "ABCDEFGH"}
ALIAS_OF_F = str(uuid.uuid4())
# One day from midnight: the graph's day interval has exactly one bucket.
WINDOW_START = datetime(2026, 8, 1)
WINDOW_END = WINDOW_START + timedelta(days=1)
SERVICE = "tracer.services.users_list_manager.V2AnalyticsQueryService"

# The latest live in-window value of every native column, per user:
#   A ('gpt-4o',)             B ('',) - its trace has a NULL name
#   C ('', 'gpt-4o')
#   D ('claude',) - an older version said 'gpt-4o' (its trace was renamed
#     between the two INSERTs, so the stored trace_name changed too)
#   E ('Claude-3',) - a tombstoned span said 'gpt-4o'
#   F ('GPT-4O',) - only through a remap alias
#   G ('',) - its 'gpt-4o' span is outside the window; the in-window span's
#     trace was unknown to trace_dict at INSERT, so it stored ''
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


def _lane_database() -> str:
    database = (os.environ.get("CH25_DATABASE") or "").strip()
    if not database:
        pytest.skip("no lane database named: set CH25_DATABASE")
    # CI gives every job its own throwaway ClickHouse, whose test_tfc carries
    # the deployed schema and runs one test at a time. Locally test_tfc is shared
    # with other runs, and this module stops merges on spans, so a local run needs
    # its own database provisioned with provision-lane-ch-db.sh.
    if database == "test_tfc" and os.environ.get("GITHUB_ACTIONS") == "true":
        return database
    if database == "test_tfc" or not database.startswith("test_"):
        pytest.skip(
            f"not writing to {database!r}: point CH25_DATABASE at a database "
            "provisioned with provision-lane-ch-db.sh"
        )
    return database


@pytest.fixture(scope="module")
def ch_client():
    database = _lane_database()
    with _ch_test_native_client(database=database) as client:
        kinds = dict(
            client.execute(
                "SELECT name, default_kind FROM system.columns "
                "WHERE database = currentDatabase() AND table = 'spans' "
                "AND name IN ('trace_name', 'trace_id')"
            )
        )
        types = dict(
            client.execute(
                "SELECT name, type FROM system.columns "
                "WHERE database = currentDatabase() AND table = 'spans' "
                "AND name = 'trace_id'"
            )
        )
        if kinds.get("trace_name") != "MATERIALIZED" or types.get("trace_id") != (
            "String"
        ):
            pytest.fail(
                f"{database} does not carry the deployed spans schema "
                f"(trace_name {kinds.get('trace_name')!r}, trace_id "
                f"{types.get('trace_id')!r}); provision it with "
                "provision-lane-ch-db.sh"
            )
        client.database_name = database
        yield client


def _trace_uuid(sid: str) -> str:
    return str(uuid.uuid5(uuid.UUID(PROJECT), f"trace-{sid}"))


@pytest.fixture(scope="module")
def seeded(ch_client):
    database = ch_client.database_name
    project = uuid.UUID(PROJECT)

    def insert_traces(names: dict[str, str | None]) -> None:
        ch_client.execute(
            "INSERT INTO traces (id, project_id, name, created_at) VALUES",
            [
                (uuid.UUID(_trace_uuid(sid)), project, name, WINDOW_START)
                for sid, name in names.items()
            ],
        )

    def reload_dictionary() -> None:
        ch_client.execute(f"SYSTEM RELOAD DICTIONARY {database}.trace_dict")

    def span(user, sid, value, *, hours, version=1, deleted=0, kind=None):
        start = WINDOW_START.replace(tzinfo=UTC) + timedelta(hours=hours)
        return {
            "project_id": project,
            # observation_type is part of the replay identity, so a span with
            # several versions keeps one; every other span carries the value.
            "observation_type": value if kind is None else kind,
            "service_name": "svc",
            "start_time": start,
            "trace_id": _trace_uuid(sid),
            "id": sid,
            "name": value,
            "end_user_id": uuid.UUID(user),
            "end_time": start + timedelta(seconds=1),
            "latency_ms": 10,
            "cost": 1.0,
            "total_tokens": 3,
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "status": value,
            "model": value,
            "provider": value,
            "is_deleted": deleted,
            "_version": version,
        }

    def insert_spans(rows) -> None:
        # One INSERT a row: a block is deduplicated on insert
        # (``optimize_on_insert``), which would collapse two versions of a span.
        for row in rows:
            columns = list(row)
            ch_client.execute(
                f"INSERT INTO spans ({', '.join(columns)}) VALUES",
                [tuple(row[column] for column in columns)],
            )

    # Keep every version a physical row: both replays must pick the winner.
    ch_client.execute("SYSTEM STOP MERGES spans")
    try:
        # 1. Traces first, then the dictionary, so each span stores its name.
        #    g2's trace is never written: trace_dict does not know it.
        insert_traces(
            {
                "a1": "gpt-4o",
                "b1": None,
                "c1": "",
                "c2": "gpt-4o",
                "d1": "gpt-4o",
                "e1": "gpt-4o",
                "e2": "Claude-3",
                "f1": "GPT-4O",
                "g1": "gpt-4o",
                "h1": "gpt-4o",
            }
        )
        reload_dictionary()
        names = dict(
            ch_client.execute(
                "SELECT sid, dictGetOrDefault(%(dictionary)s, 'name', "
                "toUUID(trace), '<unknown>') FROM (SELECT arrayJoin(%(pairs)s) AS "
                "pair, pair.1 AS sid, pair.2 AS trace)",
                {
                    "dictionary": f"{database}.trace_dict",
                    "pairs": [(sid, _trace_uuid(sid)) for sid in ("a1", "d1", "g2")],
                },
            )
        )
        assert names == {"a1": "gpt-4o", "d1": "gpt-4o", "g2": "<unknown>"}, names
        # 2. Spans; d1's first version is written while its trace says gpt-4o.
        insert_spans(
            [
                span(USERS["A"], "a1", "gpt-4o", hours=1),
                span(USERS["B"], "b1", "", hours=2),
                span(USERS["C"], "c1", "", hours=3),
                span(USERS["C"], "c2", "gpt-4o", hours=4),
                span(USERS["D"], "d1", "gpt-4o", hours=5, kind="llm"),
                span(USERS["E"], "e1", "gpt-4o", hours=6, kind="llm"),
                span(
                    USERS["E"],
                    "e1",
                    "gpt-4o",
                    hours=6,
                    version=2,
                    deleted=1,
                    kind="llm",
                ),
                span(USERS["E"], "e2", "Claude-3", hours=7),
                span(ALIAS_OF_F, "f1", "GPT-4O", hours=8),
                span(USERS["G"], "g1", "gpt-4o", hours=-30),
                span(USERS["G"], "g2", "", hours=9),
                span(USERS["H"], "h1", "gpt-4o", hours=60),
            ]
        )
        # 3. The trace is renamed, then d1's correction is written: its stored
        #    trace_name follows the rename, as a production correction does.
        ch_client.execute(
            "ALTER TABLE traces DELETE WHERE id = %(id)s SETTINGS mutations_sync = 2",
            {"id": uuid.UUID(_trace_uuid("d1"))},
        )
        insert_traces({"d1": "claude"})
        reload_dictionary()
        insert_spans([span(USERS["D"], "d1", "claude", hours=5, version=2, kind="llm")])
        stored = ch_client.execute(
            "SELECT id, _version, trace_name FROM spans "
            "WHERE project_id = %(project)s ORDER BY id, _version",
            {"project": project},
        )
        assert stored == [
            ("a1", 1, "gpt-4o"),
            ("b1", 1, ""),
            ("c1", 1, ""),
            ("c2", 1, "gpt-4o"),
            ("d1", 1, "gpt-4o"),
            ("d1", 2, "claude"),
            ("e1", 1, "gpt-4o"),
            ("e1", 2, "gpt-4o"),
            ("e2", 1, "Claude-3"),
            ("f1", 1, "GPT-4O"),
            ("g1", 1, "gpt-4o"),
            ("g2", 1, ""),
            ("h1", 1, "gpt-4o"),
        ], stored
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
                    WINDOW_START.replace(tzinfo=UTC),
                )
                for label, user in [*USERS.items(), ("alias-f", ALIAS_OF_F)]
            ],
        )
        # A consolidation group survives as its least old id.
        survivor, alias = sorted((USERS["F"], ALIAS_OF_F))
        ch_client.execute(
            "INSERT INTO end_user_id_remap (old_id, new_id) VALUES",
            [(uuid.UUID(survivor), uuid.UUID(alias))],
        )
        yield survivor
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
            {"ids": [uuid.UUID(USERS["F"]), uuid.UUID(ALIAS_OF_F)]},
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


def _date_filter():
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        },
    }


def _leaf(column_id, operation, value, *, col_type="SYSTEM_METRIC"):
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


def _labels(survivor: str) -> dict[str, str]:
    labels = {user: label for label, user in USERS.items()}
    # The group's survivor answers for F, whichever of the two ids it is.
    labels[survivor] = "F"
    return labels


def _list_members(ch_client, survivor, item) -> set[str]:
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
    users = [user for label, user in USERS.items() if label != "F"] + [survivor]
    executor = _LiveExecutor(ch_client)
    with patch(SERVICE, return_value=executor):
        manager._read_native_span_dimensions(
            [{"end_user_id": user} for user in users], builder, None
        )
    assert len(executor.statements) == 1
    labels = _labels(survivor)
    return {
        labels[user]
        for user in users
        if manager._row_matches_filters({"end_user_id": user})
    }


def _graph_members(ch_client, survivor, *items) -> set[str]:
    query, params, needs_eval = exact_graph_reads._user_id_membership_sql(
        project_id=PROJECT,
        filters=[_date_filter(), *items],
        start_date=WINDOW_START,
        end_date=WINDOW_END,
        all_snapshot_users=True,
    )
    assert needs_eval is False
    labels = _labels(survivor)
    return {labels[str(row[0])] for row in ch_client.execute(query, params)}


def _graph_active_users(ch_client, *items) -> int:
    """The real graph composition: the membership reused inside the graph."""

    membership, params, _needs_eval = exact_graph_reads._user_id_membership_sql(
        project_id=PROJECT,
        filters=[_date_filter(), *items],
        start_date=WINDOW_START,
        end_date=WINDOW_END,
        all_snapshot_users=True,
        reuse_outer_snapshot=True,
    )
    builder = UserTimeSeriesQueryBuilderV2(
        project_id=PROJECT,
        filters=[_date_filter()],
        interval="day",
        user_membership_sql=membership,
        user_membership_params=params,
        exact_snapshot_start=WINDOW_START,
        exact_snapshot_end=WINDOW_END,
    )
    query, graph_params = builder.build()
    rows, columns = ch_client.execute(query, graph_params, with_column_types=True)
    names = [name for name, _type in columns]
    buckets = [dict(zip(names, row, strict=True)) for row in rows]
    assert len(buckets) <= 1, buckets
    return int(buckets[0]["active_users"]) if buckets else 0


@pytest.mark.parametrize(
    ("operation", "value", "expected_labels"), MODEL_EXPECTED, ids=OPERATION_IDS
)
@pytest.mark.parametrize("column_id", ["model", "trace_name"])
def test_model_and_trace_name_leaves_are_the_any_span_answer(
    ch_client, seeded, column_id, operation, value, expected_labels
):
    expected = set(expected_labels)
    item = _leaf(column_id, operation, value)
    assert _graph_members(ch_client, seeded, item) == expected
    assert _list_members(ch_client, seeded, item) == expected
    assert _graph_active_users(ch_client, item) == len(expected)


@pytest.mark.parametrize("col_type", ["SYSTEM_METRIC", None], ids=["system", "none"])
@pytest.mark.parametrize("column_id", sorted(USER_NATIVE_SPAN_DIMENSIONS))
@pytest.mark.parametrize(("operation", "value"), OPERATIONS, ids=OPERATION_IDS)
def test_every_native_leaf_matches_the_users_graph_on_the_deployed_schema(
    ch_client, seeded, column_id, operation, value, col_type
):
    item = _leaf(column_id, operation, value, col_type=col_type)
    members = _list_members(ch_client, seeded, item)
    assert members == _graph_members(ch_client, seeded, item)
    assert _graph_active_users(ch_client, item) == len(members)


NEWEST_EXPECTED = [
    # The newest latest live in-window span satisfying the witness flag: D's
    # stale gpt-4o version, E's tombstone and G's out-of-window span never
    # count.
    ("equals", "gpt-4o", "SYSTEM_METRIC", {"A": 1, "C": 4, "F": 8}),
    # A negation without a family witnesses on its presence flag.
    ("not_equals", "gpt-4o", None, {"B": 2, "C": 3, "D": 5, "E": 7, "G": 9}),
]


@pytest.mark.parametrize(
    ("operation", "value", "col_type", "expected"),
    NEWEST_EXPECTED,
    ids=["equals", "not_equals-no-family"],
)
@pytest.mark.parametrize("column_id", ["model", "trace_name"])
def test_the_native_statement_returns_each_users_newest_witnessed_span(
    ch_client, seeded, column_id, operation, value, col_type, expected
):
    item = _leaf(column_id, operation, value, col_type=col_type)
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
    assert builder.native_matching_activity_witness().leaf_index == 1
    users = [user for label, user in USERS.items() if label != "F"] + [seeded]
    with patch(SERVICE, return_value=_LiveExecutor(ch_client)):
        manager._read_native_span_dimensions(
            [{"end_user_id": user} for user in users], builder, None, newest=1
        )
    labels = _labels(seeded)
    newest = {
        labels[user]: keys[1]
        for user, keys in manager._native_matching_activity_by_user.items()
        if keys
    }
    start = WINDOW_START.replace(tzinfo=UTC)
    assert newest == {
        label: start + timedelta(hours=hours) for label, hours in expected.items()
    }
