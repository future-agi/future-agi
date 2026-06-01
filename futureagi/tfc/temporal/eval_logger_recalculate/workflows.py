"""Workflow for batch-recalculating sibling EvalLoggers.

IMPORTANT: Do NOT use workflow.logger - stdlib logging locks cause sandbox
deadlocks. Logging goes in activities.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from tfc.temporal.eval_logger_recalculate.types import (
    DispatchRerunInput,
    RecalculateEvalTaskWorkflowInput,
    RecalculateEvalTaskWorkflowOutput,
    SoftDeleteSiblingsInput,
)

RECALCULATE_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)


@workflow.defn
class RecalculateEvalTaskWorkflow:
    """Bulk soft-delete + per-target rerun fan-out."""

    @workflow.run
    async def run(
        self, input: RecalculateEvalTaskWorkflowInput
    ) -> RecalculateEvalTaskWorkflowOutput:
        await workflow.execute_activity(
            "soft_delete_sibling_eval_loggers_activity",
            SoftDeleteSiblingsInput(
                eval_task_id=input.eval_task_id,
                custom_eval_config_id=input.custom_eval_config_id,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RECALCULATE_RETRY_POLICY,
        )

        total = len(input.targets)
        semaphore = asyncio.Semaphore(input.max_concurrent)

        async def process_one(target) -> bool:
            async with semaphore:
                try:
                    result = await workflow.execute_activity(
                        "dispatch_rerun_activity",
                        DispatchRerunInput(
                            target_type=target.target_type,
                            observation_span_id=target.observation_span_id,
                            trace_id=target.trace_id,
                            trace_session_id=target.trace_session_id,
                            custom_eval_config_id=input.custom_eval_config_id,
                            eval_task_id=input.eval_task_id,
                            feedback_id=input.feedback_id,
                        ),
                        start_to_close_timeout=timedelta(hours=2),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=RECALCULATE_RETRY_POLICY,
                    )
                    return result["status"] == "completed"
                except Exception:
                    return False

        tasks = [process_one(t) for t in input.targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        completed = sum(1 for r in results if isinstance(r, bool) and r)
        failed = total - completed
        status = (
            "COMPLETED" if failed == 0 else "PARTIAL" if completed > 0 else "FAILED"
        )

        return RecalculateEvalTaskWorkflowOutput(
            total=total,
            completed=completed,
            failed=failed,
            status=status,
        )


__all__ = ["RecalculateEvalTaskWorkflow"]
