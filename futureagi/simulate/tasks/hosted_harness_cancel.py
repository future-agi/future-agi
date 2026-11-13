from __future__ import annotations

import structlog
from temporalio.common import WorkflowIDConflictPolicy

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)


@temporal_activity(time_limit=60, max_retries=20, retry_delay=15, queue="default")
def confirm_hosted_harness_provider_cancel(job_id: str) -> dict:
    from simulate.models import HostedHarnessJob
    from simulate.services.harness_cancel import (
        Confirmation,
        ProviderCalls,
        ProviderCallsActive,
        provider_calls,
        record_confirmation,
    )

    job = HostedHarnessJob.no_workspace_objects.get(id=job_id)
    if job.cancel_confirmed_by in (
        Confirmation.SANDBOX_ABSENT,
        Confirmation.PROVIDER_CALL_ENDED,
    ):
        return {"job_id": job_id, "confirmed": True}
    outcome = provider_calls(job)
    if outcome is ProviderCalls.ACTIVE:
        raise ProviderCallsActive(job_id)
    if outcome is ProviderCalls.ENDED:
        record_confirmation(job.id, Confirmation.PROVIDER_CALL_ENDED)
        return {"job_id": job_id, "confirmed": True}
    logger.info("hosted_harness_cancel_provider_unconfirmable", job_id=job_id)
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
