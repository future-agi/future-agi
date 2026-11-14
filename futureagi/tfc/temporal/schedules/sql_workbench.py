"""Bounded background erasure for expired SQL result snapshots."""

from temporalio.client import ScheduleOverlapPolicy

from tfc.temporal.queues import require_exact_aggregation_task_queue
from tfc.temporal.schedules.config import ScheduleConfig
from tfc.temporal.sql_workbench import SQLRetentionWorkflow

SQL_WORKBENCH_SCHEDULES = [
    ScheduleConfig(
        schedule_id="sql-workbench-result-retention",
        activity_name="tracer.sql_workbench.purge_expired_results",
        interval_seconds=900,
        queue=require_exact_aggregation_task_queue(),
        workflow_class=SQLRetentionWorkflow,
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Expire at most 1,000 SQL result bodies/opt-in terminal audit records every 15 minutes",
    ),
    ScheduleConfig(
        schedule_id="sql-workbench-deadline-reconciliation",
        activity_name="tracer.sql_workbench.reconcile_expired",
        interval_seconds=60,
        queue="tasks_s",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Finalize at most 1,000 abandoned SQL executions without competing for scan slots",
    ),
]
