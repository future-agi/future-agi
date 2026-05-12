from typing import Literal
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    markdown_table,
    section,
    truncate,
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
    run_test_id: UUID | None = Field(
        default=None,
        description=(
            "Optional UUID of the run test to list executions for. "
            "Omit it to list recent executions across the workspace."
        ),
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    status: ExecutionStatus | None = Field(
        default=None,
        description="Filter by execution status",
    )


@register_tool
class ListTestExecutionsTool(BaseTool):
    name = "list_test_executions"
    description = (
        "Lists recent test executions across the workspace, or for a specific "
        "run test when run_test_id is provided. "
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

        filters = {"run_test__organization": context.organization}
        if params.run_test_id:
            filters["run_test_id"] = params.run_test_id

        base_qs = TestExecution.objects.filter(**filters)
        if params.status:
            base_qs = base_qs.filter(status=params.status)

        status_counts = list(
            base_qs.values("status").annotate(count=Count("id")).order_by("status")
        )

        qs = (
            base_qs.select_related("run_test")
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

        total = qs.count()
        executions = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for ex in executions:
            run_test_name = ex.run_test.name if ex.run_test else "—"
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

            row = [f"`{ex.id}`"]
            if not params.run_test_id:
                row.append(truncate(run_test_name, 36))
            row.extend(
                [
                    format_status(ex.status),
                    f"{completed_calls}/{total_calls}",
                    str(failed_calls),
                    success_rate,
                    duration,
                    format_datetime(ex.created_at),
                ]
            )
            rows.append(row)
            data_list.append(
                {
                    "id": str(ex.id),
                    "run_test_id": str(ex.run_test_id),
                    "run_test": run_test_name,
                    "status": ex.status,
                    "total_calls": total_calls,
                    "completed_calls": completed_calls,
                    "failed_calls": failed_calls,
                    "total_scenarios": ex.total_scenarios,
                }
            )

        headers = ["ID"]
        if not params.run_test_id:
            headers.append("Run Test")
        headers.extend(
            [
                "Status",
                "Calls (Done/Total)",
                "Failed",
                "Success Rate",
                "Duration",
                "Created",
            ]
        )
        table = markdown_table(headers, rows)

        status_table = markdown_table(
            ["Status", "Count"],
            [
                [format_status(row["status"]), str(row["count"])]
                for row in status_counts
            ],
        )

        showing = f"Showing {len(rows)} of {total}"
        content = section(
            f"Test Executions ({total})",
            f"{showing}\n\n### Status Breakdown\n\n{status_table}\n\n{table}",
        )

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(
            content=content,
            data={
                "executions": data_list,
                "total": total,
                "status_counts": {row["status"]: row["count"] for row in status_counts},
            },
        )
