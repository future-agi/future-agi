# ruff: noqa: F811 -- pytest fixtures are imported from the ClickHouse suite
"""Typed reductions/conditions are tested independently of numeric formula math."""

import pytest

from tracer.services.derived_metrics.formula import FormulaError, compile_formula
from tracer.services.derived_metrics.typed_fields import (
    REDUCERS,
    compile_conditions,
    order_sql,
    reduce_sql,
    reduced_type,
    span_observation_sql,
    span_value_sql,
    validate_descriptor,
    value_column,
)
from tracer.tests.test_derived_metric_clickhouse import metric_ch  # noqa: F401


def descriptor(kind="number", reducer="latest", **kwargs):
    return {
        "alias": "value",
        "source": "span",
        "value_type": kind,
        "field": {
            "number": "attribute_number",
            "boolean": "attribute_bool",
            "text": "attribute_text",
        }[kind],
        "attribute_key": "value",
        "reducer": reducer,
        **kwargs,
    }


@pytest.mark.parametrize("kind,reducers", list(REDUCERS.items()))
def test_capability_matrix_has_only_validated_pairs(kind, reducers):
    for reducer in reducers:
        validate_descriptor(descriptor(kind, reducer))
    for reducer in {"median", "min", "sum", "any", "all"} - reducers:
        with pytest.raises(FormulaError):
            validate_descriptor(descriptor(kind, reducer))
    assert (
        value_column(kind)
        == {"number": "value_number", "boolean": "value_bool", "text": "value_text"}[
            kind
        ]
    )


def test_sentence_order_is_explicit_and_legacy_order_is_unchanged():
    assert (
        order_sql(descriptor(), source="span")
        == "tuple(m.start_time, m.raw_trace_id, m.raw_span_id)"
    )
    legacy = {"alias": "value", "source": "span", "field": "cost", "reducer": "latest"}
    assert order_sql(legacy, source="span") == "tuple(m.updated_at, m.span_id)"
    assert (
        order_sql(descriptor(source="eval"), source="eval")
        == "tuple(created_at, toString(id))"
    )


def test_sentence_span_observations_are_present_values_and_legacy_keeps_nulls():
    assert span_observation_sql(descriptor("text"), "m.v_value") == (
        "isNotNull(m.v_value)"
    )
    legacy = {"alias": "value", "source": "span", "field": "cost", "reducer": "latest"}
    assert span_observation_sql(legacy, "m.v_value") == "1"


@pytest.mark.parametrize(
    "kind,reducer,values,expected",
    [
        ("text", "first", [None, "fr", "en"], "fr"),
        ("text", "latest", ["fr", "en", None], "en"),
        ("boolean", "all", [None, 1, 1], 1),
        ("boolean", "any", [0, None, 0], 0),
        ("number", "count", [None, 0, None], 1),
        ("number", "latest", [None, None], None),
    ],
)
def test_live_span_presence_skips_spans_without_the_attribute(
    metric_ch, kind, reducer, values, expected
):
    item, params = descriptor(kind, reducer), {"values": values}
    sql = reduce_sql(
        "value",
        item,
        params,
        order="sequence",
        where=span_observation_sql(item, "value"),
    )
    rows = metric_ch.execute(
        f"SELECT {sql} FROM (SELECT arrayJoin(arrayEnumerate(%(values)s)) AS sequence, %(values)s[sequence] AS value)",
        params,
    )
    assert rows == [(expected,)]


def test_text_stays_typed_and_condition_constants_are_parameters():
    item = descriptor("text")
    params = {}
    source = span_value_sql(item, params, {})
    assert "toFloat" not in source and params == {"attr_value": "value"}
    compiled = compile_conditions([{"alias": "value", "value": "fr' OR 1=1"}], [item])
    assert "fr" not in compiled.sql
    assert compiled.parameters == {"field_condition_0": "fr' OR 1=1"}
    assert "isNotNull(v_value)" in compiled.complete_sql
    with pytest.raises(FormulaError):
        compile_formula("mean(value == 'fr')", ["value"])


@pytest.mark.parametrize(
    "condition",
    [
        {"alias": "value", "operator": "lt", "value": "fr"},
        {"alias": "missing", "value": "fr"},
        {"alias": "value", "value": 0},
        {"alias": "value", "value": "fr", "condition_stage": "observation"},
    ],
)
def test_unsupported_typed_conditions_fail_closed(condition):
    with pytest.raises(FormulaError):
        compile_conditions([condition], [descriptor("text")])


def test_cross_source_observation_predicates_are_rejected():
    with pytest.raises(FormulaError, match="Cross-source"):
        validate_descriptor(
            descriptor(
                "text",
                "any",
                condition_stage="observation",
                condition={"right_alias": "other"},
            )
        )
    with pytest.raises(FormulaError):
        compile_conditions(
            [{"alias": "value", "right_alias": "other"}],
            [descriptor("text"), descriptor("number", alias="other")],
        )


@pytest.mark.parametrize(
    "kind,reducer,values,expected",
    [
        ("boolean", "any", [None, 1], 1),
        ("boolean", "any", [None, 0], None),
        ("boolean", "any", [0, 0], 0),
        ("boolean", "all", [None, 0], 0),
        ("boolean", "all", [None, 1], None),
        ("boolean", "all", [1, 1], 1),
        ("boolean", "all", [None, None], None),
        ("number", "sum", [None, 2, 4], 6),
        ("number", "sum", [None, None], None),
        ("number", "count", [None, 0, 4], 2),
        ("text", "count", [None, "", "fr"], 2),
        ("text", "count", [None, None], None),
        ("text", "first", [None, "fr"], None),
        ("text", "latest", ["fr", None], None),
        ("number", "first", [0, 4], 0),
    ],
)
def test_live_reducer_null_and_three_valued_rules(
    metric_ch, kind, reducer, values, expected
):
    item, params = descriptor(kind, reducer), {"values": values}
    sql = reduce_sql("value", item, params, order="sequence")
    rows = metric_ch.execute(
        f"SELECT {sql} FROM (SELECT arrayJoin(arrayEnumerate(%(values)s)) AS sequence, %(values)s[sequence] AS value)",
        params,
    )
    assert rows == [(expected,)]
    empty = metric_ch.execute(
        f"SELECT {sql} FROM (SELECT CAST(NULL, 'Nullable({'String' if kind == 'text' else 'Float64'})') AS value, 1 AS sequence WHERE 0)",
        params,
    )
    assert empty == [(None,)]


def test_live_explicit_observation_predicate_is_not_reduce_then_compare(metric_ch):
    after = descriptor("number", "mean")
    observation = descriptor(
        "number",
        "any",
        condition_stage="observation",
        condition={"operator": "gt", "value": 5},
    )
    params = {}
    mean = reduce_sql("value", after, params, order="sequence")
    any_predicate = reduce_sql("value", observation, params, order="sequence")
    # Mean is 5, so mean > 5 is false; ANY observation > 5 is true.
    rows = metric_ch.execute(
        f"SELECT ({mean}) > 5, {any_predicate} FROM (SELECT number AS sequence, if(number = 0, 0., 10.) AS value FROM numbers(2))",
        params,
    )
    assert rows == [(0, 1)]
    assert reduced_type(observation) == "boolean"


def test_live_mixed_source_conditions_are_complete_cases(metric_ch):
    inputs = [
        descriptor("text", alias="locale"),
        descriptor("boolean", alias="resolved", source="eval"),
    ]
    compiled = compile_conditions(
        [{"alias": "locale", "value": "fr"}, {"alias": "resolved", "value": True}],
        inputs,
    )
    rows = metric_ch.execute(
        f"SELECT {compiled.sql} FROM (SELECT 'fr' AS v_locale, toNullable(toUInt8(1)) AS v_resolved)",
        compiled.parameters,
    )
    assert rows == [(1.0,)]
    missing = metric_ch.execute(
        f"SELECT {compiled.sql} FROM (SELECT 'en' AS v_locale, CAST(NULL, 'Nullable(UInt8)') AS v_resolved)",
        compiled.parameters,
    )
    assert missing == [(None,)]
