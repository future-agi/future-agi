from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class GetTestExecutionAnalyticsInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    test_execution_id: str = Field(
        default="",
        description="The UUID of the test execution. If omitted, candidates are returned.",
    )
    run_test_id: str = Field(
        default="",
        description="Run test UUID or name. When provided, analytics use its latest execution.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["test_execution_id"] = (
            normalized.get("test_execution_id")
            or normalized.get("execution_id")
            or normalized.get("id")
            or ""
        )
        normalized["run_test_id"] = (
            normalized.get("run_test_id")
            or normalized.get("run_test")
            or normalized.get("test_id")
            or normalized.get("suite_id")
            or ""
        )
        return normalized


@register_tool
class GetTestExecutionAnalyticsTool(BaseTool):
    name = "get_test_execution_analytics"
    description = (
        "Returns detailed analytics for a test execution, including "
        "call breakdown, pass/fail stats, timing, and score distribution."
    )
    category = "simulation"
    input_model = GetTestExecutionAnalyticsInput

    def execute(
        self, params: GetTestExecutionAnalyticsInput, context: ToolContext
    ) -> ToolResult:
        from django.db.models import Avg, Count, Max, Min, Sum
        from simulate.models.test_execution import CallExecution, TestExecution

        from ai_tools.resolvers import is_uuid
        from ai_tools.tools.agents._utils import resolve_run_test

        def candidate_executions_result(title: str, detail: str = "") -> ToolResult:
            qs = TestExecution.objects.select_related(
                "run_test", "agent_definition"
            ).filter(run_test__organization=context.organization)
            executions = list(qs.order_by("-created_at")[:10])
            rows = []
            data = []
            for candidate in executions:
                rows.append(
                    [
                        f"`{candidate.id}`",
                        candidate.run_test.name if candidate.run_test else "—",
                        format_status(candidate.status),
                        str(candidate.total_calls),
                        format_datetime(candidate.created_at),
                    ]
                )
                data.append(
                    {
                        "id": str(candidate.id),
                        "run_test_id": (
                            str(candidate.run_test_id)
                            if candidate.run_test_id
                            else None
                        ),
                        "run_test_name": (
                            candidate.run_test.name if candidate.run_test else None
                        ),
                        "status": candidate.status,
                    }
                )
            body = detail or (
                "Provide `test_execution_id`, or provide `run_test_id` to use the "
                "latest execution for a test suite."
            )
            if rows:
                body += "\n\n" + markdown_table(
                    ["Execution ID", "Run Test", "Status", "Calls", "Created"],
                    rows,
                )
            else:
                body += "\n\nNo test executions found in this workspace."
            return ToolResult(
                content=section(title, body),
                data={
                    "requires_test_execution_id_or_run_test_id": True,
                    "executions": data,
                },
            )

        execution_ref = str(params.test_execution_id or "").strip()
        run_test_ref = str(params.run_test_id or "").strip()

        if execution_ref and is_uuid(execution_ref):
            try:
                execution = TestExecution.objects.select_related(
                    "run_test", "agent_definition"
                ).get(id=execution_ref, run_test__organization=context.organization)
            except TestExecution.DoesNotExist:
                return candidate_executions_result(
                    "Test Execution Not Found",
                    f"Test execution `{execution_ref}` was not found.",
                )
        else:
            if execution_ref and not run_test_ref:
                run_test_ref = execution_ref
            if not run_test_ref:
                return candidate_executions_result("Test Execution Required")

            run_test, unresolved = resolve_run_test(
                run_test_ref,
                context,
                title="Run Test Required For Analytics",
            )
            if unresolved:
                return unresolved
            execution = (
                TestExecution.objects.select_related("run_test", "agent_definition")
                .filter(run_test=run_test)
                .order_by("-created_at")
                .first()
            )
            if not execution:
                return candidate_executions_result(
                    "Test Execution Not Found",
                    f"No executions were found for run test `{run_test.name}`.",
                )

        run_test_name = execution.run_test.name if execution.run_test else "—"
        agent_name = (
            execution.agent_definition.agent_name if execution.agent_definition else "—"
        )

        # Duration
        duration = "—"
        if execution.started_at and execution.completed_at:
            dur_sec = (execution.completed_at - execution.started_at).total_seconds()
            if dur_sec < 60:
                duration = f"{dur_sec:.0f}s"
            else:
                duration = f"{dur_sec / 60:.1f}m"

        # Success rate
        success_rate = "—"
        if execution.total_calls and execution.total_calls > 0:
            success_rate = (
                f"{(execution.completed_calls / execution.total_calls) * 100:.1f}%"
            )

        info = key_value_block(
            [
                ("Execution ID", f"`{execution.id}`"),
                ("Test", run_test_name),
                ("Agent", agent_name),
                ("Status", format_status(execution.status)),
                ("Duration", duration),
                ("Total Calls", str(execution.total_calls)),
                ("Completed", str(execution.completed_calls)),
                ("Failed", str(execution.failed_calls)),
                ("Success Rate", success_rate),
                ("Started", format_datetime(execution.started_at)),
                ("Completed At", format_datetime(execution.completed_at)),
            ]
        )

        content = section(f"Execution Analytics: {run_test_name}", info)

        # Call-level analytics
        calls = CallExecution.objects.filter(test_execution=execution)

        call_stats = calls.aggregate(
            avg_score=Avg("overall_score"),
            min_score=Min("overall_score"),
            max_score=Max("overall_score"),
            avg_duration=Avg("duration_seconds"),
            total_cost=Sum("cost_cents"),
            avg_response_time=Avg("response_time_ms"),
        )

        if call_stats["avg_score"] is not None:
            score_info = key_value_block(
                [
                    ("Average Score", format_number(call_stats["avg_score"])),
                    ("Min Score", format_number(call_stats["min_score"])),
                    ("Max Score", format_number(call_stats["max_score"])),
                    (
                        "Avg Duration",
                        (
                            f"{call_stats['avg_duration']:.0f}s"
                            if call_stats["avg_duration"]
                            else "—"
                        ),
                    ),
                    (
                        "Total Cost",
                        (
                            f"${call_stats['total_cost'] / 100:.2f}"
                            if call_stats["total_cost"]
                            else "—"
                        ),
                    ),
                    (
                        "Avg Response Time",
                        (
                            f"{call_stats['avg_response_time']:.0f}ms"
                            if call_stats["avg_response_time"]
                            else "—"
                        ),
                    ),
                ]
            )
            content += f"\n\n### Call Metrics\n\n{score_info}"

        # Call status breakdown
        status_counts = (
            calls.values("status").annotate(count=Count("id")).order_by("status")
        )
        if status_counts:
            status_rows = [[s["status"], str(s["count"])] for s in status_counts]
            status_table = markdown_table(["Status", "Count"], status_rows)
            content += f"\n\n### Call Status Breakdown\n\n{status_table}"

        # Score distribution by scenario
        scenario_stats = (
            calls.filter(overall_score__isnull=False)
            .values("scenario__name")
            .annotate(
                avg_score=Avg("overall_score"),
                call_count=Count("id"),
            )
            .order_by("-avg_score")[:10]
        )
        if scenario_stats:
            scenario_rows = [
                [
                    s["scenario__name"] or "—",
                    format_number(s["avg_score"]),
                    str(s["call_count"]),
                ]
                for s in scenario_stats
            ]
            scenario_table = markdown_table(
                ["Scenario", "Avg Score", "Calls"], scenario_rows
            )
            content += f"\n\n### Scores by Scenario\n\n{scenario_table}"

        data = {
            "id": str(execution.id),
            "status": execution.status,
            "total_calls": execution.total_calls,
            "completed_calls": execution.completed_calls,
            "failed_calls": execution.failed_calls,
            "avg_score": (
                float(call_stats["avg_score"]) if call_stats["avg_score"] else None
            ),
            "total_cost_cents": call_stats["total_cost"],
        }

        return ToolResult(content=content, data=data)
