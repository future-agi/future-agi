"""AgentFixWorkflow against fake activities: what it does with each thing the service saw.
The service's own decisions (deadline, attempt limit, stale reports) are tested in
test_agent_fix_sandbox.py."""

import asyncio
import uuid

import pytest
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from simulate.temporal.constants import QUEUE_RUNNER
from simulate.temporal.types.agent_fix import (
    AgentFixCloseInput,
    AgentFixInput,
    AgentFixLaunch,
    AgentFixOutput,
    AgentFixVariations,
    AgentFixWatch,
    AgentFixWatchInput,
    Observation,
    VariationsStep,
)
from simulate.temporal.workflows.agent_fix_workflow import AgentFixWorkflow


class FakeActivities:
    """Each launch returns the next of ``launches``; each watch the next of ``watches``;
    each look at the first step the next of ``variations``, then DONE. The step may take
    the builder's 60 minutes while it writes and ``check_minutes`` once it checks."""

    def __init__(self, launches, watches=(), variations=(), check_minutes=90):
        self.launches = list(launches)
        self.watches = list(watches)
        self.variations = list(variations)
        self.check_minutes = check_minutes
        self.variation_looks = 0
        self.launch_calls = 0
        self.watched: list[str] = []
        self.released = 0
        self.closed: list[AgentFixCloseInput] = []

    @activity.defn(name="advance_agent_fix_variations")
    async def advance(self, _input: AgentFixInput) -> AgentFixVariations:
        self.variation_looks += 1
        step = self.variations.pop(0) if self.variations else VariationsStep.DONE
        await asyncio.sleep(0.01)
        minutes = {
            VariationsStep.WRITING: 60,
            VariationsStep.CHECKING: self.check_minutes,
        }.get(step, 0)
        return AgentFixVariations(step=step, minutes=minutes)

    @activity.defn(name="launch_agent_fix_sandbox")
    async def launch(self, _input: AgentFixInput) -> AgentFixLaunch:
        self.launch_calls += 1
        outcome = self.launches.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @activity.defn(name="watch_agent_fix_engine")
    async def watch(self, input: AgentFixWatchInput) -> AgentFixWatch:
        self.watched.append(input.command_id)
        outcome = self.watches.pop(0) if self.watches else Observation.RUNNING
        if isinstance(outcome, Exception):
            raise outcome
        # Lets a test's signal land.
        await asyncio.sleep(0.01)
        return AgentFixWatch(observation=outcome)

    @activity.defn(name="release_agent_fix_sandbox")
    async def release(self, _input: AgentFixInput) -> None:
        self.released += 1

    @activity.defn(name="close_agent_fix_run")
    async def close(self, input: AgentFixCloseInput) -> None:
        self.closed.append(input)

    def all(self):
        return [self.advance, self.launch, self.watch, self.release, self.close]


def _started(command_id="engine-1"):
    return AgentFixLaunch(observation=Observation.RUNNING, command_id=command_id)


async def _run(
    activities,
    temporal_test_server_options,
    *,
    signal_after_watch=False,
    signal_after_launch=False,
):
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[AgentFixWorkflow],
            activities=activities.all(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                AgentFixWorkflow.run,
                AgentFixInput(fix_run_id="fix-1"),
                id=f"agent-fix-test-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )
            if signal_after_watch:
                while not activities.watched:
                    await asyncio.sleep(0.01)
                await handle.signal(AgentFixWorkflow.cancel)
            if signal_after_launch:
                while not activities.launch_calls:
                    await asyncio.sleep(0.01)
                await handle.signal(AgentFixWorkflow.cancel)
            return await handle.result()


@pytest.mark.asyncio
async def test_engine_finishing_the_run_ends_the_workflow_and_deletes_the_sandbox(
    temporal_test_server_options,
):
    activities = FakeActivities(
        launches=[_started()],
        watches=[Observation.RUNNING, Observation.ENDED],
    )

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.ENDED
    assert activities.launch_calls == 1
    assert activities.watched == ["engine-1", "engine-1"]
    assert activities.released == 1
    # The engine's report closed the run; the workflow adds nothing.
    assert activities.closed == []


@pytest.mark.asyncio
async def test_cancel_signal_deletes_the_sandbox_and_closes_the_run_as_stopped(
    temporal_test_server_options,
):
    activities = FakeActivities(launches=[_started()])

    result = await _run(
        activities, temporal_test_server_options, signal_after_watch=True
    )

    assert result.observation == Observation.STOPPING
    assert activities.launch_calls == 1
    assert activities.released == 1
    assert activities.closed == [
        AgentFixCloseInput(fix_run_id="fix-1", state="stopped")
    ]


@pytest.mark.asyncio
async def test_a_stopping_run_seen_by_the_watch_is_closed_as_stopped(
    temporal_test_server_options,
):
    # request_stop writes "stopping" before it signals; the watch alone is enough.
    activities = FakeActivities(launches=[_started()], watches=[Observation.STOPPING])

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.STOPPING
    assert activities.released == 1
    assert [close.state for close in activities.closed] == ["stopped"]


@pytest.mark.asyncio
async def test_engine_death_relaunches_once_then_the_launch_ends_the_run(
    temporal_test_server_options,
):
    # The second launch is the last sandbox; the third finds them used up and closes the
    # run as fix_engine_died (the service's decision), so it reports ENDED.
    activities = FakeActivities(
        launches=[
            _started("engine-1"),
            _started("engine-2"),
            AgentFixLaunch(observation=Observation.ENDED),
        ],
        watches=[Observation.ENGINE_GONE, Observation.ENGINE_GONE],
    )

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.ENDED
    assert activities.launch_calls == 3
    assert activities.watched == ["engine-1", "engine-2"]
    assert activities.released == 1
    assert activities.closed == []


@pytest.mark.asyncio
async def test_a_sandbox_that_cannot_start_fails_the_run_instead_of_leaving_it_running(
    temporal_test_server_options,
):
    activities = FakeActivities(
        launches=[
            ApplicationError(
                "E2B template not found", type="HostedHarnessError", non_retryable=True
            )
        ]
    )

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.ENDED
    assert activities.launch_calls == 1
    assert activities.closed == [
        AgentFixCloseInput(
            fix_run_id="fix-1",
            state="failed",
            code="fix_sandbox_failed",
            message="E2B template not found",
        )
    ]
    assert activities.released == 1


@pytest.mark.asyncio
async def test_a_failing_watch_is_retried_instead_of_ending_a_live_engine(
    temporal_test_server_options,
):
    # Three failures exhaust the first watch activity's retries; the workflow watches again.
    unavailable = ApplicationError("sandbox API timed out")
    activities = FakeActivities(
        launches=[_started()],
        watches=[unavailable, unavailable, unavailable, Observation.ENDED],
    )

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.ENDED
    assert activities.launch_calls == 1
    assert len(activities.watched) == 4
    assert activities.closed == []
    assert activities.released == 1


@pytest.mark.asyncio
async def test_the_workflow_looks_again_after_each_wait(temporal_test_server_options):
    activities = FakeActivities(
        launches=[_started()],
        watches=[Observation.RUNNING, Observation.RUNNING, Observation.ENDED],
    )

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.ENDED
    assert len(activities.watched) == 3


@pytest.mark.asyncio
async def test_a_fix_held_back_by_the_caps_asks_again_then_starts(
    temporal_test_server_options,
):
    activities = FakeActivities(
        launches=[
            AgentFixLaunch(observation=Observation.WAITING),
            AgentFixLaunch(observation=Observation.WAITING),
            _started(),
        ],
        watches=[Observation.ENDED],
    )

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.ENDED
    assert activities.launch_calls == 3
    assert activities.watched == ["engine-1"]
    assert activities.closed == []


@pytest.mark.asyncio
async def test_a_stop_ends_the_wait_for_room_at_once(temporal_test_server_options):
    activities = FakeActivities(
        launches=[AgentFixLaunch(observation=Observation.WAITING)],
    )

    result = await _run(
        activities, temporal_test_server_options, signal_after_launch=True
    )

    assert result.observation == Observation.STOPPING
    assert activities.launch_calls == 1
    assert activities.watched == []
    assert [closed.state for closed in activities.closed] == ["stopped"]


@pytest.mark.asyncio
async def test_a_run_stopped_before_its_first_sandbox_is_closed_as_stopped(
    temporal_test_server_options,
):
    activities = FakeActivities(
        launches=[AgentFixLaunch(observation=Observation.STOPPING)]
    )

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.STOPPING
    assert activities.watched == []
    assert [close.state for close in activities.closed] == ["stopped"]
    assert activities.released == 1


@pytest.mark.asyncio
async def test_the_first_step_runs_before_the_engine_starts(
    temporal_test_server_options,
):
    activities = FakeActivities(
        launches=[_started()],
        watches=[Observation.ENDED],
        variations=[
            VariationsStep.WAITING,
            VariationsStep.WRITING,
            VariationsStep.CHECKING,
            VariationsStep.DONE,
        ],
    )

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.ENDED
    assert activities.variation_looks == 4
    assert activities.launch_calls == 1
    assert activities.closed == []


@pytest.mark.asyncio
async def test_a_first_step_that_ends_the_fix_starts_no_engine(
    temporal_test_server_options,
):
    activities = FakeActivities(launches=[], variations=[VariationsStep.ENDED])

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.ENDED
    assert activities.launch_calls == 0
    assert activities.released == 1
    assert activities.closed == []


@pytest.mark.asyncio
async def test_a_builder_past_its_cap_ends_the_fix_on_the_step_s_own_clock(
    temporal_test_server_options,
):
    # Each look waits a minute; the builder has 60 of them.
    activities = FakeActivities(launches=[], variations=[VariationsStep.WRITING] * 70)

    result = await _run(activities, temporal_test_server_options)

    assert result.observation == Observation.ENDED
    assert activities.launch_calls == 0
    assert [(one.state, one.code) for one in activities.closed] == [
        ("failed", "fix_variations_timeout")
    ]
    assert 60 <= activities.variation_looks <= 62


@pytest.mark.asyncio
@pytest.mark.parametrize(("check_minutes", "timed_out"), [(20, True), (200, False)])
async def test_the_check_ends_on_the_time_its_run_allows_not_a_fixed_cap(
    temporal_test_server_options, check_minutes, timed_out
):
    # 120 looks a minute apart: past a 20-minute run's time, within a 200-minute one's.
    activities = FakeActivities(
        launches=[_started()],
        watches=[Observation.ENDED],
        variations=[VariationsStep.CHECKING] * 120,
        check_minutes=check_minutes,
    )

    result = await _run(activities, temporal_test_server_options)

    closed = [(one.state, one.code) for one in activities.closed]
    if timed_out:
        assert closed == [("failed", "fix_variations_timeout")]
        assert activities.launch_calls == 0
        assert 20 <= activities.variation_looks <= 22
    else:
        assert closed == []
        assert activities.launch_calls == 1
        assert result.observation == Observation.ENDED


@workflow.defn(name="AgentFixWorkflow")
class _BeforeVariations(AgentFixWorkflow):
    """The workflow as fixes started before their first step existed ran it."""

    @workflow.run
    async def run(self, input: AgentFixInput) -> AgentFixOutput:
        try:
            observation = await self._drive(input)
        finally:
            await self._release(input)
        return AgentFixOutput(fix_run_id=input.fix_run_id, observation=observation)


@workflow.defn(name="AgentFixWorkflow")
class _Unguarded(AgentFixWorkflow):
    """The first step without its patch guard."""

    @workflow.run
    async def run(self, input: AgentFixInput) -> AgentFixOutput:
        observation = await self._variations(input) or await self._drive(input)
        await self._release(input)
        return AgentFixOutput(fix_run_id=input.fix_run_id, observation=observation)


@pytest.mark.asyncio
async def test_a_fix_started_before_its_first_step_existed_replays_unchanged(
    temporal_test_server_options,
):
    activities = FakeActivities(launches=[_started()], watches=[Observation.ENDED])
    async with await WorkflowEnvironment.start_time_skipping(
        **temporal_test_server_options
    ) as env:
        async with Worker(
            env.client,
            task_queue=QUEUE_RUNNER,
            workflows=[_BeforeVariations],
            activities=activities.all(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _BeforeVariations.run,
                AgentFixInput(fix_run_id="fix-1"),
                id=f"agent-fix-test-{uuid.uuid4()}",
                task_queue=QUEUE_RUNNER,
            )
            await handle.result()
        history = await handle.fetch_history()

    def replay(workflow_class):
        return Replayer(
            workflows=[workflow_class], workflow_runner=UnsandboxedWorkflowRunner()
        ).replay_workflow(history, raise_on_replay_failure=False)

    assert (await replay(AgentFixWorkflow)).replay_failure is None
    # Without the guard, the same history would not replay.
    assert (await replay(_Unguarded)).replay_failure is not None
