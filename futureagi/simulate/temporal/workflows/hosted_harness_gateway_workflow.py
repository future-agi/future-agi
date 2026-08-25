from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

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
        launched = await workflow.execute_activity(
            "launch_hosted_harness_job",
            input,
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=15),
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
            outcome = await workflow.execute_activity(
                "poll_hosted_harness_attempt",
                attempt_input,
                task_queue=QUEUE_RUNNER,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=15),
                ),
                result_type=HostedHarnessPollOutput,
            )
            if outcome.done:
                return HostedHarnessGatewayOutput(
                    job_id=input.job_id, state=outcome.state
                )
            await workflow.sleep(timedelta(seconds=15))
