from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_number,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class ListSpansInput(PydanticBaseModel):
    trace_id: str = Field(
        default="",
        description="Trace UUID or exact trace name to list spans for.",
    )
    limit: int = Field(default=50, ge=1, le=200, description="Max spans to return")
    span_type: Optional[str] = Field(
        default=None,
        description="Filter by span type: tool, chain, llm, retriever, embedding, agent, reranker, unknown, guardrail, evaluator, conversation",
    )


@register_tool
class ListSpansTool(BaseTool):
    name = "list_spans"
    description = (
        "Lists spans/observations for a specific trace, optionally filtered by type. "
        "Shows spans as an indented tree based on parent/child relationships."
    )
    category = "tracing"
    input_model = ListSpansInput

    def execute(self, params: ListSpansInput, context: ToolContext) -> ToolResult:

        from tracer.models.observation_span import ObservationSpan
        from ai_tools.tools.tracing._utils import resolve_trace

        trace, unresolved = resolve_trace(
            params.trace_id,
            context,
            title="Trace Required To List Spans",
        )
        if unresolved:
            return unresolved

        qs = ObservationSpan.objects.filter(trace=trace, deleted=False).order_by(
            "start_time", "created_at"
        )

        if params.span_type:
            qs = qs.filter(observation_type=params.span_type)

        total = qs.count()
        spans = list(qs[: params.limit])

        if not spans:
            filter_msg = f" (type={params.span_type})" if params.span_type else ""
            return ToolResult(
                content=section(
                    "Spans",
                    f"No spans found for trace `{trace.id}`{filter_msg}.",
                ),
                data={"spans": [], "total": 0},
            )

        # Build tree structure
        span_map = {s.id: s for s in spans}
        children_map = {}
        roots = []

        for s in spans:
            pid = s.parent_span_id
            if pid and pid in span_map:
                children_map.setdefault(pid, []).append(s)
            else:
                roots.append(s)

        # Flatten tree with indentation level
        flat_list = []

        def walk(node, depth=0):
            flat_list.append((node, depth))
            for child in children_map.get(node.id, []):
                walk(child, depth + 1)

        for root in roots:
            walk(root)

        # Build table
        rows = []
        data_list = []
        for span, depth in flat_list:
            indent = "  " * depth + ("|- " if depth > 0 else "")
            duration = f"{span.latency_ms}ms" if span.latency_ms else "—"
            tokens = str(span.total_tokens) if span.total_tokens else "—"
            cost = f"${format_number(span.cost, 4)}" if span.cost else "—"

            rows.append(
                [
                    f"`{str(span.id)}`",
                    indent + truncate(span.name, 30),
                    span.observation_type or "—",
                    span.model or "—",
                    duration,
                    tokens,
                    cost,
                    span.status or "—",
                ]
            )
            data_list.append(
                {
                    "id": str(span.id),
                    "name": span.name,
                    "type": span.observation_type,
                    "model": span.model,
                    "latency_ms": span.latency_ms,
                    "total_tokens": span.total_tokens,
                    "cost": float(span.cost) if span.cost else None,
                    "status": span.status,
                    "depth": depth,
                }
            )

        table = markdown_table(
            ["ID", "Name", "Type", "Model", "Duration", "Tokens", "Cost", "Status"],
            rows,
        )

        # Summary stats
        total_tokens = sum(s.total_tokens or 0 for s in spans)
        total_cost = sum(s.cost or 0 for s in spans)

        showing = f"Showing {len(rows)} of {total} spans"
        if params.span_type:
            showing += f" (type: {params.span_type})"
        showing += f"\n**Total Tokens:** {total_tokens} | **Total Cost:** ${format_number(total_cost, 4)}"

        content = section(
            f"Spans for Trace `{str(params.trace_id)}`", f"{showing}\n\n{table}"
        )

        if total > params.limit:
            content += f"\n\n_Showing {params.limit} of {total} spans. Increase limit to see more._"

        return ToolResult(content=content, data={"spans": data_list, "total": total})
