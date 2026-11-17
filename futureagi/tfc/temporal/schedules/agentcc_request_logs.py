"""Temporal schedule for gateway Request Logs retention."""

from tfc.temporal.schedules.config import ScheduleConfig

# Off peak, once a day, in UTC. The Settings panel's "next cleanup" comes from
# the same constant.
REQUEST_LOG_RETENTION_HOUR_UTC = 3

AGENTCC_REQUEST_LOG_SCHEDULES = [
    ScheduleConfig(
        schedule_id="agentcc-request-log-retention",
        activity_name="purge_expired_agentcc_request_logs",
        cron_expression=f"0 {REQUEST_LOG_RETENTION_HOUR_UTC} * * *",
        # A worker that was down at 03:00 still runs the day's cleanup.
        catchup_window_seconds=86400,
        queue="default",
        description="Delete gateway Request Logs past each organization's retention",
    ),
]
