"""Temporal activities for trajectory_items GC and project erasure.

The daily ``trajectory-items-gc`` schedule runs ``tracer.trajectory_items_gc``.
``TrajectoryErasureWorkflow`` (tfc.temporal.trajectory_erasure) calls the
erasure steps by name; each step is idempotent and safe to retry. None of them
reads the trajectory read flag, so they keep working after it is turned off.
"""

from __future__ import annotations

import structlog

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)


@temporal_activity(
    name="tracer.trajectory_items_gc",
    queue="tasks_l",
    time_limit=4 * 60 * 60,
    max_retries=0,
)
def trajectory_items_gc() -> dict:
    from tracer.services.clickhouse.v2.trajectory_lifecycle import (
        trajectory_items_gc as run_gc,
    )

    return run_gc()


@temporal_activity(
    name="tracer.trajectory_erasure_gate", queue="tasks_l", time_limit=600
)
def trajectory_erasure_gate(project_id: str) -> dict:
    from tracer.services.clickhouse.v2.trajectory_lifecycle import erasure_gate

    return erasure_gate(project_id)


@temporal_activity(
    name="tracer.trajectory_erasure_invalidate", queue="tasks_l", time_limit=120
)
def trajectory_erasure_invalidate(project_id: str) -> dict:
    from tracer.services.project_deletion import publish_project_invalidation
    from tracer.services.trajectory_seen_contract import canonical_project_id

    pid = canonical_project_id(project_id)
    publish_project_invalidation([pid])
    return {"project_id": pid}


@temporal_activity(
    name="tracer.trajectory_erasure_clear_seen", queue="tasks_l", time_limit=1800
)
def trajectory_erasure_clear_seen(project_id: str) -> dict:
    from tracer.services.trajectory_seen_contract import (
        canonical_project_id,
        clear_project_seen_keys,
        open_seen_redis,
    )

    pid = canonical_project_id(project_id)
    client = open_seen_redis()
    try:
        removed = clear_project_seen_keys(client, pid)
    finally:
        client.close()
    return {"project_id": pid, "keys_removed": removed}


@temporal_activity(
    name="tracer.trajectory_erasure_delete_items", queue="tasks_l", time_limit=7200
)
def trajectory_erasure_delete_items(project_id: str) -> dict:
    from tracer.services.clickhouse.v2.trajectory_lifecycle import (
        erase_project_items,
    )

    return erase_project_items(project_id)


@temporal_activity(
    name="tracer.trajectory_erasure_verify", queue="tasks_l", time_limit=600
)
def trajectory_erasure_verify(project_id: str) -> dict:
    from tracer.services.clickhouse.v2.trajectory_lifecycle import (
        verify_project_erased,
    )

    return verify_project_erased(project_id)


@temporal_activity(
    name="tracer.trajectory_erasure_stalled", queue="tasks_l", time_limit=60
)
def trajectory_erasure_stalled(project_id: str, step: str) -> None:
    # Alert: a step ran out of retries. Ids and step names only.
    logger.error("trajectory_erasure_stalled", project_id=project_id, step=step)
