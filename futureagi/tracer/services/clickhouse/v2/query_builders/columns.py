"""
Single source of truth for v1 → v2 column-name mapping.

Used by:
  - v2 query builders (for direct reference, e.g. `cols.ATTRS_STRING`)
  - the parity-shadow harness (for cross-version result comparison)
  - the migration adapter (sanity check at backfill time)

Every column reference in v2 query builders MUST come through this module,
not hard-coded strings. This makes a future schema change a one-file edit.
"""
from __future__ import annotations

from typing import Final


# ─── Soft-delete + dedup ─────────────────────────────────────────────────────
IS_DELETED: Final[str] = "is_deleted"             # v1: _peerdb_is_deleted
VERSION:    Final[str] = "_version"               # v1: _peerdb_version

# ─── Typed attribute Maps ────────────────────────────────────────────────────
ATTRS_STRING: Final[str] = "attrs_string"         # v1: span_attr_str
ATTRS_NUMBER: Final[str] = "attrs_number"         # v1: span_attr_num
ATTRS_BOOL:   Final[str] = "attrs_bool"           # v1: span_attr_bool

# ─── Typed JSON overflow ─────────────────────────────────────────────────────
ATTRIBUTES_EXTRA: Final[str] = "attributes_extra" # v1: span_attributes_raw  (String JSON)
RESOURCE_ATTRS:   Final[str] = "resource_attrs"   # v1: resource_attributes_raw (String JSON)
METADATA_JSON:    Final[str] = "metadata"         # v1: metadata_map (Map)

# ─── MATERIALIZED hot keys (NEW in v2 — surface them so builders can use directly) ─
LLM_REQUEST_MODEL:  Final[str] = "llm_request_model"
LLM_RESPONSE_MODEL: Final[str] = "llm_response_model"
LLM_FINISH_REASON:  Final[str] = "llm_finish_reason"
EMBEDDING_MODEL:    Final[str] = "embedding_model"
STREAMING:          Final[str] = "streaming"
TEMPERATURE:        Final[str] = "temperature"
TOP_P:              Final[str] = "top_p"
MAX_TOKENS:         Final[str] = "max_tokens"


# ─── JSON path-access helpers ────────────────────────────────────────────────
# CH 25.x typed JSON columns are queried via path access, NOT via JSONExtract*.
# Helper functions produce the correct expression text given a JSON column name,
# a dotted path, and a CH type hint.

def json_path(column: str, path: str, ch_type: str) -> str:
    """
    Return the CH expression for `column.path.:Type`.

    >>> json_path("attributes_extra", "gen_ai.request.model", "String")
    'attributes_extra.gen_ai.request.model.:String'

    The dot-flattening happens at write time (DECISIONS #018), so a customer
    attribute originally keyed `"gen_ai.request.model": "gpt-4o"` lands stored
    as `{"gen_ai": {"request": {"model": "gpt-4o"}}}` and is read via the
    natural dotted path.

    Operators querying via clickhouse-client can use the same syntax:
        SELECT attributes_extra.gen_ai.request.model.:String AS model FROM spans;
    """
    if not column or not path or not ch_type:
        raise ValueError("json_path: column, path, and ch_type must all be non-empty")
    return f"{column}.{path}.:{ch_type}"


def json_path_string(column: str, path: str) -> str:
    return json_path(column, path, "String")


def json_path_int(column: str, path: str) -> str:
    return json_path(column, path, "Int64")


def json_path_float(column: str, path: str) -> str:
    return json_path(column, path, "Float64")


def json_path_bool(column: str, path: str) -> str:
    return json_path(column, path, "Bool")


# Reverse-mapping for the parity shadow harness so it can translate a v1
# query's WHERE-clause column refs to v2 equivalents and verify the diff is
# only the renames (not a semantic drift).
V1_TO_V2: Final[dict[str, str]] = {
    "_peerdb_is_deleted":     IS_DELETED,
    "_peerdb_version":        VERSION,
    "span_attr_str":          ATTRS_STRING,
    "span_attr_num":          ATTRS_NUMBER,
    "span_attr_bool":         ATTRS_BOOL,
    # NOTE: span_attributes_raw / metadata_map don't have a simple rename
    # because their TYPES differ. The query builder MUST manually rewrite.
}
