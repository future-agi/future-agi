"""Managed-credit schedules for the agentcc gateway.

Three schedules, side by side (the BL-LEDGER-2A interface's integration items):

- the settlement drain (every 30 s): the gateway's outboxes into the ledger;
- the push sweep (every 60 s): ``sweep_unacked_pushes`` re-requests the granted
  push of every account whose gateway ack is behind its version (accounts in
  ``na_managed_off`` are re-tried at most hourly by the ledger itself);
- the R4 gateway reconciler (hourly): ``reconcile.check_gateway`` runs I5, I6
  and I11 for each managed-credit account from one credits-v1 read, records a
  ``ReconciliationRun`` per invariant and stamps the account's
  ``last_reconciled_at``. The least recently reconciled accounts go first, at
  most ``RECONCILE_BATCH`` per run.

A build without the ledger skips each one with a warning (PLAN 161.1): a build
that bills must never get there. Auto-reload adds its schedule to this list.
"""

import structlog
from django.db.models import F

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)

RECONCILE_BATCH = 500


def _skipped(dependency: str) -> dict:
    logger.warning(
        "managed_ledger_schedule_skipped",
        dependency=dependency,
        reason="ledger_dependency_unavailable",
    )
    return {}


@temporal_activity(time_limit=120, queue="default")
def drain_managed_settlements_activity():
    """Settle the gateway's managed-credit outboxes into the ledger."""
    try:
        from ee.usage.services.managed_settlement import drain_managed_settlements
    except ImportError:
        drain_managed_settlements = None

    if drain_managed_settlements is None:
        # A build without the ledger has no managed spend to settle; say so
        # loudly (PLAN 161.1), since a build that bills must never get here.
        logger.warning(
            "managed_settlement_drain_skipped",
            dependency="ee.usage.services.managed_settlement",
            reason="settlement_dependency_unavailable",
        )
        return {}
    return drain_managed_settlements()


@temporal_activity(time_limit=120, queue="default")
def sweep_unacked_pushes_activity():
    """Re-request the granted push of every account the gateway has not acked."""
    try:
        from ee.usage.services.managed_credits import sweep_unacked_pushes
    except ImportError:
        return _skipped("ee.usage.services.managed_credits")
    return {"swept": sweep_unacked_pushes()}


@temporal_activity(time_limit=900, queue="default")
def reconcile_managed_gateway_activity():
    """R4: I5, I6 and I11 for each managed-credit account against the gateway."""
    try:
        # The full dotted path, so a ledger module that cannot load is an
        # ImportError here even when its package already bound the name.
        from ee.usage.models.managed_credits import ManagedCreditAccount
        from ee.usage.services.reconcile import check_gateway
    except ImportError:
        return _skipped("ee.usage.services.reconcile")
    from django.utils import timezone

    counts = {"orgs": 0, "failed": 0, "errors": 0}
    accounts = ManagedCreditAccount.objects.select_related("organization").order_by(
        F("last_reconciled_at").asc(nulls_first=True), "organization_id"
    )[:RECONCILE_BATCH]
    for account in accounts:
        org = account.organization
        try:
            results = check_gateway(org)
        except Exception:
            # One org's failure never stops the others; it is retried next run.
            logger.exception("managed_gateway_reconcile_failed", org_id=str(org.pk))
            counts["errors"] += 1
            continue
        counts["orgs"] += 1
        if not all(result.ok for result in results):
            counts["failed"] += 1
        ManagedCreditAccount.objects.filter(pk=account.pk).update(
            last_reconciled_at=timezone.now()
        )
    logger.info("managed_gateway_reconcile_completed", **counts)
    return counts


AGENTCC_MANAGED_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="agentcc-managed-settlement-drain",
        activity_name="drain_managed_settlements_activity",
        interval_seconds=30,
        queue="default",
        description=(
            "Settle gateway managed-credit outboxes into the ledger and re-push "
            "accounts the gateway lost (every 30 s)"
        ),
    ),
    ScheduleConfig(
        schedule_id="agentcc-managed-push-sweep",
        activity_name="sweep_unacked_pushes_activity",
        interval_seconds=60,
        queue="default",
        description=(
            "Re-request the granted push of managed-credit accounts the gateway "
            "has not acknowledged (every 60 s)"
        ),
    ),
    ScheduleConfig(
        schedule_id="agentcc-managed-gateway-reconcile",
        activity_name="reconcile_managed_gateway_activity",
        interval_seconds=3600,
        catchup_window_seconds=6 * 3600,
        queue="default",
        description=(
            "R4: reconcile each managed-credit account with the gateway "
            "(I5, I6, I11; hourly)"
        ),
    ),
]
