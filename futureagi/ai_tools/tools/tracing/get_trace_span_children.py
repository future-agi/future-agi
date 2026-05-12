from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section
from ai_tools.registry import register_tool


class GetTraceSpanChildrenInput(PydanticBaseModel):
    trace_id: str = Field(
        default="",
        description="Trace UUID or exact trace name.",
    )
    span_id: str = Field(description="The ID of the parent span")


@register_tool
class GetTraceSpanChildrenTool(BaseTool):
    name = "get_trace_span_children"
    description = (
        "Returns the child spans of a given parent span in a trace. "
        "Use this to navigate the span hierarchy and understand execution flow."
    )
    category = "tracing"
    input_model = GetTraceSpanChildrenInput

    def execute(
        self, params: GetTraceSpanChildrenInput, context: ToolContext
    ) -> ToolResult:
        from tracer.models.observation_span import ObservationSpan
        from ai_tools.tools.tracing._utils import resolve_trace

        trace, unresolved = resolve_trace(
            params.trace_id,
            context,
            title="Trace Required To List Span Children",
        )
        if unresolved:
            return unresolved

        children = ObservationSpan.objects.filter(
            trace=trace,
            parent_span_id=params.span_id,
            deleted=False,
        ).order_by("start_time", "created_at")[:50]

        if not children:
            return ToolResult(
                content=section(
                    "Child Spans",
                    f"Span `{params.span_id}` has no child spans.",
                ),
                data={"children": [], "count": 0},
            )

        rows = []
        data_list = []
        for child in children:
            rows.append(
                [
                    f"`{child.id}`",
                    child.name or "—",
                    child.observation_type or "—",
                    child.status or "—",
                    f"{child.latency_ms}ms" if child.latency_ms else "—",
                    child.model or "—",
                ]
            )
            data_list.append(
                {
                    "span_id": str(child.id),
                    "name": child.name,
                    "type": child.observation_type,
                    "status": child.status,
                    "latency_ms": child.latency_ms,
                    "model": child.model,
                }
            )

        content = section(
            f"Children of `{params.span_id}` ({len(rows)})",
            markdown_table(
                ["ID", "Name", "Type", "Status", "Latency", "Model"],
                rows,
            ),
        )

        return ToolResult(
            content=content, data={"children": data_list, "count": len(data_list)}
        )
