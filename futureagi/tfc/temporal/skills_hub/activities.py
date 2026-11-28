"""Thin native activities: authorize through services, heartbeat without payloads."""

import asyncio
from contextlib import suppress
from uuid import UUID

from asgiref.sync import sync_to_async
from temporalio import activity

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.jobs import finish_job, heartbeat_job, start_job
from model_hub.services.skills_hub.v11.types import SkillScope


def _scope(request: dict) -> SkillScope:
    return SkillScope(UUID(request["organization_id"]), UUID(request["workspace_id"]))


async def _heartbeat(request: dict) -> None:
    while True:
        activity.heartbeat()
        await sync_to_async(heartbeat_job, thread_sensitive=False)(
            scope=_scope(request), job_id=UUID(request["job_id"])
        )
        await asyncio.sleep(4)


@activity.defn
async def skills_start_job(request: dict) -> dict:
    try:
        return await sync_to_async(start_job)(
            scope=_scope(request), job_id=UUID(request["job_id"])
        )
    except SkillsHubError as error:
        terminal = await sync_to_async(finish_job)(
            scope=_scope(request),
            job_id=UUID(request["job_id"]),
            failure_code=error.code,
        )
        return {**terminal, "kind": "test", "attempts": [], "remaining_seconds": 0}


@activity.defn
async def skills_execute_attempt(request: dict) -> dict:
    from model_hub.services.skills_hub.v11.runner import execute_test_attempt

    pulse = asyncio.create_task(_heartbeat(request))
    try:
        return await execute_test_attempt(
            scope=_scope(request), attempt_id=UUID(request["attempt_id"])
        )
    except SkillsHubError as error:
        # Claim/persistence failures must settle durable state, not only workflow metadata.
        await sync_to_async(finish_job)(
            scope=_scope(request),
            job_id=UUID(request["job_id"]),
            failure_code=error.code,
        )
        return {"id": request["attempt_id"], "status": "error", "code": error.code}
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse


@activity.defn
async def skills_execute_once(request: dict) -> dict:
    pulse = asyncio.create_task(_heartbeat(request))
    try:
        if request["kind"] == "assist":
            from model_hub.services.skills_hub.v11.assist import (
                generate_assist_proposal,
            )

            await generate_assist_proposal(
                scope=_scope(request), proposal_id=UUID(request["job_id"])
            )
        elif request["kind"] == "github":
            from model_hub.services.skills_hub.v11.github import execute_github_preview

            await sync_to_async(execute_github_preview, thread_sensitive=False)(
                scope=_scope(request), preview_id=UUID(request["job_id"])
            )
        return {"id": request["job_id"]}
    except SkillsHubError as error:
        return await sync_to_async(finish_job)(
            scope=_scope(request),
            job_id=UUID(request["job_id"]),
            failure_code=error.code,
        )
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse


@activity.defn
async def skills_finish_job(request: dict) -> dict:
    return await sync_to_async(finish_job)(
        scope=_scope(request),
        job_id=UUID(request["job_id"]),
        failure_code=request.get("failure_code"),
    )
