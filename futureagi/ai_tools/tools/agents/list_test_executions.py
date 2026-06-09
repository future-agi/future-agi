from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool

ExecutionStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "evaluating",
    "cancelling",
]


class ListTestExecutionsInput(PydanticBaseModel):
    run_test_id: UUID = Field(
        description="The UUID of the run test to list executions for"
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    status: Optional[ExecutionStatus] = Field(
        default=None,
        description="Filter by execution status",
    )


@register_tool
class ListTestExecutionsTool(BaseTool):
    name = "list_test_executions"
    description = (
        "Lists test executions for a specific run test (agent test). "
        "Returns execution status, total/completed/failed calls, "
        "success rate, and duration."
    )
    category = "agents"
    input_model = ListTestExecutionsInput

    def execute(
        self, params: ListTestExecutionsInput, context: ToolContext
    ) -> ToolResult:

        from django.db.models import Count, Q

        from simulate.models.test_execution import CallExecution, TestExecution

        qs = (
            TestExecution.objects.filter(
                run_test_id=params.run_test_id,
                run_test__organization=context.organization,
            )
            .annotate(
                _total_calls=Count("calls"),
                _completed_calls=Count(
                    "calls",
                    filter=Q(calls__status=CallExecution.CallStatus.COMPLETED),
                ),
                _failed_calls=Count(
                    "calls",
                    filter=Q(calls__status=CallExecution.CallStatus.FAILED),
                ),
            )
            .order_by("-created_at")
        )

        if params.status:
            qs = qs.filter(status=params.status)

        total = qs.count()
        executions = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for ex in executions:
            total_calls = ex._total_calls or 0
            completed_calls = ex._completed_calls or 0
            failed_calls = ex._failed_calls or 0

            success_rate = "—"
            if total_calls > 0:
                rate = (completed_calls / total_calls) * 100
                success_rate = f"{rate:.0f}%"

            duration = "—"
            if ex.started_at and ex.completed_at:
                dur_sec = (ex.completed_at - ex.started_at).total_seconds()
                if dur_sec < 60:
                    duration = f"{dur_sec:.0f}s"
                else:
                    duration = f"{dur_sec / 60:.1f}m"

            rows.append(
                [
                    f"`{ex.id}`",
                    format_status(ex.status),
                    f"{completed_calls}/{total_calls}",
                    str(failed_calls),
                    success_rate,
                    duration,
                    format_datetime(ex.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(ex.id),
                    "run_test_id": str(params.run_test_id),
                    "status": ex.status,
                    "total_calls": total_calls,
                    "completed_calls": completed_calls,
                    "failed_calls": failed_calls,
                    "total_scenarios": ex.total_scenarios,
                }
            )

        table = markdown_table(
            [
                "ID",
                "Status",
                "Calls (Done/Total)",
                "Failed",
                "Success Rate",
                "Duration",
                "Created",
            ],
            rows,
        )

        showing = f"Showing {len(rows)} of {total}"
        content = section(f"Test Executions ({total})", f"{showing}\n\n{table}")

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(
            content=content, data={"executions": data_list, "total": total}
        )
