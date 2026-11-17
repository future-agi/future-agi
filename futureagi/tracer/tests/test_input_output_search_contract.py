"""The reserved Observe content-search leaf is bounded before compilation."""

import pytest
from rest_framework.exceptions import ValidationError

from tracer.serializers.filters import FilterItemField


def search_filter(value="needle", **config):
    return {
        "column_id": "input_output",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "contains",
            "filter_value": value,
            **config,
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", " \t\n", None, 42, [], "a" * 1025, "é" * 513])
def test_content_search_rejects_blank_non_string_and_oversized_values(value):
    with pytest.raises(ValidationError):
        FilterItemField().run_validation(search_filter(value))


@pytest.mark.unit
@pytest.mark.parametrize(
    "config",
    [
        {"filter_type": "categorical"},
        {"filter_op": "equals"},
        {"filter_op": "not_contains"},
        {"filter_op": "is_null"},
    ],
)
def test_content_search_only_accepts_text_contains(config):
    with pytest.raises(ValidationError):
        FilterItemField().run_validation(search_filter(**config))


@pytest.mark.unit
@pytest.mark.parametrize("value", ["é" * 512, "a" * 1024, " A_%'\\文 "])
def test_content_search_preserves_literal_needle_at_utf8_boundary(value):
    item = search_filter(value)
    assert FilterItemField().run_validation(item) == item


@pytest.mark.unit
def test_attribute_named_input_output_remains_an_attribute():
    item = search_filter("", col_type="SPAN_ATTRIBUTE", filter_op="equals")
    assert FilterItemField().run_validation(item) == item
