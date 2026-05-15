"""Shared filter contract helpers.

The filter vocabulary is a FE/BE API contract. Keep the canonical values in
``api_contracts/filter_contract.json`` and have both sides consume/check that
same artifact instead of adding local alias maps in individual endpoints.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional


@lru_cache(maxsize=1)
def load_filter_contract() -> dict:
    contract_path = (
        Path(__file__).resolve().parents[3]
        / "api_contracts"
        / "filter_contract.json"
    )
    with contract_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_CONTRACT = load_filter_contract()
_OPERATORS = _CONTRACT["operators"]

FILTER_OP_ALIASES: dict[str, str] = dict(_OPERATORS["aliases"])
NO_VALUE_FILTER_OPS: set[str] = set(_OPERATORS["noValue"])
LIST_FILTER_OPS: set[str] = set(_OPERATORS["list"])
RANGE_FILTER_OPS: set[str] = set(_OPERATORS["range"])
SPAN_ATTR_ALLOWED_OPS: dict[str, set[str]] = {
    filter_type: set(ops)
    for filter_type, ops in _OPERATORS["spanAttributeAllowed"].items()
}
FILTER_TYPE_ALLOWED_OPS: dict[str, set[str]] = {
    filter_type: set(ops)
    for filter_type, ops in _OPERATORS["filterTypeAllowed"].items()
}
FIELD_TYPE_ALIASES: dict[str, str] = dict(_CONTRACT["fieldTypes"]["aliases"])
COL_TYPE_ALIASES: dict[str, str] = dict(_CONTRACT["columnTypes"]["aliases"])


def normalize_filter_op(filter_op: Optional[str]) -> str:
    if not filter_op:
        return ""
    return FILTER_OP_ALIASES.get(filter_op, filter_op)


def normalize_filter_type(filter_type: Optional[str]) -> str:
    if not filter_type:
        return ""
    return FIELD_TYPE_ALIASES.get(str(filter_type).lower(), str(filter_type).lower())


def normalize_col_type(col_type: Optional[str]) -> str:
    if not col_type:
        return ""
    raw = str(col_type)
    return COL_TYPE_ALIASES.get(raw.lower(), raw)
