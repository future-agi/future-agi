"""Read-path selectors that shape CH span/trace list rows for presentation.

Kept out of the view layer so the trace-list and span-list read paths share one
implementation of the typed-attribute merge and heavy-content flattening.
"""

import json
from collections.abc import Sequence
from typing import Any

from django.conf import settings

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

_TRUNCATION_SUFFIX = "…"


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    suffix = _TRUNCATION_SUFFIX.encode("utf-8")
    body_budget = max(0, max_bytes - len(suffix))
    return encoded[:body_budget].decode("utf-8", errors="ignore") + (
        _TRUNCATION_SUFFIX if max_bytes >= len(suffix) else ""
    )


def bound_observe_list_value(value: Any) -> Any:
    """Bound one list-cell preview while preserving ordinary scalar types.

    Trace/span detail endpoints remain the full-content source. List rows are
    deliberately previews so one exceptionally large prompt or structured
    attribute cannot be duplicated across the HTTP payload, AG Grid cache,
    renderer and tooltip until the browser tab exhausts memory.
    """

    max_bytes = int(settings.OBSERVABILITY_LIST_CELL_PREVIEW_MAX_BYTES)
    if isinstance(value, str):
        return _truncate_utf8(value, max_bytes)
    if isinstance(value, (dict, list, tuple)):
        try:
            rendered = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError):
            rendered = str(value)
        if len(rendered.encode("utf-8")) > max_bytes:
            return _truncate_utf8(rendered, max_bytes)
    return value


def flatten_span_attributes_into_entry(
    entry: dict[str, Any], row: dict[str, Any]
) -> None:
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
            entry[key] = bound_observe_list_value(value)


def merge_content_rows(
    rows: list[dict[str, Any]],
    content_rows: list[dict[str, Any]],
    *,
    id_key: str | Sequence[str],
    keys: Sequence[str],
) -> dict[Any, dict[str, Any]]:
    """Merge heavy content columns into `rows` in place; return the content index by `id_key`.

    Each key in `keys` is copied from the matching content row using a null-safe
    default (fresh instance for mutable maps/lists). Callers reuse the returned
    index for per-path extras (e.g. metadata JSON-parsing).
    """
    id_keys = (id_key,) if isinstance(id_key, str) else tuple(id_key)
    if not id_keys:
        raise ValueError("id_key must contain at least one field")

    def identity(row: dict[str, Any]) -> Any:
        values = tuple(str(row.get(key, "")) for key in id_keys)
        return values[0] if len(values) == 1 else values

    content_map = {identity(content): content for content in content_rows}
    for row in rows:
        content = content_map.get(identity(row), {})
        for key in keys:
            if key in _CONTENT_FACTORY_DEFAULTS:
                row[key] = content.get(key) or _CONTENT_FACTORY_DEFAULTS[key]()
            else:
                row[key] = content.get(key, _CONTENT_SCALAR_DEFAULTS.get(key, ""))
    return content_map
