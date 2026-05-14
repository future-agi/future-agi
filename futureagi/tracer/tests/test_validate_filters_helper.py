"""Serializer-level filter validation tests.

These tests target `validate_filters_helper` directly so malformed
SPAN_ATTRIBUTE payloads return 400 before they ever reach the
ClickHouse filter builder.
"""

import pytest
from rest_framework import serializers

from tracer.utils.helper import validate_filters_helper


def _filter(col_id, *, filter_type, filter_op, filter_value=None, col_type="SPAN_ATTRIBUTE"):
    return {
        "column_id": col_id,
        "filter_config": {
            "col_type": col_type,
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


class TestValidateFiltersHelper:
    def test_empty_list_returns_empty(self):
        assert validate_filters_helper([]) == []

    def test_missing_keys_raises(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper([{"column_id": "k"}])

    def test_missing_config_keys_raises(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [{"column_id": "k", "filter_config": {"filter_type": "text"}}]
            )

    # SPAN_ATTRIBUTE — happy paths
    def test_span_attr_text_equals_valid(self):
        out = validate_filters_helper(
            [_filter("k", filter_type="text", filter_op="equals", filter_value="v")]
        )
        assert len(out) == 1

    def test_span_attr_number_between_valid(self):
        out = validate_filters_helper(
            [_filter("n", filter_type="number", filter_op="between", filter_value=["1", "2"])]
        )
        assert len(out) == 1

    def test_span_attr_boolean_native_bool_valid(self):
        out = validate_filters_helper(
            [_filter("b", filter_type="boolean", filter_op="equals", filter_value=True)]
        )
        assert len(out) == 1

    def test_span_attr_is_null_valid_without_value(self):
        out = validate_filters_helper(
            [_filter("k", filter_type="text", filter_op="is_null", filter_value=None)]
        )
        assert len(out) == 1

    # SPAN_ATTRIBUTE — contract violations
    def test_span_attr_unsupported_filter_type(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("k", filter_type="json", filter_op="equals", filter_value="v")]
            )

    def test_span_attr_op_not_allowed_for_type(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("n", filter_type="number", filter_op="contains", filter_value="abc")]
            )

    def test_span_attr_legacy_equal_to_rejected(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("n", filter_type="number", filter_op="equal_to", filter_value="42")]
            )

    def test_span_attr_legacy_not_in_between_rejected(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("n", filter_type="number", filter_op="not_in_between",
                         filter_value=["1", "2"])]
            )

    def test_span_attr_legacy_is_rejected(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("k", filter_type="text", filter_op="is", filter_value="v")]
            )

    def test_span_attr_between_wrong_arity(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("n", filter_type="number", filter_op="between", filter_value=["1"])]
            )

    def test_span_attr_in_empty_list_rejected(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("k", filter_type="text", filter_op="in", filter_value=[])]
            )

    def test_span_attr_in_non_list_rejected(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("k", filter_type="text", filter_op="in", filter_value="a")]
            )

    def test_span_attr_number_non_numeric_value_rejected(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("n", filter_type="number", filter_op="equals", filter_value="abc")]
            )

    def test_span_attr_boolean_string_true_rejected(self):
        """Strict: only native booleans. String forms must 400 at the serializer."""
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("b", filter_type="boolean", filter_op="equals", filter_value="true")]
            )

    def test_span_attr_boolean_int_one_rejected(self):
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper(
                [_filter("b", filter_type="boolean", filter_op="equals", filter_value=1)]
            )

    def test_non_span_attribute_filters_passthrough(self):
        """SYSTEM_METRIC and other col_types are not enforced by this validator
        (they have less-strict downstream handling). Ensure we don't accidentally
        reject them."""
        out = validate_filters_helper(
            [_filter("model", filter_type="text", filter_op="equals",
                     filter_value="gpt-4", col_type="SYSTEM_METRIC")]
        )
        assert len(out) == 1
