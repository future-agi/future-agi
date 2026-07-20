"""Prefix-dedup pagination for the CH list endpoints.

The span/trace list Phase-1 queries dropped ``LIMIT 1 BY <key>`` — that
clause de-duplicated globally but forced ClickHouse to read + full-sort the
entire filtered window before paginating (O(window) memory; OOM-crashed the
server at ~10M rows). De-duplicating only the returned page in Python fixed
the crash but broke a cross-page invariant: a duplicate pair straddling a
page boundary (un-merged ReplacingMergeTree versions), or a multi-root trace
whose roots sort far apart, could surface the same key on two pages.

``paginate_deduped()`` restores the global-dedup semantics at bounded cost:
the builder fetches the sorted PREFIX ``[0, offset + 2*page_size)`` (a
bounded top-K read — NOT the O(window) sort), this helper de-duplicates the
prefix keeping the first occurrence per key (the same row ``LIMIT 1 BY``
kept), and slices ``[offset, offset + page_size)`` of the de-duplicated
sequence. Every page is then a disjoint slice of the same global
de-duplicated stream, so:

* a key can never appear on two pages, and
* no key is ever skipped, as long as the prefix holds at least
  ``offset + page_size`` distinct keys — guaranteed for up to ``page_size``
  duplicates in the prefix (duplicates are rare, transient singletons; a
  pathological mass-duplication can only shorten a page, never duplicate
  or permanently hide a row, and self-heals on the next merge).

Cost: page 0 transfers ``2*page_size`` light rows (same order as before);
page N transfers ``N*page_size + 2*page_size`` — a few MB even hundreds of
pages deep, versus the whole window under the old ``LIMIT 1 BY``.
"""

from typing import Any


def paginate_deduped(
    rows: list[dict],
    key_field: str,
    page_number: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], bool]:
    """De-duplicate a sorted row prefix by ``key_field`` and slice one page.

    Args:
        rows: The sorted prefix ``[0, offset + 2*page_size)`` from Phase 1.
        key_field: Row key to de-duplicate on (``"id"`` / ``"trace_id"``).
        page_number: Zero-based page index.
        page_size: Rows per page.

    Returns:
        ``(page_rows, has_more)`` — the de-duplicated page slice, and whether
        at least one more de-duplicated row exists beyond it.
    """
    offset = page_number * page_size
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get(key_field, ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped[offset : offset + page_size], len(deduped) > offset + page_size
