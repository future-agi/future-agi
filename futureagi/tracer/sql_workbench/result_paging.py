"""Bounded snapshot pages; large cells are explicit previews, never silent loss."""

import json

from .types import ExecutionSnapshot, SQLResult

PAGE_BYTES = 56 * 1024


def page_snapshot(
    snapshot: ExecutionSnapshot, *, offset: int, limit: int
) -> ExecutionSnapshot:
    if not 0 <= offset <= 10000 or not 1 <= limit <= 1000:
        raise ValueError("Result offset must be 0–10,000 and limit 1–1,000.")
    if not snapshot["result"]:
        return snapshot
    result = snapshot["result"]
    rows = result.get("rows", [])
    page: SQLResult = {
        **result,
        "rows": [],
        "offset": offset,
        "next_offset": None,
        "has_more": False,
        "page_row_count": 0,
        "previewed": False,
        "retrieval_note": "",
    }
    envelope: ExecutionSnapshot = {**snapshot, "result": page}

    def size(value):
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))

    def preview(value, budget):
        # Cells are scalars (nested driver values were serialized by cell()).
        if isinstance(value, str) and size(value) > budget:
            # Bound encoded JSON bytes, not Unicode codepoints.
            low, high = 0, len(value)
            while low < high:
                mid = (low + high + 1) // 2
                if size(value[:mid] + "…") <= budget:
                    low = mid
                else:
                    high = mid - 1
            return value[:low] + "…"
        return value

    # Leave space for continuation fields and date-time normalization. Metadata
    # itself can be wide (SQL + 100 aliases): expose that preview explicitly too.
    if size(envelope) > PAGE_BYTES - 4096:
        envelope["sql"] = preview(envelope["sql"], 2048)
        page["columns"] = [
            {key: preview(value, 256) for key, value in col.items()}
            for col in result["columns"]
        ]
        page["previewed"] = True
    remaining = PAGE_BYTES - size(envelope) - 1024
    for row in rows[offset : offset + limit]:
        encoded = size(row) + 2
        if encoded > remaining:
            if page["rows"]:
                break
            budget = max(16, (remaining - 2 * len(row)) // max(1, len(row)))
            row = [preview(value, budget) for value in row]
            encoded = size(row) + 2
            page["previewed"] = True
        page["rows"].append(row)
        remaining -= encoded
    page["page_row_count"] = len(page["rows"])
    end = offset + page["page_row_count"]
    page["has_more"] = end < len(rows)
    page["next_offset"] = end if page["has_more"] else None
    if page["previewed"]:
        page["retrieval_note"] = (
            "Some text is previewed. Use the authenticated CSV/Parquet download for full values before expiry."
        )
    return envelope
