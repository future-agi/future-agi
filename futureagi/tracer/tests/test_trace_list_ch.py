"""
Tests for TraceListQueryBuilder enhancements — Phase 1C/1D.

Unit tests on the query builder (SQL generation + params),
not e2e API tests — the CH infrastructure is already tested elsewhere.
"""

import uuid

import pytest

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder


@pytest.fixture
def project_id():
    return str(uuid.uuid4())


class TestSearch:
    def test_search_adds_ilike_filter(self, project_id):
        builder = TraceListQueryBuilder(
            project_id=project_id,
            search="hello world",
        )
        query, params = builder.build()
        assert "ILIKE %(search)s" in query
        assert params["search"] == "%hello world%"

    def test_search_none_omits_filter(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build()
        assert "ILIKE" not in query
        assert "search" not in params

    def test_search_empty_string_omits_filter(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id, search="")
        query, params = builder.build()
        assert "ILIKE" not in query

    def test_search_included_in_count_query(self, project_id):
        builder = TraceListQueryBuilder(
            project_id=project_id,
            search="test",
        )
        # Must call build() first to set start_date/end_date
        builder.build()
        count_query, count_params = builder.build_count_query()
        assert "ILIKE %(search)s" in count_query


class TestConfigurableColumns:
    def test_columns_param_limits_select(self, project_id):
        builder = TraceListQueryBuilder(
            project_id=project_id,
            columns=["status", "latency_ms", "cost"],
        )
        query, _ = builder.build()
        # Should have trace_id (always) + requested columns
        assert "trace_id" in query
        assert "status" in query
        assert "latency_ms" in query
        assert "cost" in query
        # Should NOT have unrequested columns
        assert "provider" not in query
        assert "input" not in query
        assert "output" not in query

    def test_columns_none_returns_all_light_columns(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id, columns=None)
        query, _ = builder.build()
        # Should have all default LIGHT columns (no heavy: input, output, span_attr)
        assert "trace_name" in query
        assert "model" in query
        assert "provider" in query
        assert "cost" in query
        assert "latency_ms" in query

    def test_trace_id_always_included(self, project_id):
        builder = TraceListQueryBuilder(
            project_id=project_id,
            columns=["cost"],
        )
        query, _ = builder.build()
        assert "trace_id" in query

    def test_unknown_columns_ignored(self, project_id):
        builder = TraceListQueryBuilder(
            project_id=project_id,
            columns=["status", "nonexistent_column"],
        )
        query, _ = builder.build()
        assert "status" in query
        assert "nonexistent_column" not in query


class TestSpanCount:
    def test_span_count_query_returns_valid_sql(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        trace_ids = [str(uuid.uuid4()) for _ in range(3)]
        query, params = builder.build_span_count_query(trace_ids)

        assert "count() AS span_count" in query
        assert "countIf(status = 'ERROR') AS error_count" in query
        assert "GROUP BY trace_id" in query
        assert params["sc_trace_ids"] == tuple(trace_ids)

    def test_span_count_empty_trace_ids_returns_empty(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_span_count_query([])
        assert query == ""
        assert params == {}

    def test_pivot_span_count_results(self):
        rows = [
            {"trace_id": "t1", "span_count": 5, "error_count": 1},
            {"trace_id": "t2", "span_count": 3, "error_count": 0},
        ]
        result = TraceListQueryBuilder.pivot_span_count_results(rows)
        assert result["t1"] == {"span_count": 5, "error_count": 1}
        assert result["t2"] == {"span_count": 3, "error_count": 0}

    def test_pivot_span_count_empty(self):
        result = TraceListQueryBuilder.pivot_span_count_results([])
        assert result == {}


class TestSearchAndColumns:
    def test_search_with_columns_both_applied(self, project_id):
        builder = TraceListQueryBuilder(
            project_id=project_id,
            search="error",
            columns=["status", "latency_ms"],
        )
        query, params = builder.build()
        # Search applied
        assert "ILIKE %(search)s" in query
        assert params["search"] == "%error%"
        # Columns limited
        assert "status" in query
        assert "latency_ms" in query
        assert "provider" not in query


class TestExistingBehaviorPreserved:
    """Ensure existing functionality isn't broken."""

    def test_default_sort_is_start_time_desc(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build()
        assert "ORDER BY start_time DESC" in query

    def test_pagination_params(self, project_id):
        builder = TraceListQueryBuilder(
            project_id=project_id, page_number=2, page_size=25
        )
        query, params = builder.build()
        assert params["limit"] == 25  # page_size (no +1 for has_more)
        assert params["offset"] == 50  # page_number * page_size

    def test_root_span_filter(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build()
        assert "parent_span_id IS NULL OR parent_span_id = ''" in query

    def test_project_scoping(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build()
        assert "project_id = %(project_id)s" in query
        assert params["project_id"] == project_id

    def test_eval_query_empty_when_no_config_ids(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_eval_query(["t1", "t2"])
        assert query == ""

    def test_eval_query_empty_when_no_trace_ids(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id, eval_config_ids=["ec1"])
        query, params = builder.build_eval_query([])
        assert query == ""

    def test_eval_query_built_when_both_provided(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id, eval_config_ids=["ec1"])
        query, params = builder.build_eval_query(["t1"])
        assert "tracer_eval_logger" in query
        assert params["trace_ids"] == ("t1",)
        assert params["eval_config_ids"] == ("ec1",)


class TestPerfQueryShapes:
    """Pin the OOM fixes: bounded top-N pagination, page-scoped eval dedup,
    and time-bounded trace-membership filter subqueries.

    Each shape below was measured against a 10M-span / 2M-trace / 10M-eval
    stress dataset; the pinned form is the one that stays within tens-to-
    hundreds of MiB instead of OOM-crashing the ClickHouse server.
    """

    def test_build_has_no_limit_by_trace_id(self, project_id):
        """Phase-1 pagination must be a bounded top-N: `LIMIT 1 BY trace_id`
        forced a full sort of every root span in the window (O(window) memory).
        The page is deduped by trace_id in Python instead."""
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build()
        assert "LIMIT 1 BY trace_id" not in query
        assert "LIMIT %(limit)s" in query

    def test_eval_query_dedups_page_slice_without_table_final(self, project_id):
        """Phase-2 must not run table-level FINAL (it merged the WHOLE eval
        table for a ~50-trace page). Dedup happens on the page-scoped slice via
        `ORDER BY _peerdb_version DESC LIMIT 1 BY id` — verified identical
        output to FINAL on live data and on multi-version fixtures."""
        builder = TraceListQueryBuilder(project_id=project_id, eval_config_ids=["ec1"])
        builder.build()  # binds start_date so the created_at prune is emitted
        query, params = builder.build_eval_query(["t1"])
        assert "tracer_eval_logger FINAL" not in query
        assert "ORDER BY _peerdb_version DESC" in query
        assert "LIMIT 1 BY id" in query
        # The partition-pruning lower bound must survive inside the subquery.
        assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in query
        assert "start_date" in params

    def test_membership_filter_subqueries_are_time_bounded(self, project_id):
        """Trace-membership wraps (`trace_id IN (SELECT … FROM spans …)`) for
        system-metric/attr filters must carry the window's created_at lower
        bound — without it every filtered request scanned the project's entire
        span history."""
        builder = TraceListQueryBuilder(
            project_id=project_id,
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4o",
                    },
                }
            ],
        )
        query, _ = builder.build()
        sub = query.split("trace_id IN (")[1]
        assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in sub

    def test_span_date_scope_defaults_off_for_other_builders(self, project_id):
        """The membership time bound is opt-in: a compiler constructed without
        `span_date_scope` (every non-trace-list builder) emits byte-identical
        SQL to before, so callers that never bind %(start_date)s don't break."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        fb = ClickHouseFilterBuilder(table="spans", project_id=project_id)
        where, _ = fb.translate(
            [
                {
                    "column_id": "model",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4o",
                    },
                }
            ]
        )
        assert "created_at >= %(start_date)s" not in where
