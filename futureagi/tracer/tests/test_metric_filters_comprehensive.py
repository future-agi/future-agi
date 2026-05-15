"""
Comprehensive tests for metric filters: has_eval, has_annotation, status (Errors).

Covers:
- ClickHouse filter builder (unit tests)
- PG FilterEngine (unit tests)
- Filter constant format validation
- Edge cases: None, empty, string booleans, camelCase/snake_case
"""

import pytest
from django.db.models import Q

from tracer.utils.filters import FilterEngine

# ============================================================================
# Helper: build filter payloads matching what the frontend sends
# ============================================================================


def _has_eval_filter(value=True):
    """Matches FILTER_FOR_HAS_EVAL after objectCamelToSnake."""
    return {
        "column_id": "has_eval",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _has_annotation_filter(value=False):
    """Matches FILTER_FOR_NON_ANNOTATED after already being snake_case."""
    return {
        "column_id": "has_annotation",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _errors_filter():
    """Matches FILTER_FOR_ERRORS (already snake_case)."""
    return {
        "column_id": "status",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "ERROR",
        },
    }


# ============================================================================
# 1. ClickHouse Filter Builder — all three metric filters
# ============================================================================


@pytest.mark.unit
class TestCHMetricFilters:
    """ClickHouse filter builder handles all three metric filters correctly."""

    def _builder(self):
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        return ClickHouseFilterBuilder()

    # --- has_eval ---

    def test_has_eval_true_generates_subquery(self):
        where, params = self._builder().translate([_has_eval_filter(True)])
        assert "tracer_eval_logger" in where
        assert "trace_id IN" in where
        assert params == {}

    def test_has_eval_false_no_condition(self):
        where, _ = self._builder().translate([_has_eval_filter(False)])
        assert where == ""

    def test_has_eval_string_true(self):
        where, _ = self._builder().translate([_has_eval_filter("true")])
        assert "tracer_eval_logger" in where

    def test_has_eval_string_false(self):
        where, _ = self._builder().translate([_has_eval_filter("false")])
        assert where == ""

    def test_has_eval_string_True_capital(self):
        where, _ = self._builder().translate([_has_eval_filter("True")])
        assert "tracer_eval_logger" in where

    # --- has_annotation ---

    def test_has_annotation_true_generates_IN(self):
        where, params = self._builder().translate([_has_annotation_filter(True)])
        assert "model_hub_score" in where
        assert "trace_id IN" in where
        assert "NOT IN" not in where
        assert params == {}

    def test_has_annotation_false_generates_NOT_IN(self):
        where, _ = self._builder().translate([_has_annotation_filter(False)])
        assert "trace_id NOT IN" in where
        assert "model_hub_score" in where

    def test_has_annotation_string_true(self):
        where, _ = self._builder().translate([_has_annotation_filter("true")])
        assert "NOT IN" not in where
        assert "trace_id IN" in where

    def test_has_annotation_string_false(self):
        where, _ = self._builder().translate([_has_annotation_filter("false")])
        assert "trace_id NOT IN" in where

    # --- status (Errors) ---

    def test_errors_filter_generates_status_condition(self):
        where, params = self._builder().translate([_errors_filter()])
        assert "lower(status)" in where
        assert "error" in params.values()

    # --- combinations ---

    def test_all_three_combined(self):
        """All three metric filters produce AND-joined conditions."""
        filters = [
            _has_eval_filter(True),
            _has_annotation_filter(False),
            _errors_filter(),
        ]
        where, params = self._builder().translate(filters)
        assert "tracer_eval_logger" in where
        assert "model_hub_score" in where
        assert "status" in where
        # Top-level conditions joined by AND
        assert where.count("trace_id IN") >= 1
        assert where.count("trace_id NOT IN") >= 1

    def test_has_eval_with_system_metric(self):
        """has_eval combined with a SYSTEM_METRIC filter."""
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-4",
                    "col_type": "SYSTEM_METRIC",
                },
            },
            _has_eval_filter(True),
        ]
        where, params = self._builder().translate(filters)
        assert "AND" in where
        assert "model" in where
        assert "tracer_eval_logger" in where

    def test_camelCase_filter_format(self):
        """Frontend sends camelCase before objectCamelToSnake — builder handles both."""
        filters = [
            {
                "columnId": "has_eval",
                "filterConfig": {
                    "filterType": "boolean",
                    "filterOp": "equals",
                    "filterValue": True,
                },
            }
        ]
        where, _ = self._builder().translate(filters)
        assert "tracer_eval_logger" in where

    def test_has_annotation_camelCase(self):
        filters = [
            {
                "columnId": "has_annotation",
                "filterConfig": {
                    "filterType": "boolean",
                    "filterOp": "equals",
                    "filterValue": False,
                },
            }
        ]
        where, _ = self._builder().translate(filters)
        assert "trace_id NOT IN" in where

    def test_empty_filter_list(self):
        where, params = self._builder().translate([])
        assert where == ""
        assert params == {}

    def test_none_column_id_skipped(self):
        filters = [{"column_id": None, "filter_config": {}}]
        where, _ = self._builder().translate(filters)
        assert where == ""

    def test_has_eval_does_not_generate_column_reference(self):
        """has_eval must NOT generate 'has_eval = True' (column doesn't exist)."""
        where, _ = self._builder().translate([_has_eval_filter(True)])
        assert "has_eval =" not in where
        assert "has_eval =" not in where.lower()

    def test_has_annotation_does_not_generate_column_reference(self):
        """has_annotation must NOT generate 'has_annotation = ...' (column doesn't exist)."""
        where, _ = self._builder().translate([_has_annotation_filter(True)])
        # Should contain subquery, not a direct column reference
        assert "has_annotation =" not in where

    # --- NULL safety (critical for NOT IN) ---

    def test_has_annotation_false_excludes_null_trace_ids(self):
        """Subquery MUST filter NULL resolved trace_ids."""
        where, _ = self._builder().translate([_has_annotation_filter(False)])
        assert "isNotNull(" in where

    def test_has_annotation_true_excludes_null_trace_ids(self):
        """IN subquery should also exclude NULL trace_ids for correctness."""
        where, _ = self._builder().translate([_has_annotation_filter(True)])
        assert "isNotNull(" in where

    def test_has_eval_true_excludes_null_trace_ids(self):
        """has_eval subquery should exclude NULL trace_ids."""
        where, _ = self._builder().translate([_has_eval_filter(True)])
        assert "trace_id IS NOT NULL" in where

    # --- Type safety (String vs UUID) ---

    def test_has_annotation_casts_to_string(self):
        """Subquery must cast resolved trace_id to String to match spans.trace_id."""
        where, _ = self._builder().translate([_has_annotation_filter(False)])
        assert "toString(" in where

    def test_has_annotation_joins_through_span(self):
        """Score.trace_id is often NULL — must join via observation_span to resolve trace_id."""
        where, _ = self._builder().translate([_has_annotation_filter(False)])
        assert "FROM spans WHERE" in where
        assert "sp.id = s.observation_span_id" in where

    def test_has_eval_casts_to_string(self):
        """Subquery must use toString(trace_id) because spans.trace_id is String
        but tracer_eval_logger.trace_id is UUID."""
        where, _ = self._builder().translate([_has_eval_filter(True)])
        assert "toString(el.trace_id)" in where


# ============================================================================
# 2. PG FilterEngine — Q object generation
# ============================================================================


@pytest.mark.unit
class TestPGMetricFilters:
    """PG FilterEngine generates correct Q objects for metric filters."""

    # --- has_eval ---

    def test_has_eval_true_trace(self):
        q = FilterEngine.get_filter_conditions_for_has_eval(
            [_has_eval_filter(True)], observe_type="trace"
        )
        assert q != Q()

    def test_has_eval_true_span(self):
        q = FilterEngine.get_filter_conditions_for_has_eval(
            [_has_eval_filter(True)], observe_type="span"
        )
        assert q != Q()

    def test_has_eval_false_returns_empty(self):
        q = FilterEngine.get_filter_conditions_for_has_eval(
            [_has_eval_filter(False)], observe_type="trace"
        )
        assert q == Q()

    def test_has_eval_none_filters(self):
        q = FilterEngine.get_filter_conditions_for_has_eval(None)
        assert q == Q()

    def test_has_eval_empty_filters(self):
        q = FilterEngine.get_filter_conditions_for_has_eval([])
        assert q == Q()

    # --- has_annotation ---

    def test_has_annotation_true_trace(self):
        q = FilterEngine.get_filter_conditions_for_has_annotation(
            [_has_annotation_filter(True)], observe_type="trace"
        )
        assert q != Q()

    def test_has_annotation_false_trace(self):
        """false should return negated Q (NOT empty)."""
        q = FilterEngine.get_filter_conditions_for_has_annotation(
            [_has_annotation_filter(False)], observe_type="trace"
        )
        assert q != Q()

    def test_has_annotation_true_span(self):
        q = FilterEngine.get_filter_conditions_for_has_annotation(
            [_has_annotation_filter(True)], observe_type="span"
        )
        assert q != Q()

    def test_has_annotation_false_span(self):
        q = FilterEngine.get_filter_conditions_for_has_annotation(
            [_has_annotation_filter(False)], observe_type="span"
        )
        assert q != Q()

    def test_has_annotation_none_filters(self):
        q = FilterEngine.get_filter_conditions_for_has_annotation(None)
        assert q == Q()

    def test_has_annotation_empty_filters(self):
        q = FilterEngine.get_filter_conditions_for_has_annotation([])
        assert q == Q()

    # --- status (Errors) ---

    def test_status_equals_error(self):
        """status filter should be recognized as system metric."""
        q = FilterEngine.get_filter_conditions_for_system_metrics([_errors_filter()])
        assert q != Q()

    def test_status_filter_without_col_type(self):
        """Status filter from FILTER_FOR_ERRORS has no col_type — must still work."""
        f = _errors_filter()
        assert "col_type" not in f["filter_config"]
        q = FilterEngine.get_filter_conditions_for_system_metrics([f])
        assert q != Q()

    # --- combined ---

    def test_mixed_filters_only_picks_relevant(self):
        """has_eval extractor ignores non-has_eval filters."""
        filters = [_errors_filter(), _has_eval_filter(True)]
        q = FilterEngine.get_filter_conditions_for_has_eval(
            filters, observe_type="trace"
        )
        assert q != Q()

    def test_mixed_filters_annotation(self):
        """has_annotation extractor ignores non-has_annotation filters."""
        filters = [_errors_filter(), _has_annotation_filter(False)]
        q = FilterEngine.get_filter_conditions_for_has_annotation(
            filters, observe_type="trace"
        )
        assert q != Q()


# ============================================================================
# 3. SpanList ClickHouse filter builder — verify span-level filters
# ============================================================================


@pytest.mark.unit
class TestCHSpanMetricFilters:
    """ClickHouse filter builder works for span-level queries too."""

    def _builder(self):
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        return ClickHouseFilterBuilder(table="spans")

    def test_has_eval_in_span_context(self):
        """has_eval subquery uses trace_id even in span context."""
        where, _ = self._builder().translate([_has_eval_filter(True)])
        assert "trace_id IN" in where

    def test_has_annotation_in_span_context(self):
        where, _ = self._builder().translate([_has_annotation_filter(False)])
        assert "trace_id NOT IN" in where


# ============================================================================
# 4. Frontend filter constant format validation
# ============================================================================


@pytest.mark.unit
class TestFilterConstantFormats:
    """Validate the exact filter formats match what the backend expects."""

    def test_has_eval_filter_format(self):
        """FILTER_FOR_HAS_EVAL after objectCamelToSnake should have snake_case keys."""
        f = _has_eval_filter(True)
        assert f["column_id"] == "has_eval"
        assert f["filter_config"]["filter_type"] == "boolean"
        assert f["filter_config"]["filter_op"] == "equals"
        assert f["filter_config"]["filter_value"] is True

    def test_errors_filter_format(self):
        """FILTER_FOR_ERRORS already in snake_case."""
        f = _errors_filter()
        assert f["column_id"] == "status"
        assert f["filter_config"]["filter_type"] == "text"
        assert f["filter_config"]["filter_op"] == "equals"
        assert f["filter_config"]["filter_value"] == "ERROR"

    def test_non_annotated_filter_format(self):
        """FILTER_FOR_NON_ANNOTATED already in snake_case."""
        f = _has_annotation_filter(False)
        assert f["column_id"] == "has_annotation"
        assert f["filter_config"]["filter_type"] == "boolean"
        assert f["filter_config"]["filter_op"] == "equals"
        assert f["filter_config"]["filter_value"] is False

    def test_ch_builder_accepts_all_three_formats(self):
        """All three filter formats are accepted without error."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            _has_eval_filter(True),
            _errors_filter(),
            _has_annotation_filter(False),
        ]
        where, params = builder.translate(filters)
        # All three should produce conditions
        assert where != ""
        assert "AND" in where

    def test_pg_engine_accepts_all_three_formats(self):
        """All three filter formats are accepted by PG FilterEngine."""
        filters = [
            _has_eval_filter(True),
            _errors_filter(),
            _has_annotation_filter(False),
        ]
        # Each extractor should find its filter
        assert (
            FilterEngine.get_filter_conditions_for_has_eval(
                filters, observe_type="trace"
            )
            != Q()
        )
        assert (
            FilterEngine.get_filter_conditions_for_has_annotation(
                filters, observe_type="trace"
            )
            != Q()
        )
        assert FilterEngine.get_filter_conditions_for_system_metrics(filters) != Q()
