"""
Client helpers for starting Temporal workflows from Django views/services.

Provides async-to-sync wrappers for starting test execution workflows.
"""

import asyncio
from typing import Optional

import structlog
from asgiref.sync import async_to_sync

from simulate.temporal.constants import (
    QUEUE_L,
    RERUN_COORDINATOR_WORKFLOW_ID_PREFIX,
    TEST_EXECUTION_WORKFLOW_ID_PREFIX,
)
from simulate.temporal.types.rerun import MergeCallsSignal, RerunCoordinatorInput
from simulate.temporal.types.test_execution import TestExecutionInput

logger = structlog.get_logger(__name__)


def start_test_execution_workflow(
    test_execution_id: str,
    run_test_id: str,
    org_id: str,
    scenario_ids: list[str],
    simulator_id: Optional[str] = None,
) -> str:
    """
    Start a TestExecutionWorkflow from sync Django code.

    Args:
        test_execution_id: UUID of the TestExecution record
        run_test_id: UUID of the RunTest
        org_id: Organization ID
        scenario_ids: List of scenario IDs to execute
        simulator_id: Optional simulator agent ID

    Returns:
        Workflow ID of the started workflow

    Raises:
        Exception if workflow fails to start
    """
    return async_to_sync(_start_test_execution_workflow_async)(
        test_execution_id=test_execution_id,
        run_test_id=run_test_id,
        org_id=org_id,
        scenario_ids=scenario_ids,
        simulator_id=simulator_id,
    )


async def _start_test_execution_workflow_async(
    test_execution_id: str,
    run_test_id: str,
    org_id: str,
    scenario_ids: list[str],
    simulator_id: Optional[str] = None,
) -> str:
    """Async implementation for starting test execution workflow."""
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

    from simulate.temporal.workflows.test_execution_workflow import (
        TestExecutionWorkflow,
    )
    from tfc.temporal.common.client import get_client

    client = await get_client()

    workflow_id = f"{TEST_EXECUTION_WORKFLOW_ID_PREFIX}-{test_execution_id}"

    logger.info(
        f"Starting TestExecutionWorkflow: {workflow_id}, "
        f"scenarios={len(scenario_ids)}"
    )

    await client.start_workflow(
        TestExecutionWorkflow.run,
        TestExecutionInput(
            test_execution_id=test_execution_id,
            run_test_id=run_test_id,
            org_id=org_id,
            scenario_ids=scenario_ids,
            simulator_id=simulator_id,
        ),
        id=workflow_id,
        task_queue=QUEUE_L,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )

    logger.info(f"Started TestExecutionWorkflow: {workflow_id}")

    return workflow_id


def get_test_execution_status(test_execution_id: str) -> dict:
    """
    Query TestExecutionWorkflow status.

    Args:
        test_execution_id: UUID of the TestExecution

    Returns:
        Status dict with progress information
    """
    return async_to_sync(_get_test_execution_status_async)(test_execution_id)


async def _get_test_execution_status_async(test_execution_id: str) -> dict:
    """Async implementation for querying workflow status."""
    from tfc.temporal.common.client import get_client

    client = await get_client()
    workflow_id = f"{TEST_EXECUTION_WORKFLOW_ID_PREFIX}-{test_execution_id}"

    try:
        handle = client.get_workflow_handle(workflow_id)
        status = await handle.query("get_status")
        status_dict = status.__dict__ if hasattr(status, "__dict__") else status
        return {
            "source": "temporal",
            "workflow_id": workflow_id,
            **status_dict,
        }
    except Exception as e:
        logger.warning(f"Could not query workflow status: {str(e)}")
        return {
            "source": "temporal",
            "workflow_id": workflow_id,
            "error": str(e),
        }


def cancel_test_execution(test_execution_id: str) -> bool:
    """
    Cancel a running TestExecutionWorkflow.

    Args:
        test_execution_id: UUID of the TestExecution

    Returns:
        True if cancellation signal sent successfully
    """
    return async_to_sync(_cancel_test_execution_async)(test_execution_id)


async def _cancel_test_execution_async(test_execution_id: str) -> bool:
    """Async implementation for cancelling workflow using Temporal's cancellation.

    Retries on transient errors (h2 protocol, timeout) to avoid silent fallback
    to DB-only cancellation which doesn't stop the Temporal workflows.
    """
    from tfc.temporal.common.client import get_client

    client = await get_client()
    workflow_id = f"{TEST_EXECUTION_WORKFLOW_ID_PREFIX}-{test_execution_id}"

    return await _cancel_with_retries(client, workflow_id)


def cancel_workflow(workflow_id: str, cancel_signal: Optional[str] = None) -> bool:
    """
    Cancel a Temporal workflow by its full workflow ID.

    Works for any workflow type (TestExecution, RerunCoordinator, etc.).

    Args:
        workflow_id: Full Temporal workflow ID.
        cancel_signal: Optional cooperative cancel signal name to send before
            handle.cancel(). This sets a flag in the workflow for immediate
            loop exit, while handle.cancel() raises CancelledError at the
            next await as a safety net.
    """
    return async_to_sync(_cancel_workflow_async)(workflow_id, cancel_signal)


async def _cancel_workflow_async(
    workflow_id: str, cancel_signal: Optional[str] = None
) -> bool:
    """Async implementation for cancelling any workflow by ID.

    TODO: Re-evaluate cooperative signal for outbound call flow.
    Previously sent cooperative signal + handle.cancel(), but sending both
    caused a double-CancelledError that aborted _handle_cancellation cleanup.
    Using handle.cancel() alone for now.
    """
    from tfc.temporal.common.client import get_client

    client = await get_client()

    # TODO: cooperative signal disabled — see docstring above.
    # if cancel_signal:
    #     try:
    #         handle = client.get_workflow_handle(workflow_id)
    #         await handle.signal(cancel_signal)
    #         return True
    #     except Exception:
    #         pass  # fall through to handle.cancel()

    return await _cancel_with_retries(client, workflow_id)


async def _cancel_with_retries(client, workflow_id: str, max_retries: int = 3) -> bool:
    """Cancel a workflow with retries on transient errors.

    Transient errors (h2 protocol, timeout, connection reset) should be retried
    because failing to cancel means workflows keep running and overwrite the
    CANCELLED status in DB.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            handle = client.get_workflow_handle(workflow_id)
            await handle.cancel()
            logger.info(f"Cancelled workflow: {workflow_id}")
            return True
        except Exception as e:
            last_error = e
            error_msg = str(e).lower()
            # Transient errors worth retrying
            # TODO: Adding raw strings for now. Need to find better and reliable mechanisms
            is_transient = any(
                s in error_msg
                for s in ["timeout", "h2 protocol", "connection reset", "unavailable"]
            )
            if is_transient and attempt < max_retries - 1:
                wait = (attempt + 1) * 2  # 2s, 4s
                logger.warning(
                    f"Transient error cancelling {workflow_id} (attempt {attempt + 1}), "
                    f"retrying in {wait}s: {e}"
                )
                await asyncio.sleep(wait)
                continue
            # Non-transient or final attempt
            logger.warning(f"Could not cancel workflow {workflow_id}: {e}")
            return False

    logger.warning(
        f"Failed to cancel workflow {workflow_id} after {max_retries} attempts: {last_error}"
    )
    return False


def rerun_call_executions(
    test_execution_id: str,
    call_execution_ids: list[str],
    org_id: str,
    workspace_id: str,
    eval_only: bool = False,
    eval_config_ids: Optional[list[str]] = None,
    active_workflow_id: Optional[str] = None,
) -> dict:
    """
    Rerun specific call executions via RerunCoordinatorWorkflow.

    Creates a real parent workflow that receives completion signals
    from child CallExecutionWorkflows. This fixes the issue where
    the old implementation used a pseudo-parent that never existed.

    Implements merge strategy: if there's an active rerun workflow for this
    TestExecution with matching eval_only mode, new calls are merged into it
    via signal instead of starting a new workflow.

    Args:
        test_execution_id: UUID of the parent TestExecution
        call_execution_ids: List of CallExecution IDs to rerun
        org_id: Organization ID
        workspace_id: Workspace ID
        eval_only: If True, only rerun evaluations (no call execution)
        eval_config_ids: Optional subset of SimulateEvalConfig IDs to run
            in eval-only mode. None means run every eval config attached to
            the run_test. Only meaningful when eval_only=True.
        active_workflow_id: Optional workflow ID of active rerun to merge into

    Returns:
        Dict with workflow_id, rerun_id, and rerun details.
        Includes 'merged': True if calls were merged into existing workflow.
    """
    return async_to_sync(_rerun_call_executions_async)(
        test_execution_id=test_execution_id,
        call_execution_ids=call_execution_ids,
        org_id=org_id,
        workspace_id=workspace_id,
        eval_only=eval_only,
        eval_config_ids=eval_config_ids,
        active_workflow_id=active_workflow_id,
    )


async def _rerun_call_executions_async(
    test_execution_id: str,
    call_execution_ids: list[str],
    org_id: str,
    workspace_id: str,
    eval_only: bool = False,
    eval_config_ids: Optional[list[str]] = None,
    active_workflow_id: Optional[str] = None,
) -> dict:
    """
    Async implementation for rerunning call executions.

    Implements merge strategy: if there's an active rerun workflow for this
    TestExecution with matching eval_only mode, merge the new calls into it
    instead of starting a new workflow.
    """
    import time

    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.service import RPCError

    from simulate.temporal.workflows.rerun_coordinator_workflow import (
        RerunCoordinatorWorkflow,
    )
    from tfc.temporal.common.client import get_client

    client = await get_client()

    # Check if there's an active rerun workflow we can merge into
    if active_workflow_id:
        try:
            handle = client.get_workflow_handle(active_workflow_id)
            # Check if workflow is still running by querying its status
            status = await handle.query("get_status")

            # If workflow is in a terminal state, we can't merge - start new
            terminal_states = [
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "completed",
                "failed",
                "cancelled",
            ]
            workflow_status = getattr(status, "status", None)

            if workflow_status not in terminal_states:
                # Workflow is still running - send merge signal
                logger.info(
                    "merging_calls_into_existing_rerun",
                    workflow_id=active_workflow_id,
                    test_execution_id=test_execution_id,
                    call_count=len(call_execution_ids),
                    eval_only=eval_only,
                )

                await handle.signal(
                    "merge_calls",
                    MergeCallsSignal(
                        call_execution_ids=call_execution_ids,
                        eval_only=eval_only,
                        eval_config_ids=eval_config_ids,
                    ),
                )

                logger.info(
                    "merged_calls_into_existing_rerun",
                    workflow_id=active_workflow_id,
                    test_execution_id=test_execution_id,
                    call_count=len(call_execution_ids),
                )

                # Return existing workflow info with merged flag
                return {
                    "workflow_id": active_workflow_id,
                    "rerun_id": active_workflow_id.split("-")[
                        -1
                    ],  # Extract rerun_id from workflow_id
                    "successful": call_execution_ids,
                    "failed": [],
                    "total": len(call_execution_ids),
                    "eval_only": eval_only,
                    "merged": True,
                }

        except RPCError as e:
            # Workflow not found or already completed - start new
            logger.info(
                "active_rerun_workflow_not_available",
                workflow_id=active_workflow_id,
                error=str(e),
            )
        except Exception as e:
            # Query failed - start new workflow
            logger.warning(
                "failed_to_query_active_rerun_workflow",
                workflow_id=active_workflow_id,
                error=str(e),
            )

    # Start new rerun workflow
    # Generate unique rerun_id using timestamp (milliseconds)
    rerun_id = str(int(time.time() * 1000))

    # Use different prefix for eval-only reruns for clarity
    prefix = (
        f"{RERUN_COORDINATOR_WORKFLOW_ID_PREFIX}-eval"
        if eval_only
        else RERUN_COORDINATOR_WORKFLOW_ID_PREFIX
    )
    workflow_id = f"{prefix}-{test_execution_id}-{rerun_id}"

    logger.info(
        "starting_rerun_coordinator_workflow",
        workflow_id=workflow_id,
        test_execution_id=test_execution_id,
        call_count=len(call_execution_ids),
        eval_only=eval_only,
    )

    await client.start_workflow(
        RerunCoordinatorWorkflow.run,
        RerunCoordinatorInput(
            test_execution_id=test_execution_id,
            call_execution_ids=call_execution_ids,
            org_id=org_id,
            workspace_id=workspace_id,
            rerun_id=rerun_id,
            eval_only=eval_only,
            eval_config_ids=eval_config_ids,
        ),
        id=workflow_id,
        task_queue=QUEUE_L,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
    )

    logger.info(
        "started_rerun_coordinator_workflow",
        workflow_id=workflow_id,
        test_execution_id=test_execution_id,
        eval_only=eval_only,
    )

    return {
        "workflow_id": workflow_id,
        "rerun_id": rerun_id,
        "successful": call_execution_ids,
        "failed": [],
        "total": len(call_execution_ids),
        "eval_only": eval_only,
        "merged": False,
    }


def cancel_rerun_execution(test_execution_id: str, rerun_id: str) -> bool:
    """
    Cancel a running RerunCoordinatorWorkflow.

    Args:
        test_execution_id: UUID of the TestExecution
        rerun_id: The rerun_id from the original rerun_call_executions call

    Returns:
        True if cancellation signal sent successfully
    """
    return async_to_sync(_cancel_rerun_execution_async)(test_execution_id, rerun_id)


async def _cancel_rerun_execution_async(test_execution_id: str, rerun_id: str) -> bool:
    """Async implementation for cancelling rerun workflow."""
    from tfc.temporal.common.client import get_client

    client = await get_client()
    workflow_id = (
        f"{RERUN_COORDINATOR_WORKFLOW_ID_PREFIX}-{test_execution_id}-{rerun_id}"
    )

    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.cancel()
        logger.info(
            "cancelled_rerun_coordinator_workflow",
            workflow_id=workflow_id,
            test_execution_id=test_execution_id,
        )
        return True
    except Exception as e:
        logger.warning(
            "could_not_cancel_rerun_workflow",
            workflow_id=workflow_id,
            error=str(e),
        )
        return False


def rerun_evaluations_only(
    test_execution_id: str,
    call_execution_ids: list[str],
    org_id: str,
    workspace_id: str,
) -> dict:
    """
    Rerun evaluations only for specific call executions.

    Uses the RerunCoordinatorWorkflow with eval_only=True to run
    evaluations via Temporal activities instead of Celery.

    Args:
        test_execution_id: UUID of the parent TestExecution
        call_execution_ids: List of CallExecution IDs to rerun evals for
        org_id: Organization ID
        workspace_id: Workspace ID

    Returns:
        Dict with workflow_id, rerun_id, and rerun details
    """
    return rerun_call_executions(
        test_execution_id=test_execution_id,
        call_execution_ids=call_execution_ids,
        org_id=org_id,
        workspace_id=workspace_id,
        eval_only=True,
    )
