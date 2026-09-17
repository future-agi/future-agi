from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class DeleteProjectInput(PydanticBaseModel):
    project_id: UUID = Field(description="The UUID of the project to delete")


@register_tool
class DeleteProjectTool(BaseTool):
    name = "delete_project"
    description = (
        "Deletes a tracing project by ID. This is a soft delete "
        "(marks as deleted, does not permanently remove). "
        "Associated traces and spans are NOT deleted."
    )
    category = "tracing"
    input_model = DeleteProjectInput

    def execute(self, params: DeleteProjectInput, context: ToolContext) -> ToolResult:
        from django.db.models import Count

        from tracer.models.project import Project
        from tracer.services.project_deletion import soft_delete_projects

        try:
            project = Project.objects.annotate(trace_count=Count("traces")).get(
                id=params.project_id, organization=context.organization
            )
        except Project.DoesNotExist:
            return ToolResult.not_found("Project", str(params.project_id))

        project_name = project.name
        project_id = str(project.id)
        trace_count = project.trace_count

        # Cascade soft-delete + publish collector cache-invalidation.
        soft_delete_projects(
            Project.objects.filter(id=project.id), project.trace_type
        )

        info = key_value_block(
            [
                ("Project ID", f"`{project_id}`"),
                ("Name", project_name),
                ("Traces in Project", str(trace_count)),
                ("Status", "Deleted"),
            ]
        )

        content = section("Project Deleted", info)

        if trace_count > 0:
            content += (
                f"\n\n_Note: {trace_count} trace(s) were associated with this project. "
                "They remain in the database but are no longer associated with an active project._"
            )

        return ToolResult(
            content=content,
            data={
                "project_id": project_id,
                "name": project_name,
                "trace_count": trace_count,
                "deleted": True,
            },
        )
