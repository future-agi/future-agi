"""Literal semantics for tracing text filters compiled to ClickHouse SQL."""

from __future__ import annotations

import pytest

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    build_literal_text_predicate,
)
from tracer.services.clickhouse.query_builders.latest_attributes import (
    build_latest_attribute_predicate,
    build_latest_root_column_predicate,
)
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)


def _filter(
    value,
    *,
    op: str = "contains",
    column_id: str = "customer.note",
    col_type: str = "SPAN_ATTRIBUTE",
) -> dict:
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": col_type,
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "sql_token"),
    [
        ("contains", "positionUTF8(lowerUTF8"),
        ("not_contains", "positionUTF8(lowerUTF8"),
        ("starts_with", "startsWith(lowerUTF8"),
        ("ends_with", "endsWith(lowerUTF8"),
    ],
)
@pytest.mark.parametrize(
    "needle",
    ["%", "_", "\\", "a%' OR 1 = 1 --_\\", "", "ÉLITE_東京%\\"],
)
def test_typed_map_text_operators_bind_literal_needle(op, sql_token, needle):
    where, params = ClickHouseFilterBuilder(
        table="spans",
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
    ).translate([_filter(needle, op=op)])

    assert sql_token in where
    assert " ILIKE " not in where
    assert " LIKE " not in where
    assert "OR 1 = 1" not in where
    assert params == {"attr_1": needle}
    if op == "not_contains":
        assert where.endswith("= 0")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "sql_token"),
    [
        ("contains", "positionUTF8(lowerUTF8"),
        ("not_contains", "positionUTF8(lowerUTF8"),
        ("starts_with", "startsWith(lowerUTF8"),
        ("ends_with", "endsWith(lowerUTF8"),
    ],
)
def test_generic_scalar_text_operators_are_literal_and_utf8_case_insensitive(
    op, sql_token
):
    needle = "ÉLITE_%\\'"
    where, params = ClickHouseFilterBuilder(table="spans").translate(
        [
            _filter(
                needle,
                op=op,
                column_id="model",
                col_type="SYSTEM_METRIC",
            )
        ]
    )

    assert sql_token in where
    assert needle not in where
    assert params == {"col_1": needle}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case_insensitive", "op", "sql"),
    [
        (False, "contains", "positionUTF8(toString(value)"),
        (False, "not_contains", "positionUTF8(toString(value)"),
        (False, "starts_with", "startsWith(toString(value)"),
        (False, "ends_with", "endsWith(toString(value)"),
        (True, "contains", "positionUTF8(lowerUTF8(toString(value))"),
        (True, "starts_with", "startsWith(lowerUTF8(toString(value))"),
        (True, "ends_with", "endsWith(lowerUTF8(toString(value))"),
    ],
)
def test_literal_predicate_helper_preserves_requested_case_contract(
    case_insensitive, op, sql
):
    predicate = build_literal_text_predicate(
        "value",
        "needle",
        op,
        case_insensitive=case_insensitive,
    )

    assert sql in predicate
    assert "%(needle)s" in predicate


@pytest.mark.unit
@pytest.mark.parametrize("op", ["contains", "not_contains", "starts_with", "ends_with"])
def test_literal_text_ops_cannot_fall_through_generic_sql_operator_map(op):
    assert ClickHouseFilterBuilder._sql_op(op) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("factory", "expected_param"),
    [
        (build_latest_attribute_predicate, "latest_attr_param_7"),
        (build_latest_root_column_predicate, "latest_root_value_param_7"),
    ],
)
@pytest.mark.parametrize(
    ("op", "sql_token"),
    [
        ("contains", "positionUTF8(lowerUTF8"),
        ("not_contains", "positionUTF8(lowerUTF8"),
        ("starts_with", "startsWith(lowerUTF8"),
        ("ends_with", "endsWith(lowerUTF8"),
    ],
)
def test_latest_state_predicates_bind_literal_needles(
    factory, expected_param, op, sql_token
):
    item = _filter("%_\\'É", op=op)
    if factory is build_latest_root_column_predicate:
        item = _filter(
            "%_\\'É",
            op=op,
            column_id="trace_name",
            col_type="SYSTEM_METRIC",
        )

    compiled = factory(item, index=7)

    assert sql_token in compiled.predicate
    assert "%_\\'É" not in compiled.predicate
    assert compiled.params == {expected_param: "%_\\'É"}


@pytest.mark.unit
def test_multi_value_exact_filter_keeps_wildcards_literal_and_parameterized():
    values = ["a%b", "c_d", "e\\f", "g'h"]
    where, params = ClickHouseFilterBuilder(table="spans").translate(
        [_filter(values, op="in", column_id="model", col_type="SYSTEM_METRIC")]
    )

    assert "lower(model) IN %(col_1)s" in where
    assert params == {"col_1": tuple(values)}
    assert all(value not in where for value in values)


@pytest.mark.unit
def test_v2_literal_match_has_no_ascii_only_index_companion():
    needle = "ab%_\\cd"
    where, params = ClickHouseFilterBuilderV2(
        table="spans",
        query_mode=ClickHouseFilterBuilderV2.QUERY_MODE_SPAN,
    ).translate([_filter(needle)])

    assert "positionUTF8(lowerUTF8" in where
    assert "arrayStringConcat" not in where
    assert "arrayMap(x -> lower(x), mapValues(attrs_string))" not in where
    assert params == {"attr_1": needle}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "sql_token"),
    [
        ("contains", "positionUTF8(lowerUTF8"),
        ("not_contains", "positionUTF8(lowerUTF8"),
        ("starts_with", "startsWith(lowerUTF8"),
        ("ends_with", "endsWith(lowerUTF8"),
    ],
)
@pytest.mark.parametrize(
    "needle",
    ["%", "_", "\\", "a%' OR 1 = 1 --_\\", "", "ÉLITE_東京%\\"],
)
def test_user_list_text_operators_share_literal_utf8_semantics(op, sql_token, needle):
    clause, params = UserListQueryBuilder._condition(
        column="user_id",
        op=op,
        value=needle,
        prefix="user_filter_0",
    )

    assert clause is not None
    assert sql_token in clause
    assert "OR 1 = 1" not in clause
    assert params == {"user_filter_0": needle}


@pytest.mark.unit
@pytest.mark.parametrize(
    "needle",
    ["%", "_", "\\", "O'Reilly", "a%' OR 1 = 1 --", "ÉLITE_東京%\\"],
)
def test_user_list_search_binds_literal_utf8_needle(needle):
    query, params = UserListQueryBuilder(
        organization_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        filters=[],
        search=needle,
        limit=25,
        offset=0,
    ).build()

    assert (
        "positionUTF8(lowerUTF8(toString(user_id)), "
        "lowerUTF8(toString(%(search)s))) > 0" in query
    )
    assert "OR 1 = 1" not in query
    assert params["search"] == needle
