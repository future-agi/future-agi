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
        "Resumes a paused or failed eval task. Only tasks with 'paused' or "
        "'failed' status can be resumed. The task will restart processing "
        "from where it left off."
    )
    category = "tracing"
    input_model = UnpauseEvalTaskInput

    def execute(self, params: UnpauseEvalTaskInput, context: ToolContext) -> ToolResult:

        from django.db import transaction

        from tfc.temporal.eval_tasks.client import start_eval_task_workflow_sync
        from tracer.models.eval_task import (
            RESUMABLE_TASK_STATUSES,
            EvalTask,
            EvalTaskLogger,
            EvalTaskStatus,
        )
        from tracer.selectors.eval_tasks.scope import eval_tasks_in_scope

        with transaction.atomic():
            try:
                # The unpause endpoint's scope, not the organization alone: a
                # task in another workspace, or in a deleted project, is not
                # this caller's to resume — and a resume spends evaluations.
                eval_task = (
                    eval_tasks_in_scope(
                        EvalTask.objects,
                        organization=context.organization,
                        workspace=context.workspace,
                    )
                    .select_for_update(of=("self",))
                    .get(id=params.eval_task_id)
                )
            except EvalTask.DoesNotExist:
                return ToolResult.not_found("EvalTask", str(params.eval_task_id))

            # The same set the unpause endpoint accepts.
            if eval_task.status not in RESUMABLE_TASK_STATUSES:
                return ToolResult.error(
                    f"Cannot resume eval task with status '{eval_task.status}'. "
                    "Only paused or failed tasks can be resumed.",
                    error_code="VALIDATION_ERROR",
                )
            previous_status = eval_task.status

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
                ("Previous Status", format_status(previous_status)),
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
