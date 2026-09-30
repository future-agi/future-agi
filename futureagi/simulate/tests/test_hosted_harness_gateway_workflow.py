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
    HostedHarnessLaunchFailureInput,
    HostedHarnessLaunchOutput,
    HostedHarnessLaunchRecoveryOutput,
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


class FailedLaunchActivities:
    def __init__(self) -> None:
        self.launch_calls = 0
        self.recorded_job_id = None

    @activity.defn(name="launch_hosted_harness_job")
    async def launch(self, _input: HostedHarnessGatewayInput) -> HostedHarnessLaunchOutput:
        self.launch_calls += 1
        raise ApplicationError("platform simulator credential unavailable")

    @activity.defn(name="record_hosted_harness_launch_failure")
    async def record(self, input: HostedHarnessLaunchFailureInput) -> HostedHarnessLaunchRecoveryOutput:
        self.recorded_job_id = input.job_id
        return HostedHarnessLaunchRecoveryOutput(state="failed")


@pytest.mark.asyncio
async def test_exhausted_launch_marks_job_failed_instead_of_leaving_it_queued():
    activities = FailedLaunchActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[HostedHarnessGatewayWorkflow],
            activities=[activities.launch, activities.record],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                HostedHarnessGatewayWorkflow.run,
                HostedHarnessGatewayInput(
                    job_id="job-prelaunch-failure",
                    endpoint_base_url="https://example.test",
                    max_infrastructure_attempts=2,
                    initial_backoff_seconds=1,
                    max_backoff_seconds=2,
                ),
                id=f"hosted-launch-failure-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )

    assert result.state == "failed"
    assert activities.launch_calls == 2
    assert activities.recorded_job_id == "job-prelaunch-failure"


class LostLaunchResponseActivities(FailedLaunchActivities):
    @activity.defn(name="record_hosted_harness_launch_failure")
    async def record(self, input: HostedHarnessLaunchFailureInput) -> HostedHarnessLaunchRecoveryOutput:
        self.recorded_job_id = input.job_id
        return HostedHarnessLaunchRecoveryOutput(state="running", attempt_id="existing-attempt")

    @activity.defn(name="poll_hosted_harness_attempt")
    async def poll(self, input: HostedHarnessAttemptInput) -> HostedHarnessPollOutput:
        assert input.attempt_id == "existing-attempt"
        return HostedHarnessPollOutput(done=True, state="completed")


@pytest.mark.asyncio
async def test_lost_launch_response_resumes_existing_attempt_instead_of_abandoning_it():
    activities = LostLaunchResponseActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[HostedHarnessGatewayWorkflow],
            activities=[activities.launch, activities.record, activities.poll],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                HostedHarnessGatewayWorkflow.run,
                HostedHarnessGatewayInput(
                    job_id="job-lost-launch-response",
                    endpoint_base_url="https://example.test",
                    max_infrastructure_attempts=2,
                    initial_backoff_seconds=1,
                    max_backoff_seconds=2,
                ),
                id=f"hosted-lost-launch-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )

    assert result.state == "completed"
    assert activities.launch_calls == 2
    assert activities.recorded_job_id == "job-lost-launch-response"
