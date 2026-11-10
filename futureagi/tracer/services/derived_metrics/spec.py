"""Wire recipe normalization shared by validation, preview and refresh admission.

Typed descriptors are checked by B03 before the numeric formula compiler runs.
This module does not query telemetry, activate drafts or infer legacy user rules.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from django.conf import settings

from tracer.models.derived_metric import DerivedMetricVersion
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.formula import FormulaError
from tracer.services.derived_metrics.typed_fields import (
    COMPARISONS,
    REDUCERS,
    comparison_sql,
    compile_conditions,
    reduced_type,
    span_value_sql,
    validate_descriptor,
)
from tracer.services.derived_metrics.unit_reads import field_recipe

SEMANTIC_KEYS = tuple(
    key for key in DerivedMetricVersion.SEMANTIC_FIELDS if key != "definition_hash"
)
DEFAULT_LABELS = {"match": "Counts", "other": "Doesn't count"}
GROUP_FIELDS = {"model", "provider", "status", "name", "service_name"}


def trace_enabled():
    # B15 owns enabling this only after the trace measurement gate passes.
    return getattr(settings, "DERIVED_METRICS_TRACE_ENABLED", False) is True


def needs_rebuild(definition):
    """0126 preserved these fields losslessly, but changed no user semantics."""
    if isinstance(definition, dict):
        return (
            definition.get("selection_unit") == "user"
            and definition.get("level", "conversation") == "conversation"
            and definition.get("kind", "custom") == "custom"
            and not definition.get("user_rule")
        )
    return (
        definition.selection_unit == "user"
        and definition.level == "conversation"
        and definition.kind == "custom"
        and not definition.user_rule
    )


def require_computable(definition):
    if needs_rebuild(definition):
        raise MetricInputError(
            {
                "definition": "needs_rebuild: Rebuild this legacy user recipe before calculating or activating it."
            }
        )
    if definition.level == "trace" and not trace_enabled():
        raise MetricInputError({"level": "Trace metrics are not enabled."})


def snapshot_support(definition):
    if definition is None:
        return False, "A completed active definition is required."
    if needs_rebuild(definition):
        return False, "Rebuild this legacy user recipe first."
    if definition.level in {"span", "user"}:
        return False, f"Snapshots are not available for {definition.level} metrics."
    if definition.level == "trace" and not trace_enabled():
        return False, "Trace metrics are not enabled."
    return True, ""


def source_capabilities():
    """Only tested type/reducer/operator pairings appear in the picker."""
    return {
        "sources": [
            {
                "value_type": kind,
                "reducers": sorted(reducers),
                "operators": list(COMPARISONS) if kind == "number" else ["eq", "ne"],
                "observation_reducers": ["any", "all"],
                "ordering": "sentence",
            }
            for kind, reducers in sorted(REDUCERS.items())
        ],
        "levels": ["span", "conversation", "user"]
        + (["trace"] if trace_enabled() else []),
        "kinds": list(DerivedMetricVersion.Kind.values),
        "user_rules": {
            key: list(value)
            for key, value in DerivedMetricVersion.USER_LEVEL_RULES.items()
        },
        "max_group_dims": 2,
        "preview_max_days": 7,
        "preview_budget_seconds": 15,
    }


def normalize_spec(data):
    """Return the entire immutable recipe, including its compiled numeric formula.

    ``level`` is the v2 grain; ``unit`` names displayed measurement units.
    Legacy numeric recipes remain accepted, except raw-span user aggregation.
    Source/condition list order is retained in the definition hash.
    """
    kind = data.get("kind", "custom")
    level = data.get("level") or {"session": "conversation"}.get(
        data.get("selection_unit", "session"),
        data.get("selection_unit", "conversation"),
    )
    if (
        kind not in DerivedMetricVersion.Kind.values
        or level not in DerivedMetricVersion.Level.values
    ):
        raise FormulaError("Unsupported metric kind or level.")
    if level == "trace" and not trace_enabled():
        raise FormulaError("Trace metrics are not enabled.")
    selection = "span" if level == "span" else "session"
    supplied_selection = data.get("selection_unit")
    if supplied_selection and supplied_selection not in {
        selection,
        "user" if level == "user" else selection,
    }:
        raise FormulaError("Selection unit conflicts with the metric level.")
    rule = data.get("user_rule", "")
    if level == "user":
        if rule not in DerivedMetricVersion.USER_LEVEL_RULES.get(kind, ()):
            raise FormulaError(
                "User metrics require Share/Count with any/every or Average with average/total; custom formulas are not available for users."
            )
    elif rule:
        raise FormulaError("A user rule applies to user metrics only.")

    spec = deepcopy(data.get("field_spec") or {})
    inputs = deepcopy(spec.get("inputs") or data.get("inputs") or [])
    if data.get("inputs") and spec.get("inputs") and data["inputs"] != spec["inputs"]:
        raise FormulaError(
            "Inputs and field_spec.inputs must describe the same sources."
        )
    if not 1 <= len(inputs) <= 16:
        raise FormulaError("Use 1–16 inputs.")
    aliases = [item["alias"] for item in inputs]
    if len(set(aliases)) != len(aliases) or "field_condition" in aliases:
        raise FormulaError("Use unique input aliases; field_condition is reserved.")
    for item in inputs:
        validate_descriptor(item)
        if item.get("condition") and item.get("condition_stage") != "observation":
            raise FormulaError(
                "Source conditions require the explicit observation stage."
            )
        if item.get("condition_stage") == "observation" and (
            set(item["condition"]) - {"operator", "value"}
            or "value" not in item["condition"]
        ):
            raise FormulaError(
                "Observation conditions require exactly an operator and value."
            )
        if item["source"] == "span":
            # Exercise the SAME extraction/type checks the refresh compiler uses.
            from tracer.services.derived_metrics.queries import SPAN_MEASURE_SQL

            span_value_sql(item, {}, SPAN_MEASURE_SQL)
        if item.get("condition_stage") == "observation":
            comparison_sql(
                "value",
                item.get("value_type", "number"),
                item["condition"],
                {},
                prefix="check",
            )
    if spec.get("conditions"):
        compile_conditions(spec["conditions"], inputs)
    elif kind in {"share", "count"}:
        alias = spec.get("value_alias", aliases[0])
        if (
            alias not in aliases
            or reduced_type(inputs[aliases.index(alias)]) != "boolean"
        ):
            raise FormulaError(
                "Share/Count requires a boolean field or typed conditions."
            )
        spec["conditions"] = [{"alias": alias, "operator": "eq", "value": True}]
    if kind not in {"share", "count", "custom"} and spec.get("conditions"):
        raise FormulaError(
            "Conditions on numeric/unique metrics are not supported; use a custom numeric formula."
        )
    if kind != "custom" or spec or any("value_type" in item for item in inputs):
        spec["inputs"] = inputs
        spec.setdefault("value_alias", aliases[0])
    recipe = {
        "kind": kind,
        "level": level,
        "user_rule": rule,
        "field_spec": spec,
        "inputs": inputs,
        "formula": data.get("formula", ""),
        "selection_unit": selection,
        "group_dims": list(data.get("group_dims") or []),
        "value_type": "number",
        "unit": data.get("unit", ""),
        "value_format": data.get("value_format", ""),
        "value_labels": deepcopy(data.get("value_labels") or {}),
        "sampling": deepcopy(data.get("sampling") or {}),
        "weighting": data.get("weighting", "none"),
        "cohort_key": data.get("cohort_key", ""),
        "filters": deepcopy(data.get("filters") or []),
    }
    groups = recipe["group_dims"]
    if (
        len(groups) > 2
        or len(set(groups)) != len(groups)
        or any(
            not isinstance(group, str)
            or len(group) > 160
            or not (
                group in GROUP_FIELDS
                or (group.startswith("attribute:") and group.removeprefix("attribute:"))
            )
            for group in groups
        )
    ):
        raise FormulaError("Choose at most two distinct supported group dimensions.")
    if recipe["weighting"] not in {"none", "inverse_probability"}:
        raise FormulaError("Unsupported weighting.")
    if kind == "custom" and (
        not isinstance(recipe["formula"], str) or not recipe["formula"].strip()
    ):
        raise FormulaError("Custom metrics require a nonempty formula.")
    compiled = field_recipe(SimpleNamespace(**recipe))
    if compiled.formula:
        recipe["formula"] = compiled.source.formula
        used = {item["alias"] for item in compiled.required_inputs}
        if used != set(aliases):
            raise FormulaError("Remove inputs that the metric does not use.")
    elif set(aliases) != {compiled.value_alias}:
        raise FormulaError("Unique count requires exactly one source.")
    recipe["value_type"] = compiled.value_type
    if recipe["value_format"] == "percent" and not (
        compiled.formula and compiled.formula.rate_eligible
    ):
        raise FormulaError("Only proven rates may use percent formatting.")
    if data.get("value_type") and data["value_type"] != compiled.value_type:
        raise FormulaError("Value type does not match the compiled metric field.")
    labels = recipe["value_labels"]
    if compiled.value_type == "boolean":
        if set(labels) - set(DEFAULT_LABELS) or any(
            not isinstance(value, str) or not value.strip() or len(value) > 80
            for value in labels.values()
        ):
            raise FormulaError("Use match/other labels of 1–80 characters.")
        recipe["value_labels"] = {**DEFAULT_LABELS, **labels}
        if recipe["value_labels"]["match"] == recipe["value_labels"]["other"]:
            raise FormulaError("Match and other labels must be different.")
    elif labels:
        raise FormulaError("Only yes/no fields have value labels.")
    if kind == "percentile":
        percentile = spec.get("percentile", 0.95)
        if type(percentile) not in (int, float) or not 0 < percentile < 1:
            raise FormulaError("Percentile must be between zero and one.")
        spec["percentile"] = percentile
    return recipe
