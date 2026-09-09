from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_status,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class UnpauseEvalTaskInput(PydanticBaseModel):
    eval_task_id: UUID = Field(description="The UUID of the eval task to resume")


@register_tool
class UnpauseEvalTaskTool(BaseTool):
    name = "unpause_eval_task"
    description = (
        "Resumes a paused eval task. Only tasks with 'paused' status can be "
        "resumed. The task will restart processing from where it left off."
    )
    category = "tracing"
    input_model = UnpauseEvalTaskInput

    def execute(self, params: UnpauseEvalTaskInput, context: ToolContext) -> ToolResult:

        from django.db import transaction

        from tfc.temporal.eval_tasks.client import start_eval_task_workflow_sync
        from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus

        with transaction.atomic():
            try:
                eval_task = EvalTask.objects.select_for_update().get(
                    id=params.eval_task_id,
                    project__organization=context.organization,
                )
            except EvalTask.DoesNotExist:
                return ToolResult.not_found("EvalTask", str(params.eval_task_id))

            if eval_task.status != EvalTaskStatus.PAUSED:
                return ToolResult.error(
                    f"Cannot resume eval task with status '{eval_task.status}'. "
                    "Only paused tasks can be resumed.",
                    error_code="VALIDATION_ERROR",
                )

            # Resume the original selection/cursor. Mutating filters here would
            # silently change which historical rows remain eligible.
            eval_task.status = EvalTaskStatus.PENDING
            eval_task.save(update_fields=["status"])

            try:
                eval_task_logger = EvalTaskLogger.objects.get(
                    eval_task_id=params.eval_task_id
                )
            except EvalTaskLogger.DoesNotExist:
                eval_task_logger = EvalTaskLogger.objects.create(
                    eval_task_id=params.eval_task_id,
                    offset=0,
                    status=EvalTaskStatus.PENDING,
                )
            eval_task_logger.offset = 0
            eval_task_logger.save()
            transaction.on_commit(
                lambda: start_eval_task_workflow_sync(eval_task, replace_existing=True)
            )

        info = key_value_block(
            [
                ("Eval Task ID", f"`{eval_task.id}`"),
                ("Name", eval_task.name or "—"),
                ("Previous Status", format_status(EvalTaskStatus.PAUSED)),
                ("Current Status", format_status(EvalTaskStatus.PENDING)),
            ]
        )

        content = section("Eval Task Resumed", info)
        content += (
            "\n\n_The eval task has been resumed and will be picked up "
            "by the eval runner._"
        )

        return ToolResult(
            content=content,
            data={
                "id": str(eval_task.id),
                "name": eval_task.name,
                "status": "pending",
            },
        )
