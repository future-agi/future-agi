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
from ai_tools.resolvers import is_uuid
from ai_tools.tools.agents._utils import (
    candidate_run_tests_result,
    resolve_run_test,
)

logger = structlog.get_logger(__name__)


class CancelTestExecutionInput(PydanticBaseModel):
    test_execution_id: str | None = Field(
        default=None,
        description="The UUID of the test execution to cancel",
    )
    run_test_id: str | None = Field(
        default=None,
        description="The UUID or name of the run test to cancel (cancels its latest execution)",
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

        from simulate.models.test_execution import TestExecution

        from tfc.settings import settings as app_settings

        def candidate_executions_result(title: str, detail: str = "") -> ToolResult:
            executions = list(
                TestExecution.objects.select_related("run_test")
                .filter(run_test__organization=context.organization)
                .order_by("-created_at")[:10]
            )
            rows = []
            data = []
            for execution in executions:
                rows.append(
                    [
                        f"`{execution.id}`",
                        execution.run_test.name if execution.run_test else "—",
                        format_status(execution.status),
                    ]
                )
                data.append(
                    {
                        "id": str(execution.id),
                        "run_test_id": (
                            str(execution.run_test_id)
                            if execution.run_test_id
                            else None
                        ),
                        "status": execution.status,
                    }
                )
            body = detail or (
                "Provide `test_execution_id`, or provide `run_test_id` to cancel "
                "the latest execution for a test suite."
            )
            if rows:
                from ai_tools.formatting import markdown_table

                body += "\n\n" + markdown_table(
                    ["Execution ID", "Run Test", "Status"],
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

        # Validate that at least one identifier is provided
        if not params.test_execution_id and not params.run_test_id:
            return candidate_executions_result("Test Execution Required")

        # Resolve test execution with organization scoping
        execution_ref = str(params.test_execution_id or "").strip()
        run_test_ref = str(params.run_test_id or "").strip()
        if execution_ref and is_uuid(execution_ref):
            try:
                test_execution = TestExecution.objects.get(
                    id=execution_ref,
                    run_test__organization=context.organization,
                    run_test__deleted=False,
                )
            except TestExecution.DoesNotExist:
                return candidate_executions_result(
                    "Test Execution Not Found",
                    f"Test execution `{execution_ref}` was not found.",
                )
            run_test_id = str(test_execution.run_test_id)
        else:
            if execution_ref and not run_test_ref:
                run_test_ref = execution_ref
            if not run_test_ref:
                return candidate_executions_result("Run Test Required")

            # Cancel by run_test_id: verify access and find latest execution
            run_test, unresolved = resolve_run_test(
                run_test_ref,
                context,
                title="Run Test Required To Cancel",
            )
            if unresolved:
                return unresolved

            test_execution = (
                TestExecution.objects.filter(run_test=run_test)
                .order_by("-created_at")
                .first()
            )
            if not test_execution:
                return candidate_run_tests_result(
                    context,
                    "Test Execution Not Found",
                    f"No test executions found for run test `{run_test.name}`.",
                )
            run_test_id = str(run_test.id)

        # Check if execution is in a cancellable state
        cancellable_statuses = [
            TestExecution.ExecutionStatus.PENDING,
            TestExecution.ExecutionStatus.RUNNING,
            TestExecution.ExecutionStatus.EVALUATING,
        ]
        if test_execution.status not in cancellable_statuses:
            cancellable = [
                s.value if hasattr(s, "value") else s for s in cancellable_statuses
            ]
            return ToolResult(
                content=section(
                    "Test Execution Not Cancellable",
                    (
                        f"Execution `{test_execution.id}` is `{test_execution.status}`. "
                        "Only these statuses can be cancelled: "
                        + ", ".join(f"`{status}`" for status in cancellable)
                    ),
                ),
                data={
                    "id": str(test_execution.id),
                    "status": test_execution.status,
                    "cancellable_statuses": cancellable,
                    "cancelled": False,
                },
            )

        previous_status = test_execution.status
        test_execution_id = str(test_execution.id)

        # Set status to cancelling immediately
        test_execution.status = TestExecution.ExecutionStatus.CANCELLING
        test_execution.save(update_fields=["status", "updated_at"])

        # Dispatch cancellation to Temporal or Celery
        if getattr(app_settings, "TEMPORAL_TEST_EXECUTION_ENABLED", False):
            result = self._cancel_with_temporal(test_execution)
        else:
            from simulate.services.test_executor import TestExecutor

            test_executor = TestExecutor()
            result = test_executor.cancel_test(
                run_test_id=run_test_id,
                test_execution_id=test_execution_id,
            )

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
