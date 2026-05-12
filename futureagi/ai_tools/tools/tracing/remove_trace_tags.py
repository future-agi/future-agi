from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class RemoveTraceTagsInput(PydanticBaseModel):
    trace_id: str | None = Field(
        default=None,
        description="Trace UUID or exact trace name to remove tags from.",
    )
    tags: list[str] = Field(
        default_factory=list, description="List of tag strings to remove"
    )


@register_tool
class RemoveTraceTagsTool(BaseTool):
    name = "remove_trace_tags"
    description = (
        "Removes specified tags from a trace. Tags that are not present "
        "on the trace are silently ignored."
    )
    category = "tracing"
    input_model = RemoveTraceTagsInput

    def execute(self, params: RemoveTraceTagsInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.tracing._utils import candidate_traces_result, resolve_trace

        if not params.trace_id:
            return candidate_traces_result(
                context,
                "Remove Trace Tags Requirements",
                "Provide `trace_id` and `tags` to remove tags from a trace.",
            )

        trace, unresolved = resolve_trace(
            params.trace_id,
            context,
            title="Trace Required To Remove Tags",
        )
        if unresolved:
            return unresolved

        if not params.tags:
            return ToolResult(
                content=section(
                    "Remove Trace Tags Requirements",
                    (
                        f"Trace `{trace.id}` has tags: "
                        f"{', '.join(trace.tags or []) or 'none'}.\n\n"
                        "Provide `tags` with at least one existing tag to remove."
                    ),
                ),
                data={
                    "trace_id": str(trace.id),
                    "requires_tags": True,
                    "available_tags": trace.tags or [],
                },
            )

        existing_tags = set(trace.tags or [])
        to_remove = set(params.tags)
        removed = existing_tags & to_remove
        not_found = to_remove - existing_tags

        # Update tags
        trace.tags = sorted(existing_tags - to_remove)
        trace.save()

        info = key_value_block(
            [
                ("Trace ID", f"`{trace.id}`"),
                (
                    "Removed Tags",
                    (
                        ", ".join(f"`{t}`" for t in sorted(removed))
                        if removed
                        else "—(none found)"
                    ),
                ),
                (
                    "Not Found",
                    (
                        ", ".join(f"`{t}`" for t in sorted(not_found))
                        if not_found
                        else "—"
                    ),
                ),
                ("Remaining Tags", str(len(trace.tags))),
            ]
        )

        content = section("Tags Removed", info)

        return ToolResult(
            content=content,
            data={
                "trace_id": str(trace.id),
                "removed": sorted(removed),
                "not_found": sorted(not_found),
                "remaining_tags": trace.tags,
            },
        )
