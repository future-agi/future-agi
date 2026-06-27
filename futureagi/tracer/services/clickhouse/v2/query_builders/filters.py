"""
v2 ClickHouse filter compiler — targets the new CH 25.3 `spans` schema.

Strategy: SUBCLASS the legacy `ClickHouseFilterBuilder` so we inherit all
~1500 lines of frontend-filter-JSON parsing logic AND the shared canonical
filter contract (the operator/type/column-id rules pulled from
`api_contracts/filter_contract.json`). Then rewrite the COLUMN REFERENCES in
the compiled SQL output.

Why this works:
  - Filter operator/type/value contract is identical between v1 and v2. The
    only thing that changes is which CH column the SQL references.
  - Legacy column identifiers (`_peerdb_is_deleted`, `span_attr_str`, etc.)
    are unique tokens; word-boundary substitution is safe.
  - Typed-JSON access syntax (`attributes_extra.path.:Type`) replaces
    `JSONExtractString(span_attributes_raw, 'path')`; a few targeted regex
    rewrites cover the JSONExtract* calls v1 emits.

Why not refactor v1 to use overridable constants:
  - 41 column references across 1657 lines. Touching each line is high-risk
    on a hot dashboard path. The post-rewrite approach keeps v1 unchanged
    and isolates v2 risk to the rewrite + the parity-shadow harness.

Risk mitigations:
  - The parity-shadow harness (tracer/services/clickhouse/v2/shadow.py) runs
    v1 and v2 in parallel and logs diffs. Any v1 emission pattern the
    rewriter doesn't anticipate surfaces as a shadow diff long before any
    query type is flipped to v2-primary.
  - Tests in `tracer/tests/test_ch25_filter_compiler.py` cover every
    column-rewrite case + every JSONExtract* pattern v1 currently emits.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    _coerce_strict_bool,
)
from tracer.services.clickhouse.v2.query_builders import columns as cols

# ─── Simple column-name renames ───────────────────────────────────────────────
# These are tokens; word-boundary regex substitutes them safely.
_COL_RENAMES: dict[str, str] = {
    "_peerdb_is_deleted": cols.IS_DELETED,
    "_peerdb_version": cols.VERSION,
    "span_attr_str": cols.ATTRS_STRING,
    "span_attr_num": cols.ATTRS_NUMBER,
    "span_attr_bool": cols.ATTRS_BOOL,
}

# Pre-compile a single regex that matches any legacy column name as a whole word.
_COL_RENAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _COL_RENAMES.keys()) + r")\b"
)


# Legacy CDC dict names → v2 CH-native dicts (same key/attrs, so a token rename).
# Sourced from the now-renamed v2 curated dimension tables (end_users RMT,
# trace_sessions RMT) instead of the legacy CDC landing tables.
_DICT_RENAMES: dict[str, str] = {
    "enduser_dict": "end_users_dict",
    "trace_session_dict": "trace_sessions_dict",
}
_DICT_RENAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _DICT_RENAMES.keys()) + r")\b"
)


# ─── JSON-overflow access rewrites ────────────────────────────────────────────
# v1 emits `JSONExtractType(span_attributes_raw, 'path.with.dots')`; v2 uses
# CH 25.x typed JSON path access `attributes_extra.path.with.dots.:Type`.
# Same translation applies to `metadata_map` (v1 Map) → `metadata` (v2 typed JSON).
_JSON_EXTRACT_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"JSONExtractString\(\s*span_attributes_raw\s*,\s*'([^']+)'\s*\)"),
        cols.ATTRIBUTES_EXTRA,
        "String",
    ),
    (
        re.compile(r"JSONExtractFloat\(\s*span_attributes_raw\s*,\s*'([^']+)'\s*\)"),
        cols.ATTRIBUTES_EXTRA,
        "Float64",
    ),
    (
        re.compile(r"JSONExtractInt\(\s*span_attributes_raw\s*,\s*'([^']+)'\s*\)"),
        cols.ATTRIBUTES_EXTRA,
        "Int64",
    ),
    (
        re.compile(r"JSONExtractBool\(\s*span_attributes_raw\s*,\s*'([^']+)'\s*\)"),
        cols.ATTRIBUTES_EXTRA,
        "Bool",
    ),
    (
        re.compile(
            r"JSONExtractString\(\s*resource_attributes_raw\s*,\s*'([^']+)'\s*\)"
        ),
        cols.RESOURCE_ATTRS,
        "String",
    ),
    (
        re.compile(r"JSONExtractString\(\s*metadata_map\s*,\s*'([^']+)'\s*\)"),
        cols.METADATA_JSON,
        "String",
    ),
]

# `JSONHas(span_attributes_raw, 'path')` → `(attributes_extra.path.:String IS NOT NULL)`
_JSON_HAS_PATTERN = re.compile(
    r"JSONHas\(\s*(span_attributes_raw|resource_attributes_raw|metadata_map)\s*,\s*'([^']+)'\s*\)"
)
_JSON_HAS_TARGET = {
    "span_attributes_raw": (cols.ATTRIBUTES_EXTRA, "String"),
    "resource_attributes_raw": (cols.RESOURCE_ATTRS, "String"),
    "metadata_map": (cols.METADATA_JSON, "String"),
}

# Map from legacy bare JSON column → v2 typed-JSON column. Used by the bare-
# column rewriters below. These columns CAN'T just be renamed (their TYPES
# differ — v1 is String, v2 is typed JSON), so:
#   • In SELECT lists: wrap with toJSONString(v2_col) AS legacy_col so the
#     callers' Python code can still do row["legacy_col"] and get a JSON string.
#   • In WHERE emptiness checks: rewrite to length-based predicates on the
#     toJSONString form (semantically equivalent for "has any keys").
_BARE_JSON_REWRITES = {
    "span_attributes_raw": cols.ATTRIBUTES_EXTRA,
    "metadata_map": cols.METADATA_JSON,
    "resource_attributes_raw": cols.RESOURCE_ATTRS,
}

# WHERE emptiness checks v1 emits: `<legacy_col> != '{}'`, `!= ''`, `= '{}'`, `= ''`.
# Pattern allows single or doubled `{}` (the `{{}}` form appears when the SQL
# was built via `f.format(...)` — the double-brace escapes inside an f-string).
_WHERE_EMPTY_PATTERN = re.compile(
    r"\b(span_attributes_raw|resource_attributes_raw|metadata_map)\b"
    r"\s*(!=|=)\s*'(\{?\}?|\{\{?\}?\}?)'"
)

# Bare SELECT-list / projection reference. The negative lookahead skips matches
# that are immediately followed by `[` (Map subscript — but the legacy Map
# columns are span_attr_*, never these) or `(` (function call — already
# consumed by the JSONExtract patterns above), and negative lookbehind skips
# matches inside identifiers (preceded by alphanumeric or underscore).
_BARE_REF_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_'])"
    r"\b(span_attributes_raw|resource_attributes_raw|metadata_map)\b"
    r"(?![\[\(A-Za-z0-9_])"
)


# ─── v2 attribute-type meta (same shape as v1 module-level constant, retargeted) ─
_SPAN_ATTR_TYPE_META_V2: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "text": (cols.ATTRS_STRING, lambda v: v if isinstance(v, str) else str(v)),
    "number": (cols.ATTRS_NUMBER, lambda v: float(v)),
    "boolean": (cols.ATTRS_BOOL, _coerce_strict_bool),
}


_V2_REQUIRED_SETTINGS = (
    # CRITICAL for trillion-row scale: FINAL on ReplacingMergeTree bypasses
    # skip indexes by default. Without this, every dashboard query that uses
    # FINAL (almost all of them) full-scans the parts. Measured 47× slowdown
    # locally without this setting. See DECISIONS #026 in
    # internal-docs/clickhouse-analytics/migration-to-ch25/.
    "use_skip_indexes_if_final = 1",
    # Encourage projection auto-routing for dashboard aggregates. Falls
    # through to base-table read if no projection matches — zero risk.
    "optimize_use_projections = 1",
    # Streaming aggregation order: when ORDER BY matches the table's ORDER
    # BY prefix, CH can stream-aggregate without sorting. Big win on time-
    # bucketed dashboard queries.
    "optimize_aggregation_in_order = 1",
)


def _append_v2_settings(sql: str) -> str:
    """Append the v2-required settings to a SQL string.

    Idempotent: if the SQL already ends with a SETTINGS clause, merge the
    v2 settings into it (don't double-apply). If not, append a fresh one.

    Handles trailing FORMAT clause: SETTINGS must come BEFORE FORMAT.
    """
    sql_stripped = sql.rstrip().rstrip(";").rstrip()
    # Check for an existing SETTINGS clause (case-insensitive, at end before
    # any FORMAT clause). Use a simple heuristic — the v1 builders rarely
    # emit SETTINGS, so the common case is "no existing clause."
    import re as _re

    # Pull out a trailing FORMAT clause so we can re-attach it after SETTINGS.
    fmt_match = _re.search(r"\s+FORMAT\s+\w+\s*$", sql_stripped, _re.IGNORECASE)
    if fmt_match:
        format_clause = fmt_match.group(0)
        sql_stripped = sql_stripped[: fmt_match.start()].rstrip()
    else:
        format_clause = ""

    settings_clause = "SETTINGS " + ", ".join(_V2_REQUIRED_SETTINGS)
    existing = _re.search(r"\s+SETTINGS\s+", sql_stripped, _re.IGNORECASE)
    if existing:
        # Merge — append our settings to the existing clause (later wins on
        # duplicate keys, which is what we want).
        sql_stripped = sql_stripped + ", " + ", ".join(_V2_REQUIRED_SETTINGS)
    else:
        sql_stripped = sql_stripped + "\n" + settings_clause

    return sql_stripped + format_clause


def rewrite_v1_sql_to_v2(sql: str) -> str:
    """Translate a v1-compiled SQL string to v2 column references.

    Public so tests can pin every rewrite case directly without going through
    the full filter compiler.

    Order matters:
      1. JSON path access — JSONExtract*(legacy_col, ...) → typed JSON path.
         Consumes legacy_col occurrences that are inside function calls.
      2. JSON has — JSONHas(legacy_col, key) → (typed JSON path IS NOT NULL).
      3. WHERE emptiness predicates — `WHERE legacy_col != '{}'` →
         length-based check on toJSONString(v2_col).
      4. Bare SELECT-list refs — `SELECT … legacy_col …` →
         `SELECT … toJSONString(v2_col) AS legacy_col …`. Preserves the
         downstream Python `row["legacy_col"]` shape (still a JSON string).
      5. Naked simple renames — `_peerdb_is_deleted` → `is_deleted`, etc.
         Word-boundary substitution; runs last.
      6. Append v2-required settings (use_skip_indexes_if_final etc).
    """
    # 1. JSON path access
    for pat, target_col, ch_type in _JSON_EXTRACT_PATTERNS:
        sql = pat.sub(
            lambda m, c=target_col, t=ch_type: cols.json_path(c, m.group(1), t),
            sql,
        )

    # 2. JSON has
    def _has_repl(m):
        col, ch_type = _JSON_HAS_TARGET[m.group(1)]
        return f"({cols.json_path(col, m.group(2), ch_type)} IS NOT NULL)"

    sql = _JSON_HAS_PATTERN.sub(_has_repl, sql)

    # 3. WHERE emptiness predicates
    def _empty_repl(m):
        legacy_col = m.group(1)
        op = m.group(2)
        literal = m.group(3)
        v2_col = _BARE_JSON_REWRITES[legacy_col]
        wrapped = f"toJSONString({v2_col})"
        # `'{}'` or `'{{}}'` mean "empty object literal" → 2 chars (or 4 if
        # the double-brace was a Python format-string escape, which CH never
        # sees — by the time SQL reaches us, the braces are concrete).
        is_empty_obj = literal in ("{}", "{{}}", "{")
        if op == "!=" and is_empty_obj:
            return f"length({wrapped}) > 2"
        if op == "!=" and literal == "":
            return f"length({wrapped}) > 0"
        if op == "=" and is_empty_obj:
            return f"length({wrapped}) <= 2"
        if op == "=" and literal == "":
            return f"length({wrapped}) = 0"
        # Fall back — wrap with toJSONString and keep the literal compare
        return f"{wrapped} {op} '{literal}'"

    sql = _WHERE_EMPTY_PATTERN.sub(_empty_repl, sql)

    # 4. Bare SELECT-list refs — wrap with toJSONString() AS legacy_col so the
    # caller's row["legacy_col"] still works.
    def _bare_repl(m):
        legacy_col = m.group(1)
        v2_col = _BARE_JSON_REWRITES[legacy_col]
        return f"toJSONString({v2_col}) AS {legacy_col}"

    sql = _BARE_REF_PATTERN.sub(_bare_repl, sql)

    # 5. Naked simple renames (must come last so we don't accidentally rewrite
    # inside the AS aliases we just produced).
    sql = _COL_RENAME_RE.sub(lambda m: _COL_RENAMES[m.group(1)], sql)
    # 5b. Legacy CDC dictionary names → v2 CH-native dictionary names.
    sql = _DICT_RENAME_RE.sub(lambda m: _DICT_RENAMES[m.group(1)], sql)
    # NOTE: this function does NOT append the v2 SETTINGS clause. The settings
    # are appended at the BUILDER boundary (v2 `build()`/`build_count_query()` etc)
    # via `_append_v2_settings()` — see ClickHouseFilterBuilderV2.translate.
    # Keeping rewrite_v1_sql_to_v2 pure lets tests assert exact-string rewrites
    # without dragging SETTINGS into every expectation.
    return sql


def rewrite_and_apply_v2_settings(sql: str) -> str:
    """One-call helper for builder methods: pure rewrite + SETTINGS append."""
    return _append_v2_settings(rewrite_v1_sql_to_v2(sql))


class ClickHouseFilterBuilderV2(ClickHouseFilterBuilder):
    """Filter compiler for the new CH 25.3 spans schema.

    Drop-in replacement for the v1 builder:
      v1: from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
      v2: from tracer.services.clickhouse.v2.query_builders.filters import ClickHouseFilterBuilderV2

    Call sites swap one import line; everything else works.
    """

    # Expose the v2 attribute-type meta on the instance.
    SPAN_ATTR_TYPE_META = _SPAN_ATTR_TYPE_META_V2

    # End-user filter subquery reads the v2 `end_users` RMT (keyed by
    # end_user_id, soft-deleted via is_deleted) instead of the dropped legacy
    # `tracer_enduser` (id + _peerdb_is_deleted/deleted).
    _ENDUSER_DIM_TABLE = "end_users"
    _ENDUSER_DIM_ID_COL = "end_user_id"
    _ENDUSER_DIM_NOT_DELETED = "is_deleted = 0"

    def translate(self, filters):  # type: ignore[override]
        # `translate` returns a WHERE fragment that gets stitched into a larger
        # SELECT statement by callers. Do NOT append SETTINGS here — that
        # happens at the full-SELECT boundary in the per-builder `build()`
        # methods (SpanListQueryBuilderV2.build, TraceListQueryBuilderV2.build,
        # etc.). Otherwise we'd end up with `WHERE ... SETTINGS ... AND ...`
        # which is a syntax error.
        sql, params = super().translate(filters)
        return rewrite_v1_sql_to_v2(sql), params

    def translate_sort(self, sort_params, *args, **kwargs):  # type: ignore[override]
        # Forward extra args (e.g. field_map) to the v1 implementation — callers
        # like the list builders pass field_map=SORT_FIELD_MAP.
        result = super().translate_sort(sort_params, *args, **kwargs)
        if isinstance(result, tuple):
            sql, *rest = result
            return (rewrite_v1_sql_to_v2(sql), *rest)
        return rewrite_v1_sql_to_v2(result)


__all__ = [
    "ClickHouseFilterBuilderV2",
    "rewrite_v1_sql_to_v2",
    "rewrite_and_apply_v2_settings",
]
