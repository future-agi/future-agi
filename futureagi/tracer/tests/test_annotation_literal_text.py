"""Annotation text is literal data, including LIKE metacharacters."""

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize("mode", ["trace", "span"])
@pytest.mark.parametrize("needle", ["100%", "a_b", r"a\b", "Équipe_50%"])
@pytest.mark.parametrize(
    "op,function",
    [
        ("contains", "positionUTF8"),
        ("not_contains", "positionUTF8"),
        ("starts_with", "startsWith"),
        ("ends_with", "endsWith"),
    ],
)
def test_annotation_text_never_interprets_like_wildcards(
    builder_cls, mode, needle, op, function
):
    builder = builder_cls(
        project_id="11111111-1111-4111-8111-111111111111", query_mode=mode
    )
    sql, params = builder.translate(
        [
            {
                "column_id": "22222222-2222-4222-8222-222222222222",
                "filter_config": {
                    "col_type": "ANNOTATION",
                    "filter_type": "text",
                    "filter_op": op,
                    "filter_value": needle,
                },
            }
        ]
    )
    assert needle in params.values()
    assert needle not in sql
    assert "ILIKE" not in sql and " LIKE " not in sql
    assert function in sql and "lowerUTF8" in sql
    assert "s.tracer_project_id" in sql
    assert "s._peerdb_is_deleted = 0" in sql
    assert "s.deleted = false" in sql
    assert "JSONExtractString(s.value, 'text') != ''" in sql
    if op == "not_contains":
        assert ") = 0" in sql


@pytest.mark.parametrize(
    "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize("mode", ["trace", "span"])
@pytest.mark.parametrize("op", ["equals", "not_equals", "in", "not_in"])
def test_annotation_text_comparisons_fold_both_operands_in_clickhouse(
    builder_cls, mode, op
):
    values = ["ÉQUIPE", "ΟΣ", "other_%"] if op in ("in", "not_in") else "ÉQUIPE"
    sql, params = builder_cls(
        project_id="11111111-1111-4111-8111-111111111111", query_mode=mode
    ).translate(
        [
            {
                "column_id": "22222222-2222-4222-8222-222222222222",
                "filter_config": {
                    "col_type": "ANNOTATION",
                    "filter_type": "text",
                    "filter_op": op,
                    "filter_value": values,
                },
            }
        ]
    )
    assert values in params.values()  # no Python lower()/lossy normalization
    assert "lower(" not in sql
    assert "lowerUTF8(JSONExtractString(s.value, 'text'))" in sql
    if op in ("in", "not_in"):
        assert "has(arrayMap(x -> lowerUTF8(x), %(ann_" in sql
        assert ("AND NOT has(" in sql) == (op == "not_in")
    else:
        assert "lowerUTF8(%(ann_" in sql


@pytest.mark.parametrize(
    "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize("mode", ["trace", "span"])
@pytest.mark.parametrize(
    "op,value",
    [
        ("equals", 0),
        ("not_equals", 4),
        ("greater_than", -1),
        ("greater_than_or_equal", 0),
        ("less_than", 1),
        ("less_than_or_equal", 0),
        ("between", [-1, 1]),
        ("not_between", [1, 4]),
        ("in", [0, 1]),
        ("not_in", [1, 2]),
    ],
)
def test_numeric_annotation_values_require_actual_json_numbers(
    builder_cls, mode, op, value
):
    sql, _ = builder_cls(
        project_id="11111111-1111-4111-8111-111111111111", query_mode=mode
    ).translate(
        [
            {
                "column_id": "22222222-2222-4222-8222-222222222222",
                "filter_config": {
                    "col_type": "ANNOTATION",
                    "filter_type": "number",
                    "filter_op": op,
                    "filter_value": value,
                },
            }
        ]
    )
    assert (
        "JSONType(s.value, if(JSONHas(s.value, 'rating'), 'rating', 'value')) "
        "IN ('Int64', 'UInt64', 'Double')"
    ) in sql
    assert "s.deleted = false" in sql
