"""
Small queue activities (tasks_s).

Fast operations for signals, status updates, and quick database operations.
These activities run on the tasks_s queue with short timeouts.

All activities use async functions with Django's async ORM for non-blocking operations.

IMPORTANT: Each activity calls _close_old_connections() at the start to prevent
connection pool exhaustion when using PgBouncer. Without this, connections
accumulate and hit PgBouncer's pool limit (~20 by default).
"""

from django.db import close_old_connections
from temporalio import activity

from simulate.models.test_execution import CallExecution
from simulate.temporal.types.activities import (
    CheckBalanceInput,
    CheckBalanceOutput,
    PersistProcessingSkipStateInput,
    ReleaseSlotInput,
    ReportErrorInput,
    RequestSlotInput,
    SignalCallAnalyzingInput,
    SignalCallCompleteInput,
    SignalSlotBatchInput,
    UpdateCallStatusInput,
)
from simulate.utils.processing_outcomes import (
    build_skipped_eval_output_payload,
    set_processing_skip_metadata,
)


@activity.defn(name="update_call_status")
async def update_call_status(input: UpdateCallStatusInput) -> None:
    """
    Update CallExecution status in database.

    Updates intermediate workflow progress (REGISTERED, ONGOING, etc.).
    CreateCallExecution status is NOT updated here - it stays ONGOING until
    persist_call_result updates it to final status (COMPLETED/FAILED/CANCELLED).

    If test_execution_id is provided and status is REGISTERED, also updates
    TestExecution status from PENDING to RUNNING (first slot allocated).

    Timeout: 30 seconds
    Queue: tasks_s
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(f"Updating call status: {input.call_id} -> {input.status}")

        from simulate.models.test_execution import TestExecution

        call = await CallExecution.objects.aget(id=input.call_id)
        call.status = input.status
        await call.asave(update_fields=["status"])

        activity.logger.info(f"Updated call status: {input.call_id} -> {input.status}")

        # If test_execution_id provided and status is ONGOING, mark TestExecution as RUNNING
        # This handles the transition from PENDING -> RUNNING when first call starts
        status_value = (
            input.status.value if hasattr(input.status, "value") else input.status
        )
        if input.test_execution_id and status_value in [
            CallExecution.CallStatus.ONGOING,
        ]:
            await TestExecution.objects.filter(
                id=input.test_execution_id,
                status=TestExecution.ExecutionStatus.PENDING,
            ).aupdate(status=TestExecution.ExecutionStatus.RUNNING)
            activity.logger.info(
                f"Marked test execution {input.test_execution_id} as RUNNING (if was PENDING)"
            )

        # If status is ANALYZING, check if all calls are now analyzing
        # If so, mark TestExecution as EVALUATING
        if input.test_execution_id and status_value in [
            CallExecution.CallStatus.ANALYZING,
        ]:
            # Count how many calls are NOT in a "done with voice call" state
            # A call is "done with voice" if it's analyzing, completed, failed, or cancelled
            non_analyzing_count = (
                await CallExecution.objects.filter(
                    test_execution_id=input.test_execution_id,
                    deleted=False,
                )
                .exclude(
                    status__in=[
                        CallExecution.CallStatus.ANALYZING,
                        CallExecution.CallStatus.COMPLETED,
                        CallExecution.CallStatus.FAILED,
                        CallExecution.CallStatus.CANCELLED,
                    ]
                )
                .acount()
            )

            if non_analyzing_count == 0:
                # All calls are analyzing or in final state - transition to EVALUATING
                await TestExecution.objects.filter(
                    id=input.test_execution_id,
                    status=TestExecution.ExecutionStatus.RUNNING,
                ).aupdate(status=TestExecution.ExecutionStatus.EVALUATING)
                activity.logger.info(
                    f"Marked test execution {input.test_execution_id} as EVALUATING "
                    f"(all calls are analyzing or complete)"
                )

    except CallExecution.DoesNotExist:
        activity.logger.error(f"Call not found: {input.call_id}")
        activity.logger.exception(f"Call not found: {input.call_id}")
        raise Exception(f"Call not found: {input.call_id}")

    except Exception as e:
        activity.logger.error(f"Failed to update call status: {str(e)}")
        activity.logger.exception(f"Failed to update call status: {str(e)}")
        raise


@activity.defn(name="persist_processing_skip_state")
async def persist_processing_skip_state(input: PersistProcessingSkipStateInput) -> None:
    """Persist post-call processing skip state.

    Stores a general processing skip marker + reason in call_metadata and
    optionally marks all evaluation cells as skipped for clear UI rendering.
    """
    close_old_connections()

    try:
        from simulate.models import SimulateEvalConfig

        call = await CallExecution.objects.select_related(
            "test_execution__run_test"
        ).aget(id=input.call_id)

        call_metadata = call.call_metadata or {}
        update_fields = []

        processing_skipped = input.processing_skipped
        skip_reason = input.processing_skip_reason

        if processing_skipped is not None:
            call_metadata = set_processing_skip_metadata(
                call_metadata,
                skipped=bool(processing_skipped),
                reason=skip_reason,
            )
            update_fields.append("call_metadata")

        if (
            input.mark_eval_outputs_skipped
            and bool(processing_skipped)
            and call.test_execution
            and call.test_execution.run_test
        ):
            eval_outputs = call.eval_outputs or {}
            eval_configs = SimulateEvalConfig.objects.filter(
                run_test=call.test_execution.run_test,
                deleted=False,
            ).values("id", "name")

            async for eval_config in eval_configs:
                eval_outputs[str(eval_config["id"])] = (
                    build_skipped_eval_output_payload(
                        eval_name=eval_config["name"],
                        reason=skip_reason,
                    )
                )

            call.eval_outputs = eval_outputs
            call_metadata["eval_completed"] = True
            if "eval_outputs" not in update_fields:
                update_fields.append("eval_outputs")
            if "call_metadata" not in update_fields:
                update_fields.append("call_metadata")

        if update_fields:
            call.call_metadata = call_metadata
            await call.asave(update_fields=list(set(update_fields)))

    except CallExecution.DoesNotExist:
        activity.logger.error(f"Call not found while setting outcomes: {input.call_id}")
        raise Exception(f"Call not found: {input.call_id}")
    except Exception as e:
        activity.logger.error(f"Failed to set call processing outcomes: {str(e)}")
        activity.logger.exception(f"Failed to set call processing outcomes: {str(e)}")
        raise


@activity.defn(name="check_call_balance")
async def check_call_balance(input: CheckBalanceInput) -> CheckBalanceOutput:
    """
    Check if organization can make a call (free tier limit check).

    Uses the postpaid metering system. Free plans have hard caps on
    voice_sim_minutes / text_sim_tokens. Paid plans always pass.

    Timeout: 30 seconds
    Queue: tasks_s
    """
    close_old_connections()

    try:
        activity.logger.info(f"Checking call usage for org_id={input.org_id}")

        from asgiref.sync import sync_to_async

        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        # Check voice_call limit (covers both voice and text — both are sim calls)
        result = await sync_to_async(check_usage)(input.org_id, "voice_call")

        if not result.allowed:
            activity.logger.warning(
                f"Call blocked for org {input.org_id}: {result.reason}"
            )
            return CheckBalanceOutput(
                sufficient=False,
                error=result.reason or "Usage limit exceeded",
            )

        return CheckBalanceOutput(sufficient=True)

    except Exception as e:
        activity.logger.error(f"Failed to check balance: {str(e)}")
        activity.logger.exception(f"Failed to check balance: {str(e)}")
        return CheckBalanceOutput(
            sufficient=False,
            error=str(e),
        )


@activity.defn(name="release_call_slot")
async def release_call_slot(input: ReleaseSlotInput) -> None:
    """
    Signal the dispatcher to release this call's rate limit slot.

    Sends signal to CallDispatcherWorkflow to free up capacity.

    Timeout: 10 seconds
    Queue: tasks_s
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(f"Releasing call slot for call_id={input.call_id}")

        from simulate.temporal.signals import SIGNAL_RELEASE_SLOT
        from tfc.temporal.common.client import get_client

        # Get singleton Temporal client
        client = await get_client()

        # Send signal to dispatcher
        handle = client.get_workflow_handle("call-dispatcher-singleton")
        await handle.signal(SIGNAL_RELEASE_SLOT, input.call_id)

        activity.logger.info(f"Released call slot for call_id={input.call_id}")

    except Exception as e:
        activity.logger.error(f"Failed to release call slot: {str(e)}")
        activity.logger.exception(f"Failed to release call slot: {str(e)}")
        # Re-raise so Temporal's SIGNAL_RETRY_POLICY can retry the signal delivery.
        # Swallowing this exception causes permanent slot leaks in the dispatcher.
        raise


@activity.defn(name="signal_call_completed")
async def signal_call_completed(input: SignalCallCompleteInput) -> None:
    """
    Signal parent workflow that this call completed.

    Sends minimal completion data to TestExecutionWorkflow.

    Timeout: 10 seconds
    Queue: tasks_s
    """
    try:
        activity.logger.info(
            f"Signaling call completion: call_id={input.call_id}, status={input.status}"
        )

        from simulate.temporal.signals import SIGNAL_CALL_COMPLETED
        from tfc.temporal.common.client import get_client

        # Get singleton Temporal client
        client = await get_client()

        from simulate.temporal.types.test_execution import CallCompletedSignal

        # Send signal to parent workflow
        # Note: signal() only accepts one argument after signal name, so we use a dataclass
        handle = client.get_workflow_handle(input.workflow_id)
        await handle.signal(
            SIGNAL_CALL_COMPLETED,
            CallCompletedSignal(
                call_id=input.call_id,
                status=input.status,
                failed=input.failed,
            ),
        )

        activity.logger.info(f"Signaled call completion for call_id={input.call_id}")

    except Exception as e:
        activity.logger.error(f"Failed to signal call completion: {str(e)}")
        activity.logger.exception(f"Failed to signal call completion: {str(e)}")
        # Re-raise so SIGNAL_RETRY_POLICY can retry delivery.
        # Swallowing this causes the parent to wait indefinitely for signals
        # that were "sent" but never received.
        raise


@activity.defn(name="signal_call_analyzing")
async def signal_call_analyzing(input: SignalCallAnalyzingInput) -> None:
    """
    Signal parent workflow that this call has entered ANALYZING state.

    Sent when a call completes and begins processing results/evaluations.
    This allows the parent TestExecutionWorkflow to transition to EVALUATING
    status when all calls have entered this state.

    Timeout: 10 seconds
    Queue: tasks_s
    """
    try:
        activity.logger.info(f"Signaling call analyzing: call_id={input.call_id}")

        from simulate.temporal.signals import SIGNAL_CALL_ANALYZING
        from tfc.temporal.common.client import get_client

        # Get singleton Temporal client
        client = await get_client()

        from simulate.temporal.types.test_execution import CallAnalyzingSignal

        # Send signal to parent workflow
        handle = client.get_workflow_handle(input.workflow_id)
        await handle.signal(
            SIGNAL_CALL_ANALYZING,
            CallAnalyzingSignal(call_id=input.call_id),
        )

        activity.logger.info(f"Signaled call analyzing for call_id={input.call_id}")

    except Exception as e:
        activity.logger.error(f"Failed to signal call analyzing: {str(e)}")
        activity.logger.exception(f"Failed to signal call analyzing: {str(e)}")
        # Re-raise so SIGNAL_RETRY_POLICY can retry delivery.
        # Swallowing this causes the parent to deadlock waiting for
        # call_analyzing signals that were never received.
        raise


@activity.defn(name="signal_slots_granted_batch")
async def signal_slots_granted_batch(input: SignalSlotBatchInput) -> None:
    """
    Signal multiple workflows that their slots are granted.

    Batch signaling for efficiency - dispatcher grants multiple slots at once.
    Uses asyncio.gather for parallel signaling to avoid sequential bottleneck.

    If a grant signal fails (workflow not found/already completed), we release
    the slot back to the dispatcher to prevent permanent slot leaks.

    Timeout: 30 seconds
    Queue: tasks_s
    """
    import asyncio

    try:
        activity.logger.info(f"Signaling {len(input.grants)} slot grants in parallel")

        from simulate.temporal.signals import SIGNAL_RELEASE_SLOT, SIGNAL_SLOT_GRANTED
        from tfc.temporal.common.client import get_client

        # Get singleton Temporal client
        client = await get_client()

        async def signal_one(grant: dict) -> tuple[str, bool, str]:
            """Signal a single workflow, return (call_id, success, error)."""
            try:
                handle = client.get_workflow_handle(grant["workflow_id"])
                await handle.signal(SIGNAL_SLOT_GRANTED, grant["call_id"])
                return (grant["call_id"], True, "")
            except Exception as e:
                return (grant["call_id"], False, str(e))

        # Signal all workflows in parallel
        results = await asyncio.gather(
            *[signal_one(grant) for grant in input.grants],
            return_exceptions=True,
        )

        # Log results and collect failed grant call_ids for cleanup
        success_count = 0
        failed_call_ids = []
        for result in results:
            if isinstance(result, Exception):
                activity.logger.error(
                    f"Signal task failed with exception: {str(result)}"
                )
            elif result[1]:  # success
                success_count += 1
            else:  # failed
                activity.logger.error(
                    f"Failed to signal slot grant for call {result[0]}: {result[2]}"
                )
                failed_call_ids.append(result[0])

        activity.logger.info(
            f"Completed signaling {success_count}/{len(input.grants)} slot grants"
        )

        # Release slots for failed grants back to the dispatcher.
        # Without this, the dispatcher thinks these calls are active but the
        # workflows never received the grant — causing permanent slot leaks.
        if failed_call_ids:
            activity.logger.warning(
                f"Releasing {len(failed_call_ids)} slots for failed grant signals: {failed_call_ids}"
            )
            dispatcher_handle = client.get_workflow_handle("call-dispatcher-singleton")
            release_tasks = [
                dispatcher_handle.signal(SIGNAL_RELEASE_SLOT, call_id)
                for call_id in failed_call_ids
            ]
            release_results = await asyncio.gather(
                *release_tasks, return_exceptions=True
            )
            for i, release_result in enumerate(release_results):
                if isinstance(release_result, Exception):
                    activity.logger.error(
                        f"Failed to release slot for failed grant {failed_call_ids[i]}: {release_result}"
                    )

    except Exception as e:
        activity.logger.error(f"Failed to signal slot grants: {str(e)}")
        activity.logger.exception(f"Failed to signal slot grants: {str(e)}")
        # CRITICAL: Re-raise so Temporal knows activity failed.
        # The dispatcher keeps pending_grants for retry on next iteration (line 496).
        # Without re-raising, Temporal considers the activity "succeeded" and
        # the dispatcher clears pending_grants - causing permanent slot leaks.
        raise


@activity.defn(name="request_call_slot")
async def request_call_slot(input: RequestSlotInput) -> None:
    """
    Request rate limit slot from dispatcher workflow.

    Signals the CallDispatcherWorkflow to request a slot for this call.
    Auto-starts the dispatcher if it doesn't exist (singleton pattern).
    Workflow waits for SLOT_GRANTED signal before proceeding with call execution.

    Timeout: 10 seconds
    Queue: tasks_s
    """
    try:
        activity.logger.info(
            f"Requesting slot for call_id={input.call_id}, "
            f"workflow_id={input.workflow_id}, org_id={input.org_id}"
        )

        from temporalio.common import WorkflowIDConflictPolicy
        from temporalio.service import RPCError

        from ee.voice.temporal.workflows.call_dispatcher_workflow import (
            CallDispatcherWorkflow,
        )
        from simulate.temporal.constants import QUEUE_L
        from simulate.temporal.signals import SIGNAL_REQUEST_SLOT
        from tfc.temporal.common.client import get_client

        client = await get_client()
        dispatcher_id = "call-dispatcher-singleton"

        # Retry logic for transient failures
        max_attempts = 3
        last_error = None

        for attempt in range(max_attempts):
            try:
                # Start dispatcher if not running, or get existing handle
                # USE_EXISTING policy returns existing workflow handle if already running
                handle = await client.start_workflow(
                    CallDispatcherWorkflow.run,
                    None,  # No initial state
                    id=dispatcher_id,
                    task_queue=QUEUE_L,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                )

                # Signal the dispatcher to request a slot
                signal_data = {
                    "workflow_id": input.workflow_id,
                    "call_id": input.call_id,
                    "org_id": input.org_id,
                    "priority": 0,
                }
                if input.agent_definition_id:
                    signal_data["agent_definition_id"] = input.agent_definition_id
                    signal_data["agent_concurrency_limit"] = (
                        input.agent_concurrency_limit
                    )
                await handle.signal(SIGNAL_REQUEST_SLOT, signal_data)
                activity.logger.info(f"Requested slot for call_id={input.call_id}")
                return  # Success

            except RPCError as e:
                last_error = e
                activity.logger.warning(
                    f"Failed to signal dispatcher (attempt {attempt + 1}/{max_attempts}): {str(e)}"
                )
                # Loop will retry

        raise RuntimeError(
            f"Failed to signal dispatcher after {max_attempts} attempts: {last_error}"
        )

    except Exception as e:
        activity.logger.error(f"Failed to request call slot: {str(e)}")
        activity.logger.exception(f"Failed to request call slot: {str(e)}")
        # Re-raise so Temporal can retry the activity
        raise


@activity.defn(name="report_workflow_error")
async def report_workflow_error(input: ReportErrorInput) -> None:
    """
    Report workflow exceptions via structlog.

    Workflows can't directly report errors because workflow.logger doesn't
    have error tracking integration. This activity runs outside the workflow
    sandbox and uses structlog which captures exceptions.

    Timeout: 10 seconds
    Queue: tasks_s
    """
    from tfc.logging.temporal import get_logger

    logger = get_logger(__name__)
    logger.exception(
        f"{input.workflow_name} error: {input.error_message}",
        workflow_name=input.workflow_name,
        workflow_id=input.workflow_id,
        error_type=input.error_type,
        **input.context,
    )
