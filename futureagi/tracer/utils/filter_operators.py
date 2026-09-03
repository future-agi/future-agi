"""Shared filter contract helpers.

The filter vocabulary is a FE/BE API contract. Keep the canonical values in
``api_contracts/filter_contract.json`` and have both sides consume/check that
same artifact instead of adding local alias maps in individual endpoints.
"""

from __future__ import annotations

import json
import math
import unicodedata
from functools import lru_cache
from pathlib import Path

from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES as TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)


@lru_cache(maxsize=1)
def load_filter_contract() -> dict:
    current_path = Path(__file__).resolve()
    contract_paths = (
        current_path.parents[3] / "api_contracts" / "filter_contract.json",
        current_path.parents[1] / "contracts" / "filter_contract.json",
    )
    for contract_path in contract_paths:
        if contract_path.exists():
            with contract_path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
    raise FileNotFoundError(
        "Could not find filter_contract.json in api_contracts/ or tracer/contracts/"
    )


_CONTRACT = load_filter_contract()
_OPERATORS = _CONTRACT["operators"]

NO_VALUE_FILTER_OPS: set[str] = set(_OPERATORS["noValue"])
LIST_FILTER_OPS: set[str] = set(_OPERATORS["list"])
RANGE_FILTER_OPS: set[str] = set(_OPERATORS["range"])
SPAN_ATTR_ALLOWED_OPS: dict[str, set[str]] = {
    filter_type: set(ops)
    for filter_type, ops in _OPERATORS["spanAttributeAllowed"].items()
}
STRUCTURED_SPAN_ATTR_ALLOWED_OPS: dict[str, set[str]] = {
    filter_type: set(ops)
    for filter_type, ops in _OPERATORS["structuredSpanAttributeAllowed"].items()
}
FILTER_TYPE_ALLOWED_OPS: dict[str, set[str]] = {
    filter_type: set(ops)
    for filter_type, ops in _OPERATORS["filterTypeAllowed"].items()
}
SESSION_NUMERIC_MEMBERSHIP_COLUMNS = frozenset(
    {
        "duration",
        "total_cost",
        "total_tokens",
        "traces_count",
        "total_traces_count",
    }
)
FIELD_TYPE_ALIASES: dict[str, str] = dict(_CONTRACT["fieldTypes"]["aliases"])
FILTER_COLUMN_TYPES: set[str] = set(_CONTRACT["columnTypes"]["allowed"])
COL_TYPE_ALIASES: dict[str, str] = dict(_CONTRACT["columnTypes"]["aliases"])

# Structured-array membership is compiled into bound ClickHouse IN sets inside
# each bounded classifier query. Keep both the element count and bound string
# payload finite so an authenticated request cannot inflate the query AST/set.
JSON_ARRAY_FILTER_MAX_MEMBERS = 64
JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES = 4_096
JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES = 65_536

# Flat JSON-object filters are evaluated only while replaying a finite latest-
# state candidate set.  These caps bound both the number of JSON path
# expressions added to that classifier and the total bound parameter payload.
JSON_MAP_FILTER_MAX_MEMBERS = 32
JSON_MAP_FILTER_MAX_KEY_UTF8_BYTES = 1_024
JSON_MAP_FILTER_MAX_TOTAL_KEY_UTF8_BYTES = 16_384
JSON_MAP_FILTER_MAX_STRING_UTF8_BYTES = 4_096
JSON_MAP_FILTER_MAX_TOTAL_STRING_UTF8_BYTES = 65_536
JSON_FILTER_SIGNED_INT_MIN = -(1 << 63)
JSON_FILTER_SIGNED_INT_MAX = (1 << 63) - 1
JSON_FILTER_UNSIGNED_INT_MAX = (1 << 64) - 1


def normalize_filter_type(filter_type: str | None) -> str:
    if not filter_type:
        return ""
    return FIELD_TYPE_ALIASES.get(str(filter_type).lower(), str(filter_type).lower())


def filter_op_is_allowed(
    filter_type: str | None,
    filter_op: str | None,
    *,
    column_id: str | None = None,
    column_type: str | None = None,
    allow_session_numeric_membership: bool = False,
) -> bool:
    """Return whether one field/operator pair is part of the public contract.

    Session aggregates are the one numeric family with finite membership
    semantics. The shared FE contract intentionally does not publish numeric
    ``in``/``not_in``, so callers must opt into that endpoint-specific extension
    explicitly. Keep it column- and family-scoped even after opt-in so ordinary
    scalar numbers and numeric span attributes retain the canonical vocabulary.
    """

    normalized_type = normalize_filter_type(filter_type)
    allowed_ops = FILTER_TYPE_ALLOWED_OPS.get(normalized_type)
    if allowed_ops is None:
        return False
    if filter_op in allowed_ops:
        return True
    return (
        allow_session_numeric_membership
        and normalized_type == "number"
        and filter_op in LIST_FILTER_OPS
        and isinstance(column_id, str)
        and column_id in SESSION_NUMERIC_MEMBERSHIP_COLUMNS
        and column_type == "SYSTEM_METRIC"
    )


def normalize_span_attribute_filter_type(
    filter_type: str | None,
    filter_value: object = None,
) -> str:
    """Canonicalize the value-sensitive structured span-attribute type.

    Historical clients call both JSON arrays and JSON objects ``json``.  A
    list value retains the existing ``json``/``list`` -> ``array`` contract;
    only an actual object value is canonicalized to ``map``.  Null operators
    have no value from which to infer a shape, so callers must send explicit
    ``map``/``object`` when they mean object existence.
    """

    raw_type = str(filter_type or "").lower()
    if raw_type == "json" and isinstance(filter_value, dict):
        return "map"
    return normalize_filter_type(raw_type)


def validate_json_map_filter_value(value: object) -> dict[str, object]:
    """Return one deterministic, bounded flat JSON object or raise ValueError.

    Only non-null JSON scalars are accepted as member values.  Nested arrays
    and objects would require recursive path semantics and can inflate the
    classifier expression tree, so the public map contract rejects them.
    """

    if not isinstance(value, dict) or not value:
        raise ValueError("JSON map filters require a non-empty object value")
    if len(value) > JSON_MAP_FILTER_MAX_MEMBERS:
        raise ValueError(
            f"JSON map filters support at most {JSON_MAP_FILTER_MAX_MEMBERS} members"
        )

    normalized_items: list[tuple[str, object]] = []
    total_key_bytes = 0
    total_string_bytes = 0
    for key, member_value in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("JSON map member keys must be non-empty strings")
        try:
            encoded_key = key.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("JSON map member keys must be valid UTF-8") from exc
        if len(encoded_key) > JSON_MAP_FILTER_MAX_KEY_UTF8_BYTES:
            raise ValueError(
                "JSON map member key exceeds the "
                f"{JSON_MAP_FILTER_MAX_KEY_UTF8_BYTES} UTF-8 byte limit"
            )
        total_key_bytes += len(encoded_key)
        if total_key_bytes > JSON_MAP_FILTER_MAX_TOTAL_KEY_UTF8_BYTES:
            raise ValueError(
                "JSON map member keys exceed the "
                f"{JSON_MAP_FILTER_MAX_TOTAL_KEY_UTF8_BYTES} UTF-8 byte request limit"
            )
        if any(unicodedata.category(character) in {"Cc", "Cf"} for character in key):
            raise ValueError("JSON map member keys contain control characters")

        if member_value is None:
            raise ValueError("JSON map members must be non-null JSON scalars")
        if isinstance(member_value, str):
            try:
                encoded_value = member_value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ValueError("JSON map string values must be valid UTF-8") from exc
            if len(encoded_value) > JSON_MAP_FILTER_MAX_STRING_UTF8_BYTES:
                raise ValueError(
                    "JSON map string value exceeds the "
                    f"{JSON_MAP_FILTER_MAX_STRING_UTF8_BYTES} UTF-8 byte limit"
                )
            total_string_bytes += len(encoded_value)
            if total_string_bytes > JSON_MAP_FILTER_MAX_TOTAL_STRING_UTF8_BYTES:
                raise ValueError(
                    "JSON map string values exceed the "
                    f"{JSON_MAP_FILTER_MAX_TOTAL_STRING_UTF8_BYTES} UTF-8 byte request limit"
                )
        elif isinstance(member_value, bool):
            pass
        elif isinstance(member_value, int):
            if not (
                JSON_FILTER_SIGNED_INT_MIN
                <= member_value
                <= JSON_FILTER_UNSIGNED_INT_MAX
            ):
                raise ValueError("JSON map integers must fit Int64 or UInt64")
        elif isinstance(member_value, float):
            if not math.isfinite(member_value):
                raise ValueError("JSON map numbers must be finite")
        else:
            raise ValueError(
                "Nested JSON map filter values are not supported; "
                "members must be scalars"
            )
        normalized_items.append((key, member_value))

    # Stable ordering keeps generated SQL/parameter names deterministic for
    # semantically identical objects sent with different JSON key ordering.
    return dict(sorted(normalized_items, key=lambda item: item[0].encode("utf-8")))


def normalize_col_type(col_type: str | None) -> str:
    if not col_type:
        return ""
    raw = str(col_type)
    return COL_TYPE_ALIASES.get(raw.lower(), raw)
