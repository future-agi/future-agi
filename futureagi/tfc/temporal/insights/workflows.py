"""Insights report workflow: read -> summarise (parallel batches) -> group ->
measure and write."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from tfc.temporal.insights.types import (
        InsightBatchInput,
        InsightFailInput,
        InsightRunInput,
    )

FAILED_MESSAGE = "The report could not be completed."

BATCH = 24
PARALLEL_BATCHES = 4
STAGE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)


@workflow.defn
class InsightReportWorkflow:
    async def _extract(self, run_id: str, pks: list[int], parallel: int) -> None:
        batches = [pks[i : i + BATCH] for i in range(0, len(pks), BATCH)]
        for start in range(0, len(batches), parallel):
            await asyncio.gather(
                *(
                    workflow.execute_activity(
                        "insight_extract",
                        InsightBatchInput(run_id=run_id, unit_pks=batch),
                        start_to_close_timeout=timedelta(minutes=10),
                        retry_policy=STAGE_RETRY,
                    )
                    for batch in batches[start : start + parallel]
                )
            )

    @workflow.run
    async def run(self, input: InsightRunInput) -> dict:
        try:
            ready = await workflow.execute_activity(
                "insight_prepare",
                input,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STAGE_RETRY,
            )
            await self._extract(input.run_id, ready, PARALLEL_BATCHES)
            # One more pass over units that hit a transient model error,
            # at lower concurrency.
            retry = await workflow.execute_activity(
                "insight_retry_failed",
                input,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=STAGE_RETRY,
            )
            if retry:
                await self._extract(input.run_id, retry, 1)
            await workflow.execute_activity(
                "insight_organise",
                input,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=STAGE_RETRY,
            )
            return await workflow.execute_activity(
                "insight_finish",
                input,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=STAGE_RETRY,
            )
        except ActivityError as exc:
            # Only an ApplicationError carries text written for people; a
            # timeout or cancellation cause says "activity StartToClose
            # timeout", which is not for the report page.
            cause = getattr(exc, "cause", None)
            message = (
                cause.message if isinstance(cause, ApplicationError) else ""
            ) or FAILED_MESSAGE
            await workflow.execute_activity(
                "insight_fail",
                InsightFailInput(run_id=input.run_id, message=message),
                start_to_close_timeout=timedelta(seconds=30),
            )
            raise
