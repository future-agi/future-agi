from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class RunNewEvalsOnSimulationInput(PydanticBaseModel):
    run_test_id: UUID = Field(
        description="The UUID of the RunTest (simulation) to run evaluations on"
    )
    eval_config_ids: list[UUID] = Field(
        description=(
            "List of SimulateEvalConfig IDs to run. "
            "Use list_simulate_eval_configs to find available eval config IDs."
        ),
        min_length=1,
    )
    test_execution_ids: Optional[list[UUID]] = Field(
        default=None,
        description=(
            "Specific test execution IDs to evaluate. "
            "If omitted and select_all is True, runs on all test executions."
        ),
    )
    select_all: bool = Field(
        default=False,
        description=(
            "If True, run evals on all test executions in the run test. "
            "When combined with test_execution_ids, those IDs are excluded."
        ),
    )
    enable_tool_evaluation: Optional[bool] = Field(
        default=None,
        description="Enable or disable tool evaluation for the run test. If omitted, no change.",
    )


@register_tool
class RunNewEvalsOnSimulationTool(BaseTool):
    name = "run_new_evals_on_simulation"
    description = (
        "Runs new evaluations on existing simulation test executions. "
        "Use this to run additional evals on completed test executions "
        "within a simulation (RunTest). Only works on test executions with COMPLETED status. "
        "Use list_simulate_eval_configs to find eval config IDs first."
    )
    category = "simulation"
    input_model = RunNewEvalsOnSimulationInput

    def execute(
        self, params: RunNewEvalsOnSimulationInput, context: ToolContext
    ) -> ToolResult:
        import structlog

        from simulate.models.call_execution import CallExecution
        from simulate.models.run_test import RunTest
        from simulate.models.simulate_eval_config import SimulateEvalConfig
        from simulate.models.test_execution import TestExecution
        from simulate.temporal.client import rerun_call_executions

        logger = structlog.get_logger(__name__)

        # Get the run test
        try:
            run_test = RunTest.objects.get(
                id=params.run_test_id,
                organization=context.organization,
                deleted=False,
            )
        except RunTest.DoesNotExist:
            return ToolResult.not_found("Run Test", str(params.run_test_id))

        # Update enable_tool_evaluation if provided
        if params.enable_tool_evaluation is not None:
            run_test.enable_tool_evaluation = params.enable_tool_evaluation
            run_test.save(update_fields=["enable_tool_evaluation"])

        # Get test executions
        if params.select_all:
            test_executions = TestExecution.objects.filter(run_test=run_test)
            if params.test_execution_ids:
                test_executions = test_executions.exclude(
                    id__in=params.test_execution_ids
                )
        elif params.test_execution_ids:
            test_executions = TestExecution.objects.filter(
                id__in=params.test_execution_ids, run_test=run_test
            )
        else:
            return ToolResult.error(
                "Either provide test_execution_ids or set select_all=True.",
                error_code="VALIDATION_ERROR",
            )

        if not test_executions.exists():
            return ToolResult.error(
                "No test executions found to run evaluations on.",
                error_code="NOT_FOUND",
            )

        # Validate all test executions are COMPLETED
        non_completed = test_executions.exclude(
            status=TestExecution.ExecutionStatus.COMPLETED
        )
        if non_completed.exists():
            return ToolResult.error(
                "Only test executions with COMPLETED status can have new evaluations run. "
                f"Found {non_completed.count()} non-completed execution(s).",
                error_code="VALIDATION_ERROR",
            )

        # Validate eval configs
        eval_config_ids = [str(eid) for eid in params.eval_config_ids]
        eval_configs = SimulateEvalConfig.objects.filter(
            id__in=eval_config_ids, run_test=run_test
        )
        if eval_configs.count() != len(eval_config_ids):
            return ToolResult.error(
                "One or more eval configs not found or do not belong to this run test.",
                error_code="NOT_FOUND",
            )

        calls_by_test_execution: dict[str, list[str]] = {}
        call_execution_ids: list[str] = []
        for ce_id, te_id in CallExecution.objects.filter(
            test_execution_id__in=test_executions.values_list("id", flat=True)
        ).values_list("id", "test_execution_id"):
            calls_by_test_execution.setdefault(str(te_id), []).append(str(ce_id))
            call_execution_ids.append(str(ce_id))

        if not call_execution_ids:
            return ToolResult.error(
                "No call executions found in the selected test executions.",
                error_code="NOT_FOUND",
            )

        eval_config_ids_str = [str(ec_id) for ec_id in params.eval_config_ids]

        # Seed eval_outputs placeholders before launching the workflows
        # so the activity sees the pending rows on first read.
        call_executions_to_update = CallExecution.objects.filter(
            id__in=call_execution_ids
        )
        call_executions_list = []
        for call_execution in call_executions_to_update:
            call_execution.call_metadata = call_execution.call_metadata or {}
            call_execution.call_metadata["eval_started"] = True
            call_execution.call_metadata["eval_completed"] = False

            if not call_execution.eval_outputs:
                call_execution.eval_outputs = {}

            for eval_config in eval_configs:
                call_execution.eval_outputs[str(eval_config.id)] = {"status": "pending"}

            call_executions_list.append(call_execution)

        if call_executions_list:
            CallExecution.objects.bulk_update(
                call_executions_list, ["call_metadata", "eval_outputs"]
            )

        # One workflow per TestExecution: merge strategy is keyed on
        # test_execution_id, so we can't bundle them into a single workflow.
        workspace_id = run_test.workspace_id
        workspace_id_str = str(workspace_id) if workspace_id else ""

        test_execution_count = 0
        launched_workflows: list[dict] = []
        for test_execution in test_executions:
            te_id = str(test_execution.id)
            te_call_ids = calls_by_test_execution.get(te_id, [])
            if not te_call_ids:
                continue

            if not test_execution.execution_metadata:
                test_execution.execution_metadata = {}

            column_order = test_execution.execution_metadata.get("column_order", [])
            existing_eval_ids = {
                col.get("id") for col in column_order
                if col.get("type") == "evaluation"
            }
            for eval_config in eval_configs:
                if str(eval_config.id) not in existing_eval_ids:
                    column_order.append(
                        {
                            "column_name": eval_config.name,
                            "id": str(eval_config.id),
                            "eval_config": eval_config.eval_template.config,
                            "visible": True,
                            "type": "evaluation",
                        }
                    )
            test_execution.execution_metadata["column_order"] = column_order

            active_workflow_id = test_execution.execution_metadata.get(
                "active_rerun_workflow_id"
            )

            rerun_result = rerun_call_executions(
                test_execution_id=te_id,
                call_execution_ids=te_call_ids,
                org_id=str(context.organization.id),
                workspace_id=workspace_id_str,
                eval_only=True,
                eval_config_ids=eval_config_ids_str,
                active_workflow_id=active_workflow_id,
            )

            test_execution.status = TestExecution.ExecutionStatus.EVALUATING
            test_execution.picked_up_by_executor = True
            if not rerun_result.get("merged"):
                test_execution.execution_metadata[
                    "active_rerun_workflow_id"
                ] = rerun_result.get("workflow_id")
            test_execution.save(
                update_fields=[
                    "status",
                    "execution_metadata",
                    "picked_up_by_executor",
                ]
            )

            test_execution_count += 1
            launched_workflows.append(
                {
                    "test_execution_id": te_id,
                    "workflow_id": rerun_result.get("workflow_id"),
                    "merged": rerun_result.get("merged", False),
                    "call_count": len(te_call_ids),
                }
            )

        logger.info(
            "mcp_run_new_evals_triggered",
            launched_workflows=launched_workflows,
            call_executions=len(call_execution_ids),
            test_executions=test_execution_count,
            eval_configs=len(eval_config_ids),
        )

        eval_names = [ec.name for ec in eval_configs]
        info = key_value_block(
            [
                ("Run Test", run_test.name),
                ("Test Executions", str(test_execution_count)),
                ("Call Executions", str(len(call_execution_ids))),
                ("Evals", ", ".join(eval_names)),
                ("Status", "Running"),
            ]
        )

        content = section("Simulation Evals Started", info)
        content += (
            "\n\n_Evaluations are running asynchronously. "
            "Use `get_test_execution` to check progress._"
        )

        return ToolResult(
            content=content,
            data={
                "run_test_id": str(run_test.id),
                "test_execution_count": test_execution_count,
                "call_execution_count": len(call_execution_ids),
                "eval_config_ids": eval_config_ids_str,
                "eval_names": eval_names,
                "status": "running",
            },
        )
