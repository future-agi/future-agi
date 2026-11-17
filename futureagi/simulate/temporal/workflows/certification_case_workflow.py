from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

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

    async def _wait_for_terminal(
        self, input: CertificationCaseInput
    ) -> CertificationPollOutput | None:
        deadline = workflow.now() + timedelta(seconds=input.max_duration_seconds)
        cancel_signaled = False
        while True:
            deadline_exceeded = workflow.now() >= deadline
            poll = await workflow.execute_activity(
                "certification_poll_case",
                input,
                task_queue=QUEUE_RUNNER,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_ACTIVITY_RETRY,
                result_type=CertificationPollOutput,
            )
            if poll.done:
                return poll
            if (self.cancel_requested or deadline_exceeded) and not cancel_signaled:
                try:
                    await workflow.execute_activity(
                        "certification_cancel_case",
                        input,
                        task_queue=QUEUE_RUNNER,
                        start_to_close_timeout=timedelta(minutes=4),
                        retry_policy=_ACTIVITY_RETRY,
                    )
                except ActivityError:
                    poll = await workflow.execute_activity(
                        "certification_poll_case",
                        input,
                        task_queue=QUEUE_RUNNER,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=_ACTIVITY_RETRY,
                        result_type=CertificationPollOutput,
                    )
                    if not poll.done:
                        raise
                    return poll
                cancel_signaled = True
            if deadline_exceeded:
                outcome = "canceled" if self.cancel_requested else "failed"
                await workflow.execute_activity(
                    "certification_record_case_result",
                    CertificationResultInput(
                        case_id=input.case_id,
                        outcome=outcome,
                        failure_class="environment",
                        failure_detail={"reason": "case_deadline_exceeded"},
                    ),
                    task_queue=QUEUE_RUNNER,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_ACTIVITY_RETRY,
                )
                return None
            if cancel_signaled:
                await workflow.sleep(timedelta(seconds=_CASE_POLL_SECONDS))
            else:
                try:
                    await workflow.wait_condition(
                        lambda: self.cancel_requested,
                        timeout=timedelta(seconds=_CASE_POLL_SECONDS),
                    )
                except TimeoutError:
                    pass

    async def _submit(self, activity_name: str, input: CertificationCaseInput) -> bool:
        try:
            await workflow.execute_activity(
                activity_name,
                input,
                task_queue=QUEUE_RUNNER,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_ACTIVITY_RETRY,
                result_type=CertificationSubmitOutput,
            )
        except ActivityError as exc:
            if not (
                isinstance(exc.cause, ApplicationError)
                and exc.cause.type == "CertificationConfigurationError"
            ):
                raise
            await workflow.execute_activity(
                "certification_record_case_result",
                CertificationResultInput(
                    case_id=input.case_id,
                    outcome="failed",
                    failure_class="configuration",
                    failure_detail={"reason": "submission_refused"},
                ),
                task_queue=QUEUE_RUNNER,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_ACTIVITY_RETRY,
            )
            return False
        return True

    @workflow.run
    async def run(self, input: CertificationCaseInput) -> str:
        if not await self._submit("certification_submit_case", input):
            return "failed"
        environment_poll = await self._wait_for_terminal(input)
        if environment_poll is None:
            return "canceled" if self.cancel_requested else "failed"

        poll = environment_poll
        if environment_poll.job_state == "completed" and not self.cancel_requested:
            if not await self._submit("certification_submit_run", input):
                return "failed"
            poll = await self._wait_for_terminal(input)
            if poll is None:
                return "canceled" if self.cancel_requested else "failed"

        expected_terminal = "canceled" if poll.job_state == "canceled" else "completed"
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
