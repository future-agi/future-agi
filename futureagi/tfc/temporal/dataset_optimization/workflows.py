"""
Temporal workflow orchestrating Dataset Optimization runs.

Design notes:
- Simple 3-activity workflow: setup -> run_optimization -> finalize
- Single long-running activity with heartbeats
- No signals (pause/resume/cancel) - simplify for reliability
- No queries - state is persisted in DB via callbacks
- Resume handled automatically via DatasetOptimizationTrial.metadata
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

# Import types safely (no Django)
with workflow.unsafe.imports_passed_through():
    from tfc.temporal.common.evaluation_denials import evaluation_denial_message
    from tfc.temporal.dataset_optimization.types import (
        DatasetOptimizationWorkflowInput,
        DatasetOptimizationWorkflowOutput,
    )


ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)


@workflow.defn
class DatasetOptimizationWorkflow:
    @workflow.run
    async def run(
        self, input: DatasetOptimizationWorkflowInput
    ) -> DatasetOptimizationWorkflowOutput:
        try:
            # Setup
            setup = await workflow.execute_activity(
                "dataset_optimization_setup_activity",
                {"run_id": input.run_id},
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=ACTIVITY_RETRY,
            )

            # Run ALL optimization in one activity (4 hour timeout)
            result = await workflow.execute_activity(
                "dataset_optimization_run_activity",
                {
                    "run_id": input.run_id,
                },
                start_to_close_timeout=timedelta(hours=4),
                heartbeat_timeout=timedelta(
                    minutes=5
                ),  # Activity must heartbeat every 5 min
                retry_policy=ACTIVITY_RETRY,
            )

            # Finalize
            await workflow.execute_activity(
                "dataset_optimization_finalize_activity",
                {"run_id": input.run_id, "status": "completed"},
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=ACTIVITY_RETRY,
            )

            return DatasetOptimizationWorkflowOutput(
                run_id=input.run_id,
                status="completed",
                best_prompt=result.get("best_prompt"),
                best_score=result.get("best_score"),
                trials_completed=result.get("trials_run", 0),
                error=None,
            )

        except Exception as e:
            # Detect if this is a cancellation (ActivityError wrapping CancelledError)
            is_cancel = isinstance(e, CancelledError) or (
                isinstance(e, ActivityError) and isinstance(e.cause, CancelledError)
            )
            finalize_status = "cancelled" if is_cancel else "failed"
            error_msg = (
                "Cancelled by user"
                if is_cancel
                else (evaluation_denial_message(e) or str(e))
            )

            try:
                await workflow.execute_activity(
                    "dataset_optimization_finalize_activity",
                    {
                        "run_id": input.run_id,
                        "status": finalize_status,
                        "error": error_msg,
                    },
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=ACTIVITY_RETRY,
                )
            except Exception:
                pass  # Best effort

            return DatasetOptimizationWorkflowOutput(
                run_id=input.run_id,
                status="cancelled" if is_cancel else "failed",
                best_prompt=None,
                best_score=None,
                trials_completed=0,
                error=error_msg,
            )
