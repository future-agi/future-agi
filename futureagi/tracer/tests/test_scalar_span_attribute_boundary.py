"""Contract guards for the bounded typed-Map span-attribute path."""

from __future__ import annotations

from datetime import datetime

import pytest
from rest_framework import serializers

from tracer.serializers.filters import FilterListField
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_trace_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
START = datetime(2026, 1, 1)
END = datetime(2026, 2, 1)


def _time_filter() -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _attribute_filter(
    *,
    filter_type: str = "text",
    filter_op: str = "equals",
    filter_value: object = "Rejected",
    key: str = "final_status",
) -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


@pytest.mark.parametrize(
    "filter_type,filter_value",
    [("text", "ok"), ("number", "12.5"), ("boolean", True)],
)
def test_serializer_accepts_only_supported_scalar_span_attribute_values(
    filter_type: str, filter_value: object
) -> None:
    validated = FilterListField().run_validation(
        [
            _attribute_filter(
                filter_type=filter_type,
                filter_value=filter_value,
            )
        ]
    )

    assert validated[0]["filter_config"]["filter_type"] == filter_type


@pytest.mark.parametrize(
    "filter_op,filter_value,expected",
    [
        ("equals", "12.5", 12.5),
        ("between", ["-1.25", 3], [-1.25, 3.0]),
    ],
)
def test_serializer_coerces_finite_number_span_attribute_values(
    filter_op: str,
    filter_value: object,
    expected: object,
) -> None:
    validated = FilterListField().run_validation(
        [
            _attribute_filter(
                filter_type="number",
                filter_op=filter_op,
                filter_value=filter_value,
            )
        ]
    )

    assert validated[0]["filter_config"]["filter_value"] == expected


@pytest.mark.parametrize(
    "filter_op,filter_value",
    [
        pytest.param("equals", True, id="bool"),
        pytest.param("equals", "nan", id="nan-string"),
        pytest.param("equals", "Infinity", id="inf-string"),
        pytest.param("equals", 10**400, id="overflowing-int"),
        pytest.param("between", [1, False], id="bool-in-range"),
        pytest.param("between", [0, "-Infinity"], id="inf-in-range"),
    ],
)
def test_serializer_rejects_nonfinite_bool_and_overflowing_span_numbers(
    filter_op: str,
    filter_value: object,
) -> None:
    with pytest.raises(serializers.ValidationError, match="finite numbers"):
        FilterListField().run_validation(
            [
                _attribute_filter(
                    filter_type="number",
                    filter_op=filter_op,
                    filter_value=filter_value,
                )
            ]
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_serializer_json_boundary_rejects_native_nonfinite_numbers(
    value: float,
) -> None:
    with pytest.raises(serializers.ValidationError):
        FilterListField().run_validation(
            [_attribute_filter(filter_type="number", filter_value=value)]
        )


@pytest.mark.parametrize(
    "filter_op,filter_value",
    [
        pytest.param("equals", True, id="bool"),
        pytest.param("equals", float("nan"), id="nan-float"),
        pytest.param("equals", float("inf"), id="inf-float"),
        pytest.param("equals", "-Infinity", id="inf-string"),
        pytest.param("equals", 10**400, id="overflowing-int"),
        pytest.param("between", [1, False], id="bool-in-range"),
        pytest.param("between", [0, "nan"], id="nan-in-range"),
    ],
)
def test_latest_predicate_rejects_nonfinite_bool_and_overflowing_span_numbers(
    filter_op: str,
    filter_value: object,
) -> None:
    with pytest.raises(ValueError, match="finite numbers"):
        compile_trace_filter_plans(
            [
                _attribute_filter(
                    filter_type="number",
                    filter_op=filter_op,
                    filter_value=filter_value,
                )
            ]
        )


def test_latest_predicate_coerces_finite_span_number_once_for_both_paths() -> None:
    plan = compile_trace_filter_plans(
        [
            _attribute_filter(
                filter_type="number",
                filter_op="between",
                filter_value=["-1.25", 3],
            )
        ]
    )[0]

    assert plan.params["latest_filter_param_0_low"] == -1.25
    assert plan.params["latest_filter_param_0_high"] == 3.0


@pytest.mark.parametrize(
    "filter_type,filter_op,filter_value",
    [
        ("array", "contains", "value"),
        ("text", "equals", {"nested": True}),
        ("text", "in", [["nested"]]),
        ("text", "equals", None),
        ("number", "equals", True),
        ("boolean", "equals", "true"),
    ],
)
def test_serializer_cleanly_rejects_structured_null_or_wrong_scalar_types(
    filter_type: str,
    filter_op: str,
    filter_value: object,
) -> None:
    with pytest.raises(serializers.ValidationError):
        FilterListField().run_validation(
            [
                _attribute_filter(
                    filter_type=filter_type,
                    filter_op=filter_op,
                    filter_value=filter_value,
                )
            ]
        )


def test_final_status_plan_is_typed_map_only_with_bound_key_and_value() -> None:
    plan = compile_trace_filter_plans([_attribute_filter()])[0]

    assert plan.scope == "any"
    assert "mapContains(span_attr_str, %(latest_filter_key_0)s)" in (
        plan.seed_predicate
    )
    assert (
        "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))"
        in plan.seed_predicate
    )
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" not in (
        plan.seed_predicate
    )
    assert "span_attributes_raw" not in plan.seed_predicate
    assert "JSON" not in plan.seed_predicate
    assert "final_status" not in plan.seed_predicate
    assert "Rejected" not in plan.seed_predicate
    assert plan.params["latest_filter_key_0"] == "final_status"
    assert plan.params["latest_filter_param_0"] == "rejected"


@pytest.mark.parametrize(
    ("builder_cls", "seed_applies_attribute"),
    [
        (TraceListQueryBuilderV2, True),
        (SpanListQueryBuilderV2, True),
    ],
)
def test_final_status_v2_seed_respects_trace_any_span_scope(
    builder_cls,
    seed_applies_attribute: bool,
) -> None:
    filters = [_time_filter(), _attribute_filter()]
    builder = builder_cls(project_id=PROJECT_ID, filters=filters)

    sql, params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=200,
    )

    if seed_applies_attribute:
        assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in sql
        assert (
            "arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in sql
        )
        assert "arrayMap(x -> lower(x), mapValues(attrs_string))" not in sql
        assert (
            "lowerUTF8(toString(attrs_string[%(latest_filter_key_0)s])) = "
            "%(latest_filter_param_0)s" in sql
        )
        assert params["latest_filter_key_0"] == "final_status"
        assert params["latest_filter_param_0"] == "rejected"
    else:
        assert "mapContains(attrs_string" not in sql
        assert "mapValues(attrs_string)" not in sql
        assert "latest_filter_key_0" not in params
        assert "latest_filter_param_0" not in params
    assert "attributes_extra" not in sql
    assert "JSONType" not in sql
    assert "JSONExtract" not in sql
    assert "final_status" not in sql
    assert "Rejected" not in sql


def test_typed_value_metadata_keeps_public_span_seed_indexed() -> None:
    attribute = _attribute_filter(
        filter_op="in",
        filter_value=["Rejected"],
    )
    attribute["filter_config"]["attribute_value_types"] = ["string"]
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), attribute],
    )

    sql, params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=200,
    )

    assert "indexHint(has(mapKeys(attrs_string)" in sql
    assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in sql
    assert "mapContains(attrs_string, %(latest_filter_key_0)s)" in sql
    assert "lowerUTF8(toString(attrs_string[%(latest_filter_key_0)s])) IN" in sql
    assert "WHERE 1 = 1" not in sql
    assert params["latest_filter_key_0"] == "final_status"
    assert params["latest_filter_param_0_string"] == ("rejected",)


def test_indexed_attribute_seed_conjoins_positive_model_witness() -> None:
    attribute = _attribute_filter(
        filter_op="in",
        filter_value=["Rejected"],
    )
    attribute["filter_config"]["attribute_value_types"] = ["string"]
    model = {
        "column_id": "model",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["gpt-4o-mini"],
        },
    }
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), attribute, model],
    )

    sql, params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=200,
    )

    assert "indexHint(has(mapKeys(attrs_string)" in sql
    assert "lowerUTF8(toString(model)) IN %(latest_filter_param_1)s" in sql
    assert params["latest_filter_param_1"] == ("gpt-4o-mini",)


def test_indexed_attribute_seed_does_not_apply_negative_model_witness() -> None:
    attribute = _attribute_filter(
        filter_op="in",
        filter_value=["Rejected"],
    )
    attribute["filter_config"]["attribute_value_types"] = ["string"]
    model = {
        "column_id": "model",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "not_in",
            "filter_value": ["gpt-4o-mini"],
        },
    }
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), attribute, model],
    )

    sql, params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=200,
    )

    assert "indexHint(has(mapKeys(attrs_string)" in sql
    assert "latest_filter_param_1" not in params
    assert "lowerUTF8(toString(model)) NOT IN" not in sql


def test_final_status_v2_match_classifies_latest_typed_map_state_only() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter()],
        bounded_identity_only=True,
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_match_query(["trace-a"])

    assert "argMax(mapContains(attrs_string" in sql
    assert "argMax(attrs_string[" in sql
    assert "attributes_extra" not in sql
    assert "JSONType" not in sql
    assert params["candidate_trace_ids"] == ("trace-a",)


def test_raw_call_type_span_attribute_remains_separate() -> None:
    item = _attribute_filter(key="call_type", filter_value="inbound")
    plan = compile_trace_filter_plans([item])[0]

    assert "span_attr_str[%(latest_filter_key_0)s]" in plan.seed_predicate
    assert "raw_log" not in plan.seed_predicate
    assert plan.params["latest_filter_key_0"] == "call_type"
