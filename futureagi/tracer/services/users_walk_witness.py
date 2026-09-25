"""The one predicate a typed (number/boolean) Users walk is decided on.

A span-attribute-filtered Users page walks witnessed spans newest-first and
certifies each user on the page enrichment (``users_matching_walk``). For a
plain-text filter the enrichment already narrows its physical seed and
projects the order key from the manager's exact-text values. A number or
boolean filter has no such value list: its witness is the compiler's typed-map
predicate over ``attrs_number`` / ``attrs_bool``, and this module carries that
single predicate to the three places the walk needs it, so discovery, seed
narrowing and the order key can never disagree:

* ``witness_sql`` narrows the slice scan and the enrichment's physical seed
  (raw rows; the compiler's ``key present AND value`` comparison);
* ``order_clause`` decides, on the enrichment's LATEST typed value of one
  row, whether that row is a match: the certified order key is the newest
  live span whose latest value satisfies it. It mirrors exactly the Python
  comparison ``UsersListManager._candidate_value_matches`` applies to a value
  of that storage type, which stays the authority on membership.

Only shapes whose Python and SQL comparisons are provably the same qualify:
``equals``/``in`` on real booleans; ``equals``/``in`` and the four orderings
on finite numbers, without a storage-type picker. Everything else keeps the
seeded page.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

_NUMBER_OPERATORS = {
    "equals": "=",
    "in": "IN",
    "greater_than": ">",
    "greater_than_or_equal": ">=",
    "less_than": "<",
    "less_than_or_equal": "<=",
}
_BOOLEAN_OPERATORS = {"equals": "=", "in": "IN"}


@dataclass(frozen=True)
class WalkedTypedFilter:
    key: str
    kind: str
    witness_sql: str
    witness_params: dict[str, Any]
    order_clause: str
    order_params: dict[str, Any]


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def typed_walk_filter(witness: Any, item: dict[str, Any]) -> WalkedTypedFilter | None:
    """The walked typed filter for ``item``, or ``None`` when it must stay seeded.

    ``witness`` is the raw ``MatchingActivityWitness`` the builder returned.
    """

    key, kind = witness.key, witness.kind
    witness_sql, witness_params = witness.sql, witness.params
    if kind not in {"number", "boolean"}:
        return None
    config = item.get("filter_config") or item.get("filterConfig") or {}
    if config.get("attribute_value_types", config.get("attributeValueTypes")):
        return None
    operation = str(config.get("filter_op") or config.get("filterOp") or "")
    raw = config.get("filter_value", config.get("filterValue"))
    values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    if not values or (operation != "in" and len(values) != 1):
        return None
    if kind == "boolean":
        comparison = _BOOLEAN_OPERATORS.get(operation)
        if comparison is None or not all(isinstance(v, bool) for v in values):
            return None
        bound: list[Any] = [int(v) for v in values]
        # ``latest_attribute_value_json`` is ``'true'``/``'false'`` for a
        # boolean-typed latest value; Python compares the canonical words.
        lhs = "JSONExtractBool(latest_attribute_value_json)"
        storage = "boolean"
    else:
        comparison = _NUMBER_OPERATORS.get(operation)
        numbers = [_finite_number(v) for v in values]
        if comparison is None or any(n is None for n in numbers):
            return None
        bound = numbers
        # The enrichment renders a number-typed latest value with
        # ``toString``; Python parses it back and compares as float.
        lhs = "toFloat64OrNull(latest_attribute_value_json)"
        storage = "number"
    order_params = {
        "matching_activity_typed_key": key,
        "matching_activity_typed_value": (
            tuple(bound) if comparison == "IN" else bound[0]
        ),
    }
    order_clause = (
        "attribute_key = %(matching_activity_typed_key)s"
        f" AND latest_attribute_value_type = '{storage}'"
        f" AND {lhs} {comparison} %(matching_activity_typed_value)s"
    )
    return WalkedTypedFilter(
        key=key,
        kind=kind,
        witness_sql=witness_sql,
        witness_params=dict(witness_params),
        order_clause=order_clause,
        order_params=order_params,
    )
