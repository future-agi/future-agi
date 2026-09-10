"""Bound MCP content without dropping rows or invalidating pagination cursors."""

from __future__ import annotations

import json
from typing import Any

MAX_RESPONSE_BYTES = 64 * 1024
MAX_BLOB_STRING_CHARS = 2000
BLOB_FIELDS = frozenset(
    {
        "input",
        "output",
        "metadata",
        "messages",
        "content",
        "transcript",
        "transcripts",
        "prompt_config",
        "prompt_config_snapshot",
        "error_message",
        "configuration",
        "config",
        "graph",
        "evaluation_results",
    }
)
PAGINATION_FIELDS = frozenset(
    {"next", "previous", "next_cursor", "cursor", "prev_cursor"}
)


class ResponseTooLargeError(ValueError):
    pass


def bounded_response(data: Any) -> Any:
    """Keep complete pages; preview long blob strings and reject oversized results."""
    truncated = []
    visited = 0

    def visit(value: Any, path: str, blob: bool = False, depth: int = 0) -> Any:
        nonlocal visited
        visited += 1
        # Each JSON node costs at least one byte; stop before copying a huge
        # collection or recursing through arbitrarily nested stored content.
        if visited > MAX_RESPONSE_BYTES or depth > 100:
            raise ResponseTooLargeError(
                "Response exceeds the MCP complexity limit. Request a smaller page or narrower filters."
            )
        if isinstance(value, dict):
            return {
                key: visit(
                    child,
                    f"{path}/{key}",
                    key not in PAGINATION_FIELDS and (blob or key in BLOB_FIELDS),
                    depth + 1,
                )
                for key, child in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                visit(child, f"{path}/{index}", blob, depth + 1)
                for index, child in enumerate(value)
            ]
        if isinstance(value, str) and blob and len(value) > MAX_BLOB_STRING_CHARS:
            if len(truncated) < 100:
                truncated.append(path)
            return value[:MAX_BLOB_STRING_CHARS] + "… [truncated]"
        return value

    result = visit(data, "")
    if truncated:
        if not isinstance(result, dict):
            result = {"result": result}
        result["_mcp"] = {
            "truncated": True,
            "fields": truncated,
            "message": "Long content was shortened. Use the API for full content.",
        }
    size = 0
    for chunk in json.JSONEncoder(default=str, ensure_ascii=False).iterencode(result):
        size += len(chunk.encode("utf-8"))
        if size > MAX_RESPONSE_BYTES:
            raise ResponseTooLargeError(
                "Response exceeds the MCP size limit. Request a smaller page or narrower filters; use the API for full content."
            )
    return result
