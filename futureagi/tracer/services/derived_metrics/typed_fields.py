"""Typed source/reducer SQL, separate from the numeric custom-formula language.

B05 validates the wire spec into these descriptors. Values never become SQL
identifiers or literals: source keys and comparison constants are parameters.
Legacy descriptors (without value_type) retain their numeric extraction and
updated_at ordering. Sentence descriptors explicitly declare value_type.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from tracer.services.derived_metrics.formula import ALIAS, FormulaError

TYPES = frozenset({"number", "boolean", "text"})
REDUCERS = {
    "number": frozenset({"first", "latest", "min", "max", "mean", "sum", "count"}),
    "boolean": frozenset({"first", "latest", "any", "all", "count"}),
    "text": frozenset({"first", "latest", "count"}),
}
COMPARISONS = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
VALUE_COLUMNS = {
    "boolean": "value_bool",
    "number": "value_number",
    "text": "value_text",
}


def value_column(value_type: str) -> str:
    try:
        return VALUE_COLUMNS[value_type]
    except KeyError as exc:
        raise FormulaError("Unsupported field value type.") from exc


def input_type(item: dict) -> str:
    kind: str = item.get("value_type", "number")
    if kind not in TYPES:
        raise FormulaError("Unsupported source value type.")
    return kind


def reduced_type(item: dict) -> str:
    if item["reducer"] == "count":
        return "number"
    return (
        "boolean" if item.get("condition_stage") == "observation" else input_type(item)
    )


def validate_descriptor(item: dict) -> None:
    alias = item.get("alias", "")
    if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
        raise FormulaError("Invalid typed source alias.")
    kind = input_type(item)
    stage = item.get("condition_stage", "after_reduction")
    if stage not in {"after_reduction", "observation"}:
        raise FormulaError("Unsupported condition stage.")
    reducer = item.get("reducer")
    if stage == "observation":
        if reducer not in {"any", "all"} or not isinstance(item.get("condition"), dict):
            raise FormulaError(
                "Observation predicates require an explicit any/all reducer."
            )
        if "right_alias" in item["condition"] or "alias" in item["condition"]:
            raise FormulaError("Cross-source observation predicates are not supported.")
    elif reducer not in REDUCERS[kind]:
        raise FormulaError("Unsupported type/reducer pair.")
    if item.get("ordering", "sentence" if "value_type" in item else "legacy") not in {
        "sentence",
        "legacy",
    }:
        raise FormulaError("Unsupported source ordering.")
    if item.get("source") not in {"span", "eval"}:
        raise FormulaError("Unsupported field source.")


def order_sql(item: dict, *, source: str) -> str:
    validate_descriptor(item)
    sentence = (
        item.get("ordering", "sentence" if "value_type" in item else "legacy")
        == "sentence"
    )
    if source == "span":
        return (
            "tuple(m.start_time, m.raw_trace_id, m.raw_span_id)"
            if sentence
            else "tuple(m.updated_at, m.span_id)"
        )
    if source == "eval":
        return "tuple(created_at, toString(id))"
    raise FormulaError("Unsupported ordering source.")


def comparison_sql(
    left: str,
    kind: str,
    condition: dict,
    params: dict,
    *,
    prefix: str,
    right: str | None = None,
    right_type: str | None = None,
) -> str:
    """A nullable UInt8 predicate; missing never compares equal to false/zero."""
    operator = condition.get("operator", "eq")
    if operator not in COMPARISONS or (
        kind != "number" and operator not in {"eq", "ne"}
    ):
        raise FormulaError("Unsupported typed comparison.")
    if right is not None:
        if right_type != kind:
            raise FormulaError("Comparison inputs must have the same type.")
    else:
        value = condition.get("value")
        valid = (
            (kind == "text" and isinstance(value, str) and len(value) <= 2048)
            or (kind == "boolean" and type(value) is bool)
            or (
                kind == "number"
                and type(value) in (int, float)
                # type(value) in (int, float) excludes None; mypy cannot see it.
                and abs(value) <= 1e15  # type: ignore[arg-type]
                and math.isfinite(value)  # type: ignore[arg-type]
            )
        )
        if not valid:
            raise FormulaError("Comparison constant does not match its source type.")
        params[prefix] = value
        right = f"%({prefix})s"
    return f"({left} {COMPARISONS[operator]} {right})"


def reduce_sql(
    value: str, item: dict, params: dict, *, order: str, where: str = "1"
) -> str:
    """Reduce independently, preserving selected NULLs and three-valued booleans."""
    validate_descriptor(item)
    reducer = item["reducer"]
    if item.get("condition_stage") == "observation":
        value = comparison_sql(
            value,
            input_type(item),
            item["condition"],
            params,
            prefix=f"observation_{item['alias']}",
        )
    present = f"({where}) AND isNotNull({value})"
    if reducer in {"first", "latest"}:
        function = "argMinIf" if reducer == "first" else "argMaxIf"
        # A tuple prevents ClickHouse from skipping a selected NULL observation.
        return f"if(countIf({where}) > 0, tupleElement({function}(tuple({value}), {order}, {where}), 1), NULL)"
    if reducer == "count":
        return f"if(countIf({present}) > 0, toFloat64(countIf({present})), NULL)"
    if reducer in {"any", "all"}:
        decisive = 1 if reducer == "any" else 0
        return (
            f"multiIf(countIf(({where}) AND {value} = {decisive}) > 0, toNullable(toUInt8({decisive})), "
            f"countIf({where}) = 0 OR countIf(({where}) AND isNull({value})) > 0, NULL, "
            f"toNullable(toUInt8({1 - decisive})))"
        )
    function = {
        "min": "minOrNullIf",
        "max": "maxOrNullIf",
        "mean": "avgOrNullIf",
        "sum": "sumOrNullIf",
    }[reducer]
    return f"{function}({value}, {where})"


def span_observation_sql(item: dict, value: str) -> str:
    """Which member spans are observations of a span source.

    A span map has the key or it does not, so a sentence (typed) span source
    observes only spans that carry a value: first/latest pick the first/latest
    PRESENT value, count counts present values, and a conversation with no
    present value reduces to No value. Unknowns for any/all therefore come only
    from evaluation rows whose typed output is NULL. Legacy custom descriptors
    keep their selected-NULL behaviour (latest span by updated_at, even if NULL).
    """
    validate_descriptor(item)
    return f"isNotNull({value})" if "value_type" in item else "1"


def span_value_sql(item: dict, params: dict, measures: dict[str, str]) -> str:
    validate_descriptor(item)
    field, kind = item["field"], input_type(item)
    if field in measures:
        if kind != "number":
            raise FormulaError("Built-in measures are numeric.")
        return measures[field]
    fields = {
        "attribute_number": ("attrs_number", "number"),
        "attribute_bool": ("attrs_bool", "boolean"),
        "attribute_text": ("attrs_string", "text"),
    }
    if field not in fields:
        raise FormulaError("Unsupported span field.")
    mapping, actual = fields[field]
    legacy_bool = "value_type" not in item and actual == "boolean"
    if actual != kind and not legacy_bool:
        raise FormulaError("Attribute type does not match source type.")
    key = f"attr_{item['alias']}"
    params[key] = item["attribute_key"]
    value = f"s.{mapping}[%({key})s]"
    if kind == "number":
        value = f"toFloat64({value})"
    return f"if(mapContains(s.{mapping}, %({key})s), {value}, NULL)"


def eval_value_sql(item: dict) -> str:
    validate_descriptor(item)
    if "value_type" not in item:
        return "score"
    return {"number": "output_float", "boolean": "output_bool", "text": "output_str"}[
        input_type(item)
    ]


@dataclass(frozen=True)
class TypedConditions:
    sql: str
    complete_sql: str
    parameters: dict


def compile_conditions(conditions: list[dict], inputs: list[dict]) -> TypedConditions:
    """After independent reductions, AND comparisons as one numeric indicator.

    All referenced reduced values are required complete cases. This is distinct
    from explicit observation any/all, whose unknowns may be decisive.
    """
    if not conditions or len(conditions) > 16:
        raise FormulaError("Use 1–16 field conditions.")
    kinds = {item["alias"]: reduced_type(item) for item in inputs}
    params: dict = {}
    predicates: list[str] = []
    required: set = set()
    for index, condition in enumerate(conditions):
        if condition.get("condition_stage", "after_reduction") != "after_reduction":
            raise FormulaError(
                "Observation predicates belong to one source descriptor."
            )
        alias, right_alias = condition.get("alias"), condition.get("right_alias")
        if alias not in kinds or (right_alias is not None and right_alias not in kinds):
            raise FormulaError("Unknown typed condition input.")
        required.add(alias)
        if right_alias:
            required.add(right_alias)
        predicates.append(
            comparison_sql(
                f"v_{alias}",
                kinds[alias],
                condition,
                params,
                prefix=f"field_condition_{index}",
                right=f"v_{right_alias}" if right_alias else None,
                right_type=kinds.get(right_alias),
            )
        )
    complete = " AND ".join(
        f"isNotNull(v_{name})"
        + (f" AND isFinite(v_{name})" if kinds[name] == "number" else "")
        for name in sorted(required)
    )
    return TypedConditions(
        f"if({complete}, toFloat64({' AND '.join(predicates)}), NULL)", complete, params
    )
