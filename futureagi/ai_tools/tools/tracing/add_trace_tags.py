from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class AddTraceTagsInput(PydanticBaseModel):
    trace_id: str = Field(
        default="",
        description="Trace UUID or exact trace name. Omit it to list candidate traces.",
    )
    tags: list[str] = Field(description="List of tag strings to add")


@register_tool
class AddTraceTagsTool(BaseTool):
    name = "add_trace_tags"
    description = (
        "Adds tags to a trace. Tags are string labels that help organize "
        "and filter traces. Duplicate tags are ignored."
    )
    category = "tracing"
    input_model = AddTraceTagsInput

    def execute(self, params: AddTraceTagsInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.tracing._utils import resolve_trace

        trace, unresolved = resolve_trace(
            params.trace_id,
            context,
            title="Trace Required To Add Tags",
        )
        if unresolved:
            return unresolved

        if not params.tags:
            return ToolResult.error(
                "Provide at least one tag to add.",
                error_code="VALIDATION_ERROR",
            )

        existing_tags = set(trace.tags or [])
        new_tags = set(params.tags)
        added = new_tags - existing_tags
        already_present = new_tags & existing_tags

        # Merge tags
        trace.tags = list(existing_tags | new_tags)
        trace.save()

        info = key_value_block(
            [
                ("Trace ID", f"`{trace.id}`"),
                (
                    "Added Tags",
                    (
                        ", ".join(f"`{t}`" for t in sorted(added))
                        if added
                        else "—(all already present)"
                    ),
                ),
                (
                    "Already Present",
                    (
                        ", ".join(f"`{t}`" for t in sorted(already_present))
                        if already_present
                        else "—"
                    ),
                ),
                ("Total Tags", str(len(trace.tags))),
            ]
        )

        content = section("Tags Added", info)

        return ToolResult(
            content=content,
            data={
                "trace_id": str(trace.id),
                "added": sorted(added),
                "already_present": sorted(already_present),
                "all_tags": trace.tags,
            },
        )
