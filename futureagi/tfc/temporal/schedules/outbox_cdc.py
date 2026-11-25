"""Schedules for the in-app outbox CDC (``FI_CDC_MODE=outbox``, the standalone install).

``register_temporal_schedules`` registers them only in outbox mode.
``oss_outbox_cdc.ensure_installed()`` also syncs them with the capture
triggers on every start, so capture never runs without a drain and a mode
change removes both.
"""

import os

from asgiref.sync import async_to_sync
from temporalio.client import ScheduleOverlapPolicy

from tfc.temporal.schedules.config import ScheduleConfig

DRAIN_SCHEDULE_ID = "outbox-cdc-drain"
RECONCILE_SCHEDULE_ID = "outbox-cdc-reconcile"


def outbox_cdc_enabled() -> bool:
    # oss_outbox_cdc.cdc_mode() validates the value where capture is installed.
    return os.getenv("FI_CDC_MODE", "peerdb").strip().lower() == "outbox"


def outbox_cdc_schedules() -> list[ScheduleConfig]:
    return [
        ScheduleConfig(
            schedule_id=DRAIN_SCHEDULE_ID,
            activity_name="drain_outbox_cdc",
            # PeerDB parity: fact mirrors synced every 10 s.
            interval_seconds=10,
            queue="tasks_s",
            overlap_policy=ScheduleOverlapPolicy.SKIP,
            description="Mirror captured Postgres changes into ClickHouse landing tables",
        ),
        ScheduleConfig(
            schedule_id=RECONCILE_SCHEDULE_ID,
            activity_name="reconcile_outbox_cdc",
            # Picks up requested sweeps (TRUNCATE, finished snapshots) within
            # 15 minutes; each table's full sweep still runs once a day.
            interval_seconds=900,
            queue="tasks_l",
            overlap_policy=ScheduleOverlapPolicy.SKIP,
            description="PG/CH id parity for the outbox CDC landing tables",
        ),
    ]


OUTBOX_CDC_SCHEDULES: list[ScheduleConfig] = (
    outbox_cdc_schedules() if outbox_cdc_enabled() else []
)


async def a_sync_outbox_cdc_schedules(client, *, enabled: bool) -> None:
    """Create/update both schedules when enabled, delete them otherwise."""
    from tfc.temporal.schedules.manager import (
        a_delete_schedule,
        a_register_schedules,
        a_schedule_exists,
    )

    if enabled:
        await a_register_schedules(client, outbox_cdc_schedules())
        return
    for schedule_id in (DRAIN_SCHEDULE_ID, RECONCILE_SCHEDULE_ID):
        if await a_schedule_exists(client, schedule_id):
            await a_delete_schedule(client, schedule_id)


@async_to_sync
async def sync_outbox_cdc_schedules(*, enabled: bool) -> None:
    """Sync wrapper for a_sync_outbox_cdc_schedules, using the shared client."""
    from tfc.temporal.common.client import get_client

    await a_sync_outbox_cdc_schedules(await get_client(), enabled=enabled)
