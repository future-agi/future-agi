from typing import List
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class DeleteEvalTasksInput(PydanticBaseModel):
    eval_task_ids: List[UUID] = Field(
        description="List of eval task UUIDs to delete",
        min_length=1,
    )


@register_tool
class DeleteEvalTasksTool(BaseTool):
    name = "delete_eval_tasks"
    description = (
        "Soft-deletes one or more eval tasks and their associated logs. "
        "Running tasks cannot be deleted — pause them first. "
        "This does not permanently remove data."
    )
    category = "tracing"
    input_model = DeleteEvalTasksInput

    def execute(self, params: DeleteEvalTasksInput, context: ToolContext) -> ToolResult:

        from django.utils import timezone

        from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus
        from tracer.models.observation_span import EvalLogger

        id_strs = [str(eid) for eid in params.eval_task_ids]

        eval_tasks = EvalTask.objects.filter(
            id__in=id_strs,
            project__organization=context.organization,
        )

        if not eval_tasks.exists():
            return ToolResult.error(
                "No eval tasks found for the provided IDs.",
                error_code="NOT_FOUND",
            )

        running_tasks = eval_tasks.filter(status=EvalTaskStatus.RUNNING)
        if running_tasks.exists():
            return ToolResult.error(
                "Cannot delete running eval tasks. Pause them first.",
                error_code="VALIDATION_ERROR",
            )

        count = eval_tasks.count()
        now = timezone.now()

        from tracer.services.clickhouse.v2.eval_logger_writer import (
            soft_delete_eval_loggers,
        )

        eval_tasks.update(deleted=True, deleted_at=now, status=EvalTaskStatus.DELETED)
        EvalTaskLogger.objects.filter(eval_task_id__in=id_strs).update(
            deleted=True, deleted_at=now
        )
        # mirror the EvalLogger soft-delete to CH (.update() bypasses post_save)
        soft_delete_eval_loggers(EvalLogger.objects.filter(eval_task_id__in=id_strs))

        found_ids = {str(t.id) for t in eval_tasks}
        not_found = [eid for eid in id_strs if eid not in found_ids]

        info = key_value_block(
            [
                ("Deleted", str(count)),
                ("Requested", str(len(id_strs))),
            ]
        )

        content = section("Eval Tasks Deleted", info)

        if not_found:
            content += f"\n\n_IDs not found: {', '.join(not_found)}_"

        return ToolResult(
            content=content,
            data={
                "deleted_count": count,
                "eval_task_ids": [eid for eid in id_strs if eid in found_ids],
            },
        )
