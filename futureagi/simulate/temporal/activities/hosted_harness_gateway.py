from __future__ import annotations

from temporalio import activity

from simulate.temporal.activities.hosted_runner import _run_db
from simulate.temporal.types.hosted_harness_gateway import (
    HostedHarnessAttemptInput,
    HostedHarnessGatewayInput,
    HostedHarnessLaunchOutput,
    HostedHarnessPollOutput,
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
    from simulate.models import HostedHarnessAttempt
    from simulate.services.hosted_harness_gateway import DaytonaHostedGateway

    def _poll() -> tuple[bool, str]:
        attempt = HostedHarnessAttempt.no_workspace_objects.select_related(
            "job", "job__organization"
        ).get(id=input.attempt_id)
        job = DaytonaHostedGateway().reconcile_completed(attempt)
        if job is None:
            return False, attempt.job.state
        return True, job.state

    done, state = await _run_db(_poll)
    return HostedHarnessPollOutput(done=done, state=state)


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
