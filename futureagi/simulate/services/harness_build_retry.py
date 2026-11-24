"""Start a stopped first build again, from its approved design and whatever it froze."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import HostedHarnessJob
from simulate.services.harness_design_review import (
    READING_FAILED,
    fail_design_reading,
    fail_stale_reading,
    reopen_reading,
    stored_review,
)
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from accounts.models.user import User

logger = structlog.get_logger(__name__)

ResumePoint = Literal["start", "checks"]


def retryable(job: HostedHarnessJob) -> bool:
    """A failed environment build, not one of its runs, unless reading its agent
    failed in a way trying again won't fix."""
    return (
        job.environment_id is None
        and job.state == HostedHarnessJob.State.FAILED
        and not _read_for_good(job)
    )


def _reading_failed(job: HostedHarnessJob) -> bool:
    review = stored_review(job)
    return review is not None and review.get("status") == READING_FAILED


def _read_for_good(job: HostedHarnessJob) -> bool:
    return _reading_failed(job) and (job.failure or {}).get("retryable") is False


def resume_point(job: HostedHarnessJob) -> ResumePoint:
    """Where a retry starts: the release checks once the world and scenarios were
    frozen, since the next launch reuses them; otherwise the start of the build."""
    metadata = (job.payload or {}).get("metadata") or {}
    return "checks" if metadata.get("authoring_object_key") else "start"


def _read_again(
    job: HostedHarnessJob, previous_failure: dict[str, Any], *, user: User
) -> tuple[HostedHarnessJob, ResumePoint]:
    from simulate.tasks.harness_agent_versions import schedule_design_reading

    try:
        schedule_design_reading(str(job.id))
    except Exception as exc:
        fail_design_reading(
            str(job.id),
            code=str(previous_failure.get("code") or "scheduler_unavailable"),
            message=str(
                previous_failure.get("message")
                or "Reading the agent couldn't start. Build again."
            ),
            retryable=True,
        )
        logger.warning(
            "hosted_build_reading_retry_not_started",
            environment_id=str(job.id),
            exc_info=True,
        )
        raise HostedHarnessError(
            "scheduler_unavailable",
            "The build could not start again; try again in a moment",
            status_code=503,
        ) from exc
    logger.info(
        "hosted_build_retried",
        environment_id=str(job.id),
        user_id=str(user.pk or ""),
        resume_from="start",
        reading=True,
    )
    return job, "start"


def retry_failed_build(
    job: HostedHarnessJob, *, user: User, base_url: str
) -> tuple[HostedHarnessJob, ResumePoint]:
    """Queue a failed build again and start its workflow. A build whose agent could
    not be read reads it again, and waits for its design to be approved. Refused
    with 409 for a run, a build that did not fail, or a reading that failed for good,
    and with 503 when the workflow could not start, in which case the build is
    failed again with the failure it had."""
    from simulate.services.harness_usage import require_harness_authoring
    from simulate.temporal.client import start_hosted_harness_gateway_workflow

    require_harness_authoring(str(job.organization_id))
    fail_stale_reading(job)
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        if locked.environment_id is not None:
            raise HostedHarnessError(
                "build_retry_not_a_build",
                "Only an environment's build can be tried again; start a new run instead",
                status_code=409,
            )
        if locked.state != HostedHarnessJob.State.FAILED:
            raise HostedHarnessError(
                "build_retry_not_failed",
                f"Only a stopped build can be tried again; this one is {locked.state}",
                status_code=409,
            )
        previous_failure, previous_end = locked.failure, locked.terminal_at
        if _read_for_good(locked):
            raise HostedHarnessError(
                "build_retry_not_retryable",
                "Reading the agent failed in a way trying again won't fix; "
                "change the agent and build again",
                status_code=409,
            )
        reread = _reading_failed(locked)
        point: ResumePoint = "start" if reread else resume_point(locked)
        if reread:
            reopen_reading(locked)
        else:
            locked.state = HostedHarnessJob.State.QUEUED
            locked.current_stage = "queued"
            locked.failure = None
            locked.terminal_at = None
            locked.save(
                update_fields=[
                    "state",
                    "current_stage",
                    "failure",
                    "terminal_at",
                    "updated_at",
                ]
            )
    if reread:
        return _read_again(locked, previous_failure or {}, user=user)
    retry = locked.payload["retry"]
    try:
        start_hosted_harness_gateway_workflow(
            str(locked.id),
            base_url,
            retry["max_infrastructure_attempts"],
            retry["initial_backoff_seconds"],
            retry["max_backoff_seconds"],
        )
    except Exception as exc:
        with transaction.atomic():
            reverted = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=locked.id
            )
            if reverted.state == HostedHarnessJob.State.QUEUED:
                reverted.state = HostedHarnessJob.State.FAILED
                reverted.current_stage = "failed"
                reverted.failure = previous_failure
                reverted.terminal_at = previous_end or timezone.now()
                reverted.save(
                    update_fields=[
                        "state",
                        "current_stage",
                        "failure",
                        "terminal_at",
                        "updated_at",
                    ]
                )
        logger.warning(
            "hosted_build_retry_not_started",
            environment_id=str(locked.id),
            exc_info=True,
        )
        raise HostedHarnessError(
            "scheduler_unavailable",
            "The build could not start again; try again in a moment",
            status_code=503,
        ) from exc
    logger.info(
        "hosted_build_retried",
        environment_id=str(locked.id),
        user_id=str(user.pk or ""),
        resume_from=point,
    )
    return locked, point
