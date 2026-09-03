"""Bounded latest-state coverage for structured span-attribute filters."""

from __future__ import annotations

from datetime import datetime

import pytest
from rest_framework import serializers

from tracer.serializers.filters import FilterListField
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
    compile_span_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
    rewrite_v1_sql_to_v2,
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
    key: str,
    *,
    filter_type: str,
    filter_op: str,
    filter_value: object | None = None,
) -> dict:
    config = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": filter_type,
        "filter_op": filter_op,
    }
    if filter_value is not None:
        config["filter_value"] = filter_value
    return {"column_id": key, "filter_config": config}


def test_serializer_and_compiler_preserve_mixed_typed_picker_values() -> None:
    payload = _attribute_filter(
        "attempt",
        filter_type="text",
        filter_op="in",
        filter_value=["1", 1, True],
    )
    payload["filter_config"]["attribute_value_types"] = [
        "string",
        "number",
        "boolean",
    ]

    validated = FilterListField().run_validation([payload])
    config = validated[0]["filter_config"]
    assert config["filter_value"] == ["1", 1.0, True]
    assert config["attribute_value_types"] == ["string", "number", "boolean"]

    where, params = ClickHouseFilterBuilderV2(query_mode="span").translate(validated)
    assert "mapContains(attrs_string, 'attempt')" in where
    assert "mapContains(attrs_number, 'attempt')" in where
    assert "mapContains(attrs_bool, 'attempt')" in where
    assert ("1",) in params.values()
    assert (1.0,) in params.values()
    assert (1,) in params.values()


def test_mixed_typed_not_in_negates_any_matching_representation() -> None:
    payload = _attribute_filter(
        "attempt",
        filter_type="text",
        filter_op="not_in",
        filter_value=["1", 1],
    )
    payload["filter_config"]["attribute_value_types"] = ["string", "number"]
    validated = FilterListField().run_validation([payload])

    where, _ = ClickHouseFilterBuilderV2(query_mode="span").translate(validated)
    assert "AND NOT" in where
    assert "mapContains(attrs_string, 'attempt')" in where
    assert "mapContains(attrs_number, 'attempt')" in where
    assert "NOT IN" not in where


@pytest.mark.parametrize(
    ("compiler_mode", "expected_scope"), [("span", "span"), ("trace", "any")]
)
def test_bounded_latest_compiler_preserves_mixed_typed_membership(
    compiler_mode: str, expected_scope: str
) -> None:
    payload = _attribute_filter(
        "attempt",
        filter_type="text",
        filter_op="in",
        filter_value=["ONE", 1, True],
    )
    payload["filter_config"]["attribute_value_types"] = [
        "string",
        "number",
        "boolean",
    ]
    validated = FilterListField().run_validation([payload])

    if compiler_mode == "span":
        plan = compile_span_filter_plans(validated)[0]
    else:
        from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
            compile_trace_filter_plans,
        )

        plan = compile_trace_filter_plans(validated)[0]

    aggregate_sql = " ".join(plan.aggregates)
    assert "span_attr_str" in aggregate_sql
    assert "span_attr_num" in aggregate_sql
    assert "span_attr_bool" in aggregate_sql
    assert "latest_attr_exists_0_string" in plan.predicate
    assert "latest_attr_exists_0_number" in plan.predicate
    assert "latest_attr_exists_0_boolean" in plan.predicate
    assert " OR " in plan.predicate
    assert plan.params["latest_filter_param_0_string"] == ("one",)
    assert plan.params["latest_filter_param_0_number"] == (1.0,)
    assert plan.params["latest_filter_param_0_boolean"] == (1,)
    assert plan.raw_witness_predicate is not None
    assert plan.raw_key_witness_predicate is not None
    assert plan.raw_graph_value_witness_predicate == plan.raw_witness_predicate
    assert plan.scope == expected_scope


def test_bounded_latest_mixed_not_in_requires_key_and_negates_all_types() -> None:
    payload = _attribute_filter(
        "attempt",
        filter_type="text",
        filter_op="not_in",
        filter_value=["1", 1],
    )
    payload["filter_config"]["attribute_value_types"] = ["string", "number"]
    validated = FilterListField().run_validation([payload])

    plan = compile_span_filter_plans(validated)[0]

    assert (
        "latest_attr_exists_0_string OR latest_attr_exists_0_number" in plan.predicate
    )
    assert "AND NOT" in plan.predicate
    assert plan.raw_witness_predicate is None
    assert plan.raw_key_witness_predicate is None
    assert plan.raw_graph_value_witness_predicate is None


@pytest.mark.parametrize(
    ("selected_value", "storage_type"),
    [(0, "number"), (False, "boolean")],
)
def test_bounded_latest_mixed_picker_keeps_key_only_graph_branch_when_default_matches(
    selected_value: object, storage_type: str
) -> None:
    payload = _attribute_filter(
        "attempt",
        filter_type="text",
        filter_op="in",
        filter_value=[selected_value],
    )
    payload["filter_config"]["attribute_value_types"] = [storage_type]
    validated = FilterListField().run_validation([payload])

    plan = compile_span_filter_plans(validated)[0]

    assert plan.raw_witness_predicate is not None
    assert plan.raw_key_witness_predicate is not None
    assert plan.raw_graph_value_witness_predicate == plan.raw_key_witness_predicate
    assert "latest_filter_param_0" not in plan.raw_graph_value_witness_predicate


def test_bounded_latest_mixed_picker_narrows_safe_branch_beside_default_branch() -> (
    None
):
    payload = _attribute_filter(
        "attempt",
        filter_type="text",
        filter_op="in",
        filter_value=["selected prompt", 0],
    )
    payload["filter_config"]["attribute_value_types"] = ["string", "number"]
    validated = FilterListField().run_validation([payload])

    plan = compile_span_filter_plans(validated)[0]
    graph_witness = plan.raw_graph_value_witness_predicate

    assert graph_witness is not None
    assert "latest_filter_param_0_string" in graph_witness
    assert "span_attr_str[%(latest_filter_key_0)s]" in graph_witness
    assert "has(span_attr_num.keys, %(latest_filter_key_0)s)" in graph_witness
    assert "latest_filter_param_0_number" not in graph_witness


@pytest.mark.parametrize(
    "types,values,error",
    [
        (["string"], ["1", 1], "one-for-one"),
        (["number"], [True], "finite numbers"),
        (["boolean"], ["true"], "true or false"),
        (["array"], ["x"], "string, number, boolean"),
    ],
)
def test_serializer_rejects_invalid_typed_picker_provenance(
    types: list[str], values: list[object], error: str
) -> None:
    payload = _attribute_filter(
        "attempt", filter_type="text", filter_op="in", filter_value=values
    )
    payload["filter_config"]["attribute_value_types"] = types

    with pytest.raises(serializers.ValidationError, match=error):
        FilterListField().run_validation([payload])


@pytest.mark.parametrize("filter_type", ["array", "list", "json"])
def test_serializer_accepts_and_canonicalizes_proven_array_shapes(
    filter_type: str,
) -> None:
    validated = FilterListField().run_validation(
        [
            _attribute_filter(
                "customer.tags",
                filter_type=filter_type,
                filter_op="contains",
                filter_value=["vip", 3.5, True],
            )
        ]
    )

    assert validated[0]["filter_config"]["filter_type"] == "array"
    assert validated[0]["filter_config"]["filter_value"] == ["vip", 3.5, True]


@pytest.mark.parametrize("filter_type", ["json", "map", "object"])
def test_serializer_canonicalizes_flat_object_shapes_to_map(
    filter_type: str,
) -> None:
    value = {"tier": "vip", "attempt": 2, "accepted": True}

    validated = FilterListField().run_validation(
        [
            _attribute_filter(
                "customer.context",
                filter_type=filter_type,
                filter_op="contains",
                filter_value=value,
            )
        ]
    )

    config = validated[0]["filter_config"]
    assert config["filter_type"] == "map"
    # Canonical member ordering is deterministic and does not alter values.
    assert list(config["filter_value"]) == ["accepted", "attempt", "tier"]
    assert config["filter_value"] == value


@pytest.mark.parametrize(
    "filter_op", ["equals", "not_equals", "contains", "not_contains"]
)
def test_serializer_accepts_finite_map_value_operations(filter_op: str) -> None:
    validated = FilterListField().run_validation(
        [
            _attribute_filter(
                "customer.context",
                filter_type="map",
                filter_op=filter_op,
                filter_value={"tier": "vip"},
            )
        ]
    )

    assert validated[0]["filter_config"]["filter_type"] == "map"


@pytest.mark.parametrize("filter_op", ["is_null", "is_not_null"])
def test_serializer_accepts_explicit_map_existence_operations(filter_op: str) -> None:
    validated = FilterListField().run_validation(
        [
            _attribute_filter(
                "customer.context",
                filter_type="map",
                filter_op=filter_op,
            )
        ]
    )

    assert validated[0]["filter_config"]["filter_type"] == "map"


@pytest.mark.parametrize(
    "filter_value,error",
    [
        ({}, "non-empty object"),
        ({"nested": {"tier": "vip"}}, "Nested JSON map"),
        ({"nested": ["vip"]}, "Nested JSON map"),
        ({"nullable": None}, "non-null JSON scalars"),
        ({"score": float("inf")}, "valid JSON|finite"),
        ({"id": 1 << 64}, "Int64 or UInt64"),
        ({f"key-{index}": index for index in range(33)}, "at most 32"),
        ({"k" * 1_025: "value"}, "1024 UTF-8 byte limit"),
        ({f"{index}-" + "k" * 511: index for index in range(32)}, "16384"),
        ({"key": "v" * 4_097}, "4096 UTF-8 byte limit"),
        ({f"key-{index}": "v" * 4_096 for index in range(17)}, "65536"),
        ({"bad\u0000key": "value"}, "control characters"),
    ],
)
def test_serializer_rejects_nested_or_oversized_map_shapes(
    filter_value: object,
    error: str,
) -> None:
    with pytest.raises(serializers.ValidationError, match=error):
        FilterListField().run_validation(
            [
                _attribute_filter(
                    "customer.context",
                    filter_type="map",
                    filter_op="contains",
                    filter_value=filter_value,
                )
            ]
        )


def test_map_filter_type_is_scoped_to_span_attributes() -> None:
    payload = _attribute_filter(
        "context",
        filter_type="map",
        filter_op="equals",
        filter_value={"tier": "vip"},
    )
    payload["filter_config"]["col_type"] = "SYSTEM_METRIC"

    with pytest.raises(serializers.ValidationError, match="Unsupported filter_type"):
        FilterListField().run_validation([payload])


@pytest.mark.parametrize(
    "filter_op,filter_value,error",
    [
        ("equals", ["vip"], "Unsupported filter_op"),
        ("contains", "vip", "non-empty list"),
        ("contains", [], "non-empty list"),
        ("contains", [{"tier": "vip"}], "Nested JSON"),
        ("contains", [["vip"]], "Nested JSON"),
        ("contains", [None], "non-empty JSON scalars"),
        ("contains", [float("inf")], "valid JSON|finite"),
        ("contains", [1 << 64], "Int64 or UInt64"),
        ("contains", [-(1 << 63) - 1], "Int64 or UInt64"),
        ("contains", [str(index) for index in range(65)], "at most 64"),
        ("contains", ["x" * 4_097], "at most 4096"),
        ("contains", ["x" * 4_096] * 17, "65536"),
    ],
)
def test_serializer_fails_closed_for_unproven_array_shapes(
    filter_op: str,
    filter_value: object,
    error: str,
) -> None:
    with pytest.raises(serializers.ValidationError, match=error):
        FilterListField().run_validation(
            [
                _attribute_filter(
                    "customer.tags",
                    filter_type="json",
                    filter_op=filter_op,
                    filter_value=filter_value,
                )
            ]
        )


def test_compiler_binds_array_key_and_mixed_scalar_members() -> None:
    plan = compile_span_filter_plans(
        [
            _attribute_filter(
                "customer.tags'quoted",
                filter_type="json",
                filter_op="contains",
                filter_value=["vip", 3.5, True],
            )
        ]
    )[0]

    sql = " ".join((*plan.aggregates, plan.predicate, plan.seed_predicate))
    assert plan.scope == "span"
    assert "JSONHas(span_attributes_raw, %(latest_filter_key_0)s)" in sql
    assert "JSONExtractArrayRaw(span_attributes_raw, %(latest_filter_key_0)s)" in sql
    assert (
        "toString(JSONType(span_attributes_raw, %(latest_filter_key_0)s)) = 'Array'"
        in sql
    )
    assert "JSONExtractString(latest_json_item_0)" in sql
    assert "JSONExtractFloat(latest_json_item_0)" in sql
    assert "JSONExtractBool(latest_json_item_0)" in sql
    assert "customer.tags'quoted" not in sql
    assert "vip" not in sql
    assert plan.params == {
        "latest_filter_key_0": "customer.tags'quoted",
        "latest_filter_json_0_string": ("vip",),
        "latest_filter_json_0_boolean": (1,),
        "latest_filter_json_0_number": (3.5,),
    }


def test_compiler_binds_map_paths_values_and_never_parses_the_seed() -> None:
    plan = compile_span_filter_plans(
        [
            _attribute_filter(
                "customer.context'quoted",
                filter_type="json",
                filter_op="contains",
                filter_value={
                    "tier'quoted": "vip",
                    "attempt": 2,
                    "accepted": True,
                },
            )
        ]
    )[0]

    sql = " ".join((*plan.aggregates, plan.predicate))
    assert plan.scope == "span"
    assert plan.seed_predicate == "1 = 1"
    assert "JSONExtractRaw(span_attributes_raw, %(latest_filter_key_0)s)" in sql
    assert "= 'Object'" in sql
    assert "JSONHas(latest_json_map_value_0, %(" in sql
    assert "JSONExtractString(latest_json_map_value_0, %(" in sql
    assert "JSONExtractInt(latest_json_map_value_0, %(" in sql
    assert "JSONExtractFloat(latest_json_map_value_0, %(" in sql
    assert "JSONExtractBool(latest_json_map_value_0, %(" in sql
    assert "JSONLength(" not in sql
    for literal in (
        "customer.context'quoted",
        "tier'quoted",
        "vip",
    ):
        assert literal not in sql
        assert literal in plan.params.values()


@pytest.mark.parametrize(
    "filter_op,required_sql",
    [
        ("contains", "latest_json_map_exists_0 AND"),
        ("not_contains", "NOT ("),
        ("equals", "JSONLength(latest_json_map_value_0) = 2"),
        ("not_equals", "NOT (JSONLength(latest_json_map_value_0) = 2"),
    ],
)
def test_compiler_exposes_finite_flat_object_semantics(
    filter_op: str,
    required_sql: str,
) -> None:
    plan = compile_span_filter_plans(
        [
            _attribute_filter(
                "customer.context",
                filter_type="map",
                filter_op=filter_op,
                filter_value={"tier": "vip", "attempt": 2},
            )
        ]
    )[0]

    assert plan.predicate.startswith("latest_json_map_exists_0 AND")
    assert required_sql in plan.predicate
    assert plan.predicate.count("JSONHas(latest_json_map_value_0") == 2


@pytest.mark.parametrize(
    "filter_op,predicate",
    [
        ("is_null", "NOT latest_json_map_exists_0"),
        ("is_not_null", "latest_json_map_exists_0"),
    ],
)
def test_map_null_ops_are_object_type_aware(filter_op: str, predicate: str) -> None:
    plan = compile_span_filter_plans(
        [
            _attribute_filter(
                "customer.context",
                filter_type="map",
                filter_op=filter_op,
            )
        ]
    )[0]

    assert plan.predicate == predicate
    assert plan.seed_predicate == "1 = 1"
    assert plan.params == {"latest_filter_key_0": "customer.context"}


@pytest.mark.parametrize(
    "filter_value",
    [
        {},
        {"nested": {"tier": "vip"}},
        {"nested": ["vip"]},
        {"nullable": None},
        {"score": float("nan")},
        {"id": 1 << 64},
        {f"key-{index}": index for index in range(33)},
    ],
)
def test_compiler_fails_closed_for_invalid_map_shapes(filter_value: object) -> None:
    with pytest.raises(UnsupportedFilterShapeError):
        compile_span_filter_plans(
            [
                _attribute_filter(
                    "customer.context",
                    filter_type="map",
                    filter_op="contains",
                    filter_value=filter_value,
                )
            ]
        )


def test_compiler_preserves_large_json_integer_precision() -> None:
    signed = 9_007_199_254_740_993
    negative = -9_007_199_254_740_993
    unsigned = (1 << 64) - 1
    plan = compile_span_filter_plans(
        [
            _attribute_filter(
                "customer.ids",
                filter_type="array",
                filter_op="contains",
                filter_value=[signed, negative, unsigned],
            )
        ]
    )[0]

    assert "JSONExtractInt(latest_json_item_0)" in plan.predicate
    assert "JSONExtractUInt(latest_json_item_0)" in plan.predicate
    assert "JSONExtractFloat(latest_json_item_0)" not in plan.predicate
    assert plan.params["latest_filter_json_0_integer"] == (signed, negative)
    assert plan.params["latest_filter_json_0_unsigned_integer"] == (unsigned,)


def test_compiler_matches_js_safe_integer_against_int_or_double_json_literal() -> None:
    plan = compile_span_filter_plans(
        [
            _attribute_filter(
                "customer.ids",
                filter_type="array",
                filter_op="contains",
                filter_value=[1],
            )
        ]
    )[0]

    assert "JSONExtractInt(latest_json_item_0)" in plan.predicate
    assert "JSONExtractFloat(latest_json_item_0)" in plan.predicate
    assert plan.params["latest_filter_json_0_integer"] == (1,)
    assert plan.params["latest_filter_json_0_number"] == (1.0,)


@pytest.mark.parametrize("value", [1 << 64, -(1 << 63) - 1])
def test_compiler_rejects_json_integer_outside_clickhouse_range(value: int) -> None:
    with pytest.raises(UnsupportedFilterShapeError, match="Int64 or UInt64"):
        compile_span_filter_plans(
            [
                _attribute_filter(
                    "customer.ids",
                    filter_type="array",
                    filter_op="contains",
                    filter_value=[value],
                )
            ]
        )


@pytest.mark.parametrize(
    "values,error",
    [
        ([str(index) for index in range(65)], "at most 64"),
        (["x" * 4_097], "UTF-8 byte limit"),
        (["x" * 4_096] * 17, "request byte limit"),
    ],
)
def test_compiler_caps_json_membership_set_size(values: list[str], error: str) -> None:
    with pytest.raises(UnsupportedFilterShapeError, match=error):
        compile_span_filter_plans(
            [
                _attribute_filter(
                    "customer.ids",
                    filter_type="array",
                    filter_op="contains",
                    filter_value=values,
                )
            ]
        )


def test_array_not_contains_requires_latest_array_to_exist() -> None:
    plan = compile_span_filter_plans(
        [
            _attribute_filter(
                "customer.tags",
                filter_type="array",
                filter_op="not_contains",
                filter_value=["blocked", False],
            )
        ]
    )[0]

    assert plan.predicate.startswith("latest_json_array_exists_0 AND")
    assert "NOT (arrayExists(" in plan.predicate
    assert plan.predicate.count("arrayExists(") == 1
    assert plan.seed_predicate == "1 = 1"


@pytest.mark.parametrize(
    "filter_op,predicate",
    [
        ("is_null", "NOT latest_json_array_exists_0"),
        ("is_not_null", "latest_json_array_exists_0"),
    ],
)
def test_array_null_ops_use_latest_type_aware_existence(
    filter_op: str,
    predicate: str,
) -> None:
    plan = compile_span_filter_plans(
        [
            _attribute_filter(
                "customer.tags",
                filter_type="array",
                filter_op=filter_op,
            )
        ]
    )[0]

    assert plan.predicate == predicate
    assert plan.seed_predicate == "1 = 1"
    assert plan.params == {"latest_filter_key_0": "customer.tags"}


@pytest.mark.parametrize(
    "filter_op,filter_value",
    [
        ("equals", ["vip"]),
        ("contains", "vip"),
        ("contains", []),
        ("contains", [{"tier": "vip"}]),
        ("contains", [["vip"]]),
        ("contains", [float("nan")]),
    ],
)
def test_compiler_fails_closed_for_unproven_array_shapes(
    filter_op: str,
    filter_value: object,
) -> None:
    with pytest.raises(UnsupportedFilterShapeError):
        compile_span_filter_plans(
            [
                _attribute_filter(
                    "customer.tags",
                    filter_type="array",
                    filter_op=filter_op,
                    filter_value=filter_value,
                )
            ]
        )


def test_v2_span_query_mixes_array_and_all_typed_maps_under_latest_identity() -> None:
    filters = [
        _time_filter(),
        _attribute_filter(
            "customer.tags",
            filter_type="array",
            filter_op="contains",
            filter_value=["vip"],
        ),
        _attribute_filter(
            "final_status",
            filter_type="text",
            filter_op="equals",
            filter_value="Rejected",
        ),
        _attribute_filter(
            "score",
            filter_type="number",
            filter_op="greater_than",
            filter_value=0.75,
        ),
        _attribute_filter(
            "reviewed",
            filter_type="boolean",
            filter_op="equals",
            filter_value=True,
        ),
    ]
    builder = SpanListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    assert builder.supports_bounded_filter_scan() is True
    sql, params = builder.build_filter_match_query(["span-a"])

    assert "attributes_extra" in sql
    assert "span_attributes_raw" not in sql
    assert "attrs_string" in sql
    assert "attrs_number" in sql
    assert "attrs_bool" in sql
    assert "argMax(" in sql
    assert "_version" in sql
    assert "argMax(is_deleted, _version)" in sql
    assert "candidate_span_ids" in sql
    for literal in ("customer.tags", "vip", "final_status", "Rejected"):
        assert literal not in sql
    assert params["candidate_span_ids"] == ("span-a",)
    assert params["latest_filter_key_0"] == "customer.tags"
    assert params["latest_filter_json_0_string"] == ("vip",)
    assert params["latest_filter_key_1"] == "final_status"
    assert params["latest_filter_param_1"] == "rejected"


def test_v2_span_query_composes_map_array_typed_map_and_date_filters() -> None:
    filters = [
        _time_filter(),
        _attribute_filter(
            "customer.context",
            filter_type="map",
            filter_op="contains",
            filter_value={"tier": "vip", "attempt": 2},
        ),
        _attribute_filter(
            "customer.tags",
            filter_type="array",
            filter_op="contains",
            filter_value=["priority"],
        ),
        _attribute_filter(
            "final_status",
            filter_type="text",
            filter_op="equals",
            filter_value="Rejected",
        ),
    ]
    builder = SpanListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    assert builder.supports_bounded_filter_scan() is True
    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=50,
    )
    match_sql, params = builder.build_filter_match_query(["span-a"])

    assert "attributes_extra" not in seed_sql
    assert "JSON" not in seed_sql
    assert not any(key.startswith("latest_filter_map") for key in seed_params)
    assert "JSONExtractRaw(attributes_extra" in match_sql
    assert "JSONExtractArrayRaw(attributes_extra" in match_sql
    assert "attrs_string" in match_sql
    assert match_sql.count(" AND ") >= 5
    assert params["candidate_span_ids"] == ("span-a",)


def test_v2_trace_query_replays_map_only_for_candidate_traces() -> None:
    filters = [
        _time_filter(),
        _attribute_filter(
            "customer.context",
            filter_type="map",
            filter_op="equals",
            filter_value={"tier": "vip"},
        ),
    ]
    builder = TraceListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=50,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    assert "attributes_extra" not in seed_sql
    assert "latest_filter_key_0" not in seed_params
    assert "JSONExtractRaw(attributes_extra" in match_sql
    assert "candidate_trace_ids" in match_sql
    assert match_params["candidate_trace_ids"] == ("trace-a",)


def test_v2_span_seed_never_parses_unindexed_json_overflow() -> None:
    filters = [
        _time_filter(),
        _attribute_filter(
            "customer.tags",
            filter_type="array",
            filter_op="contains",
            filter_value=["vip"],
        ),
    ]
    builder = SpanListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    sql, params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=50,
    )

    assert "attributes_extra" not in sql
    assert "JSONType" not in sql
    assert "JSONExtract" not in sql
    assert "latest_filter_key_0" not in params


def test_trace_array_filter_is_replayed_only_for_candidate_traces() -> None:
    filters = [
        _time_filter(),
        _attribute_filter(
            "customer.tags",
            filter_type="array",
            filter_op="contains",
            filter_value=["vip"],
        ),
    ]
    builder = TraceListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=50,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    assert "attributes_extra" not in seed_sql
    assert "latest_filter_key_0" not in seed_params
    assert "attributes_extra" in match_sql
    assert "candidate_trace_ids" in match_sql
    assert match_params["candidate_trace_ids"] == ("trace-a",)
    assert match_params["latest_filter_key_0"] == "customer.tags"


def test_v2_rewriter_retargets_structured_json_functions() -> None:
    sql = rewrite_v1_sql_to_v2(
        "SELECT JSONType(span_attributes_raw, %(key)s), "
        "JSONExtractArrayRaw(span_attributes_raw, %(key)s), "
        "JSONExtractRaw(span_attributes_raw, %(key)s) FROM spans"
    )

    assert "JSONType(attributes_extra, %(key)s)" in sql
    assert "JSONExtractArrayRaw(attributes_extra, %(key)s)" in sql
    assert "JSONExtractRaw(attributes_extra, %(key)s)" in sql
    assert "span_attributes_raw" not in sql
