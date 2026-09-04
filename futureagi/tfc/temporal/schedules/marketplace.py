"""Cloud Marketplace schedules: one supervisor and two reporting jobs.

The supervisor keeps the Pub/Sub consumer alive; nothing else starts it.

Hourly reporting is the cadence Google's usage reporting guide states.
Reconciliation runs daily: it is the only thing that detects a silent
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
    ScheduleConfig(
        schedule_id="gcp-marketplace-usage-reconcile",
        activity_name="reconcile_gcp_marketplace_usage_activity",
        cron_expression="30 2 * * *",
        catchup_window_seconds=86400,
        queue="default",
        description="Compare usage ledger against reported Marketplace usage (daily)",
    ),
]
