"""Collector-order root names must identify children in every filtered reader."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.tests._users_live_ch import lane_client

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 21, tzinfo=UTC)
END = START + timedelta(days=1)
PROJECT = str(uuid.uuid4())
USER = str(uuid.uuid4())
TRACE = str(uuid.uuid4())
ROOT = uuid.uuid4().hex
CHILD = uuid.uuid4().hex
NAME = "SQL Agent Trace"


def _leaf():
    return {
        "column_id": "trace_name",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": NAME,
        },
    }


def _date():
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


@pytest.fixture(scope="module")
def seeded():
    with lane_client() as ch:
        columns = (
            "project_id, observation_type, service_name, start_time, trace_id, "
            "id, parent_span_id, name, end_user_id"
        )
        # The collector writes spans before the traces row and never reloads
        # trace_dict; the child has no stored trace_name under either schema.
        ch.execute(
            f"INSERT INTO spans ({columns}) VALUES",
            [
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    START + timedelta(hours=1),
                    TRACE,
                    ROOT,
                    "",
                    NAME,
                    None,
                ),
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    START + timedelta(hours=2),
                    TRACE,
                    CHILD,
                    ROOT,
                    "SQL Agent Executor",
                    uuid.UUID(USER),
                ),
            ],
        )
        ch.execute(
            "INSERT INTO traces (id, project_id, name, created_at) VALUES",
            [(uuid.UUID(TRACE), uuid.UUID(PROJECT), "Different trace row", START)],
        )
        assert (
            dict(
                ch.execute(
                    "SELECT id, trace_name FROM spans WHERE project_id = %(project)s",
                    {"project": uuid.UUID(PROJECT)},
                )
            )[CHILD]
            == ""
        )
        ch.execute(
            "INSERT INTO end_users (project_id, end_user_id, organization_id, "
            "user_id, user_id_type, first_seen) VALUES",
            [
                (
                    uuid.UUID(PROJECT),
                    uuid.UUID(USER),
                    uuid.UUID(int=272),
                    "child-user",
                    "string",
                    START,
                )
            ],
        )
        yield ch


def test_span_filter_returns_root_and_child(seeded):
    builder = ClickHouseFilterBuilderV2(project_id=PROJECT, query_mode="span")
    predicate, values = builder.translate([_leaf()])
    sql = (
        "SELECT id FROM spans WHERE project_id = toUUID(%(project_id)s) "
        "AND is_deleted = 0 AND start_time >= %(start_date)s "
        "AND start_time < %(end_date)s AND " + predicate
    )
    rows = seeded.execute(
        sql,
        {
            "project_id": PROJECT,
            "start_date": START,
            "end_date": END,
            **values,
        },
    )
    assert {row[0] for row in rows} == {ROOT, CHILD}


def test_bounded_span_list_resolves_child_name(seeded):
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=[_date(), _leaf()], bounded_identity_only=True
    )
    query, params = builder.build_filter_match_query([ROOT, CHILD])
    rows = seeded.execute(query, params)
    assert {row[1] for row in rows} == {ROOT, CHILD}


def test_span_graph_matches_root_and_child(seeded):
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=[_leaf()],
        interval="day",
        observe_type="span",
        start_date=START,
        end_date=END,
        exact_snapshot=True,
        resolve_span_versions=True,
    )
    query, params = builder.build()
    rows = seeded.execute(query, params)
    assert rows and rows[0][4] == 2


def test_trace_graph_matches_named_root_without_requiring_child_name(seeded):
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=[_leaf()],
        interval="day",
        observe_type="trace",
        start_date=START,
        end_date=END,
        exact_snapshot=True,
        resolve_span_versions=True,
    )
    query, params = builder.build()
    rows = seeded.execute(query, params)
    assert rows and rows[0][4] == 2


def test_users_list_membership_via_matching_root_child(seeded):
    builder = UserListQueryBuilderV2(
        organization_id=str(uuid.UUID(int=272)),
        project_ids=[PROJECT],
        filters=[_date(), _leaf()],
    )
    query, params = builder.build_native_span_dimension_query([USER], [(1, _leaf())])
    assert [(str(row[0]), row[1]) for row in seeded.execute(query, params)] == [
        (USER, 1)
    ]


def test_users_membership_via_matching_root_child(seeded):
    query, params, needs_eval = exact_graph_reads._user_id_membership_sql(
        project_id=PROJECT,
        filters=[_date(), _leaf()],
        start_date=START,
        end_date=END,
        all_snapshot_users=True,
    )
    assert not needs_eval
    assert {str(row[0]) for row in seeded.execute(query, params)} == {USER}
