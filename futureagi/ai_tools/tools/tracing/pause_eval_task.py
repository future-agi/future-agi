from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_status,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class PauseEvalTaskInput(PydanticBaseModel):
    eval_task_id: str = Field(
        default="",
        description="Eval task UUID or exact task name to pause.",
    )


@register_tool
class PauseEvalTaskTool(BaseTool):
    name = "pause_eval_task"
    description = (
        "Pauses a running eval task. Only tasks with 'running' status can be "
        "paused. Paused tasks can be resumed with unpause_eval_task."
    )
    category = "tracing"
    input_model = PauseEvalTaskInput

    def execute(self, params: PauseEvalTaskInput, context: ToolContext) -> ToolResult:

        from tracer.models.eval_task import EvalTaskStatus
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
                "Eval Task Required To Pause",
                f"Eval task `{params.eval_task_id}` was not found.",
            )
        eval_task = eval_tasks[0]

        if eval_task.status != EvalTaskStatus.RUNNING:
            return ToolResult.error(
                f"Cannot pause eval task with status '{eval_task.status}'. "
                "Only running tasks can be paused.",
                error_code="VALIDATION_ERROR",
            )

        eval_task.status = EvalTaskStatus.PAUSED
        eval_task.save()

        info = key_value_block(
            [
                ("Eval Task ID", f"`{eval_task.id}`"),
                ("Name", eval_task.name or "—"),
                ("Previous Status", format_status(EvalTaskStatus.RUNNING)),
                ("Current Status", format_status(EvalTaskStatus.PAUSED)),
            ]
        )

        content = section("Eval Task Paused", info)
        content += "\n\n_Use `unpause_eval_task` to resume this task._"

        return ToolResult(
            content=content,
            data={
                "id": str(eval_task.id),
                "name": eval_task.name,
                "status": "paused",
            },
        )
