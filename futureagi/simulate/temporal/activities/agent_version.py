"""Activities of AgentVersionWorkflow. Each runs one step of
simulate.services.agent_version_steps, where the decisions live."""

from __future__ import annotations

from temporalio import activity

from simulate.temporal.activities.hosted_runner import _run_db
from simulate.temporal.types.agent_version import (
    AgentVersionFailure,
    AgentVersionInput,
    AgentVersionStep,
)
from tfc.temporal.common.heartbeat import Heartbeater


def _step(outcome) -> AgentVersionStep:
    return AgentVersionStep(
        status=outcome.status,
        code=outcome.code,
        message=outcome.message,
        retryable=outcome.retryable,
    )


@activity.defn(name="supersede_agent_versions")
async def supersede_agent_versions(input: AgentVersionInput) -> list[str]:
    from simulate.services.agent_version_steps import supersede

    superseded = await _run_db(supersede, input.version_id)
    for version_id in superseded:
        from simulate.temporal.client import cancel_agent_version_workflow

        await _run_db(cancel_agent_version_workflow, version_id)
    return superseded


@activity.defn(name="snapshot_agent_version")
async def snapshot_agent_version(input: AgentVersionInput) -> AgentVersionStep:
    from simulate.services.agent_version_steps import snapshot

    async with Heartbeater():
        return _step(await _run_db(snapshot, input.version_id))


@activity.defn(name="read_agent_version")
async def read_agent_version(input: AgentVersionInput) -> AgentVersionStep:
    from simulate.services.agent_version_steps import read

    async with Heartbeater():
        return _step(await _run_db(read, input.version_id))


@activity.defn(name="check_agent_version")
async def check_agent_version(input: AgentVersionInput) -> str:
    from simulate.services.agent_version_steps import check

    async with Heartbeater():
        return await _run_db(check, input.version_id)


@activity.defn(name="fail_agent_version")
async def fail_agent_version(input: AgentVersionFailure) -> None:
    from simulate.services.agent_version_steps import fail

    await _run_db(
        fail,
        input.version_id,
        code=input.code,
        message=input.message,
        retryable=input.retryable,
    )
