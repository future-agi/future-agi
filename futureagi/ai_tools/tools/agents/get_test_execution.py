from typing import Optional
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


class GetTestExecutionInput(PydanticBaseModel):
    execution_id: Optional[UUID] = Field(
        default=None,
        description="The UUID of the test execution",
    )
    include_calls: bool = Field(
        default=True, description="Include individual call results"
    )
    call_limit: int = Field(default=10, ge=1, le=50, description="Max calls to include")


@register_tool
class GetTestExecutionTool(BaseTool):
    name = "get_test_execution"
    description = (
        "Returns detailed information about a test execution including "
        "overall status, call results with scores, durations, and error details. "
        "If the execution UUID is unknown, call this with no parameters to list candidates."
    )
    category = "agents"
    input_model = GetTestExecutionInput

    def execute(
        self, params: GetTestExecutionInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.test_execution import CallExecution, TestExecution

        if params.execution_id is None:
            executions = (
                TestExecution.objects.filter(run_test__organization=context.organization)
                .select_related("run_test")
                .order_by("-created_at")[:10]
            )
            rows = []
            data_list = []
            for execution in executions:
                run_test_name = execution.run_test.name if execution.run_test else "—"
                rows.append(
                    f"- `{execution.id}` — {run_test_name} ({format_status(execution.status)})"
                )
                data_list.append(
                    {
                        "id": str(execution.id),
                        "run_test": run_test_name,
                        "status": execution.status,
                    }
                )
            content = section(
                "Test Execution Candidates",
                (
                    "Choose one of these executions, then call "
                    "`get_test_execution` with `execution_id`.\n\n"
                    + ("\n".join(rows) if rows else "No test executions found.")
                ),
            )
            return ToolResult(content=content, data={"executions": data_list})

        try:
            execution = TestExecution.objects.select_related(
                "run_test", "agent_definition", "agent_version"
            ).get(id=params.execution_id, run_test__organization=context.organization)
        except TestExecution.DoesNotExist:
            return ToolResult.not_found("Test Execution", str(params.execution_id))

        run_test_name = execution.run_test.name if execution.run_test else "—"
        agent_name = (
            execution.agent_definition.agent_name if execution.agent_definition else "—"
        )

        success_rate = "—"
        if execution.total_calls and execution.total_calls > 0:
            rate = (execution.completed_calls / execution.total_calls) * 100
            success_rate = f"{rate:.0f}%"

        duration = "—"
        if execution.started_at and execution.completed_at:
            dur_sec = (execution.completed_at - execution.started_at).total_seconds()
            if dur_sec < 60:
                duration = f"{dur_sec:.0f}s"
            else:
                duration = f"{dur_sec / 60:.1f}m"

        info = key_value_block(
            [
                ("ID", f"`{execution.id}`"),
                ("Run Test", run_test_name),
                ("Agent", agent_name),
                ("Status", format_status(execution.status)),
                ("Total Calls", str(execution.total_calls)),
                ("Completed", str(execution.completed_calls)),
                ("Failed", str(execution.failed_calls)),
                ("Success Rate", success_rate),
                ("Duration", duration),
                ("Scenarios", str(execution.total_scenarios)),
                ("Started", format_datetime(execution.started_at)),
                ("Completed At", format_datetime(execution.completed_at)),
            ]
        )

        content = section(f"Test Execution: {run_test_name}", info)

        if execution.error_reason:
            content += (
                f"\n\n### Error\n\n```\n{truncate(execution.error_reason, 500)}\n```"
            )

        # Eval explanation summary
        if execution.eval_explanation_summary and isinstance(
            execution.eval_explanation_summary, dict
        ):
            content += "\n\n### Evaluation Summary\n\n"
            for eval_name, summary in list(execution.eval_explanation_summary.items())[
                :5
            ]:
                content += f"- **{eval_name}**: {truncate(str(summary), 200)}\n"

        # Call executions
        call_data = []
        if params.include_calls:
            calls = (
                CallExecution.objects.filter(test_execution=execution)
                .select_related("scenario")
                .order_by("-created_at")[: params.call_limit]
            )

            if calls:
                content += "\n\n### Calls\n\n"
                call_rows = []
                for call in calls:
                    scenario_name = call.scenario.name if call.scenario else "—"
                    call_type = call.simulation_call_type or "—"
                    score = (
                        format_number(call.overall_score)
                        if call.overall_score is not None
                        else "—"
                    )
                    dur = f"{call.duration_seconds}s" if call.duration_seconds else "—"

                    call_rows.append(
                        [
                            f"`{str(call.id)}`",
                            truncate(scenario_name, 25),
                            call_type,
                            format_status(call.status),
                            score,
                            dur,
                            (
                                truncate(call.ended_reason, 20)
                                if call.ended_reason
                                else "—"
                            ),
                        ]
                    )
                    call_data.append(
                        {
                            "id": str(call.id),
                            "scenario": scenario_name,
                            "type": call_type,
                            "status": call.status,
                            "overall_score": (
                                float(call.overall_score)
                                if call.overall_score is not None
                                else None
                            ),
                            "duration_seconds": call.duration_seconds,
                        }
                    )

                content += markdown_table(
                    [
                        "ID",
                        "Scenario",
                        "Type",
                        "Status",
                        "Score",
                        "Duration",
                        "End Reason",
                    ],
                    call_rows,
                )

        data = {
            "id": str(execution.id),
            "run_test": run_test_name,
            "agent": agent_name,
            "status": execution.status,
            "total_calls": execution.total_calls,
            "completed_calls": execution.completed_calls,
            "failed_calls": execution.failed_calls,
            "calls": call_data,
        }

        return ToolResult(content=content, data=data)
