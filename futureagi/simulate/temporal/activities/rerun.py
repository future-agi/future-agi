"""
Rerun-specific activities for RerunCoordinatorWorkflow.

Activities for finalizing reruns and cancelling rerun child workflows.
These run on tasks_l queue with moderate timeouts.

IMPORTANT: Each activity calls close_old_connections() at the start to prevent
connection pool exhaustion when using PgBouncer.
"""

from django.db import close_old_connections
from temporalio import activity

from simulate.temporal.signals import SIGNAL_CANCEL_CALL
from simulate.temporal.types.rerun import (
    CancelRerunCallsInput,
    FinalizeRerunInput,
)
from tfc.logging.temporal import get_logger

logger = get_logger(__name__)


@activity.defn(name="finalize_rerun_execution")
async def finalize_rerun_execution(input: FinalizeRerunInput) -> None:
    """
    Finalize rerun by recalculating TestExecution counts from DB.

    Since reruns update individual CallExecution statuses, we need to
    recalculate the total counts rather than incrementing.

    This activity checks if there are still CallExecutions in non-final states
    (PENDING, REGISTERED, ONGOING, ANALYZING) before marking as completed.
    This prevents race conditions when multiple rerun workflows are running.

    Also triggers the eval summary task if applicable.

    Timeout: 2 minutes
    Queue: tasks_l
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        logger.info(
            "finalizing_rerun_execution",
            test_execution_id=input.test_execution_id,
        )

        from django.db.models import Count, Q
        from django.utils import timezone

        from simulate.models.test_execution import (
            CallExecution,
            EvalExplanationSummaryStatus,
            TestExecution,
        )

        # Aggregate counts from CallExecution statuses
        counts = await CallExecution.objects.filter(
            test_execution_id=input.test_execution_id
        ).aaggregate(
            completed=Count("id", filter=Q(status=CallExecution.CallStatus.COMPLETED)),
            failed=Count(
                "id",
                filter=Q(
                    status__in=[
                        CallExecution.CallStatus.FAILED,
                        CallExecution.CallStatus.CANCELLED,
                    ]
                ),
            ),
            # Count calls still in progress (call execution phase)
            in_progress=Count(
                "id",
                filter=Q(
                    status__in=[
                        CallExecution.CallStatus.PENDING,
                        CallExecution.CallStatus.REGISTERED,
                        CallExecution.CallStatus.ONGOING,
                    ]
                ),
            ),
            # Count calls still being analyzed (evaluation phase)
            analyzing=Count(
                "id",
                filter=Q(status=CallExecution.CallStatus.ANALYZING),
            ),
        )

        completed_count = counts.get("completed", 0)
        failed_count = counts.get("failed", 0)
        in_progress_count = counts.get("in_progress", 0)
        analyzing_count = counts.get("analyzing", 0)

        # Determine final status based on what's still running
        if in_progress_count > 0:
            # Still have calls in progress - set to RUNNING
            final_status = TestExecution.ExecutionStatus.RUNNING
            logger.info(
                "rerun_has_calls_in_progress",
                test_execution_id=input.test_execution_id,
                in_progress=in_progress_count,
            )
        elif analyzing_count > 0:
            # Calls done but evals still running - set to EVALUATING
            final_status = TestExecution.ExecutionStatus.EVALUATING
            logger.info(
                "rerun_has_calls_analyzing",
                test_execution_id=input.test_execution_id,
                analyzing=analyzing_count,
            )
        elif failed_count > 0 and completed_count == 0:
            # All finished, all failed
            final_status = TestExecution.ExecutionStatus.FAILED
        else:
            # All finished with at least some success
            final_status = TestExecution.ExecutionStatus.COMPLETED

        # Get test execution to update
        test_execution = await TestExecution.objects.aget(id=input.test_execution_id)

        # Update TestExecution
        test_execution.status = final_status
        test_execution.completed_calls = completed_count
        test_execution.failed_calls = failed_count

        # Only set completed_at if actually completed/failed
        if final_status in [
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.FAILED,
        ]:
            test_execution.completed_at = timezone.now()
            test_execution.picked_up_by_executor = False

        # Clear active rerun workflow ID from metadata if requested and in terminal state
        update_fields = ["status", "completed_calls", "failed_calls"]
        if input.clear_active_rerun and final_status in [
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.FAILED,
            TestExecution.ExecutionStatus.CANCELLED,
        ]:
            metadata = test_execution.execution_metadata or {}
            if "active_rerun_workflow_id" in metadata:
                del metadata["active_rerun_workflow_id"]
                test_execution.execution_metadata = metadata
                update_fields.append("execution_metadata")
                logger.info(
                    "cleared_active_rerun_workflow",
                    test_execution_id=input.test_execution_id,
                )

        # Trigger eval summary if completed
        if final_status == TestExecution.ExecutionStatus.COMPLETED:
            test_execution.eval_explanation_summary_status = (
                EvalExplanationSummaryStatus.PENDING
            )
            update_fields.extend(
                [
                    "completed_at",
                    "picked_up_by_executor",
                    "eval_explanation_summary_status",
                ]
            )
            await test_execution.asave(update_fields=update_fields)

            # Trigger eval summary task
            try:
                from simulate.tasks.eval_summary_tasks import run_eval_summary_task

                run_eval_summary_task.apply_async(args=(str(test_execution.id),))
                logger.info(
                    "triggered_eval_summary_for_rerun",
                    test_execution_id=input.test_execution_id,
                )
            except Exception as e:
                logger.warning(
                    "failed_to_trigger_eval_summary",
                    test_execution_id=input.test_execution_id,
                    error=str(e),
                )
        elif final_status == TestExecution.ExecutionStatus.FAILED:
            update_fields.extend(["completed_at", "picked_up_by_executor"])
            await test_execution.asave(update_fields=update_fields)
        else:
            # Still in progress - just update counts and status
            await test_execution.asave(update_fields=update_fields)

        if final_status in [
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.FAILED,
        ]:
            from asgiref.sync import sync_to_async
            from simulate.services.reproducibility_passport import (
                safe_capture_reproducibility_snapshot,
            )

            await sync_to_async(
                safe_capture_reproducibility_snapshot,
                thread_sensitive=True,
            )(test_execution, "completion")

        logger.info(
            "finalized_rerun_execution",
            test_execution_id=input.test_execution_id,
            completed=completed_count,
            failed=failed_count,
            in_progress=in_progress_count,
            analyzing=analyzing_count,
            status=final_status,
        )

    except Exception as e:
        logger.error(
            "failed_to_finalize_rerun",
            test_execution_id=input.test_execution_id,
            error=str(e),
        )
        logger.exception(
            "failed_to_finalize_rerun",
            test_execution_id=input.test_execution_id,
        )
        raise


@activity.defn(name="cancel_rerun_calls")
async def cancel_rerun_calls(input: CancelRerunCallsInput) -> None:
    """
    Cancel all rerun child workflows and update CallExecution statuses.

    Called when a RerunCoordinatorWorkflow is cancelled by the user.
    Cancels child Temporal workflows and updates DB records.

    Timeout: 2 minutes
    Queue: tasks_l
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        logger.info(
            "cancelling_rerun_calls",
            test_execution_id=input.test_execution_id,
            call_count=len(input.call_execution_ids),
            reason=input.reason,
        )

        from simulate.models.run_test import CreateCallExecution
        from simulate.models.test_execution import CallExecution, TestExecution
        from simulate.temporal.constants import RERUN_CALL_EXECUTION_WORKFLOW_ID_PREFIX

        # Find calls that are not in a final state
        # Include ANALYZING: calls that finished voice but are still running evals
        non_final_statuses = [
            CallExecution.CallStatus.PENDING,
            CallExecution.CallStatus.REGISTERED,
            CallExecution.CallStatus.ONGOING,
            CallExecution.CallStatus.ANALYZING,
        ]

        # Update CallExecution statuses to CANCELLED
        cancelled_count = await CallExecution.objects.filter(
            id__in=input.call_execution_ids,
            status__in=non_final_statuses,
        ).aupdate(
            status=CallExecution.CallStatus.CANCELLED,
            ended_reason=input.reason,
        )

        # Update corresponding CreateCallExecution records (only non-final)
        await CreateCallExecution.objects.filter(
            call_execution_id__in=input.call_execution_ids,
            status__in=non_final_statuses,
        ).aupdate(
            status=CreateCallExecution.CallStatus.CANCELLED,
        )

        logger.info(
            "updated_call_execution_statuses",
            test_execution_id=input.test_execution_id,
            cancelled_count=cancelled_count,
        )

        # Cancel ALL child Temporal workflows regardless of DB status.
        # This is critical for eval-only reruns where CallExecution.status stays
        # COMPLETED (view doesn't change it), so the DB filter above finds zero
        # calls — but the child workflows are still running evals.
        # We try to cancel every workflow ID; already-completed ones fail silently.
        try:
            from tfc.temporal.common.client import get_client

            client = await get_client()
            cancelled_workflows = 0

            for call_id in input.call_execution_ids:
                workflow_id = f"{RERUN_CALL_EXECUTION_WORKFLOW_ID_PREFIX}-{call_id}-{input.rerun_id}"

                try:
                    handle = client.get_workflow_handle(workflow_id)
                    # TODO: Re-evaluate cooperative signal for outbound call flow.
                    # Previously sent handle.signal(SIGNAL_CANCEL_CALL) here to
                    # set _cancelled=True and unblock wait_condition, but sending
                    # both signal + handle.cancel() caused a double-CancelledError
                    # that aborted _handle_cancellation cleanup (room not deleted,
                    # DB status not updated).  Using handle.cancel() alone for now.
                    await handle.cancel()
                    cancelled_workflows += 1
                    logger.debug(
                        "cancelled_child_workflow",
                        workflow_id=workflow_id,
                    )
                except Exception as e:
                    # Workflow already completed/failed - slot already released
                    logger.debug(
                        "could_not_cancel_workflow",
                        workflow_id=workflow_id,
                        error=str(e),
                    )

            logger.info(
                "cancelled_rerun_child_workflows",
                test_execution_id=input.test_execution_id,
                cancelled_workflows=cancelled_workflows,
                total_calls=len(input.call_execution_ids),
            )

        except Exception as e:
            logger.error(
                "failed_to_cancel_child_workflows",
                test_execution_id=input.test_execution_id,
                error=str(e),
            )
            logger.exception(
                "failed_to_cancel_child_workflows",
                test_execution_id=input.test_execution_id,
            )

        # Update TestExecution status to CANCELLED and clear active_rerun_workflow_id
        test_execution = await TestExecution.objects.aget(
            id=input.test_execution_id,
        )
        test_execution.status = TestExecution.ExecutionStatus.CANCELLED
        update_fields = ["status"]

        metadata = test_execution.execution_metadata or {}
        if "active_rerun_workflow_id" in metadata:
            del metadata["active_rerun_workflow_id"]
            test_execution.execution_metadata = metadata
            update_fields.append("execution_metadata")
            logger.info(
                "cleared_active_rerun_workflow",
                test_execution_id=input.test_execution_id,
            )

        await test_execution.asave(update_fields=update_fields)

        logger.info(
            "cancelled_rerun_calls_complete",
            test_execution_id=input.test_execution_id,
            cancelled_count=cancelled_count,
        )

    except Exception as e:
        logger.error(
            "failed_to_cancel_rerun_calls",
            test_execution_id=input.test_execution_id,
            error=str(e),
        )
        logger.exception(
            "failed_to_cancel_rerun_calls",
            test_execution_id=input.test_execution_id,
        )
        raise
