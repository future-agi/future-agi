"""Exact-boundary guards for the v2 trace-list count fast path."""

from datetime import UTC, datetime

import pytest

from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-4111-8111-111111111111"


def _filters(start: str, end: str):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start, end],
            },
        }
    ]


def test_partial_hours_use_rollup_interior_and_exact_final_boundaries(settings):
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2020, 1, 1, tzinfo=UTC)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=_filters(
            "2026-05-10T02:17:00Z",
            "2026-05-17T06:41:00Z",
        ),
    )

    sql, params = builder.build_count_query()

    assert "FROM trace_count_rollup" in sql
    assert sql.count("FROM spans FINAL") == 2
    assert params["interior_start"] == datetime(2026, 5, 10, 3)
    assert params["interior_end"] == datetime(2026, 5, 17, 6)
    assert params["first_boundary_end"] == params["interior_start"]
    assert params["last_boundary_start"] == params["interior_end"]
    assert "use_skip_indexes_if_final = 1" in sql


def test_aligned_hours_need_no_raw_boundary_scan(settings):
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2020, 1, 1, tzinfo=UTC)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=_filters(
            "2026-05-10T03:00:00Z",
            "2026-05-17T06:00:00Z",
        ),
    )

    sql, params = builder.build_count_query()

    assert "FROM trace_count_rollup" in sql
    assert "FROM spans FINAL" not in sql
    assert params["interior_start"] == datetime(2026, 5, 10, 3)
    assert params["interior_end"] == datetime(2026, 5, 17, 6)


def test_single_partial_hour_is_read_once_without_rollup(settings):
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2020, 1, 1, tzinfo=UTC)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=_filters(
            "2026-05-10T02:17:00Z",
            "2026-05-10T02:59:59Z",
        ),
    )

    sql, _params = builder.build_count_query()

    assert "FROM trace_count_rollup" not in sql
    assert sql.count("FROM spans FINAL") == 1


def test_window_before_declared_coverage_falls_back_to_raw_count(settings):
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2026, 5, 11, tzinfo=UTC)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=_filters(
            "2026-05-10T02:17:00Z",
            "2026-05-17T06:41:00Z",
        ),
    )

    sql, _params = builder.build_count_query()

    assert "FROM trace_count_rollup" not in sql
    # Before declared coverage the builder must preserve the established raw
    # count path rather than silently reading an incomplete rollup.
    assert "uniq(trace_id)" in sql


def test_multi_project_scope_is_preserved_in_every_state_source(settings):
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2020, 1, 1, tzinfo=UTC)
    builder = TraceListQueryBuilderV2(
        project_ids=[PROJECT_ID, "22222222-2222-4222-8222-222222222222"],
        filters=_filters(
            "2026-05-10T02:17:00Z",
            "2026-05-17T06:41:00Z",
        ),
    )

    sql, params = builder.build_count_query()

    assert sql.count("project_id IN %(project_ids)s") == 3
    assert len(params["project_ids"]) == 2
