from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
    truncate,
)
from ai_tools.registry import register_tool

# Spans larger than this are truncated (head+tail) unless exact=True
LARGE_SPAN_THRESHOLD = 3000


class ReadTraceSpanInput(PydanticBaseModel):
    trace_id: UUID = Field(description="The UUID of the trace")
    span_id: str = Field(description="The ID of the span to read")
    exact: bool = Field(
        default=False,
        description=(
            "If True, return the FULL raw content regardless of size. "
            "Use this when you need verbatim quotes for evidence in submit_trace_finding. "
            "If False (default), large spans are truncated to save context."
        ),
    )


def _format_content(raw, max_len: int) -> str:
    """Format span content (input/output/metadata) with truncation."""
    if raw is None:
        return ""
    text = str(raw)
    if len(text) <= max_len:
        return text
    # Head + tail pattern for large content
    head = text[: max_len - 500]
    tail = text[-400:]
    omitted = len(text) - (max_len - 100)
    return f"{head}\n\n... [{omitted} chars omitted] ...\n\n{tail}"


@register_tool
class ReadTraceSpanTool(BaseTool):
    name = "read_trace_span"
    description = (
        "Reads the content of a specific span in a trace, including input, "
        "output, metadata, and model info. Use exact=true only when you need "
        "verbatim quotes for evidence in submit_trace_finding."
    )
    category = "tracing"
    input_model = ReadTraceSpanInput

    def execute(self, params: ReadTraceSpanInput, context: ToolContext) -> ToolResult:
        from tracer.models.trace import Trace
        from tracer.services.clickhouse.v2 import get_reader

        # Verify trace access
        try:
            Trace.objects.get(
                id=params.trace_id,
                project__organization=context.organization,
            )
        except Trace.DoesNotExist:
            return ToolResult.not_found("Trace", str(params.trace_id))

        # Read all spans for the trace from CH 25.3 — was
        # ObservationSpan.objects.filter(trace_id=, deleted=False)
        # .values_list("id", "name") + multiple subsequent .get() calls.
        # Loading once and indexing in Python avoids 1-4 extra CH round-trips
        # for the id/exact-name/case-insensitive-name fallback chain.
        with get_reader() as reader:
            ch_spans = reader.list_by_trace(str(params.trace_id))
        all_spans = [(s.id, s.name) for s in ch_spans]
        valid_ids = {str(sid) for sid, _ in all_spans}
        valid_names = {name: str(sid) for sid, name in all_spans if name}
        by_id = {str(s.id): s for s in ch_spans}

        span = None

        # 1. Exact ID match
        if params.span_id in valid_ids:
            span = by_id[params.span_id]

        # 2. Exact name match
        if not span and params.span_id in valid_names:
            span = by_id[valid_names[params.span_id]]

        # 3. Case-insensitive name match
        if not span:
            for name, sid in valid_names.items():
                if params.span_id.lower() == name.lower():
                    span = by_id[sid]
                    break

        # 4. Not found — return error with valid IDs so LLM can self-correct
        if not span:
            hint_lines = [f"  `{s[0]}` — {s[1] or '(unnamed)'}" for s in all_spans[:20]]
            return ToolResult.error(
                f"Span `{params.span_id}` not found in trace `{params.trace_id}`.\n\n"
                f"Valid span IDs for this trace:\n" + "\n".join(hint_lines),
                error_code="NOT_FOUND",
            )

        # Build header
        info = key_value_block(
            [
                ("Span ID", f"`{span.id}`"),
                ("Name", span.name or "—"),
                ("Type", span.observation_type or "—"),
                ("Status", span.status or "—"),
                ("Model", span.model or "—"),
                ("Latency", f"{span.latency_ms}ms" if span.latency_ms else "—"),
                ("Tokens", str(span.total_tokens) if span.total_tokens else "—"),
                (
                    "Parent",
                    f"`{span.parent_span_id}`" if span.parent_span_id else "root",
                ),
                ("Time", format_datetime(span.start_time)),
            ]
        )

        content = section(f"Span: {span.name or span.id}", info)

        # Content size limit depends on exact flag
        max_len = 50000 if params.exact else LARGE_SPAN_THRESHOLD

        # Input
        if span.input:
            formatted = _format_content(span.input, max_len)
            content += f"\n\n### Input\n\n```\n{formatted}\n```"

        # Output
        if span.output:
            formatted = _format_content(span.output, max_len)
            content += f"\n\n### Output\n\n```\n{formatted}\n```"

        # Metadata
        if span.metadata and str(span.metadata) != "{}":
            formatted = _format_content(span.metadata, min(max_len, 1000))
            content += f"\n\n### Metadata\n\n```\n{formatted}\n```"

        # Span events (errors, logs, etc.)
        if span.span_events and str(span.span_events) != "[]":
            formatted = _format_content(span.span_events, min(max_len, 1000))
            content += f"\n\n### Events\n\n```\n{formatted}\n```"

        data = {
            "span_id": str(span.id),
            "name": span.name,
            "type": span.observation_type,
            "status": span.status,
            "model": span.model,
            "latency_ms": span.latency_ms,
            "total_tokens": span.total_tokens,
            "parent_span_id": span.parent_span_id,
        }

        return ToolResult(content=content, data=data)
