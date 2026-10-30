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
# The failures whose text is written for people: the stages' refusals and
# the model's own errors. Anything else, a timeout or an error the SDK raises
# itself (an activity a worker hasn't registered lists the worker's
# activities), shows FAILED_MESSAGE.
SHOWN_ERRORS = {"CapabilityDenied", "CapabilityUnavailable", "InsightModelError"}

BATCH = 24
PARALLEL_BATCHES = 4
STAGE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)
# A batch is BATCH units on the pipeline's 8 threads, 3 units in a row per
# thread, and a unit makes up to 3 model attempts of up to 3 tries of 180 s
# each when the provider hangs: about 27 minutes a unit, 81 a batch. The
# bound is above that to avoid start-to-close retries during those calls.
# Heartbeats detect a lost worker sooner; they do not stop in-flight threads
# or make paid calls idempotent if an activity loses its heartbeat.
EXTRACT_TIMEOUT = timedelta(minutes=90)
# Grouping and writing make several thinking-model calls in a row, each up to
# about 27 minutes when the provider hangs, and add their spend to the run on
# every attempt; the same reasoning puts their bound above that. The
# workflow's own limit (client.RUN_TIMEOUT) caps the whole report.
STAGE_TIMEOUT = timedelta(minutes=90)
HEARTBEAT_TIMEOUT = timedelta(minutes=2)


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
                        start_to_close_timeout=EXTRACT_TIMEOUT,
                        heartbeat_timeout=HEARTBEAT_TIMEOUT,
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
                start_to_close_timeout=STAGE_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=STAGE_RETRY,
            )
            return await workflow.execute_activity(
                "insight_finish",
                input,
                start_to_close_timeout=STAGE_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=STAGE_RETRY,
            )
        except ActivityError as exc:
            cause = getattr(exc, "cause", None)
            shown = isinstance(cause, ApplicationError) and cause.type in SHOWN_ERRORS
            message = (cause.message if shown else "") or FAILED_MESSAGE
            details = cause.details if shown else ()
            await workflow.execute_activity(
                "insight_fail",
                InsightFailInput(
                    run_id=input.run_id,
                    message=message,
                    code=str(details[0]) if details else "",
                ),
                start_to_close_timeout=timedelta(seconds=30),
            )
            raise
