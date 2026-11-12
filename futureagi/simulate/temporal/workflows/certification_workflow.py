from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError

from simulate.temporal.constants import QUEUE_RUNNER
from simulate.temporal.types.certification import (
    CertificationCampaignInput,
    CertificationCasesOutput,
    CertificationFinalizeOutput,
    CertificationResultInput,
    CertificationWorkflowInput,
    CertificationWorkflowOutput,
)
from simulate.temporal.workflows.certification_case_workflow import (
    CertificationCaseWorkflow,
)

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
        self.cancel_requested = self.cancel_requested or loaded.cancel_requested
        pending = list(loaded.pending_cases)
        resume_active = list(loaded.active_cases)
        max_active = max(1, loaded.max_active_cases)
        running = {}
        cancel_handled = False

        async def wait_for_child(handle):
            return await handle

        async def start_case(case_input):
            handle = await workflow.start_child_workflow(
                CertificationCaseWorkflow.run,
                case_input,
                id=f"{workflow.info().workflow_id}-case-{case_input.case_id}",
                task_queue=QUEUE_RUNNER,
            )
            running[asyncio.create_task(wait_for_child(handle))] = (
                case_input,
                handle,
            )

        for case_input in resume_active:
            await start_case(case_input)

        while pending or running:
            while pending and len(running) < max_active and not self.cancel_requested:
                await start_case(pending.pop(0))
            if self.cancel_requested and not cancel_handled:
                for _, handle in running.values():
                    await handle.signal(CertificationCaseWorkflow.cancel)
                for case_input in pending:
                    await workflow.execute_activity(
                        "certification_record_case_result",
                        CertificationResultInput(
                            case_id=case_input.case_id,
                            outcome="canceled",
                            failure_detail={"reason": "canceled_before_dispatch"},
                        ),
                        task_queue=QUEUE_RUNNER,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=_ACTIVITY_RETRY,
                    )
                pending = []
                cancel_handled = True
            if not running:
                break
            await workflow.wait_condition(
                lambda cancel_handled=cancel_handled: (
                    (self.cancel_requested and not cancel_handled)
                    or any(task.done() for task in running)
                )
            )
            done = [task for task in running if task.done()]
            for task in done:
                case_input, _ = running.pop(task)
                try:
                    task.result()
                except CancelledError:
                    raise
                except Exception as exc:  # case failure must not stop the campaign
                    workflow.logger.error(
                        "certification case %s raised: %s", case_input.case_id, exc
                    )
                    await workflow.execute_activity(
                        "certification_record_case_result",
                        CertificationResultInput(
                            case_id=case_input.case_id,
                            outcome="failed",
                            failure_class="environment",
                            failure_detail={
                                "reason": "workflow_activity_exhausted",
                                "error": str(exc)[:500],
                            },
                        ),
                        task_queue=QUEUE_RUNNER,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=_ACTIVITY_RETRY,
                    )
                # Flush outbox rows as cases land; delivery failure never
                # changes results (the activity records per-row state).
                await workflow.execute_activity(
                    "certification_deliver_notifications",
                    campaign_input,
                    task_queue=QUEUE_RUNNER,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_ACTIVITY_RETRY,
                )
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
            cases_completed=finalize.cases_completed,
            cases_failed=finalize.cases_failed,
        )
