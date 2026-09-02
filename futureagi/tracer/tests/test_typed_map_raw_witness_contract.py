from __future__ import annotations

from collections.abc import Callable

import pytest

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    LatestFilterPredicate,
    compile_span_filter_plans,
    compile_trace_filter_plans,
)

Compiler = Callable[[list[dict[str, object]]], list[LatestFilterPredicate]]


def _attribute_filter(
    *,
    filter_type: str,
    operation: str,
    value: object,
) -> dict[str, object]:
    return {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _plan(
    compiler: Compiler,
    *,
    filter_type: str,
    operation: str,
    value: object,
) -> LatestFilterPredicate:
    plans = compiler(
        [
            _attribute_filter(
                filter_type=filter_type,
                operation=operation,
                value=value,
            )
        ]
    )
    assert len(plans) == 1
    return plans[0]


@pytest.mark.parametrize(
    "compiler",
    [compile_trace_filter_plans, compile_span_filter_plans],
    ids=["trace", "span"],
)
@pytest.mark.parametrize(
    ("filter_type", "map_column", "equals_value", "in_values"),
    [
        ("text", "span_attr_str", "Rejected", ["Rejected", "Approved"]),
        ("number", "span_attr_num", 7, [7, 9]),
        ("boolean", "span_attr_bool", True, [True, False]),
    ],
)
@pytest.mark.parametrize("operation", ["equals", "in"])
def test_positive_typed_map_equality_raw_witness_binds_key_and_value(
    compiler: Compiler,
    filter_type: str,
    map_column: str,
    equals_value: object,
    in_values: list[object],
    operation: str,
) -> None:
    plan = _plan(
        compiler,
        filter_type=filter_type,
        operation=operation,
        value=equals_value if operation == "equals" else in_values,
    )

    witness = plan.raw_witness_predicate
    assert witness is not None
    assert plan.raw_witness_rank == 0
    assert f"indexHint(has(mapKeys({map_column}), %(latest_filter_key_0)s))" in witness
    assert f"has({map_column}.keys, %(latest_filter_key_0)s)" in witness
    assert f"mapContains({map_column}, %(latest_filter_key_0)s)" in witness
    assert "%(latest_filter_param_0)s" in witness
    assert plan.params["latest_filter_key_0"] == "final_status"

    comparison = "=" if operation == "equals" else "IN"
    assert f"{map_column}[%(latest_filter_key_0)s]" in witness
    assert f" {comparison} %(latest_filter_param_0)s" in witness

    key_witness = plan.raw_key_witness_predicate
    assert key_witness is not None
    assert f"has({map_column}.keys, %(latest_filter_key_0)s)" in key_witness
    assert "latest_filter_param_0" not in key_witness
    assert f"{map_column}[%(latest_filter_key_0)s]" not in key_witness


@pytest.mark.parametrize(
    ("filter_type", "map_column", "operation", "value", "index_expression"),
    [
        (
            "text",
            "span_attr_str",
            "equals",
            "Rejected",
            "has(arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str)), "
            "%(latest_filter_param_0)s)",
        ),
        (
            "text",
            "span_attr_str",
            "in",
            ["Rejected", "Approved"],
            "hasAny(arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str)), [",
        ),
        (
            "number",
            "span_attr_num",
            "equals",
            7,
            "has(mapValues(span_attr_num), %(latest_filter_param_0)s)",
        ),
        (
            "number",
            "span_attr_num",
            "in",
            [7, 9],
            "hasAny(mapValues(span_attr_num), [",
        ),
    ],
)
def test_text_and_numeric_positive_witnesses_keep_only_safe_index_companions(
    filter_type: str,
    map_column: str,
    operation: str,
    value: object,
    index_expression: str,
) -> None:
    plan = _plan(
        compile_span_filter_plans,
        filter_type=filter_type,
        operation=operation,
        value=value,
    )

    assert plan.raw_witness_predicate is not None
    assert index_expression in plan.seed_predicate
    assert index_expression in plan.raw_witness_predicate
    if operation == "in":
        assert "%(latest_filter_index_0_0)s" in plan.raw_witness_predicate
        assert "%(latest_filter_index_0_1)s" in plan.raw_witness_predicate
    assert map_column in plan.raw_witness_predicate


def test_ascii_filter_does_not_make_unicode_stored_values_ascii_safe() -> None:
    stored = "\N{KELVIN SIGN}"
    assert not stored.isascii()
    assert stored.lower() == "k"

    plan = _plan(
        compile_span_filter_plans,
        filter_type="text",
        operation="equals",
        value="K",
    )

    assert plan.params["latest_filter_param_0"] == "k"
    assert "lowerUTF8(toString(span_attr_str[" in plan.seed_predicate
    assert "lowerUTF8(toString(span_attr_str[" in plan.raw_witness_predicate
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" not in plan.seed_predicate
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" not in (
        plan.raw_witness_predicate or ""
    )
    assert "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))" in (
        plan.seed_predicate or ""
    )
    assert "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))" in (
        plan.raw_witness_predicate or ""
    )


@pytest.mark.parametrize(
    ("filter_type", "operation", "value", "map_column"),
    [
        ("text", "contains", "ject", "span_attr_str"),
        ("number", "greater_than", 7, "span_attr_num"),
        ("boolean", "is_not_null", None, "span_attr_bool"),
    ],
)
def test_other_positive_typed_map_witnesses_remain_key_only(
    filter_type: str,
    operation: str,
    value: object,
    map_column: str,
) -> None:
    plan = _plan(
        compile_span_filter_plans,
        filter_type=filter_type,
        operation=operation,
        value=value,
    )

    witness = plan.raw_witness_predicate
    assert witness is not None
    assert plan.raw_witness_rank == 10
    assert f"has({map_column}.keys, %(latest_filter_key_0)s)" in witness
    assert "latest_filter_param_0" not in witness
    assert f"{map_column}[%(latest_filter_key_0)s]" not in witness
    assert plan.raw_key_witness_predicate == witness


@pytest.mark.parametrize(
    ("filter_type", "operation", "value"),
    [
        ("text", "not_equals", "Rejected"),
        ("text", "not_in", ["Rejected", "Approved"]),
        ("text", "not_contains", "ject"),
        ("number", "not_equals", 7),
        ("boolean", "not_in", [True]),
        ("boolean", "is_null", None),
    ],
)
def test_negative_typed_map_filters_have_no_raw_witness(
    filter_type: str,
    operation: str,
    value: object,
) -> None:
    plan = _plan(
        compile_span_filter_plans,
        filter_type=filter_type,
        operation=operation,
        value=value,
    )

    assert plan.raw_witness_predicate is None
    assert plan.raw_key_witness_predicate is None
    assert plan.raw_witness_rank is None
