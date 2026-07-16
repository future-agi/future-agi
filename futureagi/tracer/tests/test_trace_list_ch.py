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


class TestEvalAveragingAcrossSpans:
    """Eval scores are aggregated per (trace, config) across ALL of the trace's
    spans — not the root span. The averaging happens in ``build_eval_query``
    (SQL ``avgIf``) and, for CHOICES, in ``pivot_eval_results``."""

    def test_eval_query_averages_across_spans(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id, eval_config_ids=["ec1"])
        query, _ = builder.build_eval_query(["t1"])
        # SCORE = avg(output_float); PASS_FAIL = avg(output_bool as 0/100).
        assert "avgIf(\n                output_float" in query
        assert "output_bool = 1 THEN 100.0 ELSE 0.0" in query
        # One aggregated row per (trace, config) → averages every span's eval.
        assert "GROUP BY trace_id, custom_eval_config_id" in query

    _COLS = [
        "trace_id", "eval_config_id", "avg_score", "pass_rate", "success_count",
        "error_count", "eval_count", "str_lists", "skipped_count", "running_count",
        "pending_count", "skipped_reason",
    ]

    def _row(self, **kw):
        base = {c: None for c in self._COLS}
        base.update(
            trace_id="t1", eval_config_id="c1", success_count=0, error_count=0,
            eval_count=0, str_lists=[], skipped_count=0, running_count=0,
            pending_count=0,
        )
        base.update(kw)
        return base

    def _pivot(self, rows):
        return TraceListQueryBuilder.pivot_eval_results(rows, self._COLS)

    def test_choices_percentage_averaged_over_spans(self):
        # 3 spans: neutral, neutral, joy → neutral 66.67%, joy 33.33%.
        row = self._row(
            success_count=3, eval_count=3,
            str_lists=['["neutral"]', '["neutral"]', '["joy"]'],
        )
        pc = self._pivot([row])["t1"]["c1"]["per_choice"]
        assert pc["neutral"] == 66.67
        assert pc["joy"] == 33.33

    def test_score_avg_scaled_to_percentage(self):
        # avg(output_float)=0.6 (0-1 in CH) → 60.0 after ×100.
        row = self._row(avg_score=0.6, success_count=2, eval_count=2)
        assert self._pivot([row])["t1"]["c1"]["avg_score"] == 60.0

    def test_pass_rate_passthrough(self):
        # 1 pass + 1 fail across two spans → 50% pass rate.
        row = self._row(pass_rate=50.0, success_count=2, eval_count=2)
        assert self._pivot([row])["t1"]["c1"]["pass_rate"] == 50.0

    def test_all_errored_yields_error_marker(self):
        row = self._row(success_count=0, error_count=2, eval_count=2)
        assert self._pivot([row])["t1"]["c1"] == {"error": True}
