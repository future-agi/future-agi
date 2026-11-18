"""Shared annotation value truth, tested on existing native Score fixtures."""

import json
from uuid import NAMESPACE_URL, uuid5

import pytest

from tracer.serializers.filters import FilterItemField
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    _annotation_value_condition,
    _compile_annotation_filter,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.tests.test_relational_filter_native import (
    _drop_legacy_ch_spans_mvs,  # noqa: F401 -- disable external integration DDL
    _ensure_test_score_tenant_column,  # noqa: F401
    offline_only,  # noqa: F401
)
from tracer.tests.test_span_physical_identity_latest import OTHER_PROJECT, PROJECT
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_span_score_physical_identity import LABEL
from tracer.tests.test_span_score_physical_identity import (
    scored_engine as scored_engine,
)

pytestmark = pytest.mark.unit


def leaf(kind, op, value):
    return {
        "column_id": LABEL,
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


def predicate(route, mode, item):
    if route == "graph":
        [(sql, required, params)] = _compile_annotation_filter(
            column_id=LABEL,
            config=item["filter_config"],
            project_id=PROJECT,
            observe_type=mode,
        )
        return sql if required else f"NOT ({sql})", params
    compiler = ClickHouseFilterBuilder if route == "base" else ClickHouseFilterBuilderV2
    sql, params = compiler(
        project_id=PROJECT,
        query_mode=mode,
        score_date_scope=False,
    ).translate([item])
    return sql, {"project_id": PROJECT, **params}


@pytest.fixture
def typed_scores(scored_engine):  # noqa: F811 -- pytest fixture injection
    execute, insert_span, insert_score = scored_engine

    def seed(kind):
        matched = {"rating": 0} if kind == "number" else {"value": "up"}
        other = {"rating": 2} if kind == "number" else {"value": "down"}
        key = "rating" if kind == "number" else "value"
        payloads = {
            "match": matched,
            "fallback": {"value": 0} if kind == "number" else matched,
            "other": other,
            "blank": {},
            "null": {key: None},
            "wrong_string": {key: "0" if kind == "number" else "unknown"},
            "wrong_bool": {key: False},
            "wrong_array": {key: []},
            "precedence": {"rating": None, "value": 0},
            "changed": matched,
            "hard": matched,
            "soft": matched,
            "foreign": matched,
            "absent": None,
        }
        for name, payload in payloads.items():
            trace = str(uuid5(NAMESPACE_URL, "annotation-parity-" + name))
            insert_span(id=name, trace_id=trace, parent_span_id=None)
            if payload is None:
                continue
            score = {"id": name, "trace_id": trace, "observation_span_id": name}
            insert_score(
                **score,
                value=json.dumps(payload),
                tracer_project_id=OTHER_PROJECT if name == "foreign" else PROJECT,
            )
            if name in {"changed", "hard", "soft"}:
                insert_score(
                    **score,
                    value=json.dumps(other if name == "changed" else matched),
                    _peerdb_version=2,
                    _peerdb_is_deleted=int(name == "hard"),
                    deleted=int(name == "soft"),
                )
        return execute

    return seed


NUMBER_CASES = [
    ("equals", 0, {"match", "fallback"}),
    ("not_equals", 0, {"other", "changed"}),
    ("greater_than", -1, {"match", "fallback", "other", "changed"}),
    ("greater_than_or_equal", 0, {"match", "fallback", "other", "changed"}),
    ("less_than", 1, {"match", "fallback"}),
    ("less_than_or_equal", 0, {"match", "fallback"}),
    ("between", [-1, 1], {"match", "fallback"}),
    ("not_between", [-1, 1], {"other", "changed"}),
    ("in", [0, 1], {"match", "fallback"}),
    ("not_in", [0, 1], {"other", "changed"}),
]
VOTE_CASES = [
    ("boolean", "equals", True, {"match", "fallback"}),
    ("boolean", "not_equals", True, {"other", "changed"}),
    ("boolean", "equals", False, {"other", "changed"}),
    ("thumbs", "in", ["Thumbs Up"], {"match", "fallback"}),
    ("thumbs", "not_in", ["up"], {"other", "changed"}),
    ("thumbs", "in", ["up", "down"], {"match", "fallback", "other", "changed"}),
    ("thumbs", "not_in", ["up", "down"], set()),
]


@pytest.mark.parametrize("route", ["base", "v2", "graph"])
@pytest.mark.parametrize("mode", ["trace", "span"])
@pytest.mark.parametrize("scored_engine", ["Nullable(UUID)"], indirect=True)
@pytest.mark.parametrize(
    "kind,op,value,expected",
    [
        *(("number", *case) for case in NUMBER_CASES),
        *VOTE_CASES,
    ],
)
def test_native_values_reject_defaults_and_use_latest_live_score(
    typed_scores, route, mode, kind, op, value, expected
):
    execute = typed_scores(kind)
    sql, params = predicate(route, mode, leaf(kind, op, value))
    actual = execute(
        "SELECT id FROM spans FINAL WHERE is_deleted = 0 AND (" + sql + ")", params
    )
    assert {row["id"] for row in actual} == expected


@pytest.mark.parametrize("route", ["base", "v2", "graph"])
@pytest.mark.parametrize("scored_engine", ["Nullable(UUID)"], indirect=True)
@pytest.mark.parametrize("op", ["is_null", "is_not_null"])
def test_label_presence_still_differs_from_valid_value_presence(
    typed_scores, route, op
):
    execute = typed_scores("number")
    sql, params = predicate(route, "trace", leaf("number", op, None))
    actual = execute("SELECT id FROM spans FINAL WHERE (" + sql + ")", params)
    expected = (
        {"hard", "soft", "foreign", "absent"}
        if op == "is_null"
        else {
            "match",
            "fallback",
            "other",
            "blank",
            "null",
            "wrong_string",
            "wrong_bool",
            "wrong_array",
            "precedence",
            "changed",
        }
    )
    assert {row["id"] for row in actual} == expected


@pytest.mark.parametrize(
    "op,value",
    [
        ("equals", True),
        ("greater_than", "not-a-number"),
        ("between", [0, "nan"]),
        ("in", [0, float("inf")]),
        ("contains", 1),
    ],
)
def test_invalid_numeric_operands_and_operators_fail_closed(op, value):
    for route in ("base", "v2"):
        sql, _ = predicate(route, "trace", leaf("number", op, value))
        assert sql == "0 = 1"
    with pytest.raises(UnsupportedFilterShapeError):
        predicate("graph", "trace", leaf("number", op, value))


@pytest.mark.parametrize("route", ["base", "v2", "graph"])
@pytest.mark.parametrize(
    "op",
    [
        "equals",
        "not_equals",
        "in",
        "not_in",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
    ],
)
@pytest.mark.parametrize("text", ["ÉQUIPE_50%", "ΟΣ", "İ", r"a\b"])
def test_unicode_is_folded_by_the_same_engine_on_both_operands(route, op, text):
    value = [text, "other"] if op in {"in", "not_in"} else text
    sql, params = predicate(route, "trace", leaf("text", op, value))
    assert value in params.values()
    assert "ILIKE" not in sql and "lower(" not in sql
    assert "lowerUTF8" in sql
    if op in {"in", "not_in"}:
        assert "arrayMap(x -> lowerUTF8(x), %(ann_" in sql
    elif op in {"equals", "not_equals"}:
        assert "lowerUTF8(%(ann_" in sql


@pytest.mark.parametrize(
    "kind,op,value",
    [
        ("boolean", "equals", "not-a-vote"),
        ("boolean", "not_equals", "not-a-vote"),
        ("boolean", "equals", 1),
        ("thumbs", "equals", "not-a-vote"),
        ("thumbs", "in", ["up", "not-a-vote"]),
        ("thumbs", "not_in", ["up", None]),
        ("thumbs", "in", [False, 0]),
    ],
)
def test_malformed_votes_accepted_by_public_shape_validation_fail_closed(
    kind, op, value
):
    item = FilterItemField().run_validation(leaf(kind, op, value))
    for route in ("base", "v2"):
        sql, _ = predicate(route, "trace", item)
        assert sql == "0 = 1"  # Never dropped or aliased to a down vote.
    with pytest.raises(UnsupportedFilterShapeError):
        predicate("graph", "trace", item)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("number", 0),
        ("boolean", True),
        ("thumbs", ["up"]),
        ("text", "ÉQUIPE"),
        ("categorical", ["red", "blue"]),
    ],
)
def test_value_parameters_do_not_overwrite_graph_label_or_sibling_parameters(
    kind, value
):
    params = {"ann_1": "existing", "annotation_label_1": LABEL}
    sql = _annotation_value_condition(
        filter_type=kind, filter_op="equals", filter_value=value, params=params
    )
    assert params["ann_1"] == "existing" and params["annotation_label_1"] == LABEL
    assert "%(ann_2)s" in sql
