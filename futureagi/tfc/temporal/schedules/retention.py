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
        logger.info(
            "soft_delete_activity_skipped",
            dependency="ee.usage.tasks.retention",
            reason="retention_dependency_unavailable",
        )
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
        logger.info(
            "hard_delete_activity_skipped",
            dependency="ee.usage.tasks.retention",
            reason="retention_dependency_unavailable",
        )
        return {}

    result = hard_delete_expired_data()
    total = sum(result.values())
    logger.info(
        "hard_delete_activity_completed",
        data_types_affected=len(result),
        total_rows=total,
    )
    _run_trajectory_items_gc()
    return result


def _run_trajectory_items_gc() -> None:
    """Follow span hard delete with the trajectory-items GC.

    GC derives every cut from the spans still present, so running it right
    after span deletion only brings forward what the daily schedule does. It
    is a no-op unless FI_TRAJECTORY_GC_DELETES_ENABLED is on, and a failure
    never fails the retention run (the daily schedule retries).
    """
    try:
        from tracer.services.clickhouse.v2.trajectory_lifecycle import (
            trajectory_items_gc,
        )

        trajectory_items_gc()
    except Exception:
        logger.exception("trajectory_items_gc_after_hard_delete_failed")


RETENTION_SCHEDULES: list[ScheduleConfig] = [
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
