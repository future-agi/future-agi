from __future__ import annotations

import structlog
from temporalio.common import WorkflowIDConflictPolicy

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)


@temporal_activity(time_limit=60, max_retries=30, retry_delay=15, queue="default")
def confirm_hosted_harness_provider_cancel(job_id: str) -> dict:
    from django.utils import timezone

    from simulate.models import HostedHarnessJob
    from simulate.services.harness_cancel import (
        PROVIDER_CHECK_WINDOW,
        Confirmation,
        ProviderCalls,
        ProviderCallsActive,
        provider_calls,
        record_confirmation,
        unchecked_confirmation,
    )

    job = HostedHarnessJob.no_workspace_objects.get(id=job_id)
    if job.cancel_confirmed_by in (
        Confirmation.SANDBOX_ABSENT,
        Confirmation.PROVIDER_CALL_ENDED,
    ):
        return {"job_id": job_id, "confirmed": True}
    outcome = provider_calls(job)
    if outcome is ProviderCalls.ENDED:
        record_confirmation(job.id, Confirmation.PROVIDER_CALL_ENDED)
        return {"job_id": job_id, "confirmed": True}
    if outcome is ProviderCalls.ACTIVE:
        if timezone.now() - job.cancel_requested_at < PROVIDER_CHECK_WINDOW:
            raise ProviderCallsActive(job_id)
        record_confirmation(job.id, Confirmation.PROVIDER_CALL_OPEN)
    else:
        record_confirmation(job.id, unchecked_confirmation(outcome))
    logger.info(
        "hosted_harness_cancel_provider_unconfirmed", job_id=job_id, outcome=outcome
    )
    return {"job_id": job_id, "confirmed": False}


def schedule_provider_call_check(job_id: str) -> bool:
    try:
        confirm_hosted_harness_provider_cancel.apply_async(
            args=[job_id],
            task_id=f"hosted-harness-cancel-confirm-{job_id}",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            dispatch_timeout_seconds=30,
        )
    except Exception:
        logger.error("hosted_harness_cancel_confirm_not_scheduled", job_id=job_id)
        return False
    return True
