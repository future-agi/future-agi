"""Read-path selectors that shape CH span/trace list rows for presentation.

Kept out of the view layer so the trace-list and span-list read paths share one
implementation of the typed-attribute merge and heavy-content flattening.
"""

from collections.abc import Sequence
from typing import Any

# Attribute keys hidden from custom columns: internal payloads / duplicates of
# the input/output columns.
SKIP_ATTR_PREFIXES = (
    "raw.",
    "llm.input_messages",
    "llm.output_messages",
    "input.value",
    "output.value",
)

# Heavy content columns fetched in the Phase-1b query, with null-safe defaults.
# Mutable defaults use a factory so merged rows never share one instance.
_CONTENT_SCALAR_DEFAULTS: dict[str, str] = {
    "input": "",
    "output": "",
    "attributes_extra": "{}",
}
_CONTENT_FACTORY_DEFAULTS: dict[str, Any] = {
    "attrs_string": dict,
    "attrs_number": dict,
    "attrs_bool": dict,
    "trace_tags": list,
}


def flatten_span_attributes_into_entry(entry: dict[str, Any], row: dict[str, Any]) -> None:
    """Surface a span's merged attributes as top-level keys on `entry` for custom columns.

    Standard columns already on `entry` are not clobbered; internal/oversized
    payloads are skipped/truncated.
    """
    from tracer.services.clickhouse.v2.span_reader import merge_span_attributes

    attrs = merge_span_attributes(
        row.get("attrs_string"),
        row.get("attrs_number"),
        row.get("attrs_bool"),
        row.get("attributes_extra", "{}"),
    )
    for key, value in attrs.items():
        if key in entry or key.startswith(SKIP_ATTR_PREFIXES):
            continue
        if isinstance(value, str) and len(value) > 500:
            entry[key] = value[:500] + "..."
        else:
            entry[key] = value


def merge_content_rows(
    rows: list[dict[str, Any]],
    content_rows: list[dict[str, Any]],
    *,
    id_key: str,
    keys: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Merge heavy content columns into `rows` in place; return the content index by `id_key`.

    Each key in `keys` is copied from the matching content row using a null-safe
    default (fresh instance for mutable maps/lists). Callers reuse the returned
    index for per-path extras (e.g. metadata JSON-parsing).
    """
    content_map = {str(c.get(id_key, "")): c for c in content_rows}
    for row in rows:
        content = content_map.get(str(row.get(id_key, "")), {})
        for key in keys:
            if key in _CONTENT_FACTORY_DEFAULTS:
                row[key] = content.get(key) or _CONTENT_FACTORY_DEFAULTS[key]()
            else:
                row[key] = content.get(key, _CONTENT_SCALAR_DEFAULTS.get(key, ""))
    return content_map
