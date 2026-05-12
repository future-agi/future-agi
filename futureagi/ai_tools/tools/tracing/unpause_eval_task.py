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
    eval_task_id: str = Field(
        default="",
        description="Eval task UUID or exact task name to resume.",
    )


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

        from django.utils import timezone

        from tracer.models.eval_task import EvalTaskLogger, EvalTaskStatus
        from ai_tools.tools.tracing._utils import (
            candidate_eval_tasks_result,
            resolve_eval_tasks,
        )

        eval_tasks, missing, unresolved = resolve_eval_tasks(
            [params.eval_task_id],
            context,
        )
        if unresolved:
            return unresolved
        if missing or not eval_tasks:
            return candidate_eval_tasks_result(
                context,
                "Eval Task Required To Resume",
                f"Eval task `{params.eval_task_id}` was not found.",
            )
        eval_task = eval_tasks[0]

        if eval_task.status != EvalTaskStatus.PAUSED:
            return ToolResult.error(
                f"Cannot resume eval task with status '{eval_task.status}'. "
                "Only paused tasks can be resumed.",
                error_code="VALIDATION_ERROR",
            )

        eval_task.status = EvalTaskStatus.PENDING
        filters = eval_task.filters.copy() if eval_task.filters else {}
        filters["created_at"] = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        eval_task.filters = filters
        eval_task.save()

        try:
            eval_task_logger = EvalTaskLogger.objects.get(
                eval_task_id=eval_task.id
            )
        except EvalTaskLogger.DoesNotExist:
            eval_task_logger = EvalTaskLogger.objects.create(
                eval_task_id=eval_task.id,
                offset=0,
                status=EvalTaskStatus.PENDING,
            )
        eval_task_logger.offset = 0
        eval_task_logger.save()

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
