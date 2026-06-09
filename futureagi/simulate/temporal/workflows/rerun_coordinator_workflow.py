"""
RerunCoordinatorWorkflow - Parent orchestrator for call execution reruns.

This is a simplified version of TestExecutionWorkflow that manages rerunning
specific CallExecutions. The key difference is that it skips the preparation
phase (setup, create records) since the CallExecution records already exist.

Supports two modes (passed to child workflows):
1. eval_only=False (default): Re-executes calls via CallExecutionWorkflow children
   - Child workflows compete for rate limit slots via CallDispatcherWorkflow
   - Full call + evaluation flow

2. eval_only=True: Only re-runs evaluations on existing call data
   - Child workflows skip call execution and only run evaluations
   - No rate limiting or phone acquisition needed

Architecture:
- Single active rerun per TestExecution (merge strategy for concurrent requests)
- Always launches CallExecutionWorkflow children (eval_only flag passed down)
- Continue-as-new for large reruns
- Graceful cancellation support
"""

import asyncio
from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.workflow import ParentClosePolicy

from simulate.temporal.constants import (
    CONTINUE_AS_NEW_THRESHOLD,
    QUEUE_L,
    RERUN_CALL_EXECUTION_WORKFLOW_ID_PREFIX,
)
from simulate.temporal.retry_policies import DB_RETRY_POLICY
from simulate.temporal.types.call_execution import CallExecutionInput
from simulate.temporal.types.rerun import (
    CancelRerunCallsInput,
    FinalizeRerunInput,
    MergeCallsSignal,
    RerunCoordinatorInput,
    RerunCoordinatorOutput,
    RerunCoordinatorState,
    RerunCoordinatorStatus,
)
from simulate.temporal.types.test_execution import CallCompletedSignal

# Import Django model with sandbox passthrough for status enum access
with workflow.unsafe.imports_passed_through():
    from simulate.models.test_execution import TestExecution as TestExecutionModel


# Batch size for launching child workflows
LAUNCH_BATCH_SIZE = 50


@workflow.defn
class RerunCoordinatorWorkflow:
    """
    Parent orchestrator for call execution reruns.

    Simplified version of TestExecutionWorkflow that manages rerunning
    specific CallExecutions without the preparation phase.

    Always launches CallExecutionWorkflow children - the eval_only flag
    is passed to children to determine whether they run call+eval or eval-only.

    Supports merge strategy: new rerun requests are merged into existing
    workflow via the merge_calls signal instead of starting a new workflow.

    Phases:
    1. LAUNCHING: Spawn CallExecutionWorkflow children
    2. RUNNING: Wait for children to complete (via signals)
    3. FINALIZING: Update TestExecution status and counts
    """

    def __init__(self):
        self._status = "PENDING"
        self._test_execution_id: Optional[str] = None
        self._rerun_id: Optional[str] = None
        self._org_id: Optional[str] = None
        self._workspace_id: Optional[str] = None

        # Progress tracking
        self._total_calls = 0
        self._launched_calls = 0
        self._completed_calls = 0
        self._failed_calls = 0

        # Pending calls to launch as tuples of (call_id, eval_only)
        # Each call carries its own eval_only flag
        self._pending_calls: list[tuple[str, bool]] = []

        # Track all call IDs (for cancellation)
        self._all_call_ids: list[str] = []

        # Cancellation flag
        self._cancelled = False

        # Event count for continue-as-new
        self._event_count = 0

    @workflow.run
    async def run(self, input: RerunCoordinatorInput) -> RerunCoordinatorOutput:
        """Main workflow execution."""
        self._test_execution_id = input.test_execution_id
        self._rerun_id = input.rerun_id
        self._org_id = input.org_id
        self._workspace_id = input.workspace_id

        # Convert call IDs to tuples with their eval_only flag
        # (done for both initial and restored runs)
        self._pending_calls = [
            (call_id, input.eval_only) for call_id in input.call_execution_ids
        ]
        # Restore full call ID list from continue-as-new, or initialize from input
        if input.all_call_ids:
            self._all_call_ids = input.all_call_ids.copy()
        else:
            self._all_call_ids = input.call_execution_ids.copy()

        # Restore state if continuing from checkpoint
        if input.state:
            self._restore_state(input.state)
            workflow.logger.info(
                f"Restored from checkpoint: launched={self._launched_calls}, "
                f"completed={self._completed_calls}, failed={self._failed_calls}"
            )
        else:
            # Initial run - set up tracking
            self._total_calls = len(input.call_execution_ids)

        try:
            return await self._run_rerun(input)

        except asyncio.CancelledError:
            # Handle Temporal cancellation (from handle.cancel())
            workflow.logger.info(
                f"RerunCoordinatorWorkflow cancelled: {input.test_execution_id}"
            )
            return await self._handle_cancellation(input)

        except Exception as e:
            workflow.logger.warning(f"RerunCoordinatorWorkflow failed: {str(e)}")
            return await self._fail(input, str(e))

    async def _run_rerun(self, input: RerunCoordinatorInput) -> RerunCoordinatorOutput:
        """Run the rerun workflow - launches child workflows for all calls."""
        # ========================================
        # PHASE 1: LAUNCHING
        # ========================================
        self._status = "LAUNCHING"

        workflow.logger.info(f"Starting rerun for {self._total_calls} calls")

        while self._pending_calls and not self._cancelled:
            # Check for continue-as-new
            if self._event_count >= CONTINUE_AS_NEW_THRESHOLD:
                return await self._checkpoint(input)

            # Get next batch
            batch = self._pending_calls[:LAUNCH_BATCH_SIZE]
            self._pending_calls = self._pending_calls[LAUNCH_BATCH_SIZE:]

            # Launch batch
            await self._launch_batch(input, batch)

            self._launched_calls += len(batch)
            self._event_count += len(batch)

        # ========================================
        # PHASE 2: RUNNING (wait for completions)
        # ========================================
        self._status = "RUNNING"

        while not self._is_complete() and not self._cancelled:
            # Check for continue-as-new
            if self._event_count >= CONTINUE_AS_NEW_THRESHOLD:
                return await self._checkpoint(input)

            # Wait for signals or timeout (also allows merge_calls signal to be processed)
            await workflow.sleep(10)
            self._event_count += 1

            # Check if new calls were merged and need launching
            if self._pending_calls and not self._cancelled:
                self._status = "LAUNCHING"
                while self._pending_calls and not self._cancelled:
                    if self._event_count >= CONTINUE_AS_NEW_THRESHOLD:
                        return await self._checkpoint(input)

                    batch = self._pending_calls[:LAUNCH_BATCH_SIZE]
                    self._pending_calls = self._pending_calls[LAUNCH_BATCH_SIZE:]
                    await self._launch_batch(input, batch)
                    self._launched_calls += len(batch)
                    self._event_count += len(batch)

                self._status = "RUNNING"

        # Check if cancelled during loops (cooperative cancel signal)
        if self._cancelled:
            return await self._handle_cancellation(input)

        # ========================================
        # PHASE 3: FINALIZING
        # ========================================
        self._status = "FINALIZING"

        workflow.logger.info(
            f"All rerun calls completed: completed={self._completed_calls}, "
            f"failed={self._failed_calls}"
        )

        # Recalculate TestExecution counts from DB
        await workflow.execute_activity(
            "finalize_rerun_execution",
            FinalizeRerunInput(
                test_execution_id=input.test_execution_id,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DB_RETRY_POLICY,
            task_queue=QUEUE_L,
        )

        final_status = TestExecutionModel.ExecutionStatus.COMPLETED
        if self._failed_calls > 0 and self._completed_calls == 0:
            final_status = TestExecutionModel.ExecutionStatus.FAILED

        self._status = final_status

        return RerunCoordinatorOutput(
            status=final_status,
            total_calls=self._total_calls,
            completed_calls=self._completed_calls,
            failed_calls=self._failed_calls,
        )

    # ========================================
    # SIGNAL HANDLERS
    # ========================================

    @workflow.signal
    async def call_completed(self, signal: CallCompletedSignal) -> None:
        """Signal from child CallExecutionWorkflow on completion."""
        self._event_count += 1

        if signal.failed:
            self._failed_calls += 1
        else:
            self._completed_calls += 1

        workflow.logger.info(
            f"Rerun call completed: {signal.call_id}, status={signal.status}, "
            f"failed={signal.failed}, progress={self._completed_calls + self._failed_calls}/{self._total_calls}"
        )

    @workflow.signal
    async def merge_calls(self, signal: MergeCallsSignal) -> None:
        """
        Signal to merge additional call IDs into this running rerun workflow.

        This enables the "single active rerun" pattern where new rerun requests
        are merged into the existing workflow instead of starting a new one.

        Each merged call carries its own eval_only flag - the coordinator doesn't
        enforce a single mode. Children can be a mix of eval-only and call+eval.
        """
        # Filter out calls that are already being processed
        existing_calls = set(self._all_call_ids)
        new_calls = [
            call_id
            for call_id in signal.call_execution_ids
            if call_id not in existing_calls
        ]

        if not new_calls:
            workflow.logger.info("No new calls to merge - all already in queue")
            return

        # Add new calls to pending list and tracking
        # Store as tuples of (call_id, eval_only) for proper child workflow launch
        for call_id in new_calls:
            self._pending_calls.append((call_id, signal.eval_only))
            self._all_call_ids.append(call_id)

        self._total_calls += len(new_calls)
        self._event_count += 1

        workflow.logger.info(
            f"Merged {len(new_calls)} calls (eval_only={signal.eval_only}) into rerun workflow. "
            f"Total calls now: {self._total_calls}, pending: {len(self._pending_calls)}"
        )

    @workflow.signal
    async def cancel(self):
        """Cooperative cancel signal for immediate cancellation.

        Unlike CancelledError (which only raises at the next await),
        this sets the flag immediately so the main loops can check it.
        """
        self._cancelled = True
        workflow.logger.info(
            f"Cancel signal received for rerun coordinator: "
            f"{self._test_execution_id}"
        )

    # ========================================
    # QUERIES
    # ========================================

    @workflow.query
    def get_status(self) -> RerunCoordinatorStatus:
        """Query current workflow status."""
        return RerunCoordinatorStatus(
            status=self._status,
            total_calls=self._total_calls,
            completed_calls=self._completed_calls,
            failed_calls=self._failed_calls,
            launched_calls=self._launched_calls,
        )

    # ========================================
    # HELPER METHODS
    # ========================================

    async def _launch_batch(
        self, input: RerunCoordinatorInput, calls: list[tuple[str, bool]]
    ) -> None:
        """Launch a batch of CallExecutionWorkflow children for reruns.

        Args:
            input: Coordinator input with org_id, workspace_id, etc.
            calls: List of (call_id, eval_only) tuples
        """
        try:
            from ee.voice.temporal.workflows.call_execution_workflow import (
                CallExecutionWorkflow,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Voice call execution workflow is unavailable without Enterprise Edition."
            ) from exc

        for call_id, eval_only in calls:
            # Unique workflow ID includes rerun_id to allow multiple reruns
            workflow_id = (
                f"{RERUN_CALL_EXECUTION_WORKFLOW_ID_PREFIX}-{call_id}-{input.rerun_id}"
            )

            await workflow.start_child_workflow(
                CallExecutionWorkflow.run,
                CallExecutionInput(
                    call_id=call_id,
                    org_id=input.org_id,
                    workspace_id=input.workspace_id,
                    test_workflow_id=workflow.info().workflow_id,  # This coordinator!
                    test_execution_id=input.test_execution_id,
                    eval_only=eval_only,  # Each call has its own eval_only flag
                ),
                id=workflow_id,
                task_queue=QUEUE_L,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                # ABANDON allows children to continue running when parent does continue-as-new
                parent_close_policy=ParentClosePolicy.ABANDON,
            )

    def _is_complete(self) -> bool:
        """Check if all calls have completed."""
        return (self._completed_calls + self._failed_calls) >= self._total_calls

    async def _fail(
        self, input: RerunCoordinatorInput, error: str
    ) -> RerunCoordinatorOutput:
        """Mark workflow as failed and update database."""
        self._status = TestExecutionModel.ExecutionStatus.FAILED

        # Cancel running child workflows to prevent orphaned children
        # holding dispatcher slots and phone numbers indefinitely.
        # Versioned: old workflows didn't cancel children on failure.
        if workflow.patched("fail-cancels-children") and self._all_call_ids:
            try:
                await workflow.execute_activity(
                    "cancel_rerun_calls",
                    CancelRerunCallsInput(
                        test_execution_id=input.test_execution_id,
                        call_execution_ids=self._all_call_ids,
                        rerun_id=input.rerun_id,
                        reason=f"Rerun coordinator failed: {error}",
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_L,
                )
            except Exception as e:
                workflow.logger.warning(
                    f"Failed to cancel children on failure: {str(e)}"
                )

        # Update database with failed status
        try:
            from simulate.temporal.types.activities import FinalizeInput

            await workflow.execute_activity(
                "finalize_test_execution",
                FinalizeInput(
                    test_execution_id=input.test_execution_id,
                    status=TestExecutionModel.ExecutionStatus.FAILED,
                    completed_calls=self._completed_calls,
                    failed_calls=self._failed_calls,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_L,
            )
        except Exception as e:
            workflow.logger.warning(
                f"Failed to update TestExecution status to FAILED: {str(e)}"
            )

        return RerunCoordinatorOutput(
            status=TestExecutionModel.ExecutionStatus.FAILED,
            total_calls=self._total_calls,
            completed_calls=self._completed_calls,
            failed_calls=self._failed_calls,
            error=error,
        )

    async def _handle_cancellation(
        self, input: RerunCoordinatorInput
    ) -> RerunCoordinatorOutput:
        """Handle workflow cancellation (from handle.cancel()).

        In the Python Temporal SDK, once CancelledError is caught the workflow
        can run cleanup activities normally — no shielding scope is needed
        (CancellationScope is a Go SDK concept, not available in Python SDK).
        """
        self._status = TestExecutionModel.ExecutionStatus.CANCELLED
        self._cancelled = True

        # Cancel child workflows
        try:
            await workflow.execute_activity(
                "cancel_rerun_calls",
                CancelRerunCallsInput(
                    test_execution_id=input.test_execution_id,
                    call_execution_ids=self._all_call_ids,
                    rerun_id=input.rerun_id,
                    reason="Rerun cancelled by user",
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_L,
            )
        except Exception as e:
            workflow.logger.warning(f"Failed to cancel rerun calls: {str(e)}")

        # Update database with cancelled status
        try:
            from simulate.temporal.types.activities import FinalizeInput

            await workflow.execute_activity(
                "finalize_test_execution",
                FinalizeInput(
                    test_execution_id=input.test_execution_id,
                    status=TestExecutionModel.ExecutionStatus.CANCELLED,
                    completed_calls=self._completed_calls,
                    failed_calls=self._failed_calls,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_L,
            )
        except Exception as e:
            workflow.logger.warning(
                f"Failed to update TestExecution status to CANCELLED: {str(e)}"
            )

        return RerunCoordinatorOutput(
            status=TestExecutionModel.ExecutionStatus.CANCELLED,
            total_calls=self._total_calls,
            completed_calls=self._completed_calls,
            failed_calls=self._failed_calls,
        )

    def _restore_state(self, state: RerunCoordinatorState) -> None:
        """Restore state from continue-as-new checkpoint."""
        self._total_calls = state.total_calls
        self._completed_calls = state.completed_calls
        self._failed_calls = state.failed_calls
        self._launched_calls = state.launched_calls
        # Restore all call IDs for cancellation across continue-as-new
        if state.all_call_ids:
            self._all_call_ids = state.all_call_ids.copy()

    async def _checkpoint(self, input: RerunCoordinatorInput) -> RerunCoordinatorOutput:
        """Checkpoint state and continue-as-new.

        Note: On continue-as-new, remaining calls use input.eval_only for their mode.
        This is a simplification - in practice, most reruns won't hit the checkpoint threshold.
        """
        workflow.logger.info(
            f"Checkpointing rerun: events={self._event_count}, "
            f"completed={self._completed_calls}, launched={self._launched_calls}"
        )

        state = RerunCoordinatorState(
            total_calls=self._total_calls,
            completed_calls=self._completed_calls,
            failed_calls=self._failed_calls,
            launched_calls=self._launched_calls,
            # Preserve all call IDs for cancellation across continue-as-new
            all_call_ids=self._all_call_ids.copy(),
        )

        # Extract call_ids from pending tuples for continue-as-new
        remaining_call_ids = [call_id for call_id, _ in self._pending_calls]

        # Continue with preserved state
        workflow.continue_as_new(
            RerunCoordinatorInput(
                test_execution_id=input.test_execution_id,
                call_execution_ids=remaining_call_ids,
                org_id=input.org_id,
                workspace_id=input.workspace_id,
                rerun_id=input.rerun_id,
                eval_only=input.eval_only,
                state=state,
                # Also pass all_call_ids at input level for initial restoration
                all_call_ids=self._all_call_ids.copy(),
            )
        )

        # This return is never reached but satisfies type checker
        return RerunCoordinatorOutput(status="CHECKPOINT")
