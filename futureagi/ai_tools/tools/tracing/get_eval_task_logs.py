from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    key_value_block,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetEvalTaskLogsInput(PydanticBaseModel):
    eval_task_id: str = Field(
        default="",
        description="Eval task UUID or exact task name to get logs for.",
    )


@register_tool
class GetEvalTaskLogsTool(BaseTool):
    name = "get_eval_task_logs"
    description = (
        "Gets execution logs and statistics for an eval task, including "
        "success/error counts and error messages. Use this to monitor eval "
        "task progress and diagnose failures."
    )
    category = "tracing"
    input_model = GetEvalTaskLogsInput

    def execute(self, params: GetEvalTaskLogsInput, context: ToolContext) -> ToolResult:

        from django.contrib.postgres.aggregates import ArrayAgg
        from django.db.models import Count, Q

        from tracer.models.observation_span import EvalLogger
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
                "Eval Task Required For Logs",
                f"Eval task `{params.eval_task_id}` was not found.",
            )
        eval_task = eval_tasks[0]

        log_stats = EvalLogger.objects.filter(
            eval_task_id=str(eval_task.id), deleted=False
        ).aggregate(
            errors_count=Count("id", filter=Q(error=True)),
            success_count=Count("id", filter=Q(error=False)),
            errors_message=ArrayAgg("eval_explanation", filter=Q(error=True)),
        )

        total_count = log_stats["errors_count"] + log_stats["success_count"]
        errors = log_stats["errors_message"] or []

        info = key_value_block(
            [
                ("Eval Task", f"`{eval_task.id}`"),
                ("Task Name", eval_task.name or "—"),
                ("Status", format_status(eval_task.status)),
                ("Start Time", format_datetime(eval_task.start_time)),
                ("End Time", format_datetime(eval_task.end_time)),
                ("Total Processed", str(total_count)),
                ("Successful", str(log_stats["success_count"])),
                ("Errors", str(log_stats["errors_count"])),
            ]
        )

        content = section(f"Eval Task Logs: {eval_task.name or eval_task.id}", info)

        if errors:
            unique_errors = list(dict.fromkeys(e for e in errors if e))[:10]
            if unique_errors:
                error_lines = "\n".join(
                    f"- {truncate(err, 200)}" for err in unique_errors
                )
                content += f"\n\n### Error Messages\n\n{error_lines}"
                if len(errors) > 10:
                    content += f"\n\n_Showing 10 of {len(errors)} errors._"

        return ToolResult(
            content=content,
            data={
                "eval_task_id": str(eval_task.id),
                "start_time": (
                    str(eval_task.start_time) if eval_task.start_time else None
                ),
                "end_time": str(eval_task.end_time) if eval_task.end_time else None,
                "total_count": total_count,
                "success_count": log_stats["success_count"],
                "errors_count": log_stats["errors_count"],
                "errors_message": errors[:10] if errors else [],
            },
        )
