from __future__ import annotations

import asyncio
from django.utils import timezone
from temporalio import activity

from simulate.temporal.activities.hosted_runner import _run_db
from simulate.temporal.types.hosted_harness_gateway import (
    HostedHarnessAttemptInput,
    HostedHarnessAuthoringOutput,
    HostedHarnessGatewayInput,
    HostedHarnessLaunchOutput,
    HostedHarnessPollOutput,
)


@activity.defn(name="author_hosted_harness_job")
async def author_hosted_harness_job(
    input: HostedHarnessGatewayInput,
) -> HostedHarnessAuthoringOutput:
    """Author the frozen bundle inside a Daytona sandbox before the execution launch.

    Contract/environment/scenario generation is model-heavy, so it runs in a throwaway sandbox
    (authoring credentials only) rather than on the control-plane worker. A job that already
    carries ``authoring_object_key`` is treated as authored and skipped.
    """
    from simulate.models import HostedHarnessJob
    from simulate.services.hosted_harness_gateway import (
        DaytonaHostedGateway,
        store_authoring_archive,
    )

    def _prepare() -> bool:
        job = HostedHarnessJob.no_workspace_objects.select_related("organization").get(
            id=input.job_id
        )
        metadata = (job.payload or {}).get("metadata") or {}
        if metadata.get("authoring_object_key"):
            return True
        job.current_stage = "understanding_agent"
        job.state = HostedHarnessJob.State.ADMITTED
        job.save(update_fields=["current_stage", "state", "updated_at"])
        return False

    def _author_and_store() -> None:
        job = HostedHarnessJob.no_workspace_objects.select_related("organization").get(
            id=input.job_id
        )
        body = DaytonaHostedGateway().author(job)
        store_authoring_archive(job, body)

    def _failed(detail: str) -> None:
        job = HostedHarnessJob.no_workspace_objects.get(id=input.job_id)
        job.state = HostedHarnessJob.State.FAILED
        job.current_stage = "failed"
        job.failure = {
            "domain": "simulator",
            "stage": "authoring",
            "code": "authoring_failed",
            "message": detail[:1000],
        }
        job.terminal_at = timezone.now()
        job.save(
            update_fields=[
                "state",
                "current_stage",
                "failure",
                "terminal_at",
                "updated_at",
            ]
        )

    def _touch_authoring_heartbeat() -> None:
        HostedHarnessJob.no_workspace_objects.filter(id=input.job_id).update(
            updated_at=timezone.now()
        )

    try:
        cached = await _run_db(_prepare)
        if cached:
            return HostedHarnessAuthoringOutput(ready=True, state="admitted")
        # The sandbox authoring run blocks for minutes with no streamed output; keep the
        # Temporal activity alive with periodic heartbeats until it settles.
        task = asyncio.ensure_future(_run_db(_author_and_store))
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=20)
            except TimeoutError:
                activity.heartbeat("ALK authoring in sandbox is active")
                await _run_db(_touch_authoring_heartbeat)
        await task
        return HostedHarnessAuthoringOutput(ready=True, state="admitted")
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        await _run_db(_failed, detail)
        return HostedHarnessAuthoringOutput(
            ready=False, state="failed", detail=detail[:1000]
        )


@activity.defn(name="launch_hosted_harness_job")
async def launch_hosted_harness_job(
    input: HostedHarnessGatewayInput,
) -> HostedHarnessLaunchOutput:
    from simulate.models import HostedHarnessJob
    from simulate.services.hosted_harness_gateway import DaytonaHostedGateway

    def _launch() -> str:
        job = HostedHarnessJob.no_workspace_objects.select_related("organization").get(
            id=input.job_id
        )
        attempt = DaytonaHostedGateway().launch(
            job, endpoint_base_url=input.endpoint_base_url
        )
        return str(attempt.id)

    attempt_id = await _run_db(_launch)
    return HostedHarnessLaunchOutput(attempt_id=attempt_id)


@activity.defn(name="poll_hosted_harness_attempt")
async def poll_hosted_harness_attempt(
    input: HostedHarnessAttemptInput,
) -> HostedHarnessPollOutput:
    from simulate.models import HostedHarnessAttempt, HostedHarnessJob
    from simulate.services.hosted_harness_gateway import DaytonaHostedGateway

    def _poll() -> tuple[bool, str, bool]:
        attempt = HostedHarnessAttempt.no_workspace_objects.select_related(
            "job", "job__organization"
        ).get(id=input.attempt_id)
        job = DaytonaHostedGateway().reconcile_completed(attempt)
        if job is None:
            return False, attempt.job.state, False
        retryable = job.state == HostedHarnessJob.State.RETRY_WAIT
        return (not retryable), job.state, retryable

    done, state, retryable = await _run_db(_poll)
    return HostedHarnessPollOutput(done=done, state=state, retryable=retryable)


@activity.defn(name="cancel_hosted_harness_attempt")
async def cancel_hosted_harness_attempt(
    input: HostedHarnessAttemptInput,
) -> HostedHarnessPollOutput:
    from simulate.models import HostedHarnessAttempt
    from simulate.services.hosted_harness_gateway import DaytonaHostedGateway

    def _cancel() -> str:
        attempt = HostedHarnessAttempt.no_workspace_objects.select_related(
            "job", "job__organization"
        ).get(id=input.attempt_id)
        reason = attempt.job.cancel_reason or "user_canceled"
        job = DaytonaHostedGateway().cancel(attempt.job, reason=reason)
        return job.state

    state = await _run_db(_cancel)
    return HostedHarnessPollOutput(done=True, state=state)
