"""Tests for the canonical filter payload consumed by FilterEngine.

Filters are an API contract. Runtime serializers reject aliases at endpoint
boundaries; the lower-level FilterEngine should consume the canonical
snake_case shape and must not silently translate camelCase UI state.
"""

from django.db.models import Q

from tracer.utils.filters import FilterEngine


class TestNormalizeFilterParams:
    def test_snake_case_passthrough(self):
        item = {
            "column_id": "avg_cost",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 0.5,
                "col_type": "SYSTEM_METRIC",
            },
        }

        col_id, filter_config = FilterEngine._normalize_filter_params(item)

        assert col_id == "avg_cost"
        assert filter_config == item["filter_config"]

    def test_camel_case_outer_keys_are_not_translated(self):
        item = {
            "columnId": "avg_cost",
            "filterConfig": {
                "filterType": "number",
                "filterOp": "greater_than",
                "filterValue": 0.5,
                "colType": "SYSTEM_METRIC",
            },
        }

        col_id, filter_config = FilterEngine._normalize_filter_params(item)

        assert col_id is None
        assert filter_config == {}

    def test_camel_case_inner_keys_are_not_translated(self):
        item = {
            "column_id": "avg_cost",
            "filter_config": {
                "filterType": "number",
                "filterOp": "greater_than",
                "filterValue": 0.5,
                "colType": "SYSTEM_METRIC",
            },
        }

        col_id, filter_config = FilterEngine._normalize_filter_params(item)

        assert col_id == "avg_cost"
        assert "filter_op" not in filter_config
        assert "filter_type" not in filter_config
        assert "filter_value" not in filter_config

    def test_empty_filter_config(self):
        col_id, filter_config = FilterEngine._normalize_filter_params(
            {"column_id": "foo"}
        )

        assert col_id == "foo"
        assert filter_config == {}


class TestFilterEngineCanonicalFilters:
    def test_span_attributes_snake_case_filter_builds_condition(self):
        filter_item = {
            "column_id": "test_attr",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "hello",
            },
        }

        condition = FilterEngine.get_filter_conditions_for_span_attributes(
            [filter_item]
        )

        assert condition != Q()

    def test_span_attributes_camel_case_filter_is_not_translated(self):
        filter_item = {
            "column_id": "test_attr",
            "filter_config": {
                "colType": "SPAN_ATTRIBUTE",
                "filterType": "text",
                "filterOp": "equals",
                "filterValue": "hello",
            },
        }

        condition = FilterEngine.get_filter_conditions_for_span_attributes(
            [filter_item]
        )

        assert condition == Q()

    def test_non_system_metric_canonical_filter_does_not_crash(self):
        filter_item = {
            "column_id": "some_metric",
            "filter_config": {
                "col_type": "EVAL_METRIC",
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 0.5,
            },
        }

        condition = FilterEngine.get_filter_conditions_for_non_system_metrics(
            [filter_item]
        )

        assert isinstance(condition, Q)
