"""Retention schedules for agentcc gateway request logs, attempts and outcomes."""

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)


@temporal_activity(time_limit=7200, queue="default")
def purge_expired_agentcc_request_logs_activity():
    """Delete gateway request records past each org's retention TTLs."""
    from agentcc.services.retention import purge_expired_request_logs

    result = purge_expired_request_logs()
    logger.info(
        "agentcc_retention_activity_completed",
        orgs_with_retention=len(result),
        rows_deleted=sum(sum(counts.values()) for counts in result.values()),
    )
    return result


AGENTCC_RETENTION_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="agentcc-log-retention",
        activity_name="purge_expired_agentcc_request_logs_activity",
        interval_seconds=86400,
        # A day-long gap would otherwise drop a missed run.
        catchup_window_seconds=86400,
        queue="default",
        description="Delete gateway request logs, attempts and outcomes past each org's retention TTL (daily)",
    ),
]
