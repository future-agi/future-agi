from __future__ import annotations

from django.utils import timezone
from temporalio import activity
from temporalio.exceptions import ApplicationError

from simulate.temporal.activities.hosted_runner import _run_db
from simulate.temporal.types.hosted_harness_gateway import (
    HostedHarnessAttemptInput,
    HostedHarnessGatewayInput,
    HostedHarnessLaunchFailureInput,
    HostedHarnessLaunchOutput,
    HostedHarnessLaunchRecoveryOutput,
    HostedHarnessPollOutput,
)

_EXHAUSTED_LAUNCH_FAILURE = {
    "domain": "infrastructure",
    "stage": "acquiring_source",
    "code": "hosted_launch_failed",
    "message": "The hosted run could not launch after its bounded retries; no call was attempted.",
}


def _fail_before_attempt(job_id, failure: dict) -> HostedHarnessLaunchRecoveryOutput:
    """Fail a job whose launch never got an attempt going, with ``failure``.

    A job already ended keeps its ending, and one whose launched attempt still owns a
    sandbox is handed back to be polled instead.
    """
    from django.db import transaction

    from simulate.models import HostedHarnessJob
    from simulate.services.hosted_harness import project_terminal_job_onto_run

    job = HostedHarnessJob.no_workspace_objects.get(id=job_id)
    if job.state in {
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.FAILED,
        HostedHarnessJob.State.CANCELED,
    }:
        return HostedHarnessLaunchRecoveryOutput(state=job.state)
    # If a launch created an attempt, its provider cleanup belongs to the
    # gateway's attempt protocol.  Do not turn an active call into a false
    # terminal job just because the Temporal activity response was lost.
    # An attempt whose sandbox is already verified gone (an earlier attempt of a
    # re-run job) is not that call.
    attempt = job.attempts.order_by("-attempt_number").first()
    if (
        attempt is not None
        and attempt.provider_ref
        and attempt.cleanup_verified_at is None
    ):
        return HostedHarnessLaunchRecoveryOutput(
            state=job.state, attempt_id=str(attempt.id)
        )
    job.state = HostedHarnessJob.State.FAILED
    job.current_stage = "failed"
    job.failure = dict(failure)
    job.terminal_at = timezone.now()
    with transaction.atomic():
        job.save(
            update_fields=[
                "state",
                "current_stage",
                "failure",
                "terminal_at",
                "updated_at",
            ]
        )
        project_terminal_job_onto_run(job, job.terminal_at)
    return HostedHarnessLaunchRecoveryOutput(state=job.state)


@activity.defn(name="launch_hosted_harness_job")
async def launch_hosted_harness_job(
    input: HostedHarnessGatewayInput,
) -> HostedHarnessLaunchOutput:
    from simulate.models import HostedHarnessJob
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_gateway import (
        PRIVATE_GITHUB_UNAVAILABLE_CODE,
        HostedHarnessGateway,
        private_github_environment_failure,
    )

    def _launch() -> str:
        job = HostedHarnessJob.no_workspace_objects.select_related("organization").get(
            id=input.job_id
        )
        try:
            attempt = HostedHarnessGateway().launch(
                job, endpoint_base_url=input.endpoint_base_url
            )
        except HostedHarnessError as exc:
            if exc.code != PRIVATE_GITHUB_UNAVAILABLE_CODE:
                raise
            # The private repository can no longer be read here, so another try reads
            # it no better: the job fails now, with the reason the user can act on.
            _fail_before_attempt(job.id, private_github_environment_failure())
            raise ApplicationError(
                exc.message, type=exc.code, non_retryable=True
            ) from exc
        return str(attempt.id)

    attempt_id = await _run_db(_launch)
    return HostedHarnessLaunchOutput(attempt_id=attempt_id)


@activity.defn(name="record_hosted_harness_launch_failure")
async def record_hosted_harness_launch_failure(
    input: HostedHarnessLaunchFailureInput,
) -> HostedHarnessLaunchRecoveryOutput:
    """Make exhausted pre-attempt launch failures visible instead of leaving jobs queued."""
    return await _run_db(_fail_before_attempt, input.job_id, _EXHAUSTED_LAUNCH_FAILURE)


@activity.defn(name="poll_hosted_harness_attempt")
async def poll_hosted_harness_attempt(
    input: HostedHarnessAttemptInput,
) -> HostedHarnessPollOutput:
    from simulate.models import HostedHarnessAttempt, HostedHarnessJob
    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    def _poll() -> tuple[bool, str, bool]:
        attempt = HostedHarnessAttempt.no_workspace_objects.select_related(
            "job", "job__organization"
        ).get(id=input.attempt_id)
        job = HostedHarnessGateway().reconcile_completed(attempt)
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
    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    def _cancel() -> str:
        attempt = HostedHarnessAttempt.no_workspace_objects.select_related(
            "job", "job__organization"
        ).get(id=input.attempt_id)
        reason = attempt.job.cancel_reason or "user_canceled"
        job = HostedHarnessGateway().cancel(attempt.job, reason=reason)
        return job.state

    try:
        state = await _run_db(_cancel)
    except Exception:
        activity.logger.exception(
            "hosted harness cancellation cleanup is still pending; attempt_id=%s",
            input.attempt_id,
        )
        return HostedHarnessPollOutput(
            done=False,
            state="cleaning_up",
        )
    return HostedHarnessPollOutput(done=True, state=state)
