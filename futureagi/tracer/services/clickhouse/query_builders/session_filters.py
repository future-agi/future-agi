"""Shared session-ID filter helpers for the session query builders.

Both the session list (``SessionListQueryBuilder``) and the session
time-series (``SessionTimeSeriesQueryBuilder``) translate a frontend
``session``/``session_id``/``trace_session_id`` filter into the same
``trace_session_id {IN|NOT IN} (...)`` predicate. The only difference is
the column expression each binds against:

- the list builder applies it in the OUTER ``WHERE`` of its remap-resolved
  subquery, where the column is already projected as ``trace_session_id``;
- the time-series builder applies it inline, so it passes the full
  ``resolved_id_expr(...)`` expression.

Keeping the translation in one place avoids the two copies drifting on
operator/null/empty-value handling.
"""

from __future__ import annotations

from typing import Any

# Frontend column aliases that target the session identity.
SESSION_ID_FILTER_COLS = frozenset({"session", "session_id", "trace_session_id"})

_NEGATED_OPS = ("not_equals", "not_in")


def build_session_id_filter_clause(
    filters: list[dict],
    params: dict[str, Any],
    *,
    session_col: str,
    param_prefix: str,
) -> str:
    """Build a ``session_col {IN|NOT IN} (...)`` predicate from filters.

    Iterates ``filters`` for any session-identity column and emits an
    ``AND``-joined clause, binding placeholder params into ``params``.
    ``is_null``/``is_not_null`` and empty value lists degrade to the
    constant ``0 = 1`` / ``1 = 1`` that matches the operator's intent.

    Args:
        filters: frontend filter dicts.
        params: query param dict, mutated in place with bound tuples.
        session_col: SQL expression the predicate binds against
            (a bare column or a resolved-id expression).
        param_prefix: unique prefix for generated placeholder names so two
            builders sharing a param dict never collide.
    """
    clauses: list[str] = []
    counter = 0

    for f in filters:
        col_id = f.get("column_id") or f.get("columnId")
        if col_id not in SESSION_ID_FILTER_COLS:
            continue

        config = f.get("filter_config") or f.get("filterConfig") or {}
        filter_op = config.get("filter_op") or config.get("filterOp")
        raw_value = config.get("filter_value", config.get("filterValue"))
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        values = [str(value) for value in values if value]

        if filter_op in ("is_null", "is_not_null"):
            clauses.append("0 = 1" if filter_op == "is_null" else "1 = 1")
            continue

        if not values:
            clauses.append("1 = 1" if filter_op in _NEGATED_OPS else "0 = 1")
            continue

        counter += 1
        param_name = f"{param_prefix}{counter}"
        params[param_name] = tuple(values)
        operator = "NOT IN" if filter_op in _NEGATED_OPS else "IN"
        clauses.append(f"{session_col} {operator} %({param_name})s")

    return " AND ".join(clauses)


__all__ = ["SESSION_ID_FILTER_COLS", "build_session_id_filter_clause"]
