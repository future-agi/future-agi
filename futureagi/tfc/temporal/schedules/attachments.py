"""Attachment schedules, registered only where attachments are enabled."""

import os

from temporalio.client import ScheduleOverlapPolicy

from tfc.temporal.schedules.config import ScheduleConfig

BINDING_SCHEDULE_ID = "attachment-binding-reconcile"


def attachments_enabled() -> bool:
    # Same switch as tfc.settings.attachments.FI_ATTACHMENTS_ENABLED.
    return os.getenv("FI_ATTACHMENTS_ENABLED", "false").lower() == "true"


ATTACHMENT_SCHEDULES: list[ScheduleConfig] = (
    [
        ScheduleConfig(
            schedule_id=BINDING_SCHEDULE_ID,
            activity_name="reconcile_attachment_bindings",
            interval_seconds=15,
            queue="tasks_s",
            overlap_policy=ScheduleOverlapPolicy.SKIP,
            description="Bind uploaded attachment occurrences to persisted spans",
        )
    ]
    if attachments_enabled()
    else []
)
