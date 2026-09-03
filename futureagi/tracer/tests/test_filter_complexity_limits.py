from __future__ import annotations

import pytest
from rest_framework import serializers

from tracer.serializers.filters import (
    FILTER_LIST_MAX_ITEMS,
    FILTER_LIST_MAX_VALUES,
    FILTER_STRING_MAX_UTF8_BYTES,
    FILTER_VALUE_MAX_DEPTH,
    FilterListField,
)
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)


def _filter(*, value: object = "ok") -> dict:
    return {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in" if isinstance(value, list) else "equals",
            "filter_value": value,
        },
    }


@pytest.mark.unit
def test_filter_list_rejects_more_than_the_contract_limit():
    payload = [_filter() for _ in range(FILTER_LIST_MAX_ITEMS + 1)]

    with pytest.raises(serializers.ValidationError, match="filters may be applied"):
        FilterListField().run_validation(payload)


@pytest.mark.unit
def test_filter_list_rejects_unbounded_in_values():
    payload = [
        _filter(value=[str(index) for index in range(FILTER_LIST_MAX_VALUES + 1)])
    ]

    with pytest.raises(serializers.ValidationError, match="supports at most"):
        FilterListField().run_validation(payload)


@pytest.mark.unit
def test_filter_list_rejects_oversized_utf8_values_before_compilation():
    payload = [_filter(value="é" * (FILTER_STRING_MAX_UTF8_BYTES // 2 + 1))]

    with pytest.raises(serializers.ValidationError, match="UTF-8 byte limit"):
        FilterListField().run_validation(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "x" * (FILTER_STRING_MAX_UTF8_BYTES + 1),
        "é" * (TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES // 2),
    ],
)
def test_filter_list_accepts_retained_typed_picker_strings_through_16_kib(
    value: str,
):
    payload = [_filter(value=[value])]
    payload[0]["filter_config"]["attribute_value_types"] = ["string"]

    validated = FilterListField().run_validation(payload)

    assert validated[0]["filter_config"]["filter_value"] == [value]


@pytest.mark.unit
def test_filter_list_rejects_typed_picker_string_above_16_kib():
    value = "é" * (TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES // 2) + "x"
    payload = [_filter(value=[value])]
    payload[0]["filter_config"]["attribute_value_types"] = ["string"]

    with pytest.raises(
        serializers.ValidationError,
        match=f"{TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES} UTF-8 byte limit",
    ):
        FilterListField().run_validation(payload)


@pytest.mark.unit
def test_filter_list_rejects_pathological_value_nesting_before_recursing():
    value: object = "leaf"
    for _ in range(FILTER_VALUE_MAX_DEPTH + 1):
        value = {"nested": value}

    with pytest.raises(serializers.ValidationError, match="nested levels"):
        FilterListField().run_validation([_filter(value=value)])
