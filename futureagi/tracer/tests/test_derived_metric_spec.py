"""The public typed AST and the refresh compiler share one capability matrix."""

from copy import deepcopy
from itertools import product
from types import SimpleNamespace

import pytest
from django.test import override_settings

from tracer.serializers.derived_metric import DerivedMetricWriteSerializer
from tracer.services.derived_metrics.formula import FormulaError
from tracer.services.derived_metrics.service import _hash
from tracer.services.derived_metrics.spec import (
    normalize_spec,
    source_capabilities,
)
from tracer.services.derived_metrics.typed_fields import (
    COMPARISONS,
    REDUCERS,
    reduced_type,
)
from tracer.services.derived_metrics.unit_reads import field_recipe


def sentence(project_id=None, **overrides):
    from uuid import UUID

    return {
        "project_id": str(project_id or UUID(int=1)),
        "name": "French conversations",
        "kind": "share",
        "level": "conversation",
        "inputs": [
            {
                "alias": "locale",
                "source": "span",
                "field": "attribute_text",
                "attribute_key": "locale",
                "value_type": "text",
                "reducer": "latest",
            }
        ],
        "field_spec": {
            "conditions": [{"alias": "locale", "operator": "eq", "value": "fr"}]
        },
        **overrides,
    }


def parsed(data):
    serializer = DerivedMetricWriteSerializer(data=data)
    assert serializer.is_valid(), serializer.errors
    return serializer.validated_data


@pytest.mark.parametrize("formula", [None, "", "   "])
@pytest.mark.parametrize("explicit_kind", [False, True])
def test_custom_formula_is_required_even_when_kind_defaults(formula, explicit_kind):
    data = {
        "inputs": [
            {
                "alias": "latency",
                "source": "span",
                "field": "latency_ms",
                "reducer": "latest",
                "value_type": "number",
            }
        ]
    }
    if explicit_kind:
        data["kind"] = "custom"
    if formula is not None:
        data["formula"] = formula
    with pytest.raises(FormulaError, match="nonempty formula"):
        normalize_spec(data)


REDUCER_NAMES = sorted(set().union(*REDUCERS.values()))
KINDS = ["share", "count", "average", "total", "unique_count", "percentile", "custom"]


@pytest.mark.parametrize(
    "kind,level,source_type,reducer,operator",
    product(
        KINDS,
        ["span", "conversation", "user", "trace"],
        REDUCERS,
        REDUCER_NAMES,
        COMPARISONS,
    ),
)
@override_settings(DERIVED_METRICS_TRACE_ENABLED=True)
def test_kind_level_type_reducer_operator_matrix(
    kind, level, source_type, reducer, operator
):
    field = {
        "number": "attribute_number",
        "boolean": "attribute_bool",
        "text": "attribute_text",
    }[source_type]
    source = {
        "alias": "value",
        "source": "span",
        "field": field,
        "attribute_key": "field",
        "value_type": source_type,
        "reducer": reducer,
    }
    reduced = reduced_type(source)
    constant = {"number": 1, "boolean": True, "text": "fr"}[reduced]
    conditions = [{"alias": "value", "operator": operator, "value": constant}]
    spec = (
        {} if kind not in {"share", "count", "custom"} else {"conditions": conditions}
    )
    data = {
        "kind": kind,
        "level": level,
        "inputs": [source],
        "field_spec": spec,
        "formula": "mean(field_condition > 0)",
    }
    if level == "user":
        data["user_rule"] = "average" if kind == "average" else "any"
    legal = (
        reducer in REDUCERS[source_type]
        and (kind not in {"average", "total", "percentile"} or reduced == "number")
        and (not spec or reduced == "number" or operator in {"eq", "ne"})
        and (level != "user" or kind in {"share", "count", "average"})
    )
    if not legal:
        with pytest.raises(FormulaError):
            normalize_spec(data)
        return
    recipe = normalize_spec(data)
    refresh = field_recipe(SimpleNamespace(**recipe))
    assert refresh.value_type == recipe["value_type"]
    assert recipe["level"] == level
    assert recipe["selection_unit"] != "user"
    assert recipe["value_type"] == (
        "boolean"
        if kind in {"share", "count"}
        else (reduced if kind == "unique_count" else "number")
    )


def test_locale_compiles_to_numeric_indicator_before_custom_math():
    recipe = normalize_spec(parsed(sentence()))
    refresh = field_recipe(SimpleNamespace(**recipe))
    assert refresh.formula.required == ("field_condition",)
    assert refresh.source.field_spec["conditions"][0]["value"] == "fr"
    assert recipe["formula"] == "mean(field_condition > 0)"
    assert recipe["value_labels"] == {"match": "Counts", "other": "Doesn't count"}


@pytest.mark.parametrize(
    "kind,rule",
    [(k, r) for k in KINDS for r in ["", "any", "every", "average", "total"]],
)
def test_user_kinds_require_their_explicit_rule(kind, rule):
    data = sentence(kind=kind, level="user", user_rule=rule)
    if kind == "average":
        data["inputs"] = [
            {
                "alias": "score",
                "source": "span",
                "field": "latency_ms",
                "value_type": "number",
                "reducer": "mean",
            }
        ]
        data["field_spec"] = {}
    valid = (kind in {"share", "count"} and rule in {"any", "every"}) or (
        kind == "average" and rule in {"average", "total"}
    )
    if valid:
        assert normalize_spec(data)["user_rule"] == rule
    else:
        with pytest.raises(FormulaError):
            normalize_spec(data)


@pytest.mark.parametrize(
    "change",
    [
        {"level": "trace"},
        {"group_dims": ["model", "model"]},
        {"group_dims": ["model", "provider", "status"]},
        {"group_dims": ["arbitrary_sql"]},
        {"value_labels": {"match": "", "other": "No"}},
        {"value_labels": {"match": "Same", "other": "Same"}},
        {"value_type": "text"},
        {"user_rule": "any"},
    ],
)
def test_invalid_semantics_reject_before_telemetry(change):
    with pytest.raises(FormulaError):
        normalize_spec(sentence(**change))


@pytest.mark.parametrize(
    "key,value",
    [
        ("level", "span"),
        ("group_dims", ["model"]),
        ("value_labels", {"match": "French", "other": "Other"}),
        ("unit", "conversations"),
        ("weighting", "inverse_probability"),
        ("sampling", {"scope_key": "scope"}),
    ],
)
def test_definition_hash_covers_all_recipe_semantics(key, value):
    data = sentence()
    assert _hash(normalize_spec(data)) != _hash(normalize_spec({**data, key: value}))


def test_condition_order_is_hashed_not_sorted_away():
    data = sentence()
    data["field_spec"]["conditions"].append(
        {"alias": "locale", "operator": "ne", "value": "en"}
    )
    first = _hash(normalize_spec(data))
    data["field_spec"]["conditions"].reverse()
    assert _hash(normalize_spec(data)) != first


def test_picker_has_only_the_compilers_tested_pairings():
    caps = source_capabilities()
    assert "trace" not in caps["levels"]
    for source in caps["sources"]:
        assert set(source["reducers"]) == REDUCERS[source["value_type"]]
        assert source["operators"] == (
            list(COMPARISONS) if source["value_type"] == "number" else ["eq", "ne"]
        )
    assert caps["user_rules"] == {
        "share": ["any", "every"],
        "count": ["any", "every"],
        "average": ["average", "total"],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_top",
        "unknown_source",
        "unknown_condition",
        "wrong_operator",
        "numeric_text_reducer",
    ],
)
def test_strict_wire_or_compiler_rejects_invalid_ast(mutation):
    data = deepcopy(sentence())
    if mutation == "unknown_top":
        data["unknown"] = True
    elif mutation == "unknown_source":
        data["inputs"][0]["sql"] = "arbitrary"
    elif mutation == "unknown_condition":
        data["field_spec"]["conditions"][0]["sql"] = "arbitrary"
    elif mutation == "wrong_operator":
        data["field_spec"]["conditions"][0]["operator"] = "contains"
    else:
        data["inputs"][0]["reducer"] = "sum"
    serializer = DerivedMetricWriteSerializer(data=data)
    if serializer.is_valid():
        with pytest.raises(FormulaError):
            normalize_spec(serializer.validated_data)
    else:
        assert serializer.errors
