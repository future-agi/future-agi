"""Recover signups and trace publications whose immediate dispatch failed."""

from tfc.temporal.schedules.config import ScheduleConfig

DEMO_SEEDING_SCHEDULES = [
    ScheduleConfig(
        schedule_id="recover-demo-seeding",
        activity_name="recover_demo_data_activity",
        interval_seconds=60,
        queue="default",
        description="Retry persisted demo seeding and ClickHouse publication intents",
    ),
]
