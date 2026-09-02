"""SQL contracts for exact direct-write Project user graphs."""

from __future__ import annotations

import re

import pytest

from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserDetailTimeSeriesQueryBuilderV2,
    UserTimeSeriesQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
ORGANIZATION_ID = "22222222-2222-2222-2222-222222222222"
END_USER_ID = "33333333-3333-3333-3333-333333333333"
DATE_FILTER = {
    "column_id": "created_at",
    "filter_config": {
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": [
            "2026-07-01T00:00:00Z",
            "2026-07-02T00:00:00Z",
        ],
    },
}


def _assert_latest_replay_precedes_live_filter(sql: str) -> None:
    replay = sql.index("LIMIT 1 BY project_id, trace_id, id, start_time")
    live_filter = sql.index("WHERE is_deleted = 0", replay)
    assert replay < live_filter
    assert "FROM spans FINAL" not in sql


def test_user_aggregate_graph_replays_latest_physical_span_versions():
    builder = UserTimeSeriesQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            DATE_FILTER,
            {
                "column_id": "cost",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 1,
                },
            },
        ],
        interval="hour",
    )

    sql, params = builder.build()

    _assert_latest_replay_precedes_live_filter(sql)
    assert "ORDER BY project_id, trace_id, id, start_time, _version DESC" in sql
    # Both the aggregate and trace-membership filter read the replayed CTE.
    assert sql.count("FROM latest_spans") >= 2
    assert "FROM latest_spans WHERE" in " ".join(sql.split())
    assert "candidate_end_user_ids AS" in sql
    assert "eu_survivor_map AS" in sql
    assert "OVER (PARTITION BY new_id)" not in sql
    assert params["project_id"] == PROJECT_ID
    assert params["start_date"] < params["end_date"]


def test_project_user_detail_graph_prunes_and_buckets_on_start_time():
    builder = UserDetailTimeSeriesQueryBuilderV2(
        project_id=PROJECT_ID,
        organization_id=ORGANIZATION_ID,
        end_user_id=END_USER_ID,
        filters=[DATE_FILTER],
        interval="day",
    )

    sql, params = builder.build()

    _assert_latest_replay_precedes_live_filter(sql)
    assert "candidate_span_identities AS" in sql
    assert sql.count("FROM spans") == 2
    assert "FROM latest_spans AS rs" in sql
    assert "toDate(start_time) BETWEEN" in sql
    assert "AND start_time >= %(start_date)s" in sql
    assert "AND start_time < %(end_date)s" in sql
    assert "created_at >= %(start_date)s" not in sql
    assert "rs.created_at" not in sql
    assert "toStartOfDay(start_time) AS time_bucket" in sql
    assert "FROM end_users FINAL" in sql
    assert "expanded_target_end_user_ids AS" in sql
    assert "candidate_trace_session_ids AS" in sql
    assert "ts_survivor_map AS" in sql
    assert "OVER (PARTITION BY new_id)" not in sql
    assert "FROM spans AS rs" not in sql
    assert re.search(r"\bFINAL\b", sql)  # bounded remap tables only
    assert params["project_id"] == PROJECT_ID
    assert params["org_id"] == ORGANIZATION_ID
    assert params["end_user_id"] == END_USER_ID
    assert params["target_end_user_ids"] == (END_USER_ID,)
