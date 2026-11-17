"""Activities of AgentFixWorkflow. Each runs one step of
simulate.services.agent_fix_sandbox, where the decisions live."""

from __future__ import annotations

from temporalio import activity

from simulate.temporal.activities.hosted_runner import _run_db
from simulate.temporal.types.agent_fix import (
    AgentFixCloseInput,
    AgentFixInput,
    AgentFixLaunch,
    AgentFixWatch,
    AgentFixWatchInput,
)
from tfc.temporal.common.heartbeat import Heartbeater


@activity.defn(name="launch_agent_fix_sandbox")
async def launch_agent_fix_sandbox(input: AgentFixInput) -> AgentFixLaunch:
    from simulate.services.agent_fix_sandbox import launch

    async with Heartbeater():
        return await _run_db(launch, input.fix_run_id)


@activity.defn(name="watch_agent_fix_engine")
async def watch_agent_fix_engine(input: AgentFixWatchInput) -> AgentFixWatch:
    """One look at the engine. The workflow waits between looks, so a fix holds no worker
    slot while its engine works."""
    from simulate.services.agent_fix_sandbox import observe

    observation = await _run_db(observe, input.fix_run_id, input.command_id)
    return AgentFixWatch(observation=observation)


@activity.defn(name="release_agent_fix_sandbox")
async def release_agent_fix_sandbox(input: AgentFixInput) -> None:
    from simulate.services.agent_fix_sandbox import release

    async with Heartbeater():
        await _run_db(release, input.fix_run_id)


@activity.defn(name="close_agent_fix_run")
async def close_agent_fix_run(input: AgentFixCloseInput) -> None:
    from simulate.models import AgentFixRun
    from simulate.services.agent_fix_runs import close_fix_run

    failure = {"code": input.code, "message": input.message} if input.code else None
    await _run_db(
        close_fix_run, input.fix_run_id, AgentFixRun.State(input.state), failure=failure
    )
