"""
Temporal schedule manager.

Creates and manages Temporal schedules (replacing Celery Beat).

Pattern:
- Async functions prefixed with `a_` for direct async usage
- Sync wrappers using `@async_to_sync` from asgiref
- Helper functions: create, update, delete, pause, unpause, trigger, exists
"""

import asyncio
from typing import List, Optional

from asgiref.sync import async_to_sync
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.service import RPCError

from tfc.logging.temporal import get_logger
from tfc.temporal.drop_in.workflow import TaskRunnerInput, TaskRunnerWorkflow
from tfc.temporal.schedules.config import ScheduleConfig

logger = get_logger(__name__)

# =============================================================================
# Core schedule operations
# =============================================================================


async def a_schedule_exists(client: Client, schedule_id: str) -> bool:
    """Check if a schedule exists."""
    try:
        handle = client.get_schedule_handle(schedule_id)
        await handle.describe()
        return True
    except Exception:
        return False


@async_to_sync
async def schedule_exists(client: Client, schedule_id: str) -> bool:
    """Sync wrapper for a_schedule_exists."""
    return await a_schedule_exists(client, schedule_id)


async def a_create_schedule(
    client: Client,
    schedule_id: str,
    schedule: Schedule,
    *,
    trigger_immediately: bool = False,
) -> None:
    """Create a new schedule."""
    await client.create_schedule(
        schedule_id,
        schedule,
        trigger_immediately=trigger_immediately,
    )
    logger.info("schedule_created", schedule_id=schedule_id)


@async_to_sync
async def create_schedule(
    client: Client,
    schedule_id: str,
    schedule: Schedule,
    *,
    trigger_immediately: bool = False,
) -> None:
    """Sync wrapper for a_create_schedule."""
    return await a_create_schedule(
        client, schedule_id, schedule, trigger_immediately=trigger_immediately
    )


async def a_update_schedule(
    client: Client,
    schedule_id: str,
    schedule: Schedule,
    *,
    keep_tz: bool = True,
) -> None:
    """Update an existing schedule."""
    handle = client.get_schedule_handle(schedule_id)

    async def updater(input: ScheduleUpdateInput) -> ScheduleUpdate:
        if keep_tz and input.description.schedule.spec:
            # Preserve existing timezone
            schedule.spec.jitter = input.description.schedule.spec.jitter
        return ScheduleUpdate(schedule=schedule)

    await handle.update(updater)
    logger.info("schedule_updated", schedule_id=schedule_id)


@async_to_sync
async def update_schedule(
    client: Client,
    schedule_id: str,
    schedule: Schedule,
    *,
    keep_tz: bool = True,
) -> None:
    """Sync wrapper for a_update_schedule."""
    return await a_update_schedule(client, schedule_id, schedule, keep_tz=keep_tz)


async def a_delete_schedule(client: Client, schedule_id: str) -> bool:
    """Delete a schedule."""
    try:
        handle = client.get_schedule_handle(schedule_id)
        await handle.delete()
        logger.info("schedule_deleted", schedule_id=schedule_id)
        return True
    except Exception as e:
        logger.warning("schedule_delete_failed", schedule_id=schedule_id, error=str(e))
        return False


@async_to_sync
async def delete_schedule(client: Client, schedule_id: str) -> bool:
    """Sync wrapper for a_delete_schedule."""
    return await a_delete_schedule(client, schedule_id)


async def a_pause_schedule(
    client: Client,
    schedule_id: str,
    note: Optional[str] = None,
) -> None:
    """Pause a schedule."""
    handle = client.get_schedule_handle(schedule_id)
    await handle.pause(note=note or "Paused via API")
    logger.info("schedule_paused", schedule_id=schedule_id)


@async_to_sync
async def pause_schedule(
    client: Client,
    schedule_id: str,
    note: Optional[str] = None,
) -> None:
    """Sync wrapper for a_pause_schedule."""
    return await a_pause_schedule(client, schedule_id, note=note)


async def a_unpause_schedule(
    client: Client,
    schedule_id: str,
    note: Optional[str] = None,
) -> None:
    """Unpause a schedule."""
    handle = client.get_schedule_handle(schedule_id)
    await handle.unpause(note=note or "Unpaused via API")
    logger.info("schedule_unpaused", schedule_id=schedule_id)


@async_to_sync
async def unpause_schedule(
    client: Client,
    schedule_id: str,
    note: Optional[str] = None,
) -> None:
    """Sync wrapper for a_unpause_schedule."""
    return await a_unpause_schedule(client, schedule_id, note=note)


async def a_trigger_schedule(
    client: Client,
    schedule_id: str,
    overlap: ScheduleOverlapPolicy = ScheduleOverlapPolicy.SKIP,
) -> None:
    """Trigger a schedule immediately."""
    handle = client.get_schedule_handle(schedule_id)
    await handle.trigger(overlap=overlap)
    logger.info("schedule_triggered", schedule_id=schedule_id)


@async_to_sync
async def trigger_schedule(
    client: Client,
    schedule_id: str,
    overlap: ScheduleOverlapPolicy = ScheduleOverlapPolicy.SKIP,
) -> None:
    """Sync wrapper for a_trigger_schedule."""
    return await a_trigger_schedule(client, schedule_id, overlap=overlap)


async def a_describe_schedule(client: Client, schedule_id: str):
    """Get schedule details."""
    handle = client.get_schedule_handle(schedule_id)
    return await handle.describe()


@async_to_sync
async def describe_schedule(client: Client, schedule_id: str):
    """Sync wrapper for a_describe_schedule."""
    return await a_describe_schedule(client, schedule_id)


async def a_list_schedules(client: Client) -> List[str]:
    """List all schedule IDs."""
    schedules = []
    schedules_iter = await client.list_schedules()
    async for schedule in schedules_iter:
        schedules.append(schedule.id)
    return schedules


@async_to_sync
async def list_schedules(client: Client) -> List[str]:
    """Sync wrapper for a_list_schedules."""
    return await a_list_schedules(client)


# =============================================================================
# Schedule Registration (for ScheduleConfig objects)
# =============================================================================


def _build_schedule_for_config(config: ScheduleConfig) -> Schedule:
    """Build a Temporal Schedule from a ScheduleConfig."""
    if config.cron_expression:
        spec = ScheduleSpec(cron_expressions=[config.cron_expression])
    else:
        spec = ScheduleSpec(intervals=[ScheduleIntervalSpec(every=config.interval)])

    policy_kwargs: dict = {"overlap": config.overlap_policy}
    if config.catchup_window is not None:
        policy_kwargs["catchup_window"] = config.catchup_window

    if config.workflow_class is not None:
        action = ScheduleActionStartWorkflow(
            config.workflow_class.run,
            id=f"scheduled-{config.schedule_id}",
            task_queue=config.queue,
        )
    else:
        from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

        activity_metadata = _ACTIVITY_REGISTRY.get(config.activity_name, {})
        action = ScheduleActionStartWorkflow(
            TaskRunnerWorkflow.run,
            TaskRunnerInput(
                activity_name=config.activity_name,
                args=[],
                kwargs={},
                queue=config.queue,
                max_retries=activity_metadata.get("max_retries"),
                retry_delay=activity_metadata.get("retry_delay"),
            ),
            id=f"scheduled-{config.schedule_id}",
            task_queue=config.queue,
        )

    return Schedule(
        action=action,
        spec=spec,
        policy=SchedulePolicy(**policy_kwargs),
        state=ScheduleState(
            note=config.description or f"Schedule for {config.activity_name}"
        ),
    )


async def a_create_or_update_schedule(
    client: Client,
    config: ScheduleConfig,
) -> None:
    """Create or update a schedule from config."""
    schedule = _build_schedule_for_config(config)

    if await a_schedule_exists(client, config.schedule_id):
        await a_update_schedule(client, config.schedule_id, schedule)
    else:
        await a_create_schedule(client, config.schedule_id, schedule)


async def a_cleanup_orphaned_schedules(
    client: Client,
    valid_schedule_ids: set[str],
) -> int:
    """
    Delete schedules that exist in Temporal but are not in the valid set.

    This cleans up orphaned schedules from previous deployments or
    manually created schedules that are no longer needed.

    Args:
        client: Temporal client
        valid_schedule_ids: Set of schedule IDs that should be kept

    Returns:
        Number of deleted schedules
    """
    existing_schedule_ids = await a_list_schedules(client)

    deleted_count = 0
    for schedule_id in existing_schedule_ids:
        if schedule_id not in valid_schedule_ids:
            logger.info("deleting_orphaned_schedule", schedule_id=schedule_id)
            if await a_delete_schedule(client, schedule_id):
                deleted_count += 1

    if deleted_count > 0:
        logger.info("orphaned_schedules_cleaned", count=deleted_count)

    return deleted_count


@async_to_sync
async def cleanup_orphaned_schedules(
    client: Client,
    valid_schedule_ids: set[str],
) -> int:
    """Sync wrapper for a_cleanup_orphaned_schedules."""
    return await a_cleanup_orphaned_schedules(client, valid_schedule_ids)


async def a_register_schedules(
    client: Client,
    schedules: List[ScheduleConfig],
    cleanup_orphans: bool = True,
) -> None:
    """
    Register multiple schedules with Temporal.

    Args:
        client: Temporal client
        schedules: List of schedule configs to register
        cleanup_orphans: If True, delete schedules not in the provided list (default: True)
    """
    from tfc.temporal.common.registry import _import_temporal_activity_modules

    _import_temporal_activity_modules()

    logger.info("registering_schedules", count=len(schedules))

    # Cleanup orphaned schedules first
    if cleanup_orphans:
        valid_schedule_ids = {config.schedule_id for config in schedules}
        await a_cleanup_orphaned_schedules(client, valid_schedule_ids)

    for config in schedules:
        try:
            await a_create_or_update_schedule(client, config)
        except ScheduleAlreadyRunningError:
            logger.info("schedule_already_running", schedule_id=config.schedule_id)
            try:
                await a_update_schedule(
                    client, config.schedule_id, _build_schedule_for_config(config)
                )
            except RPCError as e:
                # Handle case where workflow task is in failed state
                if "Workflow Task in failed state" in str(e):
                    logger.warning(
                        "schedule_failed_workflow_task",
                        schedule_id=config.schedule_id,
                        action="recreating",
                    )
                    # Delete the problematic schedule
                    await a_delete_schedule(client, config.schedule_id)
                    # Recreate it
                    await a_create_schedule(
                        client, config.schedule_id, _build_schedule_for_config(config)
                    )
                else:
                    # Re-raise if it's a different RPCError
                    raise
        except RPCError as e:
            # Handle RPCError during create_or_update
            if "Workflow Task in failed state" in str(e):
                logger.warning(
                    "schedule_failed_workflow_task",
                    schedule_id=config.schedule_id,
                    action="recreating",
                )
                # Try to delete if it exists
                if await a_schedule_exists(client, config.schedule_id):
                    await a_delete_schedule(client, config.schedule_id)
                # Recreate it
                await a_create_schedule(
                    client, config.schedule_id, _build_schedule_for_config(config)
                )
            else:
                # Re-raise if it's a different RPCError
                raise

    logger.info("all_schedules_registered")


@async_to_sync
async def register_schedules(
    client: Client,
    schedules: List[ScheduleConfig],
    cleanup_orphans: bool = True,
) -> None:
    """Sync wrapper for a_register_schedules."""
    return await a_register_schedules(
        client, schedules, cleanup_orphans=cleanup_orphans
    )


# Legacy aliases for backwards compatibility
register_schedules_async = a_register_schedules
delete_schedule_async = a_delete_schedule
list_schedules_async = a_list_schedules


__all__ = [
    # Async functions (a_ prefix)
    "a_schedule_exists",
    "a_create_schedule",
    "a_update_schedule",
    "a_delete_schedule",
    "a_pause_schedule",
    "a_unpause_schedule",
    "a_trigger_schedule",
    "a_describe_schedule",
    "a_list_schedules",
    "a_create_or_update_schedule",
    "a_register_schedules",
    "a_cleanup_orphaned_schedules",
    # Sync wrappers
    "schedule_exists",
    "create_schedule",
    "update_schedule",
    "delete_schedule",
    "pause_schedule",
    "unpause_schedule",
    "trigger_schedule",
    "describe_schedule",
    "list_schedules",
    "register_schedules",
    "cleanup_orphaned_schedules",
    # Legacy aliases
    "register_schedules_async",
    "delete_schedule_async",
    "list_schedules_async",
]
