from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_status,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class CancelTestExecutionInput(PydanticBaseModel):
    test_execution_id: UUID | None = Field(
        default=None,
        description="The UUID of the test execution to cancel",
    )
    run_test_id: UUID | None = Field(
        default=None,
        description="The UUID of the run test to cancel (cancels its latest execution)",
    )


@register_tool
class CancelTestExecutionTool(BaseTool):
    name = "cancel_test_execution"
    description = (
        "Cancels a running test execution. "
        "Provide either a test_execution_id or a run_test_id (cancels latest execution). "
        "Sends cancellation signals to Temporal workflows or Celery tasks and stops active calls."
    )
    category = "simulation"
    input_model = CancelTestExecutionInput

    def execute(
        self, params: CancelTestExecutionInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.run_test import RunTest
        from simulate.models.test_execution import TestExecution
        from tfc.settings import settings as app_settings

        # Validate that at least one identifier is provided
        if not params.test_execution_id and not params.run_test_id:
            return ToolResult.validation_error(
                "Either test_execution_id or run_test_id must be provided."
            )

        # Resolve test execution with organization scoping
        if params.test_execution_id:
            try:
                test_execution = TestExecution.objects.get(
                    id=params.test_execution_id,
                    run_test__organization=context.organization,
                    run_test__deleted=False,
                )
            except TestExecution.DoesNotExist:
                return ToolResult.not_found(
                    "Test Execution", str(params.test_execution_id)
                )
            run_test_id = str(test_execution.run_test_id)
        else:
            # Cancel by run_test_id: verify access and find latest execution
            try:
                run_test = RunTest.objects.get(
                    id=params.run_test_id,
                    organization=context.organization,
                    deleted=False,
                )
            except RunTest.DoesNotExist:
                return ToolResult.not_found("Run Test", str(params.run_test_id))

            test_execution = (
                TestExecution.objects.filter(run_test=run_test)
                .order_by("-created_at")
                .first()
            )
            if not test_execution:
                return ToolResult.error(
                    "No test executions found for this run test.",
                    error_code="NOT_FOUND",
                )
            run_test_id = str(run_test.id)

        # Check if execution is in a cancellable state
        cancellable_statuses = [
            TestExecution.ExecutionStatus.PENDING,
            TestExecution.ExecutionStatus.RUNNING,
            TestExecution.ExecutionStatus.EVALUATING,
        ]
        if test_execution.status not in cancellable_statuses:
            return ToolResult.error(
                f"Cannot cancel execution with status '{test_execution.status}'. "
                f"Only executions with status {[s.value if hasattr(s, 'value') else s for s in cancellable_statuses]} can be cancelled.",
                error_code="VALIDATION_ERROR",
            )

        previous_status = test_execution.status
        test_execution_id = str(test_execution.id)

        # Set status to cancelling immediately
        test_execution.status = TestExecution.ExecutionStatus.CANCELLING
        test_execution.save(update_fields=["status", "updated_at"])

        result = self._cancel_with_temporal(test_execution)

        if not result.get("success"):
            error_msg = result.get("error", "Failed to cancel test execution")
            return ToolResult.error(error_msg, error_code="CANCELLATION_FAILED")

        info = key_value_block(
            [
                ("Execution ID", f"`{test_execution_id}`"),
                ("Previous Status", format_status(previous_status)),
                ("New Status", format_status("cancelling")),
                (
                    "Test",
                    test_execution.run_test.name if test_execution.run_test else "—",
                ),
            ]
        )

        content = section("Test Execution Cancelling", info)
        content += (
            "\n\n_The execution is being cancelled. Active calls will be stopped._"
        )

        return ToolResult(
            content=content,
            data={
                "id": test_execution_id,
                "previous_status": previous_status,
                "status": "cancelling",
            },
        )

    def _cancel_with_temporal(self, test_execution) -> dict:
        """Cancel test execution via Temporal workflow, with DB fallback.

        Tries to cancel both the original TestExecutionWorkflow (fresh runs)
        and any active RerunCoordinatorWorkflow (reruns).
        """
        from simulate.temporal.client import (
            cancel_test_execution,
            cancel_workflow,
        )

        test_execution_id = str(test_execution.id)
        any_cancelled = False

        try:
            # Try cancelling the original TestExecutionWorkflow (fresh run)
            if cancel_test_execution(test_execution_id):
                any_cancelled = True

            # Try cancelling the active RerunCoordinatorWorkflow (rerun)
            active_rerun_wf_id = None
            if test_execution.execution_metadata:
                active_rerun_wf_id = test_execution.execution_metadata.get(
                    "active_rerun_workflow_id"
                )

            if active_rerun_wf_id and cancel_workflow(
                active_rerun_wf_id, cancel_signal="cancel"
            ):
                any_cancelled = True

            if any_cancelled:
                return {
                    "success": True,
                    "message": "Cancellation signal sent to workflow",
                    "test_execution_id": test_execution_id,
                }
            else:
                logger.warning(
                    "no_temporal_workflows_found",
                    test_execution_id=test_execution_id,
                    msg="Falling back to DB cancellation",
                )
                return self._cancel_via_db(test_execution_id)

        except Exception as e:
            logger.exception(
                "temporal_cancel_failed",
                test_execution_id=test_execution_id,
                error=str(e),
            )
            return self._cancel_via_db(test_execution_id)

    def _cancel_via_db(self, test_execution_id: str) -> dict:
        """Fallback: cancel test execution directly in DB when Temporal is unavailable."""
        try:
            from simulate.services.test_executor import TestExecutor

            test_executor = TestExecutor()
            return test_executor.cancel_test(test_execution_id=test_execution_id)
        except Exception as e:
            logger.exception(
                "db_cancel_failed",
                test_execution_id=test_execution_id,
                error=str(e),
            )
            return {
                "success": False,
                "error": f"Failed to cancel test execution: {str(e)}",
                "test_execution_id": test_execution_id,
            }
