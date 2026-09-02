"""Direct-write session analytics routing and latest-row contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from tracer.services.clickhouse.v2.query_builders.session_analytics import (
    SessionAnalyticsQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
SESSION_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"


def _assert_latest_direct_sql(query: str) -> None:
    assert "FROM spans" in query
    assert "argMax(is_deleted, _version)" in query
    assert "latest_is_deleted = 0" in query
    assert "_peerdb_" not in query
    assert "tracer_observation_span" not in query


def test_session_metrics_replays_latest_rows_and_remaps_session_id():
    query, params = SessionAnalyticsQueryBuilderV2(
        project_id=PROJECT_ID
    ).build_session_metrics_query([SESSION_ID])

    _assert_latest_direct_sql(query)
    assert "trace_session_id_remap" in query
    assert "uniqExact(trace_id) AS trace_count" in query
    assert params["session_ids"] == (SESSION_ID,)


def test_user_stats_replays_latest_rows_and_both_identity_remaps():
    query, params = SessionAnalyticsQueryBuilderV2(
        project_id=PROJECT_ID
    ).build_user_stats_query(USER_ID)

    _assert_latest_direct_sql(query)
    assert "end_user_id_remap" in query
    assert "trace_session_id_remap" in query
    assert params["user_id"] == USER_ID


def test_navigation_is_time_bounded_and_returns_messages_in_same_query():
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2026-07-01T00:00:00Z",
                    "2026-08-01T00:00:00Z",
                ],
            },
        }
    ]
    query, params = SessionAnalyticsQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        end_user_ids=[USER_ID],
    ).build_session_navigation_query()

    _assert_latest_direct_sql(query)
    assert "argMinIf(input, start_time" in query
    assert "argMaxIf(input, start_time" in query
    assert "IN %(end_user_ids)s" in query
    assert params["end_user_ids"] == (USER_ID,)
    assert params["window_start"] == datetime(2026, 7, 1)
    assert params["window_end"] == datetime(2026, 8, 1)


def test_navigation_helper_uses_one_v2_query_and_no_legacy_builder():
    from tracer.utils.session import _try_session_navigation_ch

    current = SESSION_ID
    adjacent = "44444444-4444-4444-4444-444444444444"
    now = datetime.now(UTC)
    rows = [
        {
            "trace_session_id": current,
            "started_at": now,
            "ended_at": now,
            "trace_count": 1,
            "total_tokens": 2,
            "total_cost": 0.1,
            "first_message": "first",
            "last_message": "last",
        },
        {
            "trace_session_id": adjacent,
            "started_at": now,
            "ended_at": now,
            "trace_count": 1,
            "total_tokens": 2,
            "total_cost": 0.1,
            "first_message": "next-first",
            "last_message": "next-last",
        },
    ]
    request = SimpleNamespace(query_params={})
    service = mock.MagicMock()
    service.execute_ch_query.return_value = SimpleNamespace(data=rows)

    with mock.patch(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        return_value=service,
    ):
        result = _try_session_navigation_ch(
            request,
            PROJECT_ID,
            current,
            query_data={"filters": [], "sort_params": []},
        )

    assert result == (adjacent, None)
    service.execute_ch_query.assert_called_once()
