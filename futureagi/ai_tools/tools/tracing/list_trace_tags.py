from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class ListTraceTagsInput(PydanticBaseModel):
    trace_id: str = Field(
        default="",
        description="Trace UUID or exact trace name to list tags for.",
    )


@register_tool
class ListTraceTagsTool(BaseTool):
    name = "list_trace_tags"
    description = (
        "Lists all tags on a specific trace. Tags are string labels "
        "stored as a JSON array on the trace."
    )
    category = "tracing"
    input_model = ListTraceTagsInput

    def execute(self, params: ListTraceTagsInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.tracing._utils import resolve_trace

        trace, unresolved = resolve_trace(
            params.trace_id,
            context,
            title="Trace Required To List Tags",
        )
        if unresolved:
            return unresolved

        tags = trace.tags or []

        if not tags:
            content = section(
                "Trace Tags",
                f"No tags found on trace `{trace.id}`.",
            )
        else:
            tag_list = "\n".join(f"- `{tag}`" for tag in tags)
            info = key_value_block(
                [
                    ("Trace ID", f"`{trace.id}`"),
                    ("Trace Name", trace.name or "—"),
                    ("Tag Count", str(len(tags))),
                ]
            )
            content = section(f"Trace Tags ({len(tags)})", f"{info}\n\n{tag_list}")

        return ToolResult(
            content=content,
            data={
                "trace_id": str(trace.id),
                "tags": tags,
                "count": len(tags),
            },
        )
