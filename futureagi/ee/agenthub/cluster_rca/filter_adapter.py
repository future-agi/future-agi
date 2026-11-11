"""Filter adapter — translates the grep-shaped agent DSL into the canonical
FE/BE filter contract consumed by tracer/utils/filters.py::FilterEngine.

The LLM-facing DSL is intentionally tiny (one of two shapes):
  {key: scalar}      — equality. `null` means IS NULL.
  {key: {op: val}}   — operator dict. Keys AND together.

Column family is decided by a key prefix:
  attr.<k>  → SPAN_ATTRIBUTE  (user-defined span attribute)
  eval.<k>  → EVAL_METRIC     (eval score / category)
  ann.<k>   → ANNOTATION      (human label)
  <k>       → NORMAL          (built-in column)

Translation outputs the contract format defined in
api_contracts/filter_contract.json — a list of {column_id, filter_config{
filter_type, filter_op, filter_value, col_type}} items.
"""

from __future__ import annotations

from typing import Any


# Agent DSL operator → canonical filter_op
_OP_MAP: dict[str, str] = {
    "gt": "greater_than",
    "gte": "greater_than_or_equal",
    "lt": "less_than",
    "lte": "less_than_or_equal",
    "in": "in",
    "not_in": "not_in",
    "contains": "contains",
    "not_contains": "not_contains",
    "starts_with": "starts_with",
    "ends_with": "ends_with",
    "between": "between",
    "not_between": "not_between",
}

# Column-family prefix → canonical col_type
_PREFIX_MAP: dict[str, str] = {
    "attr.": "SPAN_ATTRIBUTE",
    "eval.": "EVAL_METRIC",
    "ann.": "ANNOTATION",
}

# Lightweight filter_type defaults for common built-in columns. The full
# resolution (via FilterEngine.DEFAULT_FIELD_MAP) lands when real handlers
# wire FilterEngine in; this map just gives us decent defaults pre-wire.
_DATETIME_COLS = {
    "created_at",
    "started_at",
    "completed_at",
    "first_seen",
    "last_seen",
    "ended_at",
    "start_time",
    "end_time",
}
_NUMBER_COLS = {
    "duration_ms",
    "cost",
    "latency",
    "latency_ms",
    "score",
    "tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "trace_count",
    "span_count",
}
_BOOLEAN_COLS = {
    "is_flagged",
    "has_eval",
    "has_annotation",
    "is_resolved",
}


def _resolve_col_type(key: str) -> tuple[str, str]:
    """Strip a column-family prefix and return (col_type, column_id)."""
    for prefix, col_type in _PREFIX_MAP.items():
        if key.startswith(prefix):
            return col_type, key[len(prefix):]
    return "NORMAL", key


def _infer_filter_type(column_id: str, col_type: str, value: Any) -> str:
    """Best-effort filter_type inference from column_id + family + value.

    Falls back to value-shape (bool/number/list/text). When we wire real
    handlers we replace this with a lookup into FilterEngine's field map.
    """
    if col_type == "EVAL_METRIC":
        # Eval scores are numeric by default; categorical evals override
        # via explicit caller logic later.
        return "number"
    if col_type == "ANNOTATION":
        return "text"

    lower = column_id.lower()
    if lower in _DATETIME_COLS:
        return "datetime"
    if lower in _NUMBER_COLS:
        return "number"
    if lower in _BOOLEAN_COLS:
        return "boolean"

    # Value-shape fallback
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, list):
        return "array"
    return "text"


def _normalize_op_and_value(value: Any) -> tuple[str, Any]:
    """Translate the agent's operator dict into (canonical_op, raw_value).

      None         → ('is_null', None)
      scalar       → ('equals', scalar)
      {op: v}      → (mapped_op, v)
      {known: ..., unknown: ...}  → ('equals', value)  # falls back, agent error
    """
    if value is None:
        return "is_null", None
    if isinstance(value, dict) and len(value) == 1:
        (op_key, op_val), = value.items()
        if op_key in _OP_MAP:
            return _OP_MAP[op_key], op_val
    return "equals", value


def to_canonical(simple_filter: dict | None) -> list[dict]:
    """Translate grep-shaped agent DSL → canonical FE/BE filter contract.

    Each key becomes one filter item AND'd with the rest. Returns an empty
    list when `simple_filter` is None or empty.
    """
    if not simple_filter:
        return []
    items: list[dict] = []
    for raw_key, value in simple_filter.items():
        col_type, column_id = _resolve_col_type(raw_key)
        filter_op, filter_value = _normalize_op_and_value(value)
        filter_type = _infer_filter_type(column_id, col_type, filter_value)
        items.append(
            {
                "column_id": column_id,
                "filter_config": {
                    "filter_type": filter_type,
                    "filter_op": filter_op,
                    "filter_value": filter_value,
                    "col_type": col_type,
                },
            }
        )
    return items


def resolve_group_by(raw: str) -> tuple[str, str]:
    """Resolve a prefix-aware group_by string into (col_type, column_id).

    Mirrors _resolve_col_type's order — col_type first.
    Examples:
      'tool_name'        → ('NORMAL', 'tool_name')
      'attr.user.tier'   → ('SPAN_ATTRIBUTE', 'user.tier')
      'eval.helpfulness' → ('EVAL_METRIC', 'helpfulness')
    """
    return _resolve_col_type(raw)
