from typing import List

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)


@temporal_activity(time_limit=7200, queue="default")
def soft_delete_expired_data_activity():
    try:
        from ee.usage.tasks.retention import soft_delete_expired_data
    except ImportError:
        soft_delete_expired_data = None

    if soft_delete_expired_data is None:
        logger.info("soft_delete_activity_skipped", reason="ee module not available")
        return {}

    result = soft_delete_expired_data()
    total = sum(sum(counts.values()) for counts in result.values())
    logger.info(
        "soft_delete_activity_completed",
        orgs_affected=len(result),
        total_rows=total,
    )
    return result


@temporal_activity(time_limit=14400, queue="default")
def hard_delete_expired_data_activity():
    try:
        from ee.usage.tasks.retention import hard_delete_expired_data
    except ImportError:
        hard_delete_expired_data = None

    if hard_delete_expired_data is None:
        logger.info("hard_delete_activity_skipped", reason="ee module not available")
        return {}

    result = hard_delete_expired_data()
    total = sum(result.values())
    logger.info(
        "hard_delete_activity_completed",
        data_types_affected=len(result),
        total_rows=total,
    )
    return result


RETENTION_SCHEDULES: List[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="retention-soft-delete",
        activity_name="soft_delete_expired_data_activity",
        interval_seconds=86400,
        queue="default",
        description="Soft-delete data older than org retention period (daily)",
    ),
    ScheduleConfig(
        schedule_id="retention-hard-delete",
        activity_name="hard_delete_expired_data_activity",
        interval_seconds=604800,
        queue="default",
        description="Hard-delete data soft-deleted >90 days ago (weekly)",
    ),
]
