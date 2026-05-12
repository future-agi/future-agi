from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.agents._utils import resolve_run_test


class GetRunTestAnalyticsInput(PydanticBaseModel):
    run_test_id: Optional[str] = Field(
        default=None,
        description="Run test UUID or exact run test name. Omit to list candidate tests.",
    )


@register_tool
class GetRunTestAnalyticsTool(BaseTool):
    name = "get_run_test_analytics"
    description = (
        "Returns analytics for a test suite, including total executions, "
        "pass rate, average score, and call counts across all executions."
    )
    category = "simulation"
    input_model = GetRunTestAnalyticsInput

    def execute(
        self, params: GetRunTestAnalyticsInput, context: ToolContext
    ) -> ToolResult:
        from django.db.models import Avg, Count, Sum

        from simulate.models.test_execution import CallExecution, TestExecution

        run_test, unresolved = resolve_run_test(
            params.run_test_id,
            context,
            title="Agent Test Required For Analytics",
        )
        if unresolved:
            return unresolved

        agent_name = (
            run_test.agent_definition.agent_name if run_test.agent_definition else "—"
        )

        # Execution stats
        executions = TestExecution.objects.filter(run_test=run_test)
        total_executions = executions.count()

        exec_stats = executions.aggregate(
            total_calls=Sum("total_calls"),
            completed_calls=Sum("completed_calls"),
            failed_calls=Sum("failed_calls"),
        )
        total_calls = exec_stats["total_calls"] or 0
        completed_calls = exec_stats["completed_calls"] or 0
        failed_calls = exec_stats["failed_calls"] or 0

        # Pass rate
        pass_rate = "—"
        if total_calls > 0:
            pass_rate = f"{(completed_calls / total_calls) * 100:.1f}%"

        # Average score across all call executions
        call_stats = CallExecution.objects.filter(
            test_execution__run_test=run_test,
            overall_score__isnull=False,
        ).aggregate(avg_score=Avg("overall_score"), call_count=Count("id"))

        avg_score = (
            format_number(call_stats["avg_score"]) if call_stats["avg_score"] else "—"
        )

        # Status breakdown
        status_counts = (
            executions.values("status").annotate(count=Count("id")).order_by("status")
        )

        info = key_value_block(
            [
                ("Test Suite", run_test.name),
                ("Agent", agent_name),
                ("Total Executions", str(total_executions)),
                ("Total Calls", str(total_calls)),
                ("Completed Calls", str(completed_calls)),
                ("Failed Calls", str(failed_calls)),
                ("Pass Rate", pass_rate),
                ("Average Score", avg_score),
                ("Scenarios", str(run_test.scenarios.count())),
                ("Created", format_datetime(run_test.created_at)),
            ]
        )

        content = section(f"Analytics: {run_test.name}", info)

        # Status breakdown table
        if status_counts:
            status_rows = [[s["status"], str(s["count"])] for s in status_counts]
            status_table = markdown_table(["Status", "Count"], status_rows)
            content += f"\n\n### Execution Status Breakdown\n\n{status_table}"

        # Recent executions
        recent = executions.order_by("-created_at")[:5]
        if recent:
            recent_rows = []
            for ex in recent:
                sr = "—"
                if ex.total_calls and ex.total_calls > 0:
                    sr = f"{(ex.completed_calls / ex.total_calls) * 100:.0f}%"
                recent_rows.append(
                    [
                        f"`{str(ex.id)}`",
                        ex.status,
                        f"{ex.completed_calls}/{ex.total_calls}",
                        sr,
                        format_datetime(ex.created_at),
                    ]
                )
            recent_table = markdown_table(
                ["ID", "Status", "Calls", "Success", "Created"], recent_rows
            )
            content += f"\n\n### Recent Executions\n\n{recent_table}"

        data = {
            "run_test_id": str(run_test.id),
            "name": run_test.name,
            "total_executions": total_executions,
            "total_calls": total_calls,
            "completed_calls": completed_calls,
            "failed_calls": failed_calls,
            "avg_score": (
                float(call_stats["avg_score"]) if call_stats["avg_score"] else None
            ),
        }

        return ToolResult(content=content, data=data)
