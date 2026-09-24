import uuid

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from simulate.temporal.constants import QUEUE_RUNNER
from simulate.temporal.types.hosted_harness_gateway import (
    HostedHarnessAttemptInput,
    HostedHarnessGatewayInput,
    HostedHarnessLaunchOutput,
    HostedHarnessPollOutput,
)
from simulate.temporal.workflows.hosted_harness_gateway_workflow import (
    HostedHarnessGatewayWorkflow,
)


class TransientPollActivities:
    def __init__(self) -> None:
        self.poll_calls = 0

    @activity.defn(name="launch_hosted_harness_job")
    async def launch(
        self, _input: HostedHarnessGatewayInput
    ) -> HostedHarnessLaunchOutput:
        return HostedHarnessLaunchOutput(attempt_id="attempt-1")

    @activity.defn(name="poll_hosted_harness_attempt")
    async def poll(self, _input: HostedHarnessAttemptInput) -> HostedHarnessPollOutput:
        self.poll_calls += 1
        if self.poll_calls <= 3:
            raise ApplicationError("temporary Daytona timeout")
        return HostedHarnessPollOutput(done=True, state="completed")

    @activity.defn(name="cancel_hosted_harness_attempt")
    async def cancel(
        self, _input: HostedHarnessAttemptInput
    ) -> HostedHarnessPollOutput:
        return HostedHarnessPollOutput(done=True, state="canceled")


@pytest.mark.asyncio
async def test_poll_retry_exhaustion_does_not_kill_running_guest():
    activities = TransientPollActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[HostedHarnessGatewayWorkflow],
            activities=[activities.launch, activities.poll, activities.cancel],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                HostedHarnessGatewayWorkflow.run,
                HostedHarnessGatewayInput(
                    job_id="job-1",
                    endpoint_base_url="https://example.test",
                    max_infrastructure_attempts=2,
                    initial_backoff_seconds=1,
                    max_backoff_seconds=2,
                ),
                id=f"hosted-poll-recovery-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )

    assert result.state == "completed"
    # The first activity invocation exhausts its three configured retries.
    # The workflow then starts a fresh observation instead of failing.
    assert activities.poll_calls == 4
