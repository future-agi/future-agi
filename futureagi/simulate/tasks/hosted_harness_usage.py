"""Seal hosted harness usage on the backend once a sandbox is gone."""

from __future__ import annotations

from datetime import timedelta

import structlog
from django.db import transaction
from django.utils import timezone
from temporalio.common import WorkflowIDConflictPolicy

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

_SWEEP_LOOKBACK = timedelta(days=7)
_SWEEP_GRACE = timedelta(minutes=10)
_SWEEP_BATCH_LIMIT = 200


@temporal_activity(time_limit=300, max_retries=10, retry_delay=30, queue="default")
def seal_hosted_harness_usage(attempt_id: str) -> dict:
    """Replay one attempt's usage onto the billing rails and mark its cleanup receipt."""
    from simulate.models import HostedHarnessAttempt, HostedHarnessCleanupReceipt
    from simulate.services.harness_usage import replay_harness_usage

    attempt = HostedHarnessAttempt.no_workspace_objects.get(id=attempt_id)
    with transaction.atomic():
        receipt = (
            HostedHarnessCleanupReceipt.no_workspace_objects.select_for_update().get(
                attempt_id=attempt.id
            )
        )
        replay_harness_usage(attempt)
        details = dict(receipt.details)
        details["usage_sealed_at"] = timezone.now().isoformat()
        receipt.details = details
        receipt.save(update_fields=["details", "updated_at"])
    return {"attempt_id": str(attempt.id), "sealed": True}


def schedule_usage_seal(attempt_id: str) -> bool:
    """Enqueue the seal for one attempt; a seal already queued for it is reused."""
    try:
        seal_hosted_harness_usage.apply_async(
            args=[attempt_id],
            task_id=f"hosted-harness-usage-seal-{attempt_id}",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            dispatch_timeout_seconds=30,
        )
    except Exception:
        # Never raise into cleanup: the sweep re-enqueues an unsealed receipt.
        logger.error("hosted_harness_usage_seal_not_scheduled", attempt_id=attempt_id)
        return False
    return True


@temporal_activity(time_limit=300, max_retries=0, queue="default")
def seal_unsealed_hosted_harness_usage() -> dict[str, int]:
    """Re-enqueue the seal for cleanup receipts whose seal never ran."""
    from simulate.models import HostedHarnessCleanupReceipt

    now = timezone.now()
    attempt_ids = list(
        HostedHarnessCleanupReceipt.no_workspace_objects.filter(
            verified_absent=True,
            created_at__gte=now - _SWEEP_LOOKBACK,
            created_at__lte=now - _SWEEP_GRACE,
            details__usage_sealed_at__isnull=True,
        )
        .order_by("created_at")
        .values_list("attempt_id", flat=True)[:_SWEEP_BATCH_LIMIT]
    )
    scheduled = sum(schedule_usage_seal(str(attempt_id)) for attempt_id in attempt_ids)
    counts = {"scanned": len(attempt_ids), "scheduled": scheduled}
    if attempt_ids:
        logger.info("hosted_harness_usage_seal_sweep", **counts)
    return counts
