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
    trace_id: str = Field(
        default="",
        description="Trace UUID or exact trace name.",
    )
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
        from tracer.models.observation_span import ObservationSpan
        from ai_tools.tools.tracing._utils import resolve_trace

        trace, unresolved = resolve_trace(
            params.trace_id,
            context,
            title="Trace Required To Read Span",
        )
        if unresolved:
            return unresolved

        # Find span in this trace — try by ID, then by exact name, then partial name
        all_spans = list(
            ObservationSpan.objects.filter(
                trace=trace, deleted=False
            ).values_list("id", "name")
        )
        valid_ids = {str(s[0]) for s in all_spans}
        valid_names = {s[1]: str(s[0]) for s in all_spans if s[1]}

        span = None

        # 1. Exact ID match
        if params.span_id in valid_ids:
            span = ObservationSpan.objects.get(
                id=params.span_id, trace=trace
            )

        # 2. Exact name match
        if not span and params.span_id in valid_names:
            span = ObservationSpan.objects.get(
                id=valid_names[params.span_id], trace=trace
            )

        # 3. Case-insensitive name match
        if not span:
            for name, sid in valid_names.items():
                if params.span_id.lower() == name.lower():
                    span = ObservationSpan.objects.get(id=sid, trace=trace)
                    break

        # 4. Not found — return valid IDs so Falcon can self-correct
        if not span:
            hint_lines = [f"  `{s[0]}` — {s[1] or '(unnamed)'}" for s in all_spans[:20]]
            return ToolResult.needs_input(
                f"Span `{params.span_id}` not found in trace `{params.trace_id}`.\n\n"
                f"Valid span IDs for this trace:\n" + "\n".join(hint_lines),
                data={
                    "requires_span_id": True,
                    "trace_id": str(trace.id),
                    "spans": [
                        {"id": str(span_id), "name": name}
                        for span_id, name in all_spans[:20]
                    ],
                },
                missing_fields=["span_id"],
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
