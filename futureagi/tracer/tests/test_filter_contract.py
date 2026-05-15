import pytest
from rest_framework import serializers

from tracer.utils.constants import (
    LIST_OPS,
    NO_VALUE_OPS,
    RANGE_OPS,
    SPAN_ATTR_ALLOWED_OPS,
)
from tracer.utils.filter_operators import (
    FILTER_OP_ALIASES,
    FILTER_TYPE_ALLOWED_OPS,
    normalize_filter_op,
    normalize_filter_type,
)
from tracer.utils.helper import validate_filters_helper


def _span_filter(filter_type, filter_op, filter_value):
    return {
        "column_id": "contract_attr",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def _sample_value(filter_type, filter_op):
    if filter_op in NO_VALUE_OPS:
        return None
    if filter_op in RANGE_OPS:
        return ["1", "2"]
    if filter_op in LIST_OPS:
        return ["alpha", "beta"]
    if filter_type == "number":
        return "10"
    if filter_type == "boolean":
        return True
    return "alpha"


class TestFilterContract:
    def test_contract_exports_canonical_span_attribute_ops(self):
        assert "not_between" in SPAN_ATTR_ALLOWED_OPS["number"]
        assert "not_in_between" not in SPAN_ATTR_ALLOWED_OPS["number"]
        assert FILTER_TYPE_ALLOWED_OPS["number"] == SPAN_ATTR_ALLOWED_OPS["number"]

    @pytest.mark.parametrize(
        "legacy_op,canonical_op",
        [
            ("is", "equals"),
            ("is_not", "not_equals"),
            ("equal_to", "equals"),
            ("not_equal_to", "not_equals"),
            ("not_in_between", "not_between"),
            ("before", "less_than"),
            ("after", "greater_than"),
            ("on", "equals"),
        ],
    )
    def test_operator_aliases_are_contract_backed(self, legacy_op, canonical_op):
        assert FILTER_OP_ALIASES[legacy_op] == canonical_op
        assert normalize_filter_op(legacy_op) == canonical_op

    def test_field_type_aliases_are_contract_backed(self):
        assert normalize_filter_type("string") == "text"
        assert normalize_filter_type("float") == "number"
        assert normalize_filter_type("timestamp") == "datetime"

    @pytest.mark.parametrize(
        "filter_type,filter_op",
        [
            (filter_type, filter_op)
            for filter_type, ops in SPAN_ATTR_ALLOWED_OPS.items()
            for filter_op in sorted(ops)
        ],
    )
    def test_all_span_attribute_contract_ops_validate(self, filter_type, filter_op):
        value = _sample_value(filter_type, filter_op)
        out = validate_filters_helper([_span_filter(filter_type, filter_op, value)])
        assert out[0]["filter_config"]["filter_op"] == filter_op

    @pytest.mark.parametrize(
        "legacy_op,value",
        [
            ("is", "alpha"),
            ("is_not", "alpha"),
            ("equal_to", "alpha"),
            ("not_equal_to", "alpha"),
            ("not_in_between", ["1", "2"]),
        ],
    )
    def test_span_attribute_rejects_legacy_wire_ops(self, legacy_op, value):
        filter_type = "number" if legacy_op == "not_in_between" else "text"
        with pytest.raises(serializers.ValidationError):
            validate_filters_helper([_span_filter(filter_type, legacy_op, value)])
