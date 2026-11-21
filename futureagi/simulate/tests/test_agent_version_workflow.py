import uuid

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from simulate.temporal.constants import QUEUE_RUNNER
from simulate.temporal.types.agent_version import (
    AgentVersionFailure,
    AgentVersionInput,
    AgentVersionStep,
)
from simulate.temporal.workflows.agent_version_workflow import AgentVersionWorkflow


class FakeActivities:
    def __init__(self, *, broken=()):
        self.broken = set(broken)
        self.ran = []
        self.failures = []

    def _step(self, name):
        self.ran.append(name)
        if name in self.broken:
            raise ApplicationError(f"{name} gave up", non_retryable=True)

    @activity.defn(name="supersede_agent_versions")
    async def supersede(self, _input: AgentVersionInput) -> None:
        self._step("supersede_agent_versions")

    @activity.defn(name="snapshot_agent_version")
    async def snapshot(self, _input: AgentVersionInput) -> AgentVersionStep:
        self._step("snapshot_agent_version")
        return AgentVersionStep(status="done")

    @activity.defn(name="read_agent_version")
    async def read(self, _input: AgentVersionInput) -> AgentVersionStep:
        self._step("read_agent_version")
        return AgentVersionStep(status="done")

    @activity.defn(name="check_agent_version")
    async def check(self, _input: AgentVersionInput) -> str:
        self._step("check_agent_version")
        return "passed"

    @activity.defn(name="fail_agent_version")
    async def fail(self, input: AgentVersionFailure) -> None:
        self.failures.append(input)

    def all(self):
        return [self.supersede, self.snapshot, self.read, self.check, self.fail]


async def _run(activities, temporal_test_server_options):
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[AgentVersionWorkflow],
            activities=activities.all(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                AgentVersionWorkflow.run,
                AgentVersionInput(version_id="version-1"),
                id=f"agent-version-test-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )


@pytest.mark.asyncio
async def test_a_version_that_passes_every_step_is_not_failed(
    temporal_test_server_options,
):
    activities = FakeActivities()

    result = await _run(activities, temporal_test_server_options)

    assert result.status == "passed"
    assert activities.failures == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broken", "doing"),
    [
        ("supersede_agent_versions", "Starting this version"),
        ("snapshot_agent_version", "Reading the agent"),
        ("read_agent_version", "Reading the agent"),
        ("check_agent_version", "Checking this version"),
    ],
)
async def test_a_step_out_of_retries_fails_the_version_instead_of_leaving_it_pending(
    temporal_test_server_options, broken, doing
):
    activities = FakeActivities(broken=[broken])

    result = await _run(activities, temporal_test_server_options)

    assert result.status == "failed"
    assert activities.ran[-1] == broken
    assert activities.failures == [
        AgentVersionFailure(
            version_id="version-1",
            code="agent_reading_unavailable",
            message=f"{doing} couldn't finish. Try again.",
            retryable=True,
        )
    ]
