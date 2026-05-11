"""
TestExecutionWorkflow - Parent orchestrator for test executions.

Manages the complete lifecycle of a test execution:
1. Setup and validation
2. Creating CallExecution records
3. Launching CallExecutionWorkflow children
4. Tracking progress via signals
5. Finalizing with status and counts

Design:
- Launches up to 1000+ child workflows
- Signal-based progress tracking (children signal completion)
- Continue-as-new for large test runs
- Graceful cancellation support
"""

import asyncio
from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.workflow import ParentClosePolicy

from simulate.temporal.constants import (
    CALL_EXECUTION_WORKFLOW_ID_PREFIX,
    CONTINUE_AS_NEW_THRESHOLD,
    LAUNCH_BATCH_SIZE,
    LAUNCH_SUB_BATCH_DELAY_SECONDS,
    LAUNCH_SUB_BATCH_SIZE,
    QUEUE_L,
    QUEUE_S,
)
from simulate.temporal.retry_policies import DB_RETRY_POLICY
from simulate.temporal.signals import SIGNAL_CALL_ANALYZING, SIGNAL_CALL_COMPLETED
from simulate.temporal.types.activities import (
    CancelPendingCallsInput,
    CancelPendingCallsOutput,
    CreateCallRecordsInput,
    CreateCallRecordsOutput,
    FinalizeInput,
    GetUnlaunchedCallsInput,
    GetUnlaunchedCallsOutput,
    ReportErrorInput,
    SetupTestInput,
    SetupTestOutput,
)
from simulate.temporal.types.call_execution import CallExecutionInput
from simulate.temporal.types.test_execution import (
    CallAnalyzingSignal,
    CallCompletedSignal,
    TestExecutionInput,
    TestExecutionOutput,
    TestExecutionState,
    TestExecutionStatus,
)

# Import Django model with sandbox passthrough for status enum access
with workflow.unsafe.imports_passed_through():
    from simulate.models.test_execution import TestExecution as TestExecutionModel


@workflow.defn
class TestExecutionWorkflow:
    """
    Parent orchestrator for test executions.

    Manages launching and tracking of CallExecutionWorkflow children.
    Uses signal-based coordination for progress tracking.

    Phases:
    1. INITIALIZING: Setup, validate config, create call records
    2. LAUNCHING: Spawn child workflows in batches
    3. RUNNING: Wait for children to complete (via signals)
    4. FINALIZING: Update status, trigger post-processing
    """

    def __init__(self):
        self._status = "PENDING"
        self._test_execution_id: Optional[str] = None
        self._org_id: Optional[str] = None
        self._workspace_id: Optional[str] = None

        # Progress tracking
        self._total_calls = 0
        self._launched_calls = 0
        self._completed_calls = 0
        self._failed_calls = 0
        self._analyzing_calls = 0  # Calls that have entered ANALYZING state

        # Child workflow tracking
        self._pending_call_ids: list[str] = []

        # Cancellation
        self._cancelled = False

        # Event count for continue-as-new
        self._event_count = 0

    @workflow.run
    async def run(self, input: TestExecutionInput) -> TestExecutionOutput:
        """Main workflow execution."""
        self._test_execution_id = input.test_execution_id
        self._org_id = input.org_id

        # Restore state if continuing from checkpoint
        if input.state:
            self._restore_state(input.state)
            workflow.logger.info(
                f"Restored from checkpoint: launched={self._launched_calls}, "
                f"completed={self._completed_calls}, failed={self._failed_calls}"
            )

        try:
            # ========================================
            # PHASE 1: INITIALIZATION (skip if resumed)
            # ========================================
            if not input.state:
                self._status = "INITIALIZING"

                # Setup and validate
                setup_result = await workflow.execute_activity(
                    "setup_test_execution",
                    SetupTestInput(
                        test_execution_id=input.test_execution_id,
                        run_test_id=input.run_test_id,
                        scenario_ids=input.scenario_ids,
                        simulator_id=input.simulator_id,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_L,
                    result_type=SetupTestOutput,
                )

                if not setup_result.success:
                    return await self._fail(
                        input, f"Setup failed: {setup_result.error}"
                    )

                # Store workspace_id from setup (from run_test.workspace)
                self._workspace_id = setup_result.workspace_id

                # Create call records
                create_result = await workflow.execute_activity(
                    "create_call_execution_records",
                    CreateCallRecordsInput(
                        test_execution_id=input.test_execution_id,
                        scenarios=setup_result.scenarios,
                        simulator_agent=setup_result.simulator_agent,
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    heartbeat_timeout=timedelta(minutes=1),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_L,
                    result_type=CreateCallRecordsOutput,
                )

                if create_result.error:
                    return await self._fail(
                        input, f"Failed to create calls: {create_result.error}"
                    )

                self._total_calls = create_result.total_created
                self._pending_call_ids = create_result.call_ids

                workflow.logger.info(f"Created {self._total_calls} call records")

            # ========================================
            # PHASE 2: LAUNCHING
            # ========================================
            self._status = "LAUNCHING"

            # If resumed, get unlaunched calls from DB
            if input.state and not self._pending_call_ids:
                unlaunched = await workflow.execute_activity(
                    "get_unlaunched_call_ids",
                    GetUnlaunchedCallsInput(test_execution_id=input.test_execution_id),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_L,
                    result_type=GetUnlaunchedCallsOutput,
                )
                self._pending_call_ids = unlaunched.call_ids

            # Launch children in batches
            while self._pending_call_ids:
                # Check for continue-as-new
                if self._event_count >= CONTINUE_AS_NEW_THRESHOLD:
                    return await self._checkpoint(input)

                # Get next batch
                batch = self._pending_call_ids[:LAUNCH_BATCH_SIZE]
                self._pending_call_ids = self._pending_call_ids[LAUNCH_BATCH_SIZE:]

                # Launch batch
                await self._launch_batch(input, batch)

                self._launched_calls += len(batch)
                self._event_count += len(batch)

            # ========================================
            # PHASE 3: RUNNING (wait for calls to enter ANALYZING)
            # ========================================
            self._status = "RUNNING"

            # Wait for all calls to enter ANALYZING state (call completed, processing results)
            while not self._all_analyzing():
                # Check for continue-as-new
                if self._event_count >= CONTINUE_AS_NEW_THRESHOLD:
                    return await self._checkpoint(input)

                # Update progress in DB periodically (non-critical - don't fail workflow)
                try:
                    await workflow.execute_activity(
                        "update_test_execution_counts",
                        args=[
                            input.test_execution_id,
                            self._completed_calls,
                            self._failed_calls,
                        ],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=DB_RETRY_POLICY,
                        task_queue=QUEUE_S,
                    )
                except Exception as e:
                    workflow.logger.warning(f"Failed to update progress counts: {e}")

                # Wait for signals or timeout
                # Use workflow.sleep for Temporal determinism
                await workflow.sleep(10)
                self._event_count += 1

            # ========================================
            # PHASE 4: EVALUATING (all calls analyzing, wait for evals to complete)
            # ========================================
            # Note: DB status is updated to EVALUATING by update_call_status activity
            # when the last call enters ANALYZING state
            self._status = TestExecutionModel.ExecutionStatus.EVALUATING

            workflow.logger.info(
                f"All calls analyzing, transitioning to EVALUATING: "
                f"analyzing={self._analyzing_calls}, total={self._total_calls}"
            )

            # Wait for all calls to fully complete (evals done)
            while not self._is_complete():
                # Check for continue-as-new
                if self._event_count >= CONTINUE_AS_NEW_THRESHOLD:
                    return await self._checkpoint(input)

                # Update progress in DB periodically
                try:
                    await workflow.execute_activity(
                        "update_test_execution_counts",
                        args=[
                            input.test_execution_id,
                            self._completed_calls,
                            self._failed_calls,
                        ],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=DB_RETRY_POLICY,
                        task_queue=QUEUE_S,
                    )
                except Exception as e:
                    workflow.logger.warning(f"Failed to update progress counts: {e}")

                # Wait for signals or timeout
                await workflow.sleep(10)
                self._event_count += 1

            # ========================================
            # PHASE 5: FINALIZATION
            # ========================================
            self._status = "FINALIZING"

            final_status = TestExecutionModel.ExecutionStatus.COMPLETED
            if self._failed_calls > 0 and self._completed_calls == 0:
                final_status = TestExecutionModel.ExecutionStatus.FAILED

            await workflow.execute_activity(
                "finalize_test_execution",
                FinalizeInput(
                    test_execution_id=input.test_execution_id,
                    status=final_status,
                    completed_calls=self._completed_calls,
                    failed_calls=self._failed_calls,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_L,
            )

            self._status = final_status

            return TestExecutionOutput(
                status=final_status,
                total_calls=self._total_calls,
                completed_calls=self._completed_calls,
                failed_calls=self._failed_calls,
            )

        except asyncio.CancelledError:
            # Handle Temporal cancellation (from handle.cancel())
            workflow.logger.info(
                f"TestExecutionWorkflow cancelled via handle.cancel(): {input.test_execution_id}"
            )
            return await self._handle_cancellation(input)

        except Exception as e:
            workflow.logger.warning(f"TestExecutionWorkflow failed: {str(e)}")
            # Report error via activity (fire-and-forget, don't wait for completion)
            workflow.start_activity(
                "report_workflow_error",
                ReportErrorInput(
                    workflow_name="TestExecutionWorkflow",
                    workflow_id=workflow.info().workflow_id,
                    error_message=str(e),
                    error_type=type(e).__name__,
                    context={
                        "test_execution_id": input.test_execution_id,
                        "run_test_id": input.run_test_id,
                    },
                ),
                start_to_close_timeout=timedelta(seconds=10),
                task_queue=QUEUE_S,
            )
            return await self._fail(input, str(e))

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
            f"Call completed: {signal.call_id}, status={signal.status}, failed={signal.failed}, "
            f"progress={self._completed_calls + self._failed_calls}/{self._total_calls}"
        )

    @workflow.signal
    async def call_analyzing(self, signal: CallAnalyzingSignal) -> None:
        """Signal from child CallExecutionWorkflow when entering ANALYZING state."""
        self._event_count += 1
        self._analyzing_calls += 1

        workflow.logger.info(
            f"Call analyzing: {signal.call_id}, "
            f"analyzing_progress={self._analyzing_calls}/{self._total_calls}"
        )

    # ========================================
    # QUERIES
    # ========================================

    @workflow.query
    def get_status(self) -> TestExecutionStatus:
        """Query current workflow status."""
        return TestExecutionStatus(
            status=self._status,
            total_calls=self._total_calls,
            completed_calls=self._completed_calls,
            failed_calls=self._failed_calls,
            launched_calls=self._launched_calls,
            analyzing_calls=self._analyzing_calls,
        )

    # ========================================
    # HELPER METHODS
    # ========================================

    async def _launch_batch(
        self, input: TestExecutionInput, call_ids: list[str]
    ) -> None:
        """Launch a batch of CallExecutionWorkflow children.

        Launches in sub-batches of LAUNCH_SUB_BATCH_SIZE with a short delay
        between each sub-batch to prevent thundering-herd 503s from LiveKit
        agent workers when many calls are initiated simultaneously.
        """
        try:
            from ee.voice.temporal.workflows.call_execution_workflow import (
                CallExecutionWorkflow,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Voice call execution workflow is unavailable without Enterprise Edition."
            ) from exc

        for i, call_id in enumerate(call_ids):
            # Stagger: pause between sub-batches to let agent workers accept dispatches
            if i > 0 and i % LAUNCH_SUB_BATCH_SIZE == 0:
                await workflow.sleep(LAUNCH_SUB_BATCH_DELAY_SECONDS)

            workflow_id = f"{CALL_EXECUTION_WORKFLOW_ID_PREFIX}-{call_id}"

            await workflow.start_child_workflow(
                CallExecutionWorkflow.run,
                CallExecutionInput(
                    call_id=call_id,
                    org_id=input.org_id,
                    workspace_id=self._workspace_id or "",
                    test_workflow_id=workflow.info().workflow_id,
                    test_execution_id=input.test_execution_id,
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

    def _all_analyzing(self) -> bool:
        """Check if all calls have entered ANALYZING state (or completed/failed)."""
        # A call is "done with voice" if it's analyzing, completed, or failed.
        # Calls that fail before reaching ANALYZING (e.g., balance check, preparation,
        # call initiation) never send call_analyzing signal, so we must count
        # failed_calls to avoid deadlocking the parent workflow.
        return (self._analyzing_calls + self._failed_calls) >= self._total_calls

    async def _fail(self, input: TestExecutionInput, error: str) -> TestExecutionOutput:
        """Mark workflow as failed and update database."""
        self._status = TestExecutionModel.ExecutionStatus.FAILED

        # Update database with failed status
        try:
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

        return TestExecutionOutput(
            status=TestExecutionModel.ExecutionStatus.FAILED,
            total_calls=self._total_calls,
            completed_calls=self._completed_calls,
            failed_calls=self._failed_calls,
            error=error,
        )

    async def _handle_cancellation(
        self, input: TestExecutionInput
    ) -> TestExecutionOutput:
        """Handle workflow cancellation (from handle.cancel()).

        In the Python Temporal SDK, once CancelledError is caught the workflow
        can run cleanup activities normally — no shielding scope is needed
        (CancellationScope is a Go SDK concept, not available in Python SDK).
        """
        self._status = TestExecutionModel.ExecutionStatus.CANCELLED
        self._cancelled = True

        # Cancel all pending/ongoing child workflows and release slots
        try:
            await workflow.execute_activity(
                "cancel_pending_calls",
                CancelPendingCallsInput(
                    test_execution_id=input.test_execution_id,
                    reason="Cancelled by user",
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_L,
                result_type=CancelPendingCallsOutput,
            )
        except Exception as e:
            workflow.logger.warning(f"Failed to cancel pending calls: {str(e)}")

        # Update database with cancelled status
        try:
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

        return TestExecutionOutput(
            status=TestExecutionModel.ExecutionStatus.CANCELLED,
            total_calls=self._total_calls,
            completed_calls=self._completed_calls,
            failed_calls=self._failed_calls,
        )

    def _restore_state(self, state: TestExecutionState) -> None:
        """Restore state from continue-as-new checkpoint."""
        self._status = state.status
        self._total_calls = state.total_calls
        self._completed_calls = state.completed_calls
        self._failed_calls = state.failed_calls
        self._launched_calls = state.launched_calls
        self._analyzing_calls = state.analyzing_calls

    async def _checkpoint(self, input: TestExecutionInput) -> TestExecutionOutput:
        """Checkpoint state and continue-as-new."""
        workflow.logger.info(
            f"Checkpointing: events={self._event_count}, "
            f"completed={self._completed_calls}, launched={self._launched_calls}"
        )

        state = TestExecutionState(
            status=self._status,
            total_calls=self._total_calls,
            completed_calls=self._completed_calls,
            failed_calls=self._failed_calls,
            launched_calls=self._launched_calls,
            analyzing_calls=self._analyzing_calls,
        )

        # Continue with preserved state
        workflow.continue_as_new(
            TestExecutionInput(
                test_execution_id=input.test_execution_id,
                run_test_id=input.run_test_id,
                org_id=input.org_id,
                scenario_ids=input.scenario_ids,
                simulator_id=input.simulator_id,
                state=state,
            )
        )

        # This return is never reached but satisfies type checker
        return TestExecutionOutput(status="CHECKPOINT")
