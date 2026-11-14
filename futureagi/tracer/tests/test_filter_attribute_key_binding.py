"""Customer attribute keys are data, not ClickHouse SQL identifiers."""

import json
import re
from copy import deepcopy

import pytest
from clickhouse_connect.driver.binding import finalize_query
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_exact_graph_filter_predicates,
    compile_span_attribute_row_predicate,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "7189765c-d0bc-4a8a-a82a-bc344c1e4eac"
FAILED_KEY = 'e2e-obs7-0-mtu6gzdl.客户,"\\key'
DATA_KEYS = (
    FAILED_KEY,
    "key'] OR 1=1 --",
    "key\\'); DROP TABLE spans; --",
    "%(project_id)s {inject:String} ' /* comment */",
)
EXACT_KEYS = (
    "control\x00key",
    "line\nkey",
    "tab\tkey",
    " key \t\n",
    "zero\u200bwidth",
    "\u2066ID\u2069",
)


def leaf(key=FAILED_KEY, kind="text", op="in", value=None, types=None):
    config = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": kind,
        "filter_op": op,
        "filter_value": value,
    }
    if types is not None:
        config["attribute_value_types"] = types
    return {
        "column_id": key,
        "property_id": f"custom_attribute:{key}",
        "filter_config": config,
    }


def assert_bound_key(sql, params, key):
    names = [name for name, value in params.items() if value == key]
    assert names
    assert key not in sql
    assert all(f"%({name})s" in sql for name in names)
    bindings = {"project_id": PROJECT, **params}
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= bindings.keys()
    # Both installed production driver formatters must keep the entire key in
    # one escaped literal, including backslashes, quotes and %-looking syntax.
    literal = "'" + key.replace("\\", "\\\\").replace("'", "\\'") + "'"
    for rendered in (
        sql % escape_params(bindings, None),
        finalize_query(sql, bindings),
    ):
        assert literal in rendered


@pytest.mark.parametrize(
    "builder", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize("mode", ["span", "trace"])
@pytest.mark.parametrize("key", DATA_KEYS)
def test_typed_picker_customer_key_is_bound(builder, mode, key):
    item = leaf(key, value=["00123"], types=["string"])
    original = deepcopy(item)
    sql, params = builder(project_id=PROJECT, query_mode=mode).translate([item])
    assert_bound_key(sql, params, key)
    assert ("00123",) in params.values()  # Never coerced to the number 123.
    assert "lowerUTF8(toString(" in sql and " IN %(attr_" in sql
    assert "mapContains(" in sql
    if mode == "trace":
        assert "trace_id IN (SELECT trace_id FROM spans" in sql
        assert "project_id = %(project_id)s" in sql
    else:
        assert "SELECT" not in sql
    assert item == original


def test_ascii_key_and_value_are_bound_without_changing_predicate_semantics():
    sql, params = ClickHouseFilterBuilder(query_mode="span").translate(
        [leaf("company_id", value=["00123"], types=["string"])]
    )
    assert sql == (
        "((mapContains(span_attr_str, %(attr_key_1)s) AND "
        "lowerUTF8(toString(span_attr_str[%(attr_key_1)s])) IN %(attr_2)s))"
    )
    assert params == {"attr_key_1": "company_id", "attr_2": ("00123",)}


@pytest.mark.parametrize(
    "key",
    [
        "span_attr_str",
        "span_attr_num",
        "span_attr_bool",
        "_peerdb_version",
        "_peerdb_is_deleted",
        "enduser_dict",
        "trace_session_dict",
    ],
)
def test_schema_named_customer_key_is_not_rewritten(key):
    sql, params = ClickHouseFilterBuilderV2(query_mode="span").translate(
        [leaf(key, op="equals", value="00123")]
    )
    assert_bound_key(sql, params, key)


@pytest.mark.parametrize("op", ["in", "not_in"])
def test_mixed_scalar_families_and_fallback_type_remain_distinct(op):
    item = leaf(
        op=op,
        value=["00123", 123, True, False, "Other"],
        types=["string", "number", "boolean", "boolean", None],
    )
    sql, params = ClickHouseFilterBuilderV2(query_mode="span").translate([item])
    assert_bound_key(sql, params, FAILED_KEY)
    for family in ("attrs_string", "attrs_number", "attrs_bool"):
        assert f"mapContains({family}, %(attr_key_" in sql
        assert f"{family}[%(attr_key_" in sql
    assert ("00123", "other") in params.values()
    assert any(
        type(v) is tuple and v == (123.0,) and type(v[0]) is float
        for v in params.values()
    )
    assert any(
        type(v) is tuple and v == (1, 0) and all(type(x) is int for x in v)
        for v in params.values()
    )
    assert ("AND NOT" in sql) == (op == "not_in")
    assert " NOT IN " not in sql  # Negate the union, not each typed family.


@pytest.mark.parametrize(
    "kind,op,value",
    [
        ("text", "equals", "00123"),
        ("text", "not_contains", "A%_B"),
        ("number", "greater_than", 1.25),
        ("number", "not_between", [0, 2]),
        ("boolean", "equals", False),
        ("text", "is_null", None),
        ("number", "is_not_null", None),
        ("boolean", "is_null", None),
    ],
)
def test_homogeneous_scalar_and_null_presence_bind_the_same_key(kind, op, value):
    sql, params = ClickHouseFilterBuilderV2(query_mode="span").translate(
        [leaf(kind=kind, op=op, value=value)]
    )
    assert_bound_key(sql, params, FAILED_KEY)
    assert "mapContains(" in sql
    if op == "is_null":
        assert sql.startswith("NOT mapContains(")
    if op == "not_contains":
        assert "positionUTF8(" in sql and "LIKE" not in sql
        assert "A%_B" in params.values()


@pytest.mark.parametrize("op", ["is_null", "is_not_null"])
def test_trace_null_presence_retains_latest_live_membership(op):
    sql, params = ClickHouseFilterBuilderV2(
        project_id=PROJECT, query_mode="trace"
    ).translate([leaf(op=op)])
    assert_bound_key(sql, params, FAILED_KEY)
    assert f"trace_id {'NOT IN' if op == 'is_null' else 'IN'} (" in sql
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in sql
    assert "latest_is_deleted = 0 AND latest_attribute_match = 1" in sql
    assert "GROUP BY project_id, trace_id, id, start_time" in sql


@pytest.mark.parametrize(
    "kind,op,value",
    [
        ("array", "contains", ["00123", 123, True]),
        ("map", "contains", {"quoted'\\member": "00123", "flag": False}),
        ("json", "contains", {"flag": False, "number": 123}),
        ("array", "is_null", None),
        ("map", "is_not_null", None),
    ],
)
@pytest.mark.parametrize("key", ["company_id", FAILED_KEY])
def test_existing_structured_json_compiler_is_unchanged(kind, op, value, key):
    sql, params = compile_span_attribute_row_predicate(leaf(key, kind, op, value))
    assert_bound_key(sql, params, key)
    assert "JSON" in sql


def test_multiple_leaves_have_no_key_or_value_parameter_collision():
    items = [
        leaf(key, value=[str(i)], types=["string"]) for i, key in enumerate(DATA_KEYS)
    ]
    sql, params = ClickHouseFilterBuilderV2(query_mode="span").translate(items)
    for key in DATA_KEYS:
        assert_bound_key(sql, params, key)
    assert len([name for name in params if name.startswith("attr_key_")]) == len(
        DATA_KEYS
    )


@pytest.mark.parametrize("key", ["", "\ud800", "x" * 4097])
@pytest.mark.parametrize("mixed", [False, True])
def test_invalid_customer_keys_still_fail_closed(key, mixed):
    builder = ClickHouseFilterBuilder(query_mode="span")
    with pytest.raises(ValueError):
        if mixed:
            builder._build_mixed_span_attr_condition(
                key, "text", "in", ["x"], ["string"]
            )
        else:
            builder._build_span_attr_condition(key, "text", "equals", "x")


@pytest.mark.parametrize("key", EXACT_KEYS)
@pytest.mark.parametrize("kind,value", [("text", "00123"), ("array", ["00123"])])
def test_observed_keys_retain_exact_bytes_in_graph_and_latest_filters(key, kind, value):
    # The live/backfill extractor explicitly retains these valid UTF-8 keys.
    # They are bound map/JSON keys, never SQL identifiers or interpolated text.
    item = leaf(key, kind=kind, op="contains", value=value)
    for sql, params in (
        compile_exact_graph_filter_predicates(
            [item], project_id=PROJECT, observe_type="span"
        ),
        compile_span_attribute_row_predicate(item),
    ):
        bound = [name for name, stored in params.items() if stored == key]
        assert bound and key not in sql
        assert all(f"%({name})s" in sql for name in bound)


@pytest.mark.parametrize(
    "kind,op,value,types",
    [
        ("text", "equals", ["x"], ["string"]),
        ("text", "in", ["x"], []),
        ("text", "in", ["true"], ["boolean"]),
        ("text", "in", [1], ["json"]),
        ("number", "contains", 1, None),
    ],
)
def test_existing_invalid_type_and_operator_contracts_remain_rejected(
    kind, op, value, types
):
    with pytest.raises(ValueError):
        ClickHouseFilterBuilderV2(query_mode="span").translate(
            [leaf(kind=kind, op=op, value=value, types=types)]
        )


def test_normal_column_and_sort_identifiers_remain_strict():
    builder = ClickHouseFilterBuilder(query_mode="span")
    for key in (*DATA_KEYS, *EXACT_KEYS):
        item = leaf(key, op="equals", value="x")
        item["filter_config"]["col_type"] = "NORMAL"
        with pytest.raises(ValueError, match="Invalid attribute key"):
            builder.translate([item])
        assert builder.translate_sort([{"column_id": key}]) == ""


def test_structured_json_null_member_remains_unsupported():
    with pytest.raises(ValueError, match="non-null JSON scalars"):
        compile_span_attribute_row_predicate(
            leaf(kind="map", op="contains", value={"null": None})
        )


def test_numeric_null_picker_value_remains_rejected():
    with pytest.raises(TypeError, match="float"):
        ClickHouseFilterBuilderV2(query_mode="span").translate(
            [leaf(value=[None], types=["number"])]
        )


@pytest.mark.parametrize("op,counts", [("in", ["> 0"]), ("not_in", ["> 0", "= 0"])])
def test_user_membership_keeps_independent_positive_and_forbidden_witnesses(op, counts):
    from tracer.services.clickhouse.exact_graph_reads import _user_membership_having

    items = [
        leaf(op=op, value=["00123", 123, True], types=["string", "number", "boolean"])
    ]
    rows, having, params = _user_membership_having(items, project_id=PROJECT)
    assert having == " AND ".join(
        f"countIf(user_member_match_{index}) {count}"
        for index, count in enumerate(counts)
    )
    assert_bound_key(" ".join(rows), params, FAILED_KEY)
    assert len(rows) == len(counts)
    assert all("SELECT" not in row for row in rows)
    if op == "not_in":
        assert "AND NOT" in rows[0] and "AND NOT" not in rows[1]
        assert any(
            name.startswith("user_member_0_forbidden_attr_key_") for name in params
        )


def test_compound_user_membership_parameters_do_not_overwrite_sibling_keys():
    from tracer.services.clickhouse.exact_graph_reads import _user_membership_having

    items = [
        leaf(key, op="not_in", value=[str(i)], types=["string"])
        for i, key in enumerate(DATA_KEYS)
    ]
    original = deepcopy(items)
    rows, having, params = _user_membership_having(items, project_id=PROJECT)
    sql = " ".join(rows)
    for index, key in enumerate(DATA_KEYS):
        assert params[f"user_member_{index}_attr_key_1"] == key
        assert params[f"user_member_{index}_forbidden_attr_key_1"] == key
        assert params[f"user_member_{index}_attr_2"] == (str(index),)
        assert_bound_key(sql, params, key)
    assert len(rows) == 2 * len(items)
    assert having.count("= 0") == len(items)
    assert items == original


@pytest.fixture(scope="module")
def local_engine():
    # Optional constant-only engine proof; no server, tables, DDL or provider.
    return pytest.importorskip("chdb", reason="explicit offline chDB runtime required")


@pytest.mark.parametrize("key", DATA_KEYS)
@pytest.mark.parametrize(
    "kind,op,value,stored,present,matched",
    [
        ("number", "equals", 123, 123, True, 1),
        ("number", "equals", 123, 124, True, 0),
        ("number", "equals", 0, 0, False, 0),
        ("boolean", "equals", False, 0, True, 1),
        ("boolean", "equals", False, 0, False, 0),
        ("number", "is_null", None, 0, False, 1),
        ("number", "is_null", None, 0, True, 0),
    ],
)
def test_bound_numeric_boolean_and_null_predicates_execute_offline(
    local_engine, key, kind, op, value, stored, present, matched
):
    predicate, params = ClickHouseFilterBuilderV2(query_mode="span").translate(
        [leaf(key, kind, op, value)]
    )
    family = "attrs_number" if kind == "number" else "attrs_bool"
    map_type = "Map(String, Float64)" if kind == "number" else "Map(String, UInt8)"
    query = (
        f"SELECT toUInt8({predicate}) AS matched FROM (SELECT "
        f"CAST(mapFromArrays(%(fixture_keys)s, %(fixture_values)s), '{map_type}') "
        f"AS {family}) SETTINGS max_threads=1, max_execution_time=5"
    )
    params.update(
        fixture_keys=[key] if present else [],
        fixture_values=[stored] if present else [],
    )
    rendered = finalize_query(query, params)
    assert json.loads(str(local_engine.query(rendered, "JSON")))["data"] == [
        {"matched": matched}
    ]


@pytest.fixture(scope="module")
def utf8_engine(local_engine):
    try:
        local_engine.query(
            "SELECT lowerUTF8('AbC') SETTINGS max_threads=1, max_execution_time=5",
            "JSON",
        )
    except RuntimeError as exc:
        if "Code: 46." in str(exc) and "lowerUTF8" in str(exc):
            pytest.skip(
                "local chDB lacks lowerUTF8; string SQL is not engine-qualified"
            )
        raise
    return local_engine


@pytest.mark.parametrize("key", ["company_id", *DATA_KEYS])
@pytest.mark.parametrize(
    "op,stored,present,matched",
    [
        ("in", "00123", True, 1),
        ("in", "other", True, 0),
        ("in", "00123", False, 0),
        ("not_in", "00123", True, 0),
        ("not_in", "other", True, 1),
        ("not_in", "00123", False, 0),
    ],
)
def test_exact_string_picker_predicate_executes_without_rewriting_functions(
    utf8_engine, key, op, stored, present, matched
):
    predicate, params = ClickHouseFilterBuilderV2(query_mode="span").translate(
        [leaf(key, op=op, value=["00123"], types=["string"])]
    )
    assert "lowerUTF8(" in predicate
    query = (
        f"SELECT toUInt8({predicate}) AS matched FROM (SELECT "
        "CAST(mapFromArrays(%(fixture_keys)s, %(fixture_values)s), "
        "'Map(String, String)') AS attrs_string) "
        "SETTINGS max_threads=1, max_execution_time=5"
    )
    params.update(
        fixture_keys=[key] if present else [],
        fixture_values=[stored] if present else [],
    )
    assert json.loads(str(utf8_engine.query(finalize_query(query, params), "JSON")))[
        "data"
    ] == [{"matched": matched}]
