from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool
from ai_tools.tools.agents._utils import resolve_run_test


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
    test_execution_ids: list[UUID] | None = Field(
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
    enable_tool_evaluation: bool | None = Field(
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
        from simulate.models.eval_config import SimulateEvalConfig
        from simulate.models.test_execution import CallExecution, TestExecution

        from simulate.services.test_executor import (
            run_new_evals_on_call_executions_task,
        )

        logger = structlog.get_logger(__name__)

        run_test, unresolved = resolve_run_test(
            params.run_test_id,
            context,
            title="Run Test Required To Run New Evals",
        )
        if unresolved:
            return unresolved

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

        # Collect call execution IDs and update test execution status
        call_execution_ids = []
        test_execution_count = 0
        test_executions_to_update = []

        for test_execution in test_executions:
            test_execution_count += 1
            call_executions = CallExecution.objects.filter(
                test_execution=test_execution
            ).values_list("id", flat=True)
            call_execution_ids.extend([str(ce_id) for ce_id in call_executions])

            # Update status to EVALUATING
            test_execution.status = TestExecution.ExecutionStatus.EVALUATING

            # Update column_order with new eval configs
            if not test_execution.execution_metadata:
                test_execution.execution_metadata = {}

            column_order = test_execution.execution_metadata.get("column_order", [])
            existing_eval_ids = {
                col.get("id") for col in column_order if col.get("type") == "evaluation"
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
            test_execution.picked_up_by_executor = False
            test_executions_to_update.append(test_execution)

        if test_executions_to_update:
            TestExecution.objects.bulk_update(
                test_executions_to_update,
                ["status", "execution_metadata", "picked_up_by_executor"],
            )

        if not call_execution_ids:
            return ToolResult.error(
                "No call executions found in the selected test executions.",
                error_code="NOT_FOUND",
            )

        # Initialize eval_outputs for call executions
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

        # Trigger the Celery task
        eval_config_ids_str = [str(ec_id) for ec_id in params.eval_config_ids]
        task = run_new_evals_on_call_executions_task.apply_async(
            args=(call_execution_ids, eval_config_ids_str),
        )

        logger.info(
            "mcp_run_new_evals_triggered",
            task_id=task.id,
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
