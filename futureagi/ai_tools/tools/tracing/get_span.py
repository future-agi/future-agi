from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetSpanInput(PydanticBaseModel):
    span_id: str = Field(description="The ID of the span/observation to retrieve")


@register_tool
class GetSpanTool(BaseTool):
    name = "get_span"
    description = (
        "Returns detailed information about a specific span/observation, including "
        "name, type, timing, model info, token counts, cost, input/output, "
        "parent span info, and child spans."
    )
    category = "tracing"
    input_model = GetSpanInput

    def execute(self, params: GetSpanInput, context: ToolContext) -> ToolResult:

        from tracer.models.observation_span import ObservationSpan
        from ai_tools.tools.tracing._utils import resolve_span

        span, unresolved = resolve_span(
            params.span_id,
            context,
            title="Span Required",
        )
        if unresolved:
            return unresolved

        # Calculate duration
        duration = f"{span.latency_ms}ms" if span.latency_ms else "—"

        info = key_value_block(
            [
                ("ID", f"`{span.id}`"),
                ("Name", span.name or "—"),
                ("Type", span.observation_type or "—"),
                ("Status", span.status or "—"),
                ("Model", span.model or "—"),
                ("Provider", span.provider or "—"),
                ("Duration", duration),
                ("Start Time", format_datetime(span.start_time)),
                ("End Time", format_datetime(span.end_time)),
                (
                    "Prompt Tokens",
                    str(span.prompt_tokens) if span.prompt_tokens else "—",
                ),
                (
                    "Completion Tokens",
                    str(span.completion_tokens) if span.completion_tokens else "—",
                ),
                ("Total Tokens", str(span.total_tokens) if span.total_tokens else "—"),
                ("Cost", f"${format_number(span.cost, 4)}" if span.cost else "—"),
                ("Trace", f"`{span.trace_id}`" if span.trace_id else "—"),
                ("Project", span.project.name if span.project else "—"),
                (
                    "Parent Span",
                    (
                        f"`{span.parent_span_id}`"
                        if span.parent_span_id
                        else "—(root span)"
                    ),
                ),
                ("Tags", ", ".join(span.tags) if span.tags else "—"),
                ("Created", format_datetime(span.created_at)),
            ]
        )

        content = section(f"Span: {span.name or span.id}", info)

        # Input/Output
        if span.input:
            content += (
                f"\n\n### Input\n\n```json\n{truncate(str(span.input), 500)}\n```"
            )
        if span.output:
            content += (
                f"\n\n### Output\n\n```json\n{truncate(str(span.output), 500)}\n```"
            )

        # Model parameters
        if span.model_parameters:
            content += f"\n\n### Model Parameters\n\n```json\n{truncate(str(span.model_parameters), 300)}\n```"

        # Metadata
        if span.metadata:
            content += (
                f"\n\n### Metadata\n\n```json\n{truncate(str(span.metadata), 300)}\n```"
            )

        # Child spans
        children = ObservationSpan.objects.filter(
            parent_span_id=span.id, deleted=False
        ).order_by("start_time", "created_at")[:20]

        if children:
            content += f"\n\n### Child Spans ({children.count()})\n\n"
            child_rows = []
            for child in children:
                child_dur = f"{child.latency_ms}ms" if child.latency_ms else "—"
                child_rows.append(
                    [
                        f"`{str(child.id)[:12]}...`",
                        truncate(child.name, 30),
                        child.observation_type or "—",
                        child.model or "—",
                        child_dur,
                        child.status or "—",
                    ]
                )
            content += markdown_table(
                ["ID", "Name", "Type", "Model", "Duration", "Status"],
                child_rows,
            )

        data = {
            "id": str(span.id),
            "name": span.name,
            "type": span.observation_type,
            "model": span.model,
            "status": span.status,
            "latency_ms": span.latency_ms,
            "prompt_tokens": span.prompt_tokens,
            "completion_tokens": span.completion_tokens,
            "total_tokens": span.total_tokens,
            "cost": float(span.cost) if span.cost else None,
            "trace_id": str(span.trace_id) if span.trace_id else None,
            "parent_span_id": span.parent_span_id,
        }

        return ToolResult(content=content, data=data)
