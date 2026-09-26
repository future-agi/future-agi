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
    semantic, index_only = plan.seed_predicate.split(" AND indexHint(", 1)
    assert f"mapContains({map_column}, %(latest_filter_key_0)s)" in semantic
    assert f"{map_column}[%(latest_filter_key_0)s]" in semantic
    assert "mapValues(" not in semantic
    assert index_expression in index_only
    assert index_only.endswith(")")
    if operation == "in":
        assert "%(latest_filter_index_0_0)s" in plan.raw_witness_predicate
        assert "%(latest_filter_index_0_1)s" in plan.raw_witness_predicate
    assert map_column in plan.raw_witness_predicate


def test_ascii_filter_keeps_unicode_semantics_with_exhaustive_legacy_witness() -> None:
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
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" in plan.seed_predicate
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" in (
        plan.raw_witness_predicate or ""
    )
    assert "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))" in (
        plan.seed_predicate or ""
    )
    assert "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))" in (
        plan.raw_witness_predicate or ""
    )
    assert {
        value
        for key, value in plan.params.items()
        if key.startswith("latest_filter_legacy_index_0_")
    } == {"k", stored}


def test_non_ascii_filter_declines_legacy_ascii_bloom_witness() -> None:
    plan = _plan(
        compile_span_filter_plans,
        filter_type="text",
        operation="equals",
        value="İstanbul",
    )

    assert "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))" in (
        plan.seed_predicate or ""
    )
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" not in (
        plan.seed_predicate or ""
    )
    assert not any(
        key.startswith("latest_filter_legacy_index_0_") for key in plan.params
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
def test_negative_typed_map_value_filters_use_only_necessary_presence(
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

    if operation == "is_null":
        assert plan.raw_witness_predicate is None
        assert plan.raw_key_witness_predicate is None
        assert plan.raw_witness_rank is None
    else:
        map_column = {
            "text": "span_attr_str",
            "number": "span_attr_num",
            "boolean": "span_attr_bool",
        }[filter_type]
        witness = (
            f"(indexHint(has(mapKeys({map_column}), %(latest_filter_key_0)s)) AND "
            f"has({map_column}.keys, %(latest_filter_key_0)s))"
        )
        assert plan.raw_witness_predicate == plan.raw_key_witness_predicate == witness
        assert plan.raw_witness_rank == 10
        assert "latest_attr_exists_0 AND" in plan.predicate
    assert plan.raw_graph_value_witness_predicate is None


@pytest.mark.parametrize("compiler", [compile_trace_filter_plans, compile_span_filter_plans])
@pytest.mark.parametrize(
    ("values", "types", "indexed_maps"),
    [
        (["K", "Approved"], ["string", "string"], ["span_attr_str"]),
        (["İstanbul"], ["string"], ["span_attr_str"]),
        ([7, 9], ["number", "number"], ["span_attr_num"]),
        (["K", 7, True], ["string", "number", "boolean"], ["span_attr_str", "span_attr_num"]),
    ],
)
def test_picker_in_reuses_value_indexes_without_changing_typed_membership(
    compiler, values, types, indexed_maps,
) -> None:
    leaf = _attribute_filter(filter_type="text", operation="in", value=values)
    leaf["filter_config"]["attribute_value_types"] = types
    plan, = compiler([leaf])
    raw = plan.raw_witness_predicate
    assert raw is not None
    for map_column in indexed_maps:
        assert f"mapValues({map_column})" in raw
        assert f"mapContains({map_column}, %(latest_filter_key_0)s)" in raw
    assert "mapValues(span_attr_bool)" not in raw
    assert "mapValues(" not in plan.seed_predicate
    assert "indexHint(" not in plan.predicate
    assert plan.raw_graph_value_witness_predicate == raw
    for storage_type in set(types):
        assert f"latest_filter_param_0_{storage_type}" in plan.params
        assert f"latest_attr_exists_0_{storage_type}" in plan.predicate
    legacy = {v for k, v in plan.params.items() if k.startswith("latest_filter_legacy_index_")}
    if "K" in values:
        assert {"k", "\N{KELVIN SIGN}"} <= legacy
    else:
        assert not legacy


@pytest.mark.parametrize("compiler", [compile_trace_filter_plans, compile_span_filter_plans])
@pytest.mark.parametrize(("value", "storage_type"), [(0, "number"), (False, "boolean")])
def test_picker_default_values_keep_graph_key_presence(compiler, value, storage_type):
    leaf = _attribute_filter(filter_type="text", operation="in", value=[value])
    leaf["filter_config"]["attribute_value_types"] = [storage_type]
    plan, = compiler([leaf])
    assert plan.raw_graph_value_witness_predicate == plan.raw_key_witness_predicate
    assert "mapValues(" not in plan.raw_graph_value_witness_predicate
    assert "latest_filter_param_0" in plan.seed_predicate


def test_picker_not_in_does_not_use_positive_value_index_hints():
    leaf = _attribute_filter(filter_type="text", operation="not_in", value=["K", 7, False])
    leaf["filter_config"]["attribute_value_types"] = ["string", "number", "boolean"]
    plan, = compile_span_filter_plans([leaf])
    assert plan.raw_witness_predicate == plan.raw_key_witness_predicate
    assert "mapValues(" not in plan.raw_witness_predicate
    assert plan.raw_graph_value_witness_predicate is None
    assert not any("index_" in key for key in plan.params)
