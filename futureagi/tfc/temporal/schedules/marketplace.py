"""Cloud Marketplace schedules: a consumer supervisor, reporting, reconciliation.

The supervisor keeps the Pub/Sub consumer alive; nothing else starts it.

Hourly reporting is the cadence Google's usage reporting guide states. Usage
reconciliation runs daily: it is the only thing that detects a silent
under-report, since nobody complains about being charged too little.
"""

from typing import Any

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)


def _consumer_workflow() -> Any:
    """Lazy import: the workflow module pulls in temporalio's sandbox guards."""
    from tfc.temporal.marketplace.workflows import GCPMarketplaceConsumerWorkflow

    return GCPMarketplaceConsumerWorkflow


@temporal_activity(time_limit=900, queue="default")
def report_gcp_marketplace_usage_activity():
    from accounts.gcp_marketplace_usage import report_all_usage

    result = report_all_usage()
    logger.info("gcp_marketplace_usage_run", **result)
    return result


@temporal_activity(time_limit=900, queue="default")
def reconcile_gcp_marketplace_usage_activity():
    from accounts.gcp_marketplace_usage import reconcile_usage

    discrepancies = reconcile_usage()
    logger.info("gcp_marketplace_reconciliation_run", discrepancies=len(discrepancies))
    return {"discrepancies": len(discrepancies)}


@temporal_activity(time_limit=900, queue="default")
def reconcile_gcp_marketplace_plans_activity():
    from accounts.gcp_marketplace_events import reconcile_entitlement_plans
    from accounts.gcp_marketplace_utils import reconcile_unapproved_accounts

    result = reconcile_entitlement_plans()
    logger.info("gcp_marketplace_plan_reconciliation_run", **result)

    accounts = reconcile_unapproved_accounts()
    logger.info("gcp_marketplace_signup_reconciliation_run", **accounts)
    return {**result, "accounts": accounts}


MARKETPLACE_SCHEDULES = [
    # The consumer is a singleton that runs forever, so this is a supervisor
    # rather than a job: the default SKIP overlap policy makes every tick a
    # no-op while it is alive, and restarts it within 5 minutes if it dies.
    # The fixed workflow id means a missed skip cannot produce two consumers
    # racing on the same subscription.
    ScheduleConfig(
        schedule_id="gcp-marketplace-consumer",
        activity_name="drain_gcp_marketplace_events_activity",
        interval_seconds=300,
        queue="default",
        description="Keep the Marketplace Pub/Sub consumer running",
        workflow_class=_consumer_workflow(),
    ),
    ScheduleConfig(
        schedule_id="gcp-marketplace-usage-report",
        activity_name="report_gcp_marketplace_usage_activity",
        cron_expression="5 * * * *",
        catchup_window_seconds=6 * 3600,
        queue="default",
        description="Report Marketplace usage to Service Control (hourly)",
    ),
    # Hourly: the recovery path for a customer an unmapped plan id left on
    # free should not take a day to run once the mapping is added.
    ScheduleConfig(
        schedule_id="gcp-marketplace-plan-reconcile",
        activity_name="reconcile_gcp_marketplace_plans_activity",
        cron_expression="15 * * * *",
        catchup_window_seconds=6 * 3600,
        queue="default",
        description="Re-apply Marketplace plans that drifted or were unmapped",
    ),
    ScheduleConfig(
        schedule_id="gcp-marketplace-usage-reconcile",
        activity_name="reconcile_gcp_marketplace_usage_activity",
        cron_expression="30 2 * * *",
        catchup_window_seconds=86400,
        queue="default",
        description="Compare usage ledger against reported Marketplace usage (daily)",
    ),
]
