"""
Tests for Dataset Analytics Dashboard feature.

Covers:
- DatasetQueryBuilder (all metric types, time ranges, filters, breakdowns)
- System metrics (row_count, tokens, response_time, cell_error_rate)
- Eval metrics (SCORE, PASS_FAIL, CHOICE)
- Annotation metrics (numeric, thumbs_up_down, categorical)
- Custom column metrics (float, boolean)
- Breakdowns (dataset, eval_template, column_name, cell_status)
- Filters (dataset name, column_source, cell_status)
- Result formatting
- Dataset-specific aggregations (pass_rate, fail_rate, true_rate)
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tracer.services.clickhouse.query_builders.dataset_dashboard import (
    DATASET_AGGREGATIONS,
    DATASET_BREAKDOWN_COLUMNS,
    DATASET_FILTER_COLUMNS,
    DATASET_METRIC_UNITS,
    DATASET_SYSTEM_METRICS,
    DatasetQueryBuilder,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_query_config():
    return {
        "workflow": "dataset",
        "workspace_id": str(uuid.uuid4()),
        "granularity": "day",
        "time_range": {"preset": "7D"},
        "metrics": [],
        "filters": [],
        "breakdowns": [],
    }


@pytest.fixture
def system_metric_config(base_query_config):
    base_query_config["metrics"] = [
        {
            "id": "row_count",
            "name": "row_count",
            "type": "system_metric",
            "aggregation": "count",
        }
    ]
    return base_query_config


@pytest.fixture
def eval_score_config(base_query_config):
    eval_id = str(uuid.uuid4())
    base_query_config["metrics"] = [
        {
            "id": f"faithfulness_avg",
            "name": "faithfulness",
            "type": "eval_metric",
            "aggregation": "avg",
            "config_id": eval_id,
            "output_type": "SCORE",
        }
    ]
    return base_query_config


@pytest.fixture
def eval_passfail_config(base_query_config):
    eval_id = str(uuid.uuid4())
    base_query_config["metrics"] = [
        {
            "id": "toxicity_pass_rate",
            "name": "toxicity",
            "type": "eval_metric",
            "aggregation": "pass_rate",
            "config_id": eval_id,
            "output_type": "PASS_FAIL",
        }
    ]
    return base_query_config


@pytest.fixture
def annotation_numeric_config(base_query_config):
    label_id = str(uuid.uuid4())
    base_query_config["metrics"] = [
        {
            "id": "quality_avg",
            "name": "quality",
            "type": "annotation_metric",
            "aggregation": "avg",
            "label_id": label_id,
            "output_type": "numeric",
        }
    ]
    return base_query_config


@pytest.fixture
def custom_column_config(base_query_config):
    col_id = str(uuid.uuid4())
    base_query_config["metrics"] = [
        {
            "id": "confidence_avg",
            "name": "confidence",
            "type": "custom_column",
            "aggregation": "avg",
            "column_id": col_id,
            "data_type": "float",
        }
    ]
    return base_query_config


# ============================================================================
# 1. System Metrics Constants
# ============================================================================


@pytest.mark.unit
class TestDatasetSystemMetricsConstants:
    """Test DATASET_SYSTEM_METRICS map."""

    def test_all_system_metrics_defined(self):
        expected = {
            "row_count",
            "dataset_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "response_time",
            "cell_error_rate",
        }
        assert set(DATASET_SYSTEM_METRICS.keys()) == expected

    def test_all_metrics_have_table_and_column(self):
        for name, (table, col_expr) in DATASET_SYSTEM_METRICS.items():
            assert table == "model_hub_cell"
            assert col_expr, f"{name} has empty column expression"

    def test_metric_units_defined_for_all(self):
        for name in DATASET_SYSTEM_METRICS:
            assert name in DATASET_METRIC_UNITS


# ============================================================================
# 2. Aggregation Constants
# ============================================================================


@pytest.mark.unit
class TestDatasetAggregations:
    """Test DATASET_AGGREGATIONS map."""

    def test_base_aggregations_exist(self):
        base = ["avg", "median", "max", "min", "count", "count_distinct", "sum"]
        for agg in base:
            assert agg in DATASET_AGGREGATIONS

    def test_percentiles_exist(self):
        percentiles = ["p25", "p50", "p75", "p90", "p95", "p99"]
        for p in percentiles:
            assert p in DATASET_AGGREGATIONS

    def test_dataset_specific_aggregations(self):
        dataset_aggs = [
            "pass_rate",
            "fail_rate",
            "pass_count",
            "fail_count",
            "true_rate",
        ]
        for agg in dataset_aggs:
            assert agg in DATASET_AGGREGATIONS

    def test_aggregation_templates_have_placeholder(self):
        for name, template in DATASET_AGGREGATIONS.items():
            if name != "count":
                assert "{col}" in template, f"{name} template missing {{col}}"


# ============================================================================
# 3. Breakdown / Filter Dimensions
# ============================================================================


@pytest.mark.unit
class TestDatasetDimensions:
    """Test breakdown and filter dimension maps."""

    def test_breakdown_dimensions(self):
        # `dataset` intentionally absent — each chart already filters to
        # one dataset, so breaking down by dataset would be meaningless.
        expected = {"eval_template", "column_name", "cell_status"}
        assert set(DATASET_BREAKDOWN_COLUMNS.keys()) == expected

    def test_dataset_not_in_breakdown_columns(self):
        assert "dataset" not in DATASET_BREAKDOWN_COLUMNS

    def test_dataset_still_in_filter_columns(self):
        assert "dataset" in DATASET_FILTER_COLUMNS

    def test_filter_dimensions(self):
        expected = {"dataset", "column_name", "column_source", "cell_status"}
        assert set(DATASET_FILTER_COLUMNS.keys()) == expected

    def test_breakdown_columns_use_supported_expressions(self):
        for name, expr in DATASET_BREAKDOWN_COLUMNS.items():
            assert "c." in expr or "dictGet" in expr or "toString" in expr


# ============================================================================
# 4. System Metric Queries
# ============================================================================


@pytest.mark.unit
class TestDatasetSystemMetricQueries:
    """Test DatasetQueryBuilder._build_system_metric_query."""

    def test_row_count_defaults_to_count_aggregation(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "count()" in sql
        assert "model_hub_cell" in sql
        assert "time_bucket" in sql

    def test_row_count_forced_to_count_for_non_count_agg(self, base_query_config):
        base_query_config["metrics"] = [
            {"name": "row_count", "type": "system_metric", "aggregation": "avg"}
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "count()" in sql

    def test_prompt_tokens_avg(self, base_query_config):
        base_query_config["metrics"] = [
            {"name": "prompt_tokens", "type": "system_metric", "aggregation": "avg"}
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "avg(prompt_tokens)" in sql

    def test_response_time_p90(self, base_query_config):
        base_query_config["metrics"] = [
            {"name": "response_time", "type": "system_metric", "aggregation": "p90"}
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "quantile(0.9)(response_time)" in sql

    def test_cell_error_rate_avg(self, base_query_config):
        base_query_config["metrics"] = [
            {"name": "cell_error_rate", "type": "system_metric", "aggregation": "avg"}
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "CASE WHEN status = 'error'" in sql

    def test_total_tokens_sum(self, base_query_config):
        base_query_config["metrics"] = [
            {"name": "total_tokens", "type": "system_metric", "aggregation": "sum"}
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)" in sql

    def test_dataset_count_emits_count_distinct(self, base_query_config):
        base_query_config["metrics"] = [
            {
                "name": "dataset_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "uniq(c.dataset_id)" in sql

    def test_dataset_count_forces_count_distinct_over_caller_agg(
        self, base_query_config
    ):
        base_query_config["metrics"] = [
            {
                "name": "dataset_count",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "uniq(c.dataset_id)" in sql
        assert "avg(c.dataset_id)" not in sql

    def test_dataset_count_respects_filters(self, base_query_config):
        base_query_config["metrics"] = [
            {
                "name": "dataset_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        ]
        base_query_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "cell_status",
                "operator": "equal_to",
                "value": "error",
            }
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "uniq(c.dataset_id)" in sql
        assert "c.status" in sql

    def test_breakdown_by_dataset_is_silently_dropped(self, system_metric_config):
        # Saved widgets that still reference a `dataset` breakdown should
        # query without breakdown rather than error. _breakdown_select() returns
        # None for unknown breakdown names, so the SQL should omit the
        # breakdown_value clause entirely.
        system_metric_config["breakdowns"] = [{"name": "dataset"}]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "breakdown_value" not in sql

    def test_breakdown_by_eval_template_still_works(self, system_metric_config):
        system_metric_config["breakdowns"] = [{"name": "eval_template"}]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "breakdown_value" in sql

    def test_workspace_scoping(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "workspace_id" in sql
        assert "workspace_id" in params

    def test_dataset_id_scoping(self, system_metric_config):
        ds_id = str(uuid.uuid4())
        system_metric_config["dataset_ids"] = [ds_id]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "dataset_id IN" in sql or "dataset_ids" in sql
        assert params["dataset_ids"] == [ds_id]

    def test_peerdb_deleted_filter(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "_peerdb_is_deleted = 0" in sql

    def test_time_range_filter(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "start_date" in params
        assert "end_date" in params
        assert "created_at >= %(start_date)s" in sql
        assert "created_at < %(end_date)s" in sql

    def test_granularity_day(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "toStartOfDay" in sql

    def test_granularity_hour(self, system_metric_config):
        system_metric_config["granularity"] = "hour"
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "toStartOfHour" in sql

    def test_granularity_month(self, system_metric_config):
        system_metric_config["granularity"] = "month"
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "toStartOfMonth" in sql


# ============================================================================
# 5. Eval Metric Queries
# ============================================================================


@pytest.mark.unit
class TestDatasetEvalMetricQueries:
    """Test DatasetQueryBuilder._build_eval_metric_query."""

    def test_score_avg(self, eval_score_config):
        builder = DatasetQueryBuilder(eval_score_config)
        sql, params = builder.build_metric_query(eval_score_config["metrics"][0])
        assert "toFloat64OrNull(c.value)" in sql
        assert "avg(" in sql
        assert "column_dict" in sql
        assert "source" in sql
        assert "eval_config_id" in params

    def test_score_p90(self, eval_score_config):
        eval_score_config["metrics"][0]["aggregation"] = "p90"
        builder = DatasetQueryBuilder(eval_score_config)
        sql, _ = builder.build_metric_query(eval_score_config["metrics"][0])
        assert "quantile(0.9)" in sql

    def test_passfail_defaults_to_pass_rate(self, eval_passfail_config):
        builder = DatasetQueryBuilder(eval_passfail_config)
        sql, _ = builder.build_metric_query(eval_passfail_config["metrics"][0])
        assert "countIf(lower" in sql
        assert "'true', 'pass', 'passed', '1'" in sql

    def test_passfail_with_fail_rate(self, eval_passfail_config):
        eval_passfail_config["metrics"][0]["aggregation"] = "fail_rate"
        builder = DatasetQueryBuilder(eval_passfail_config)
        sql, _ = builder.build_metric_query(eval_passfail_config["metrics"][0])
        assert "'false', 'fail', 'failed', '0'" in sql

    def test_passfail_with_count(self, eval_passfail_config):
        eval_passfail_config["metrics"][0]["aggregation"] = "count"
        builder = DatasetQueryBuilder(eval_passfail_config)
        sql, _ = builder.build_metric_query(eval_passfail_config["metrics"][0])
        assert "count()" in sql

    def test_passfail_invalid_agg_falls_back_to_pass_rate(self, eval_passfail_config):
        eval_passfail_config["metrics"][0]["aggregation"] = "avg"
        builder = DatasetQueryBuilder(eval_passfail_config)
        sql, _ = builder.build_metric_query(eval_passfail_config["metrics"][0])
        # avg is invalid for pass/fail, should fall back to pass_rate
        assert "countIf" in sql

    def test_choice_defaults_to_count(self, base_query_config):
        eval_id = str(uuid.uuid4())
        base_query_config["metrics"] = [
            {
                "name": "sentiment",
                "type": "eval_metric",
                "aggregation": "avg",
                "config_id": eval_id,
                "output_type": "CHOICE",
            }
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "count()" in sql

    def test_eval_filters_by_source_and_source_id(self, eval_score_config):
        builder = DatasetQueryBuilder(eval_score_config)
        sql, params = builder.build_metric_query(eval_score_config["metrics"][0])
        assert "source" in sql
        assert "'evaluation'" in sql
        assert "source_id" in sql
        assert "eval_config_id" in params


# ============================================================================
# 6. Annotation Metric Queries
# ============================================================================


@pytest.mark.unit
class TestDatasetAnnotationMetricQueries:
    """Test DatasetQueryBuilder._build_annotation_metric_query."""

    def test_numeric_avg(self, annotation_numeric_config):
        builder = DatasetQueryBuilder(annotation_numeric_config)
        sql, params = builder.build_metric_query(
            annotation_numeric_config["metrics"][0]
        )
        assert "toFloat64OrNull(c.value)" in sql
        assert "avg(" in sql
        assert "'annotation_label'" in sql
        assert "annotation_label_id" in params

    def test_thumbs_defaults_to_true_rate(self, base_query_config):
        label_id = str(uuid.uuid4())
        base_query_config["metrics"] = [
            {
                "name": "correctness",
                "type": "annotation_metric",
                "aggregation": "avg",
                "label_id": label_id,
                "output_type": "thumbs_up_down",
            }
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "countIf" in sql
        assert "'true', '1'" in sql

    def test_categorical_defaults_to_count(self, base_query_config):
        label_id = str(uuid.uuid4())
        base_query_config["metrics"] = [
            {
                "name": "category",
                "type": "annotation_metric",
                "aggregation": "avg",
                "label_id": label_id,
                "output_type": "categorical",
            }
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "count()" in sql


# ============================================================================
# 7. Custom Column Metric Queries
# ============================================================================


@pytest.mark.unit
class TestDatasetCustomColumnQueries:
    """Test DatasetQueryBuilder._build_custom_column_query."""

    def test_float_column_avg(self, custom_column_config):
        builder = DatasetQueryBuilder(custom_column_config)
        sql, params = builder.build_metric_query(custom_column_config["metrics"][0])
        assert "toFloat64OrNull(c.value)" in sql
        assert "avg(" in sql
        assert "custom_column_id" in params

    def test_boolean_column_defaults_to_true_rate(self, base_query_config):
        col_id = str(uuid.uuid4())
        base_query_config["metrics"] = [
            {
                "name": "is_correct",
                "type": "custom_column",
                "aggregation": "avg",
                "column_id": col_id,
                "data_type": "boolean",
            }
        ]
        builder = DatasetQueryBuilder(base_query_config)
        sql, _ = builder.build_metric_query(base_query_config["metrics"][0])
        assert "countIf" in sql

    def test_column_id_filter(self, custom_column_config):
        builder = DatasetQueryBuilder(custom_column_config)
        sql, params = builder.build_metric_query(custom_column_config["metrics"][0])
        assert "column_id = toUUID" in sql
        assert "custom_column_id" in params


# ============================================================================
# 8. Breakdowns
# ============================================================================


@pytest.mark.unit
class TestDatasetBreakdowns:
    """Test breakdown handling in DatasetQueryBuilder."""

    def test_dataset_breakdown_silently_dropped(self, system_metric_config):
        # `dataset` was removed from DATASET_BREAKDOWN_COLUMNS — each chart
        # already filters to one dataset, so this breakdown is meaningless.
        # Saved widgets that still reference it should run without breakdown.
        system_metric_config["breakdowns"] = [
            {"name": "dataset", "type": "system_metric"}
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "breakdown_value" not in sql
        assert "toString(c.dataset_id)" not in sql

    def test_eval_template_breakdown(self, system_metric_config):
        system_metric_config["breakdowns"] = [
            {"name": "eval_template", "type": "system_metric"}
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "breakdown_value" in sql
        assert "column_dict" in sql

    def test_cell_status_breakdown(self, system_metric_config):
        system_metric_config["breakdowns"] = [
            {"name": "cell_status", "type": "system_metric"}
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "breakdown_value" in sql
        assert "c.status" in sql

    def test_no_breakdown(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "breakdown_value" not in sql

    def test_unknown_breakdown_returns_none(self, system_metric_config):
        system_metric_config["breakdowns"] = [
            {"name": "nonexistent", "type": "system_metric"}
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "breakdown_value" not in sql


# ============================================================================
# 9. Filters
# ============================================================================


@pytest.mark.unit
class TestDatasetFilters:
    """Test filter handling in DatasetQueryBuilder."""

    def test_dataset_name_filter_contains(self, system_metric_config):
        system_metric_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "dataset",
                "operator": "contains",
                "value": ["my-dataset"],
            }
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "FROM model_hub_dataset FINAL" in sql
        assert "AND name IN %(df_0_val)s" in sql
        assert "IN" in sql
        assert "df_0_val" in params

    def test_workspace_filter_uses_dataset_table(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "SELECT id FROM model_hub_dataset FINAL" in sql
        assert "workspace_id = toUUID(%(workspace_id)s)" in sql
        assert "workspace_id" in params

    def test_cell_status_filter(self, system_metric_config):
        system_metric_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "cell_status",
                "operator": "contains",
                "value": ["error"],
            }
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "c.status" in sql
        assert "df_0_val" in params

    def test_is_set_filter(self, system_metric_config):
        system_metric_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "column_name",
                "operator": "is_set",
                "value": None,
            }
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "!= ''" in sql

    def test_between_filter(self, system_metric_config):
        system_metric_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "column_name",
                "operator": "between",
                "value": ["a", "z"],
            }
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "BETWEEN" in sql
        assert "df_0_lo" in params
        assert "df_0_hi" in params

    def test_empty_value_filter_skipped(self, system_metric_config):
        system_metric_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "dataset",
                "operator": "contains",
                "value": [],
            }
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "df_0_val" not in sql

    def test_non_system_filter_skipped(self, system_metric_config):
        system_metric_config["filters"] = [
            {
                "metric_type": "custom_attribute",
                "metric_name": "some_attr",
                "operator": "contains",
                "value": ["val"],
            }
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "some_attr" not in sql

    def test_unknown_metric_name_filter_skipped(self, system_metric_config):
        system_metric_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "nonexistent_filter",
                "operator": "contains",
                "value": ["val"],
            }
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "nonexistent_filter" not in sql


# ============================================================================
# 10. Time Range Parsing
# ============================================================================


@pytest.mark.unit
class TestDatasetTimeRange:
    """Test time range parsing."""

    def test_preset_7d(self, base_query_config):
        base_query_config["time_range"] = {"preset": "7D"}
        builder = DatasetQueryBuilder(base_query_config)
        start, end = builder.parse_time_range()
        assert (end - start).days == 7 or (end - start).days == 6

    def test_preset_30d(self, base_query_config):
        base_query_config["time_range"] = {"preset": "30D"}
        builder = DatasetQueryBuilder(base_query_config)
        start, end = builder.parse_time_range()
        assert (end - start).days >= 29

    def test_preset_today(self, base_query_config):
        base_query_config["time_range"] = {"preset": "today"}
        builder = DatasetQueryBuilder(base_query_config)
        start, end = builder.parse_time_range()
        assert start.hour == 0
        assert start.minute == 0

    def test_custom_range(self, base_query_config):
        base_query_config["time_range"] = {
            "preset": "custom",
            "custom_start": "2024-01-01T00:00:00Z",
            "custom_end": "2024-01-31T23:59:59Z",
        }
        builder = DatasetQueryBuilder(base_query_config)
        start, end = builder.parse_time_range()
        assert start.year == 2024
        assert start.month == 1
        assert end.month == 1
        assert end.day == 31


# ============================================================================
# 11. Build All Queries
# ============================================================================


@pytest.mark.unit
class TestDatasetBuildAllQueries:
    """Test build_all_queries method."""

    def test_single_metric(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        results = builder.build_all_queries()
        assert len(results) == 1
        sql, params, metric_info = results[0]
        assert isinstance(sql, str)
        assert isinstance(params, dict)
        assert metric_info["name"] == "row_count"

    def test_multiple_metrics(self, base_query_config):
        base_query_config["metrics"] = [
            {"name": "row_count", "type": "system_metric", "aggregation": "count"},
            {"name": "prompt_tokens", "type": "system_metric", "aggregation": "sum"},
        ]
        builder = DatasetQueryBuilder(base_query_config)
        results = builder.build_all_queries()
        assert len(results) == 2

    def test_mixed_metric_types(self, base_query_config):
        eval_id = str(uuid.uuid4())
        base_query_config["metrics"] = [
            {"name": "row_count", "type": "system_metric", "aggregation": "count"},
            {
                "name": "faith",
                "type": "eval_metric",
                "aggregation": "avg",
                "config_id": eval_id,
                "output_type": "SCORE",
            },
        ]
        builder = DatasetQueryBuilder(base_query_config)
        results = builder.build_all_queries()
        assert len(results) == 2
        # First should be system metric
        assert "count()" in results[0][0]
        # Second should be eval metric
        assert "toFloat64OrNull" in results[1][0]


# ============================================================================
# 12. Result Formatting
# ============================================================================


@pytest.mark.unit
class TestDatasetResultFormatting:
    """Test format_results method."""

    def test_empty_results(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        metric_info = {"id": "m1", "name": "row_count", "aggregation": "count"}
        result = builder.format_results([(metric_info, [])])
        assert "metrics" in result
        assert "time_range" in result
        assert "granularity" in result
        assert len(result["metrics"]) == 1
        assert result["metrics"][0]["series"][0]["name"] == "total"

    def test_single_series(self, system_metric_config):
        builder = DatasetQueryBuilder(system_metric_config)
        metric_info = {"id": "m1", "name": "row_count", "aggregation": "count"}
        rows = [
            {"time_bucket": datetime(2024, 1, 1), "value": 100},
            {"time_bucket": datetime(2024, 1, 2), "value": 200},
        ]
        result = builder.format_results([(metric_info, rows)])
        assert len(result["metrics"]) == 1
        series = result["metrics"][0]["series"]
        assert len(series) == 1
        assert series[0]["name"] == "total"

    def test_breakdown_series(self, system_metric_config):
        system_metric_config["breakdowns"] = [
            {"name": "dataset", "type": "system_metric"}
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        metric_info = {"id": "m1", "name": "row_count", "aggregation": "count"}
        rows = [
            {
                "time_bucket": datetime(2024, 1, 1),
                "value": 100,
                "breakdown_value": "ds-A",
            },
            {
                "time_bucket": datetime(2024, 1, 1),
                "value": 200,
                "breakdown_value": "ds-B",
            },
        ]
        result = builder.format_results([(metric_info, rows)])
        series = result["metrics"][0]["series"]
        assert len(series) == 2
        names = {s["name"] for s in series}
        assert "ds-A" in names
        assert "ds-B" in names

    def test_series_limit_100(self, system_metric_config):
        system_metric_config["breakdowns"] = [
            {"name": "dataset", "type": "system_metric"}
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        metric_info = {"id": "m1", "name": "row_count", "aggregation": "count"}
        # Create 150 breakdown values
        rows = [
            {
                "time_bucket": datetime(2024, 1, 1),
                "value": i,
                "breakdown_value": f"ds-{i}",
            }
            for i in range(150)
        ]
        result = builder.format_results([(metric_info, rows)])
        series = result["metrics"][0]["series"]
        assert len(series) <= 100

    def test_float_rounding(self, system_metric_config):
        # Use a time range that includes the test timestamp
        system_metric_config["time_range"] = {
            "preset": "custom",
            "custom_start": "2024-01-01T00:00:00Z",
            "custom_end": "2024-01-02T00:00:00Z",
        }
        builder = DatasetQueryBuilder(system_metric_config)
        metric_info = {"id": "m1", "name": "response_time", "aggregation": "avg"}
        rows = [
            {"time_bucket": datetime(2024, 1, 1), "value": 1.23456789012345},
        ]
        result = builder.format_results([(metric_info, rows)])
        series = result["metrics"][0]["series"]
        # Find the data point with the matching timestamp
        found = [d for d in series[0]["data"] if d["value"] is not None]
        assert len(found) == 1
        assert found[0]["value"] == round(1.23456789012345, 6)

    def test_zero_values_not_filtered(self, system_metric_config):
        system_metric_config["breakdowns"] = [
            {"name": "dataset", "type": "system_metric"}
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        metric_info = {"id": "m1", "name": "row_count", "aggregation": "count"}
        # Create 105 series, some with zero values — zeros should count
        rows = []
        for i in range(105):
            rows.append(
                {
                    "time_bucket": datetime(2024, 1, 1),
                    "value": 0 if i < 50 else i,
                    "breakdown_value": f"ds-{i}",
                }
            )
        result = builder.format_results([(metric_info, rows)])
        series = result["metrics"][0]["series"]
        assert len(series) == 100


# ============================================================================
# 13. Unknown Metric Type
# ============================================================================


@pytest.mark.unit
class TestDatasetUnknownMetricType:
    """Test error handling for unknown metric types."""

    def test_unknown_type_raises(self, base_query_config):
        base_query_config["metrics"] = [
            {"name": "foo", "type": "unknown_type", "aggregation": "avg"}
        ]
        builder = DatasetQueryBuilder(base_query_config)
        with pytest.raises(ValueError, match="Unknown metric type"):
            builder.build_metric_query(base_query_config["metrics"][0])


# ============================================================================
# 14. No Workspace ID
# ============================================================================


@pytest.mark.unit
class TestDatasetNoWorkspace:
    """Test behavior without workspace_id."""

    def test_no_workspace_id_omits_workspace_filter(self, system_metric_config):
        system_metric_config["workspace_id"] = ""
        builder = DatasetQueryBuilder(system_metric_config)
        sql, _ = builder.build_metric_query(system_metric_config["metrics"][0])
        assert "workspace_id" not in sql


# ============================================================================
# 15. Security Tests
# ============================================================================


@pytest.mark.unit
class TestDatasetQueryBuilderSecurity:
    """Security tests for DatasetQueryBuilder to prevent injection and misuse."""

    def test_unknown_metric_raises_value_error(self, base_query_config):
        """Verify that passing an unknown metric_name raises ValueError."""
        base_query_config["metrics"] = [
            {
                "name": "nonexistent_metric",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ]
        builder = DatasetQueryBuilder(base_query_config)
        with pytest.raises(ValueError, match="Unknown dataset system metric"):
            builder.build_metric_query(base_query_config["metrics"][0])

    def test_sql_injection_in_metric_name_blocked(self, base_query_config):
        """Verify that a SQL injection attempt in metric_name raises ValueError."""
        base_query_config["metrics"] = [
            {
                "name": "1; DROP TABLE model_hub_cell--",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ]
        builder = DatasetQueryBuilder(base_query_config)
        with pytest.raises(ValueError):
            builder.build_metric_query(base_query_config["metrics"][0])

    def test_filter_prefix_uses_df(self, system_metric_config):
        """Verify dataset filter params use df_ prefix not f_."""
        system_metric_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "dataset",
                "operator": "contains",
                "value": ["my-dataset"],
            }
        ]
        builder = DatasetQueryBuilder(system_metric_config)
        sql, params = builder.build_metric_query(system_metric_config["metrics"][0])
        # Should use df_ prefix for dataset filters
        df_keys = [k for k in params if k.startswith("df_")]
        assert len(df_keys) > 0, "Expected df_ prefixed params for dataset filters"
        f_keys = [k for k in params if k.startswith("f_") and not k.startswith("df_")]
        assert len(f_keys) == 0, "Should not have bare f_ prefix params in dataset"
