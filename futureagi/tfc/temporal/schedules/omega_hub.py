"""Temporal schedules for Omega Hub operations."""

from temporalio.client import ScheduleOverlapPolicy

from tfc.temporal.schedules.config import ScheduleConfig

# Keep this config module free of omega_hub imports. ``tfc.temporal`` imports
# schedules while the worker registry imports activity modules; importing the
# activities here would create a cycle during Django startup.
CHECK_OPERATIONS = "omega_hub.check_operations"
DELIVER_OUTBOX = "omega_hub.deliver_outbox"
RUN_RETENTION = "omega_hub.retention"
RUN_RULES_NOTIFY = "omega_hub.rules_notify"

HUB_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="omega-hub-operations-check",
        activity_name=CHECK_OPERATIONS,
        interval_seconds=300,
        queue="tasks_s",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Reap expired Hub checker leases and page operations alerts",
    ),
    ScheduleConfig(
        schedule_id="omega-hub-outbox-drain",
        activity_name=DELIVER_OUTBOX,
        interval_seconds=30,
        queue="tasks_s",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Drain durable Omega Hub email outbox",
    ),
    ScheduleConfig(
        schedule_id="omega-hub-retention",
        activity_name=RUN_RETENTION,
        cron_expression="17 2 * * *",
        queue="tasks_s",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Apply Omega Hub upload and removed-entry retention",
    ),
    ScheduleConfig(
        schedule_id="omega-hub-rules-notify",
        activity_name=RUN_RULES_NOTIFY,
        cron_expression="47 3 * * *",
        queue="tasks_s",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Notify Hub publishers about rules changes",
    ),
]

__all__ = ["HUB_SCHEDULES"]
