"""Cloud Marketplace usage reporting schedules.

Hourly reporting, which is the cadence Google's usage reporting guide states.
Reconciliation runs daily: it is the only thing that detects a silent
under-report, since nobody complains about being charged too little.
"""

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)


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
