"""Attachment lifecycle schedules, registered only where attachments are enabled.

Without these nothing drains the attachment outbox: streamed uploads never
reach READY, budgets never release (every tenant hits 429) and erased bytes
stay in the private buckets. The validate drain runs the bubblewrap sandbox,
so the ``tasks_s`` workers need it (see DEPLOY.md).
"""

import os

from temporalio.client import ScheduleOverlapPolicy

from tfc.temporal.schedules.config import ScheduleConfig

OUTBOX_DRAIN_SCHEDULE_ID = "attachment-outbox-drain"
LIFECYCLE_SWEEP_SCHEDULE_ID = "attachment-lifecycle-sweep"
ORPHAN_SWEEP_SCHEDULE_ID = "attachment-orphan-sweep"


def attachments_enabled() -> bool:
    # Same switch as tfc.settings.attachments.FI_ATTACHMENTS_ENABLED.
    return os.getenv("FI_ATTACHMENTS_ENABLED", "false").lower() == "true"


def lifecycle_schedules() -> list[ScheduleConfig]:
    if not attachments_enabled():
        return []
    return [
        ScheduleConfig(
            schedule_id=OUTBOX_DRAIN_SCHEDULE_ID,
            activity_name="drain_attachment_outbox",
            interval_seconds=15,
            queue="tasks_s",
            overlap_policy=ScheduleOverlapPolicy.SKIP,
            description="Validate, erase and account attachment uploads (outbox)",
        ),
        ScheduleConfig(
            schedule_id=LIFECYCLE_SWEEP_SCHEDULE_ID,
            activity_name="sweep_attachment_lifecycle",
            interval_seconds=60,
            queue="tasks_s",
            overlap_policy=ScheduleOverlapPolicy.SKIP,
            description="Expire attachment reservations/occurrences, purge media",
        ),
        ScheduleConfig(
            schedule_id=ORPHAN_SWEEP_SCHEDULE_ID,
            activity_name="sweep_attachment_orphans",
            interval_seconds=600,
            catchup_window_seconds=600,
            queue="tasks_s",
            overlap_policy=ScheduleOverlapPolicy.SKIP,
            description="Tombstone attachments whose parent span left ClickHouse",
        ),
    ]


ATTACHMENT_LIFECYCLE_SCHEDULES: list[ScheduleConfig] = lifecycle_schedules()
