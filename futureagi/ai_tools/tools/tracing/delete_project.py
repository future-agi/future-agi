from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class DeleteProjectInput(PydanticBaseModel):
    project_id: str = Field(
        default="",
        description="Project UUID or exact project name. Omit to list candidates.",
    )
    dry_run: bool = Field(
        default=True,
        description="Preview delete impact without modifying data.",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Must be true with dry_run=false to perform the soft delete.",
    )


@register_tool
class DeleteProjectTool(BaseTool):
    name = "delete_project"
    description = (
        "Safely soft-deletes a tracing project after confirmation. "
        "Defaults to a dry-run preview and does not delete associated traces "
        "or spans."
    )
    category = "tracing"
    input_model = DeleteProjectInput

    def execute(self, params: DeleteProjectInput, context: ToolContext) -> ToolResult:
        from django.db.models import Count

        from ai_tools.tools.tracing._utils import resolve_project
        from tracer.models.eval_task import EvalTask
        from tracer.models.monitor import UserAlertMonitor
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.project_version import ProjectVersion
        from tracer.models.trace import Trace
        from tracer.models.trace_session import TraceSession

        project, unresolved = resolve_project(
            params.project_id,
            context,
            title="Project Required For Delete",
        )
        if unresolved:
            return unresolved

        project = (
            type(project)
            .objects.annotate(trace_count=Count("traces"))
            .get(id=project.id)
        )

        project_name = project.name
        project_id = str(project.id)
        trace_count = project.trace_count
        version_count = ProjectVersion.objects.filter(project=project).count()
        session_count = TraceSession.objects.filter(project=project).count()
        span_count = ObservationSpan.objects.filter(project=project).count()
        monitor_count = UserAlertMonitor.objects.filter(
            project=project, deleted=False
        ).count()
        eval_task_count = EvalTask.objects.filter(
            project=project, deleted=False
        ).count()

        if params.dry_run or not params.confirm_delete:
            info = key_value_block(
                [
                    ("Project ID", f"`{project_id}`"),
                    ("Name", project_name),
                    ("Traces Linked", str(trace_count)),
                    ("Spans Linked", str(span_count)),
                    ("Sessions Linked", str(session_count)),
                    ("Versions Linked", str(version_count)),
                    ("Alert Monitors Linked", str(monitor_count)),
                    ("Eval Tasks Linked", str(eval_task_count)),
                    ("Mutation", "Not applied"),
                    (
                        "To Apply",
                        "Call with `dry_run=false` and `confirm_delete=true`.",
                    ),
                ]
            )
            return ToolResult(
                content=section("Project Delete Preview", info),
                data={
                    "project_id": project_id,
                    "name": project_name,
                    "trace_count": trace_count,
                    "span_count": span_count,
                    "dry_run": True,
                    "requires_confirm_delete": True,
                },
            )

        # Soft delete only the project. Linked traces/spans remain in storage.
        project.delete()

        info = key_value_block(
            [
                ("Project ID", f"`{project_id}`"),
                ("Name", project_name),
                ("Traces Linked", str(trace_count)),
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
                "linked_records_deleted": False,
                "deleted": True,
            },
        )
