from __future__ import annotations

import json
from copy import deepcopy

import pytest
from rest_framework import serializers

from tracer.serializers.filters import (
    FILTER_LIST_MAX_ITEMS,
    FILTER_LIST_MAX_SERIALIZED_UTF8_BYTES,
    FILTER_LIST_MAX_TOTAL_STRING_UTF8_BYTES,
    FILTER_LIST_MAX_VALUES,
    FILTER_STRING_MAX_UTF8_BYTES,
    FILTER_VALUE_MAX_DEPTH,
    FilterListField,
    FilterListQueryParamField,
)
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)
from tracer.utils.filter_operators import load_filter_contract


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
def test_filter_budget_is_derived_without_expanding_other_resource_limits():
    limits = load_filter_contract()["limits"]
    assert limits == {
        "maxItems": 32,
        "maxValues": 64,
        "maxDepth": 8,
        "stringMaxUtf8Bytes": 4096,
        "typedStringMaxUtf8Bytes": 16384,
        "totalStringMaxUtf8Bytes": 10 * 16384 + 65536,
        "serializedMaxUtf8Bytes": 6 * (10 * 16384 + 65536) + 65536,
        "configMaxUtf8Bytes": 131072,
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
    payload = [_filter(value="é" * (TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES // 2 + 1))]

    with pytest.raises(serializers.ValidationError, match="UTF-8 byte limit"):
        FilterListField().run_validation(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    "operator",
    [
        "equals",
        "not_equals",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
    ],
)
@pytest.mark.parametrize(
    "alias,character", [("text", "x"), ("string", "é"), ("STRING", "🙂")]
)
def test_scalar_span_text_16_kib_boundary_is_lossless(operator, alias, character):
    limit = TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
    count, remainder = divmod(limit - 2, len(character.encode()))
    value = " " + character * count + " " * (remainder + 1)
    payload = [_filter(value=value)]
    payload[0]["filter_config"].update(filter_type=alias, filter_op=operator)
    expected = deepcopy(payload)
    expected[0]["filter_config"]["filter_type"] = "text"
    assert len(value.encode()) == limit
    assert FilterListField().run_validation(deepcopy(payload)) == expected
    payload[0]["filter_config"]["filter_value"] += "x"
    with pytest.raises(serializers.ValidationError, match=f"{limit} UTF-8 byte limit"):
        FilterListField().run_validation(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    "target",
    [
        "column_id",
        "display_name",
        "SYSTEM_METRIC",
        "EVAL_METRIC",
        "ANNOTATION",
        "NORMAL",
        "in",
        "not_in",
        "array",
        "map",
        "null_provenance",
        "misaligned_provenance",
    ],
)
def test_scalar_span_extension_keeps_other_4_kib_guards(target):
    oversized = "x" * (FILTER_STRING_MAX_UTF8_BYTES + 1)
    leaf = _filter(value=oversized)
    config = leaf["filter_config"]
    if target in ("column_id", "display_name"):
        leaf[target], config["filter_value"] = oversized, "ok"
    elif target in ("SYSTEM_METRIC", "EVAL_METRIC", "ANNOTATION", "NORMAL"):
        config["col_type"] = target
    elif target == "map":
        config.update(filter_type="map", filter_value={"key": oversized})
    else:
        config.update(
            filter_op=target if target in ("in", "not_in") else "in",
            filter_value=[oversized],
        )
        if target == "array":
            config.update(filter_type="array", filter_op="contains")
        elif target.endswith("provenance"):
            config["attribute_value_types"] = (
                [None] if target == "null_provenance" else []
            )
    with pytest.raises(serializers.ValidationError, match="4096 UTF-8 byte limit"):
        FilterListField().run_validation([leaf])


@pytest.mark.unit
@pytest.mark.parametrize(
    "value,extra",
    [
        (True, {}),
        (12, {}),
        (None, {}),
        ("\ud800", {}),
        ("ok", {"filter_op": "between"}),
        ("x" * 4097, {"attribute_value_types": ["string"]}),
    ],
)
def test_scalar_span_extension_keeps_type_operator_utf8_and_provenance_validation(
    value, extra
):
    leaf = _filter(value=value)
    leaf["filter_config"].update(extra)
    with pytest.raises(serializers.ValidationError):
        FilterListField().run_validation([leaf])


@pytest.mark.unit
def test_filter_leaves_keep_exact_aggregate_boundary():
    payload = [_filter(value="") for _ in range(FILTER_LIST_MAX_ITEMS)]
    overhead = sum(
        len(leaf["column_id"].encode())
        + sum(
            len(leaf["filter_config"][key].encode())
            for key in ("filter_type", "filter_op", "col_type")
        )
        for leaf in payload
    )
    count, remainder = divmod(
        FILTER_LIST_MAX_TOTAL_STRING_UTF8_BYTES - overhead, len(payload)
    )
    for index, leaf in enumerate(payload):
        leaf["filter_config"]["filter_value"] = "x" * (count + (index < remainder))
    assert FilterListField().run_validation(deepcopy(payload)) == payload
    payload[-1]["filter_config"]["filter_value"] += "x"
    with pytest.raises(
        serializers.ValidationError,
        match=f"{FILTER_LIST_MAX_TOTAL_STRING_UTF8_BYTES} UTF-8 byte request limit",
    ):
        FilterListField().run_validation(payload)


@pytest.mark.unit
@pytest.mark.parametrize("character", ["x", "🙂", '"', "\\", "\x01"])
@pytest.mark.parametrize("mixed", [False, True])
def test_ten_retained_max_strings_preserve_values_operators_and_provenance(
    character, mixed
):
    operators = [
        "equals",
        "not_equals",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
    ]
    payload = []
    for index in range(10):
        count, remainder = divmod(
            TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES - 2, len(character.encode())
        )
        value = f"{index:02d}" + character * count + "x" * remainder
        leaf = _filter(value=value)
        leaf["column_id"] = f"typed.attribute.{index}"
        leaf["filter_config"]["filter_op"] = operators[index % len(operators)]
        if mixed and index >= 6:
            leaf["filter_config"].update(
                filter_op="in" if index % 2 else "not_in",
                filter_value=[value, 0, False],
                attribute_value_types=["string", "number", "boolean"],
            )
        payload.append(leaf)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    body = json.dumps({"filters": encoded}, ensure_ascii=False)
    assert FilterListField().run_validation(deepcopy(payload)) == payload
    assert (
        FilterListQueryParamField().run_validation(json.loads(body)["filters"])
        == payload
    )
    assert len(encoded.encode()) <= FILTER_LIST_MAX_SERIALIZED_UTF8_BYTES
    assert (
        load_filter_contract()["limits"]["typedStringMaxUtf8Bytes"]
        == TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
    )


@pytest.mark.unit
@pytest.mark.parametrize("suffix", [" ", "é"])
def test_serialized_filter_budget_rejects_before_json_parse(monkeypatch, suffix):
    encoded = json.dumps([_filter()])
    encoded += " " * (FILTER_LIST_MAX_SERIALIZED_UTF8_BYTES - len(encoded.encode()))
    assert FilterListQueryParamField().run_validation(encoded[:-1]) == [_filter()]
    assert FilterListQueryParamField().run_validation(encoded) == [_filter()]

    def unexpected_parse(*args, **kwargs):
        pytest.fail("oversized serialized input reached json.loads")

    monkeypatch.setattr("tracer.serializers.filters.json.loads", unexpected_parse)
    with pytest.raises(serializers.ValidationError, match="Serialized filters exceed"):
        FilterListQueryParamField().run_validation(
            encoded + suffix if suffix == " " else encoded[:-1] + suffix
        )


@pytest.mark.unit
def test_long_string_budget_does_not_expand_per_config_budget():
    leaf = _filter(value=["\x01" * TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES] * 2)
    leaf["filter_config"]["attribute_value_types"] = ["string", "string"]
    with pytest.raises(
        serializers.ValidationError, match="Filter config exceeds the 131072"
    ):
        FilterListField().run_validation([leaf])


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
