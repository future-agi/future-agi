from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class UpdateProjectInput(PydanticBaseModel):
    project_id: str = Field(
        default="",
        description="Project UUID or exact/fuzzy project name to update.",
    )
    name: Optional[str] = Field(default=None, description="Updated project name")
    description: Optional[str] = Field(
        default=None, description="Updated description (stored in metadata)"
    )


@register_tool
class UpdateProjectTool(BaseTool):
    name = "update_project"
    description = "Updates an existing tracing project's name or description."
    category = "tracing"
    input_model = UpdateProjectInput

    def execute(self, params: UpdateProjectInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.tracing._utils import resolve_project

        project, unresolved = resolve_project(
            params.project_id,
            context,
            title="Project Required To Update",
        )
        if unresolved:
            return unresolved

        changes = []

        if params.name is not None:
            old = project.name
            project.name = params.name
            changes.append(f"name: '{old}' -> '{params.name}'")

        if params.description is not None:
            metadata = project.metadata or {}
            old = metadata.get("description", "—")
            metadata["description"] = params.description
            project.metadata = metadata
            changes.append(f"description: '{old}' -> '{params.description}'")

        if not changes:
            return ToolResult.error(
                "No changes provided. Specify at least name or description.",
                error_code="VALIDATION_ERROR",
            )

        project.save()

        # Invalidate PII cache when project metadata changes (matches API behavior)
        try:
            from tracer.utils.pii_settings import invalidate_pii_cache

            invalidate_pii_cache(str(project.organization_id), project.name)
        except Exception:
            pass  # Non-fatal, matches API behavior

        info = key_value_block(
            [
                ("Project ID", f"`{project.id}`"),
                ("Name", project.name),
                ("Changes", "; ".join(changes)),
            ]
        )

        content = section("Project Updated", info)

        return ToolResult(
            content=content,
            data={
                "project_id": str(project.id),
                "name": project.name,
                "changes": changes,
            },
        )
