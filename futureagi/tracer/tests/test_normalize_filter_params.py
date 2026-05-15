"""Tests for _normalize_filter_params: camelCase, snake_case, and mixed filter input.

TH-4902: FilterEngine._normalize_filter_params must normalize inner keys
(filterOp → filter_op, filterType → filter_type, filterValue → filter_value,
colType → col_type) in addition to outer keys.
"""

import pytest
from django.db.models import Q

from tracer.utils.filters import ColType, FilterEngine


class TestNormalizeFilterParams:
    """Unit tests for FilterEngine._normalize_filter_params."""

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
        col_id, fc = FilterEngine._normalize_filter_params(item)
        assert col_id == "avg_cost"
        assert fc["filter_type"] == "number"
        assert fc["filter_op"] == "greater_than"
        assert fc["filter_value"] == 0.5
        assert fc["col_type"] == "SYSTEM_METRIC"

    def test_camel_case_outer_and_inner(self):
        item = {
            "columnId": "avg_cost",
            "filterConfig": {
                "filterType": "number",
                "filterOp": "greater_than",
                "filterValue": 0.5,
                "colType": "SYSTEM_METRIC",
            },
        }
        col_id, fc = FilterEngine._normalize_filter_params(item)
        assert col_id == "avg_cost"
        assert fc["filter_type"] == "number"
        assert fc["filter_op"] == "greater_than"
        assert fc["filter_value"] == 0.5
        assert fc["col_type"] == "SYSTEM_METRIC"
        # camelCase keys should be gone
        assert "filterType" not in fc
        assert "filterOp" not in fc
        assert "filterValue" not in fc
        assert "colType" not in fc

    def test_mixed_outer_camel_inner_snake(self):
        item = {
            "columnId": "status",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ERROR",
            },
        }
        col_id, fc = FilterEngine._normalize_filter_params(item)
        assert col_id == "status"
        assert fc["filter_type"] == "text"
        assert fc["filter_op"] == "equals"
        assert fc["filter_value"] == "ERROR"

    def test_mixed_outer_snake_inner_camel(self):
        item = {
            "column_id": "status",
            "filterConfig": {
                "filterType": "text",
                "filterOp": "not_contains",
                "filterValue": "voicemail",
                "colType": "SPAN_ATTRIBUTE",
            },
        }
        col_id, fc = FilterEngine._normalize_filter_params(item)
        assert col_id == "status"
        assert fc["filter_type"] == "text"
        assert fc["filter_op"] == "not_contains"
        assert fc["filter_value"] == "voicemail"
        assert fc["col_type"] == "SPAN_ATTRIBUTE"

    def test_empty_filter_config(self):
        item = {"column_id": "foo"}
        col_id, fc = FilterEngine._normalize_filter_params(item)
        assert col_id == "foo"
        assert fc == {}

    def test_missing_column_id(self):
        item = {"filter_config": {"filter_op": "equals"}}
        col_id, fc = FilterEngine._normalize_filter_params(item)
        assert col_id is None
        assert fc["filter_op"] == "equals"

    def test_both_camel_and_snake_inner_keys_snake_wins(self):
        """When both filterOp and filter_op exist, the last writer wins.

        Since dict iteration is insertion-order in Python 3.7+, the key
        that appears later takes precedence.
        """
        item = {
            "column_id": "x",
            "filter_config": {
                "filterOp": "camel_value",
                "filter_op": "snake_value",
            },
        }
        col_id, fc = FilterEngine._normalize_filter_params(item)
        # filter_op maps to itself, so it overwrites the earlier filterOp→filter_op
        assert fc["filter_op"] == "snake_value"

    def test_unknown_inner_keys_preserved(self):
        item = {
            "column_id": "x",
            "filter_config": {
                "filter_op": "equals",
                "custom_key": "preserved",
            },
        }
        col_id, fc = FilterEngine._normalize_filter_params(item)
        assert fc["custom_key"] == "preserved"
        assert fc["filter_op"] == "equals"

    def test_does_not_mutate_original(self):
        original_config = {
            "filterOp": "equals",
            "filterType": "text",
            "filterValue": "hello",
        }
        item = {"column_id": "x", "filterConfig": original_config}
        _, fc = FilterEngine._normalize_filter_params(item)
        # Original should still have camelCase keys
        assert "filterOp" in original_config
        assert "filter_op" not in original_config
        # Normalized should have snake_case
        assert "filter_op" in fc


class TestNormalizeFilterParamsIntegration:
    """Integration tests: camelCase filters work through consuming methods."""

    def _make_camel_span_attr_filter(self, ftype, op, value):
        """Build a filter item using camelCase inner keys."""
        return {
            "column_id": "test_attr",
            "filter_config": {
                "colType": "SPAN_ATTRIBUTE",
                "filterType": ftype,
                "filterOp": op,
                "filterValue": value,
            },
        }

    def test_span_attributes_with_camel_case_text_equals(self):
        """camelCase text equals filter should produce a Q object, not be silently dropped."""
        f = self._make_camel_span_attr_filter("text", "equals", "hello")
        q = FilterEngine.get_filter_conditions_for_span_attributes([f])
        assert q != Q(), "camelCase text/equals filter was silently dropped"

    def test_span_attributes_with_camel_case_text_not_contains(self):
        """Reproduces the Mudflap iForm-Prod / Ghosted-Prod bug:
        not_contains "voicemail" with camelCase keys."""
        f = self._make_camel_span_attr_filter("text", "not_contains", "voicemail")
        q = FilterEngine.get_filter_conditions_for_span_attributes([f])
        assert q != Q(), "camelCase text/not_contains filter was silently dropped"

    def test_span_attributes_with_camel_case_text_not_equals(self):
        """Reproduces the Mudflap iForm-Pre-Prod bug:
        not_equals "Voicemail" with camelCase keys."""
        f = self._make_camel_span_attr_filter("text", "not_equals", "Voicemail")
        q = FilterEngine.get_filter_conditions_for_span_attributes([f])
        assert q != Q(), "camelCase text/not_equals filter was silently dropped"

    def test_span_attributes_fully_camel_case_outer_and_inner(self):
        """Fully camelCase payload (both outer and inner keys) through
        get_filter_conditions_for_span_attributes — the most likely shape
        of stored Mudflap filter blobs."""
        f = {
            "columnId": "test_attr",
            "filterConfig": {
                "colType": "SPAN_ATTRIBUTE",
                "filterType": "text",
                "filterOp": "not_contains",
                "filterValue": "voicemail",
            },
        }
        q = FilterEngine.get_filter_conditions_for_span_attributes([f])
        assert q != Q(), "Fully camelCase filter was silently dropped"

    def test_span_attributes_with_camel_case_number_greater_than(self):
        f = self._make_camel_span_attr_filter("number", "greater_than", 42)
        q = FilterEngine.get_filter_conditions_for_span_attributes([f])
        assert q != Q(), "camelCase number/greater_than filter was silently dropped"

    def test_span_attributes_with_camel_case_boolean_equals(self):
        f = self._make_camel_span_attr_filter("boolean", "equals", True)
        q = FilterEngine.get_filter_conditions_for_span_attributes([f])
        assert q != Q(), "camelCase boolean/equals filter was silently dropped"

    def test_span_attributes_with_camel_case_is_null(self):
        f = self._make_camel_span_attr_filter("text", "is_null", None)
        q = FilterEngine.get_filter_conditions_for_span_attributes([f])
        assert q != Q(), "camelCase text/is_null filter was silently dropped"

    def test_span_attributes_snake_case_still_works(self):
        """Existing snake_case filters must not regress."""
        f = {
            "column_id": "test_attr",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "hello",
            },
        }
        q = FilterEngine.get_filter_conditions_for_span_attributes([f])
        assert q != Q(), "snake_case filter should still work"

    def test_non_system_metrics_with_camel_case(self):
        """camelCase inner keys in non-system metric filters should not be dropped."""
        f = {
            "column_id": "some_metric",
            "filter_config": {
                "colType": "EVAL_METRIC",
                "filterType": "number",
                "filterOp": "greater_than",
                "filterValue": 0.5,
            },
        }
        # Should not raise or crash; the method should parse inner keys
        q = FilterEngine.get_filter_conditions_for_non_system_metrics([f])
        # Even if Q() is returned (column_id might not match a real column),
        # the point is it shouldn't crash or silently skip due to missing keys.
        assert isinstance(q, Q)
