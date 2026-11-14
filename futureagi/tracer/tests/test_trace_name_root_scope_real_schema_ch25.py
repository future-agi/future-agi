"""Scoped trace_name root lookups give the same answers on ClickHouse 25.3.

Collector order: spans first, the child has no stored trace name, and its root
is in an earlier storage hour than the child, so a scope that missed the root
would lose the child.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from tracer.services.clickhouse import span_attribute_lookups
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.tests._users_live_ch import lane_client

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 26, tzinfo=UTC)
END = START + timedelta(days=1)
PROJECT = str(uuid.uuid4())
USER = str(uuid.uuid4())
TRACE = str(uuid.uuid4())
OTHER_TRACE = str(uuid.uuid4())
NAME = "checkout"


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
def ch():
    with lane_client() as client:
        columns = (
            "project_id, observation_type, service_name, start_time, trace_id, "
            "id, parent_span_id, name, end_user_id"
        )
        root = uuid.uuid4().hex
        other_root = uuid.uuid4().hex
        client.execute(
            f"INSERT INTO spans ({columns}) VALUES",
            [
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    START + timedelta(hours=1),
                    TRACE,
                    root,
                    "",
                    NAME,
                    None,
                ),
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    START + timedelta(hours=5),
                    TRACE,
                    uuid.uuid4().hex,
                    root,
                    "step",
                    uuid.UUID(USER),
                ),
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    START + timedelta(hours=1),
                    OTHER_TRACE,
                    other_root,
                    "",
                    "browse",
                    None,
                ),
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    START + timedelta(hours=5),
                    OTHER_TRACE,
                    uuid.uuid4().hex,
                    other_root,
                    "step",
                    None,
                ),
            ],
        )
        client.execute(
            "INSERT INTO end_users (project_id, end_user_id, organization_id, "
            "user_id, user_id_type, first_seen) VALUES",
            [
                (
                    uuid.UUID(PROJECT),
                    uuid.UUID(USER),
                    uuid.UUID(int=273),
                    "u",
                    "string",
                    START,
                )
            ],
        )
        yield client


def test_cluster_rca_traces_are_narrowed_by_root_name(ch):
    class Client:
        def execute_read(self, query, params, **_):
            return ch.execute(query, params), [], 1

    with (
        mock.patch.object(
            span_attribute_lookups, "is_clickhouse_enabled", return_value=True
        ),
        mock.patch.object(span_attribute_lookups, "ClickHouseClient", Client),
    ):
        result = span_attribute_lookups.scoped_trace_ids(
            PROJECT, [TRACE, OTHER_TRACE], [_leaf()]
        )
    assert result == [TRACE]


def test_graph_partition_of_the_child_hour_finds_its_earlier_root(ch):
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
    counts = {}
    for hour in (1, 5):
        query, params = builder.build_exact_span_partition(
            partition_start=START + timedelta(hours=hour),
            partition_end=START + timedelta(hours=hour + 1),
        )
        rows = ch.execute(query, params)
        counts[hour] = sum(row[7] for row in rows)
    # The root's hour holds the root; the child's hour holds the child, whose
    # root lies in another partition. The browse trace never matches.
    assert counts == {1: 1, 5: 1}


def test_users_walk_slice_of_the_child_hour_finds_its_earlier_root(ch):
    builder = UserListQueryBuilderV2(
        organization_id=str(uuid.UUID(int=273)),
        project_ids=[PROJECT],
        filters=[_date(), _leaf()],
    )
    builder.walk_witness = next(
        w for w in builder.matching_activity_witnesses() if w.family == "native"
    )
    query, params = builder.build_matching_activity_slice_query(
        slice_start=START + timedelta(hours=5),
        slice_end=START + timedelta(hours=6),
        limit=10,
    )
    assert USER in {str(value) for row in ch.execute(query, params) for value in row}


@pytest.mark.parametrize(
    ("root_offset", "matches"),
    [
        # Exactly the one-day lookback: in.
        (timedelta(days=-1), True),
        # Five minutes earlier, same storage hour as an in-window root: out.
        (timedelta(days=-1, minutes=-5), False),
        # Five minutes after the exclusive end, same storage hour: out.
        (timedelta(minutes=35), False),
    ],
)
def test_root_window_is_exact_after_the_whole_hour_prewhere(ch, root_offset, matches):
    """PREWHERE cuts whole hours (before FINAL); the live root's exact time
    must still fall in [start - 1 day, end)."""
    from tracer.services.clickhouse.v2.query_builders.filters import (
        ClickHouseFilterBuilderV2,
    )

    project = str(uuid.uuid4())
    trace = str(uuid.uuid4())
    window_start = START + timedelta(hours=12, minutes=30)
    window_end = window_start + timedelta(minutes=30)
    root = uuid.uuid4().hex
    child = uuid.uuid4().hex
    columns = (
        "project_id, observation_type, service_name, start_time, trace_id, "
        "id, parent_span_id, name"
    )
    ch.execute(
        f"INSERT INTO spans ({columns}) VALUES",
        [
            (
                uuid.UUID(project),
                "span",
                "svc",
                window_start + root_offset,
                trace,
                root,
                "",
                NAME,
            ),
            (
                uuid.UUID(project),
                "span",
                "svc",
                window_start + timedelta(minutes=1),
                trace,
                child,
                root,
                "step",
            ),
        ],
    )
    builder = ClickHouseFilterBuilderV2(project_id=project, query_mode="span")
    predicate, values = builder.translate([_leaf()])
    rows = ch.execute(
        "SELECT id FROM spans WHERE project_id = toUUID(%(project_id)s) "
        "AND start_time >= %(start_date)s AND start_time < %(end_date)s AND "
        + predicate,
        {
            "project_id": project,
            "start_date": window_start,
            "end_date": window_end,
            **values,
        },
    )
    assert ({row[0] for row in rows} == {child}) is matches
