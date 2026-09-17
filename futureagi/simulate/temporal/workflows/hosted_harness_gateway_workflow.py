from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

from simulate.temporal.constants import QUEUE_RUNNER
from simulate.temporal.types.hosted_harness_gateway import (
    HostedHarnessAttemptInput,
    HostedHarnessGatewayInput,
    HostedHarnessGatewayOutput,
    HostedHarnessLaunchOutput,
    HostedHarnessPollOutput,
)


@workflow.defn
class HostedHarnessGatewayWorkflow:
    def __init__(self) -> None:
        self.cancel_requested = False

    @workflow.signal
    def cancel(self) -> None:
        self.cancel_requested = True

    @workflow.run
    async def run(self, input: HostedHarnessGatewayInput) -> HostedHarnessGatewayOutput:
        backoff = input.initial_backoff_seconds
        while True:
            launched = await workflow.execute_activity(
                "launch_hosted_harness_job",
                input,
                task_queue=QUEUE_RUNNER,
                # A cold image build can exceed twenty minutes; a short bound cancels and restarts it.
                start_to_close_timeout=timedelta(minutes=45),
                retry_policy=RetryPolicy(
                    maximum_attempts=input.max_infrastructure_attempts,
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=15),
                ),
                result_type=HostedHarnessLaunchOutput,
            )
            attempt_input = HostedHarnessAttemptInput(attempt_id=launched.attempt_id)
            while True:
                if self.cancel_requested:
                    outcome = await workflow.execute_activity(
                        "cancel_hosted_harness_attempt",
                        attempt_input,
                        task_queue=QUEUE_RUNNER,
                        start_to_close_timeout=timedelta(minutes=4),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                        result_type=HostedHarnessPollOutput,
                    )
                    return HostedHarnessGatewayOutput(
                        job_id=input.job_id, state=outcome.state
                    )
                try:
                    outcome = await workflow.execute_activity(
                        "poll_hosted_harness_attempt",
                        attempt_input,
                        task_queue=QUEUE_RUNNER,
                        # Reconciliation can include a Daytona delete whose own
                        # timeout is two minutes. Keep the activity envelope
                        # comfortably outside that provider deadline.
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=RetryPolicy(
                            maximum_attempts=3,
                            initial_interval=timedelta(seconds=1),
                            maximum_interval=timedelta(seconds=15),
                        ),
                        result_type=HostedHarnessPollOutput,
                    )
                except ActivityError as exc:
                    # Polling only observes/reconciles durable provider state.
                    # A temporarily slow Daytona API must not kill the workflow
                    # while the guest is still running. Temporal already applied
                    # the bounded activity retry policy; wait, then observe again.
                    # Cancellation remains terminal and is handled by Temporal.
                    if isinstance(exc.cause, CancelledError):
                        raise
                    workflow.logger.warning(
                        "Hosted harness poll temporarily unavailable for attempt %s: %s",
                        launched.attempt_id,
                        exc,
                    )
                    await workflow.sleep(timedelta(seconds=15))
                    continue
                if outcome.done:
                    return HostedHarnessGatewayOutput(
                        job_id=input.job_id, state=outcome.state
                    )
                if outcome.retryable:
                    # Guest hit a retryable infrastructure failure and the
                    # gateway parked the job in RETRY_WAIT. Back off, then
                    # relaunch a fresh attempt (new sandbox + capability token).
                    break
                await workflow.sleep(timedelta(seconds=15))
            await workflow.sleep(timedelta(seconds=backoff))
            backoff = min(backoff * 2, input.max_backoff_seconds)
