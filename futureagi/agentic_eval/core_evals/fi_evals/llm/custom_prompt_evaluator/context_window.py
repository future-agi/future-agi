"""
Context windowing for large eval inputs.

Handles truncation, chunked reading, and prioritization of data
when eval inputs exceed LLM context limits (traces, spans, logs, etc.).

Strategy:
1. Prioritize structured fields (input, output, error) over metadata
2. Truncate large string values with markers
3. Summarize deeply nested JSON
4. Support chunked reading for very large inputs
"""

import json
import structlog
from typing import Any, Optional

logger = structlog.get_logger(__name__)

# Default context limits
DEFAULT_MAX_TOTAL_CHARS = 50000  # ~12K tokens for most LLMs
DEFAULT_MAX_FIELD_CHARS = 10000  # Max per individual field
DEFAULT_MAX_DEPTH = 5  # Max JSON nesting depth for expansion


def fit_to_context(
    data: Any,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS,
    label: str = "data",
) -> str:
    """
    Fit any data into a context window, truncating intelligently.

    Args:
        data: The data to fit (str, dict, list, or any serializable)
        max_total_chars: Maximum total character count
        max_field_chars: Maximum chars per individual field value
        label: Label for the data section

    Returns:
        Formatted string that fits within the context window
    """
    if data is None:
        return ""

    if isinstance(data, str):
        return _truncate_string(data, max_total_chars)

    if isinstance(data, dict):
        return _format_dict(data, max_total_chars, max_field_chars)

    if isinstance(data, list):
        return _format_list(data, max_total_chars, max_field_chars)

    # Fallback: serialize and truncate
    try:
        serialized = json.dumps(data, indent=2, default=str)
        return _truncate_string(serialized, max_total_chars)
    except Exception:
        return _truncate_string(str(data), max_total_chars)


def fit_trace_to_context(
    trace_data: dict,
    max_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> str:
    """
    Format trace data (with spans) for eval context.
    Prioritizes: trace input/output > span summaries > span details.
    """
    parts = []
    used = 0
    budget = max_chars

    # 1. Trace-level info (highest priority)
    trace_meta = {
        "id": trace_data.get("id"),
        "name": trace_data.get("name"),
        "status": trace_data.get("status"),
        "error": trace_data.get("error"),
    }
    trace_meta_str = _format_dict(
        {k: v for k, v in trace_meta.items() if v},
        max_total=2000, max_field=500,
    )
    parts.append(f"## Trace\n{trace_meta_str}")
    used += len(parts[-1])

    # 2. Trace input/output (high priority)
    for field in ["input", "output"]:
        val = trace_data.get(field)
        if val:
            field_budget = min((budget - used) // 2, 8000)
            if field_budget > 100:
                formatted = fit_to_context(val, max_total_chars=field_budget, label=field)
                parts.append(f"### Trace {field.title()}\n{formatted}")
                used += len(parts[-1])

    # 3. Spans summary (medium priority)
    spans = trace_data.get("observation_spans") or trace_data.get("spans") or []
    if spans and used < budget - 2000:
        span_budget = budget - used - 500
        span_text = _format_spans(spans, max_chars=span_budget)
        parts.append(f"### Spans ({len(spans)} total)\n{span_text}")

    result = "\n\n".join(parts)
    return result[:max_chars]


def fit_span_to_context(
    span_data: dict,
    max_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> str:
    """
    Format a single span for eval context.
    Prioritizes: input/output > attributes > metadata.
    """
    parts = []
    used = 0
    budget = max_chars

    # Priority fields
    priority_fields = [
        ("name", 200),
        ("observation_type", 100),
        ("model", 100),
        ("status", 100),
        ("status_message", 500),
        ("latency_ms", 50),
        ("total_tokens", 50),
        ("cost", 50),
    ]

    meta_parts = []
    for field, limit in priority_fields:
        val = span_data.get(field)
        if val is not None:
            meta_parts.append(f"**{field}:** {_truncate_string(str(val), limit)}")

    if meta_parts:
        parts.append("## Span Info\n" + "\n".join(meta_parts))
        used += len(parts[-1])

    # Input/Output (highest content priority)
    for field in ["input", "output"]:
        val = span_data.get(field)
        if val and used < budget - 2000:
            field_budget = min((budget - used) // 2, 10000)
            formatted = fit_to_context(val, max_total_chars=field_budget, label=field)
            parts.append(f"### {field.title()}\n{formatted}")
            used += len(parts[-1])

    # Span attributes (if room)
    attrs = span_data.get("span_attributes") or span_data.get("eval_attributes")
    if attrs and used < budget - 1000:
        attr_budget = min(budget - used - 500, 5000)
        formatted = fit_to_context(attrs, max_total_chars=attr_budget, label="attributes")
        parts.append(f"### Attributes\n{formatted}")
        used += len(parts[-1])

    # Metadata (lowest priority)
    metadata = span_data.get("metadata")
    if metadata and used < budget - 500:
        meta_budget = min(budget - used - 200, 2000)
        formatted = fit_to_context(metadata, max_total_chars=meta_budget, label="metadata")
        parts.append(f"### Metadata\n{formatted}")

    result = "\n\n".join(parts)
    return result[:max_chars]


def fit_row_to_context(
    row_data: dict,
    max_chars: int = DEFAULT_MAX_TOTAL_CHARS,
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS,
) -> str:
    """
    Format a dataset row for eval context with size limits.
    """
    return _format_dict(row_data, max_total=max_chars, max_field=max_field_chars)


def chunk_large_text(
    text: str,
    chunk_size: int = 10000,
    overlap: int = 500,
) -> list[str]:
    """
    Split large text into overlapping chunks for sequential reading.

    Args:
        text: The text to chunk
        chunk_size: Maximum chars per chunk
        overlap: Overlap between chunks for context continuity

    Returns:
        List of text chunks
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size

        # Try to break at a natural boundary
        if end < len(text):
            # Look for newline near the end
            newline_pos = text.rfind("\n", start + chunk_size - 200, end)
            if newline_pos > start:
                end = newline_pos + 1
            else:
                # Look for space
                space_pos = text.rfind(" ", start + chunk_size - 100, end)
                if space_pos > start:
                    end = space_pos + 1

        chunk = text[start:end]
        chunk_num = len(chunks) + 1
        total_chunks = (len(text) + chunk_size - 1) // chunk_size
        header = f"[Chunk {chunk_num}/{total_chunks}, chars {start}-{end} of {len(text)}]\n"

        chunks.append(header + chunk)
        start = end - overlap  # Overlap for context continuity

    return chunks


# ─── Private helpers ───

def _truncate_string(s: str, max_chars: int) -> str:
    """Truncate a string with a marker, guaranteeing output length <= max_chars."""
    if len(s) <= max_chars:
        return s
    logger.debug("context_window_field_truncated", original_length=len(s), limit=max_chars)
    marker = f"\n... [truncated, {len(s)} total chars]"
    prefix_len = max(0, max_chars - len(marker))
    return s[:prefix_len] + marker


def _format_dict(
    d: dict,
    max_total: int = DEFAULT_MAX_TOTAL_CHARS,
    max_field: int = DEFAULT_MAX_FIELD_CHARS,
    depth: int = 0,
) -> str:
    """Format a dict with field-level truncation."""
    if depth > DEFAULT_MAX_DEPTH:
        return "{...}"

    parts = []
    used = 0

    for key, value in d.items():
        if used >= max_total - 100:
            remaining = len(d) - len(parts)
            if remaining > 0:
                parts.append(f"... [{remaining} more fields]")
            break

        field_budget = min(max_field, max_total - used - 50)
        if field_budget < 50:
            parts.append(f"... [{len(d) - len(parts)} more fields]")
            break

        if value is None:
            continue
        elif isinstance(value, str):
            val_str = _truncate_string(value, field_budget)
        elif isinstance(value, dict):
            if depth < 2 and len(json.dumps(value, default=str)) < field_budget:
                val_str = _format_dict(value, max_total=field_budget, max_field=field_budget // 2, depth=depth + 1)
            else:
                val_str = f"{{...}} ({len(value)} keys)"
        elif isinstance(value, list):
            if len(value) <= 3 and all(isinstance(v, (str, int, float, bool)) for v in value):
                val_str = str(value)
            else:
                val_str = f"[...] ({len(value)} items)"
        else:
            val_str = _truncate_string(str(value), field_budget)

        line = f"**{key}:** {val_str}"
        parts.append(line)
        used += len(line) + 1

    return "\n".join(parts)


def _format_list(
    lst: list,
    max_total: int = DEFAULT_MAX_TOTAL_CHARS,
    max_field: int = DEFAULT_MAX_FIELD_CHARS,
) -> str:
    """Format a list with item-level truncation."""
    parts = []
    used = 0

    for i, item in enumerate(lst):
        if used >= max_total - 100:
            remaining = len(lst) - i
            parts.append(f"... [{remaining} more items]")
            break

        item_budget = min(max_field, (max_total - used) // max(1, len(lst) - i))
        if item_budget < 50:
            parts.append(f"... [{len(lst) - i} more items]")
            break

        if isinstance(item, dict):
            item_str = _format_dict(item, max_total=item_budget, max_field=item_budget // 3)
        elif isinstance(item, str):
            item_str = _truncate_string(item, item_budget)
        else:
            item_str = _truncate_string(str(item), item_budget)

        parts.append(f"{i + 1}. {item_str}")
        used += len(parts[-1]) + 1

    return "\n".join(parts)


def _format_spans(spans: list, max_chars: int = 20000) -> str:
    """Format a list of spans with summaries."""
    parts = []
    used = 0

    for i, span_wrapper in enumerate(spans):
        if used >= max_chars - 200:
            parts.append(f"... [{len(spans) - i} more spans]")
            break

        # Handle both flat and nested span formats
        span = span_wrapper
        if isinstance(span_wrapper, dict) and "observationSpan" in span_wrapper:
            span = span_wrapper["observationSpan"]

        if not isinstance(span, dict):
            continue

        name = span.get("name", "unnamed")
        obs_type = span.get("observation_type", span.get("observationType", "?"))
        model = span.get("model", "")
        latency = span.get("latency_ms", span.get("latencyMs", "?"))
        tokens = span.get("total_tokens", span.get("totalTokens", ""))
        status = span.get("status", "")

        line = f"  {i+1}. [{obs_type}] {name}"
        if model:
            line += f" (model: {model})"
        if latency:
            line += f" | {latency}ms"
        if tokens:
            line += f" | {tokens} tokens"
        if status and status != "UNSET":
            line += f" | {status}"

        parts.append(line)
        used += len(line) + 1

        # Include span input/output summary if budget allows
        per_span_budget = (max_chars - used) // max(1, len(spans) - i)
        if per_span_budget > 200:
            for field in ["input", "output"]:
                val = span.get(field)
                if val:
                    val_str = json.dumps(val, default=str) if isinstance(val, (dict, list)) else str(val)
                    if len(val_str) > 200:
                        val_str = val_str[:200] + "..."
                    parts.append(f"     {field}: {val_str}")
                    used += len(parts[-1]) + 1

    return "\n".join(parts)
