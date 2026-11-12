from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from simulate.temporal.constants import QUEUE_RUNNER
from simulate.temporal.types.certification import (
    CertificationCaseInput,
    CertificationPollOutput,
    CertificationReconcileOutput,
    CertificationResultInput,
    CertificationSubmitOutput,
)

_CASE_POLL_SECONDS = 300

_ACTIVITY_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=15),
)


@workflow.defn
class CertificationCaseWorkflow:
    """Run one case in its own bounded Temporal history."""

    def __init__(self) -> None:
        self.cancel_requested = False

    @workflow.signal
    def cancel(self) -> None:
        self.cancel_requested = True

    @workflow.run
    async def run(self, input: CertificationCaseInput) -> str:
        await workflow.execute_activity(
            "certification_submit_case",
            input,
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_ACTIVITY_RETRY,
            result_type=CertificationSubmitOutput,
        )
        deadline = workflow.now() + timedelta(seconds=input.max_duration_seconds)
        cancel_signaled = False
        while True:
            deadline_exceeded = workflow.now() >= deadline
            if (self.cancel_requested or deadline_exceeded) and not cancel_signaled:
                await workflow.execute_activity(
                    "certification_cancel_case",
                    input,
                    task_queue=QUEUE_RUNNER,
                    start_to_close_timeout=timedelta(minutes=4),
                    retry_policy=_ACTIVITY_RETRY,
                )
                cancel_signaled = True
            if deadline_exceeded:
                await workflow.execute_activity(
                    "certification_record_case_result",
                    CertificationResultInput(
                        case_id=input.case_id,
                        outcome="failed",
                        failure_class="environment",
                        failure_detail={"reason": "case_deadline_exceeded"},
                    ),
                    task_queue=QUEUE_RUNNER,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_ACTIVITY_RETRY,
                )
                return "failed"
            poll = await workflow.execute_activity(
                "certification_poll_case",
                input,
                task_queue=QUEUE_RUNNER,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_ACTIVITY_RETRY,
                result_type=CertificationPollOutput,
            )
            if poll.done:
                break
            await workflow.sleep(timedelta(seconds=_CASE_POLL_SECONDS))

        expected_terminal = "canceled" if cancel_signaled else "completed"
        reconcile = await workflow.execute_activity(
            "certification_reconcile_case",
            CertificationCaseInput(
                case_id=input.case_id,
                expected_terminal=expected_terminal,
                max_duration_seconds=input.max_duration_seconds,
            ),
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_ACTIVITY_RETRY,
            result_type=CertificationReconcileOutput,
        )
        await workflow.execute_activity(
            "certification_record_case_result",
            CertificationResultInput(
                case_id=input.case_id,
                outcome=(expected_terminal if reconcile.ok else "failed"),
                failure_class=reconcile.failure_class,
                failure_detail={"missing": reconcile.missing},
            ),
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_ACTIVITY_RETRY,
        )
        return expected_terminal if reconcile.ok else "failed"
