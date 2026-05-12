from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_number,
    section,
)
from ai_tools.registry import register_tool


class GetSpanTreeInput(PydanticBaseModel):
    trace_id: str = Field(
        default="",
        description=(
            "Trace UUID or exact trace name to get the span tree for. "
            "Omit it to list candidate traces."
        ),
    )


@register_tool
class GetSpanTreeTool(BaseTool):
    name = "get_span_tree"
    description = (
        "Returns the full span tree hierarchy for a trace, showing parent/child "
        "relationships as an indented tree with span name, duration, and status."
    )
    category = "tracing"
    input_model = GetSpanTreeInput

    def execute(self, params: GetSpanTreeInput, context: ToolContext) -> ToolResult:

        from tracer.models.observation_span import ObservationSpan
        from ai_tools.tools.tracing._utils import resolve_trace

        trace, unresolved = resolve_trace(
            params.trace_id,
            context,
            title="Trace Required For Span Tree",
        )
        if unresolved:
            return unresolved

        spans = list(
            ObservationSpan.objects.filter(trace=trace, deleted=False).order_by(
                "start_time", "created_at"
            )
        )

        if not spans:
            return ToolResult(
                content=section(
                    "Span Tree", f"No spans found for trace `{params.trace_id}`."
                ),
                data={"tree": [], "total_spans": 0},
            )

        # Build tree
        span_map = {s.id: s for s in spans}
        children_map = {}
        roots = []

        for s in spans:
            pid = s.parent_span_id
            if pid and pid in span_map:
                children_map.setdefault(pid, []).append(s)
            else:
                roots.append(s)

        # Render tree as text
        tree_lines = []
        tree_data = []

        def render(node, prefix="", is_last=True, depth=0):
            duration = f"{node.latency_ms}ms" if node.latency_ms else "—"
            tokens = f"{node.total_tokens}tok" if node.total_tokens else ""
            cost = f"${format_number(node.cost, 4)}" if node.cost else ""
            status_str = f" [{node.status}]" if node.status else ""
            model_str = f" ({node.model})" if node.model else ""

            connector = "└── " if is_last else "├── "
            if depth == 0:
                connector = ""

            details = " | ".join(
                filter(
                    None,
                    [
                        node.observation_type,
                        model_str.strip(" ()"),
                        duration,
                        tokens,
                        cost,
                    ],
                )
            )

            line = f"{prefix}{connector}{node.name}{status_str} — {details}"
            tree_lines.append(line)

            tree_data.append(
                {
                    "id": str(node.id),
                    "name": node.name,
                    "type": node.observation_type,
                    "model": node.model,
                    "latency_ms": node.latency_ms,
                    "total_tokens": node.total_tokens,
                    "cost": float(node.cost) if node.cost else None,
                    "status": node.status,
                    "depth": depth,
                    "parent_span_id": node.parent_span_id,
                }
            )

            kids = children_map.get(node.id, [])
            for i, child in enumerate(kids):
                is_child_last = i == len(kids) - 1
                child_prefix = prefix + (
                    "    " if is_last and depth > 0 else "│   " if depth > 0 else ""
                )
                render(child, child_prefix, is_child_last, depth + 1)

        for i, root in enumerate(roots):
            if i > 0:
                tree_lines.append("")  # blank line between root spans
            render(root)

        # Summary
        total_tokens = sum(s.total_tokens or 0 for s in spans)
        total_cost = sum(s.cost or 0 for s in spans)
        project_name = trace.project.name if trace.project else "—"

        header = (
            f"**Trace:** `{params.trace_id}` ({trace.name or '—'})\n"
            f"**Project:** {project_name}\n"
            f"**Total Spans:** {len(spans)} | "
            f"**Total Tokens:** {total_tokens} | "
            f"**Total Cost:** ${format_number(total_cost, 4)}\n"
        )

        tree_text = "\n".join(tree_lines)
        content = section("Span Tree", f"{header}\n```\n{tree_text}\n```")

        return ToolResult(
            content=content,
            data={"tree": tree_data, "total_spans": len(spans)},
        )
