from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

from simulate.temporal.constants import QUEUE_RUNNER
from simulate.temporal.types.certification import (
    CertificationCampaignInput,
    CertificationCaseInput,
    CertificationCasesOutput,
    CertificationFinalizeOutput,
    CertificationPollOutput,
    CertificationReconcileOutput,
    CertificationResultInput,
    CertificationSubmitOutput,
    CertificationWorkflowInput,
    CertificationWorkflowOutput,
)

# Case polling cadence inside the parent workflow.
_CASE_POLL_SECONDS = 30

_ACTIVITY_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=15),
)


@workflow.defn
class ProductionCertificationWorkflow:
    """Durable parent: runs a campaign's cases as ordinary hosted jobs.

    At most ``max_active_cases`` cases are in flight, an individual case
    failure never stops the campaign (no behavioral retry — the hosted
    gateway already owns infrastructure retries), a cancel signal drains
    running cases through the same cancellation path as manual jobs, and
    exact-once job/notification behavior comes from the case idempotency
    keys and the notification outbox.
    """

    def __init__(self) -> None:
        self.cancel_requested = False
        self._state = "pending"

    @workflow.signal
    def cancel(self) -> None:
        self.cancel_requested = True

    @workflow.query
    def status(self) -> dict:
        return {"state": self._state, "cancel_requested": self.cancel_requested}

    async def _run_case(self, case_id: str) -> str:
        """Drive one case to a reconciled terminal state. Returns its state."""
        case_input = CertificationCaseInput(case_id=case_id)
        submitted = await workflow.execute_activity(
            "certification_submit_case",
            case_input,
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_ACTIVITY_RETRY,
            result_type=CertificationSubmitOutput,
        )
        _ = submitted  # the job id is also recorded on the case row
        while True:
            if self.cancel_requested:
                await workflow.execute_activity(
                    "certification_cancel_case",
                    case_input,
                    task_queue=QUEUE_RUNNER,
                    start_to_close_timeout=timedelta(minutes=4),
                    retry_policy=_ACTIVITY_RETRY,
                )
                return "canceled"
            try:
                poll = await workflow.execute_activity(
                    "certification_poll_case",
                    case_input,
                    task_queue=QUEUE_RUNNER,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_ACTIVITY_RETRY,
                    result_type=CertificationPollOutput,
                )
            except ActivityError as exc:
                if isinstance(exc.cause, CancelledError):
                    raise
                workflow.logger.warning(
                    "certification poll temporarily unavailable for case %s: %s",
                    case_id,
                    exc,
                )
                await workflow.sleep(timedelta(seconds=15))
                continue
            if poll.done:
                break
            await workflow.sleep(timedelta(seconds=_CASE_POLL_SECONDS))

        reconcile = await workflow.execute_activity(
            "certification_reconcile_case",
            case_input,
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_ACTIVITY_RETRY,
            result_type=CertificationReconcileOutput,
        )
        await workflow.execute_activity(
            "certification_record_case_result",
            CertificationResultInput(
                case_id=case_id,
                reconcile_ok=reconcile.ok,
                failure_class=reconcile.failure_class,
            ),
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_ACTIVITY_RETRY,
        )
        return "completed" if reconcile.ok else "failed"

    @workflow.run
    async def run(
        self, input: CertificationWorkflowInput
    ) -> CertificationWorkflowOutput:
        campaign_input = CertificationCampaignInput(campaign_id=input.campaign_id)
        loaded = await workflow.execute_activity(
            "certification_load_cases",
            campaign_input,
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_ACTIVITY_RETRY,
            result_type=CertificationCasesOutput,
        )
        self._state = "running"
        pending = list(loaded.case_ids)
        max_active = max(1, loaded.max_active_cases)
        running: dict[asyncio.Task, str] = {}
        completed = failed = 0

        while pending or running:
            while pending and len(running) < max_active and not self.cancel_requested:
                case_id = pending.pop(0)
                running[asyncio.create_task(self._run_case(case_id))] = case_id
            if not running:
                break
            done, _ = await asyncio.wait(
                running.keys(),
                timeout=15.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                case_id = running.pop(task)
                try:
                    outcome = task.result()
                except CancelledError:
                    raise
                except Exception as exc:  # case failure must not stop the campaign
                    workflow.logger.error(
                        "certification case %s raised: %s", case_id, exc
                    )
                    outcome = "failed"
                if outcome == "completed":
                    completed += 1
                elif outcome == "failed":
                    failed += 1
                # Flush outbox rows as cases land; delivery failure never
                # changes results (the activity records per-row state).
                await workflow.execute_activity(
                    "certification_deliver_notifications",
                    campaign_input,
                    task_queue=QUEUE_RUNNER,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_ACTIVITY_RETRY,
                )
            if self.cancel_requested and not running and pending:
                # Nothing in flight: mark remaining cases canceled via finalize.
                pending = []

        finalize = await workflow.execute_activity(
            "certification_finalize_campaign",
            campaign_input,
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_ACTIVITY_RETRY,
            result_type=CertificationFinalizeOutput,
        )
        await workflow.execute_activity(
            "certification_deliver_notifications",
            campaign_input,
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_ACTIVITY_RETRY,
        )
        self._state = finalize.state
        return CertificationWorkflowOutput(
            campaign_id=input.campaign_id,
            state=finalize.state,
            cases_completed=completed,
            cases_failed=failed,
        )
