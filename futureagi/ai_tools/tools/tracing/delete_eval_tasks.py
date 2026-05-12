from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class DeleteEvalTasksInput(PydanticBaseModel):
    eval_task_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Eval task UUIDs or exact task names to delete. Omit to list candidates."
        ),
    )
    project_id: str = Field(
        default="",
        description="Optional project UUID or exact project name to scope candidates.",
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
class DeleteEvalTasksTool(BaseTool):
    name = "delete_eval_tasks"
    description = (
        "Safely soft-deletes one or more eval tasks and their associated logs "
        "after confirmation. Running tasks cannot be deleted — pause them first. "
        "Defaults to dry-run preview."
    )
    category = "tracing"
    input_model = DeleteEvalTasksInput

    def execute(self, params: DeleteEvalTasksInput, context: ToolContext) -> ToolResult:

        from django.utils import timezone

        from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus
        from tracer.models.observation_span import EvalLogger
        from ai_tools.tools.tracing._utils import (
            candidate_eval_tasks_result,
            resolve_eval_tasks,
        )

        if not params.eval_task_ids:
            return candidate_eval_tasks_result(
                context,
                "Eval Tasks Required For Delete",
                "Choose one or more eval tasks to delete. This tool previews by default.",
                project_ref=params.project_id,
            )

        resolved_tasks, missing, unresolved = resolve_eval_tasks(
            params.eval_task_ids,
            context,
            project_ref=params.project_id,
        )
        if unresolved:
            return unresolved
        if missing:
            return candidate_eval_tasks_result(
                context,
                "Eval Task Not Found",
                "Missing eval task reference(s): "
                + ", ".join(f"`{item}`" for item in missing)
                + ". Use one of these task IDs.",
                project_ref=params.project_id,
            )

        id_strs = [str(task.id) for task in resolved_tasks]
        eval_tasks = EvalTask.objects.filter(id__in=id_strs)

        running_tasks = eval_tasks.filter(status=EvalTaskStatus.RUNNING)
        if running_tasks.exists():
            return ToolResult.error(
                "Cannot delete running eval tasks. Pause them first.",
                error_code="VALIDATION_ERROR",
            )

        count = eval_tasks.count()

        if params.dry_run or not params.confirm_delete:
            task_rows = [
                [
                    task.name or "-",
                    f"`{task.id}`",
                    task.project.name if task.project else "-",
                    task.status,
                ]
                for task in resolved_tasks
            ]
            body = key_value_block(
                [
                    ("Tasks Matched", str(count)),
                    ("Mutation", "Not applied"),
                    (
                        "To Apply",
                        "Call with `dry_run=false` and `confirm_delete=true`.",
                    ),
                ]
            )
            if task_rows:
                from ai_tools.formatting import markdown_table

                body += "\n\n" + markdown_table(
                    ["Name", "Task ID", "Project", "Status"], task_rows
                )
            return ToolResult(
                content=section("Eval Task Delete Preview", body),
                data={
                    "eval_task_ids": id_strs,
                    "matched_count": count,
                    "dry_run": True,
                    "requires_confirm_delete": True,
                },
            )

        now = timezone.now()

        eval_tasks.update(deleted=True, deleted_at=now, status=EvalTaskStatus.DELETED)
        EvalTaskLogger.objects.filter(eval_task_id__in=id_strs).update(
            deleted=True, deleted_at=now
        )
        EvalLogger.objects.filter(eval_task_id__in=id_strs).update(
            deleted=True, deleted_at=now
        )

        info = key_value_block(
            [
                ("Deleted", str(count)),
                ("Requested", str(len(id_strs))),
            ]
        )

        content = section("Eval Tasks Deleted", info)

        return ToolResult(
            content=content,
            data={
                "deleted_count": count,
                "eval_task_ids": id_strs,
            },
        )
