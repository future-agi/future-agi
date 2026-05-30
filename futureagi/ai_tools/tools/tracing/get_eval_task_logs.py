from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetEvalTaskLogsInput(PydanticBaseModel):
    eval_task_id: UUID = Field(description="The UUID of the eval task to get logs for")


@register_tool
class GetEvalTaskLogsTool(BaseTool):
    name = "get_eval_task_logs"
    description = (
        "Gets results and execution logs for an eval task: the per-span "
        "outcomes (which span passed/failed or scored what on which eval, with "
        "the explanation), plus success/error counts and error messages. Use "
        "this to read eval results and diagnose failures after a task runs."
    )
    category = "tracing"
    input_model = GetEvalTaskLogsInput

    def execute(self, params: GetEvalTaskLogsInput, context: ToolContext) -> ToolResult:

        from django.contrib.postgres.aggregates import ArrayAgg
        from django.db.models import Count, Q

        from tracer.models.eval_task import EvalTask
        from tracer.models.observation_span import EvalLogger

        try:
            eval_task = EvalTask.objects.get(
                id=params.eval_task_id,
                project__organization=context.organization,
            )
        except EvalTask.DoesNotExist:
            return ToolResult.not_found("EvalTask", str(params.eval_task_id))

        log_stats = EvalLogger.objects.filter(
            eval_task_id=str(params.eval_task_id), deleted=False
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

        # Per-span results — the actual outcomes (which span got what
        # score/verdict on which eval). Previously this tool returned only
        # aggregate counts, so eval results were unreadable via MCP (TH-5411).
        per_span = (
            EvalLogger.objects.filter(
                eval_task_id=str(params.eval_task_id), deleted=False
            )
            .select_related("custom_eval_config")
            .order_by("-created_at")[:50]
        )
        result_rows = []
        result_data = []
        for row in per_span:
            if row.output_bool is not None:
                result = "pass" if row.output_bool else "fail"
            elif row.output_float is not None:
                result = format_number(row.output_float, 4)
            elif row.output_str:
                result = truncate(row.output_str, 60)
            else:
                result = "—"
            eval_name = (
                row.custom_eval_config.name
                if getattr(row, "custom_eval_config_id", None) and row.custom_eval_config
                else (row.eval_id or "—")
            )
            span_ref = (
                row.observation_span_id
                or row.trace_id
                or row.trace_session_id
                or "—"
            )
            verdict = "error" if row.error else result
            result_rows.append(
                [
                    f"`{str(span_ref)[:18]}`",
                    truncate(str(eval_name), 40),
                    verdict,
                    truncate(row.eval_explanation or "", 80),
                ]
            )
            result_data.append(
                {
                    "span_id": (
                        str(row.observation_span_id)
                        if row.observation_span_id
                        else None
                    ),
                    "eval": str(eval_name),
                    "result": result,
                    "error": bool(row.error),
                    "explanation": row.eval_explanation,
                }
            )
        if result_rows:
            content += "\n\n### Per-Span Results\n\n" + markdown_table(
                ["Span", "Eval", "Result", "Explanation"], result_rows
            )
            if total_count > 50:
                content += f"\n\n_Showing 50 of {total_count} results._"

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
                "results": result_data,
            },
        )
