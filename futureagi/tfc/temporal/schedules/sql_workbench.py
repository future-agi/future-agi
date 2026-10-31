"""Bounded background erasure for expired SQL result snapshots."""

from temporalio.client import ScheduleOverlapPolicy

from tfc.temporal.schedules.config import ScheduleConfig

SQL_WORKBENCH_SCHEDULES = [
    ScheduleConfig(
        schedule_id="sql-workbench-result-retention",
        activity_name="tracer.sql_workbench.purge_expired_results",
        interval_seconds=900,
        queue="exact_aggregation",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Expire at most 1,000 SQL result bodies/opt-in terminal audit records every 15 minutes",
    )
]
