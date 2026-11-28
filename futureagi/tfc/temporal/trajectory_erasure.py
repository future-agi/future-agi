"""Erase one project's trajectory items after its spans are gone.

``request_trajectory_erasure`` (tracer.services.clickhouse.v2.trajectory_lifecycle)
starts one workflow per project, id ``trajectory-erasure-<project id>``. A
second request for a project whose erasure is running joins it.

Steps, each an idempotent activity retried with backoff for up to seven days
(then an alert and the workflow fails; the daily GC stays the backstop):

1. gate: the Postgres project is absent or deleted, and no replica holds a span
   row for it. This orders erasure after span deletion.
2. invalidate: publish the collector auth-cache invalidation, then wait longer
   than a collector may still accept the project.
3. clear_seen: remove the project's seen sets, so no collector reuses an item
   that is about to be deleted.
4. delete_items: delete the project's items and apply the deleted mask.
5. Wait ``recheck_seconds`` (72 h), run 1, 3 and 4 again, then verify that no
   replica holds a span or item row for the project and log a receipt.

Nothing here reads the read flag or the GC switch: erasure must also run after
those are turned off. This module imports no Django code, so the worker can
load it before Django starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

ERASURE_QUEUE = "tasks_l"
WORKFLOW_ID_PREFIX = "trajectory-erasure-"

ACTIVITY_GATE = "tracer.trajectory_erasure_gate"
ACTIVITY_INVALIDATE = "tracer.trajectory_erasure_invalidate"
ACTIVITY_CLEAR_SEEN = "tracer.trajectory_erasure_clear_seen"
ACTIVITY_DELETE_ITEMS = "tracer.trajectory_erasure_delete_items"
ACTIVITY_VERIFY = "tracer.trajectory_erasure_verify"
ACTIVITY_STALLED = "tracer.trajectory_erasure_stalled"

DEFAULT_STEP_DEADLINE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_RECHECK_SECONDS = 72 * 60 * 60

STEP_RETRY = RetryPolicy(
    initial_interval=timedelta(minutes=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(hours=1),
    maximum_attempts=0,
    non_retryable_error_types=["ValueError"],
)
STEP_ATTEMPT_TIMEOUT = {
    ACTIVITY_GATE: timedelta(minutes=10),
    ACTIVITY_INVALIDATE: timedelta(minutes=2),
    ACTIVITY_CLEAR_SEEN: timedelta(minutes=30),
    ACTIVITY_DELETE_ITEMS: timedelta(hours=2),
    ACTIVITY_VERIFY: timedelta(minutes=10),
}


def erasure_workflow_id(project_id: str) -> str:
    return f"{WORKFLOW_ID_PREFIX}{project_id}"


@dataclass
class TrajectoryErasureInput:
    project_id: str
    # Longer than a collector may still accept the project after invalidation.
    ingest_wait_seconds: int
    recheck_seconds: int = DEFAULT_RECHECK_SECONDS
    step_deadline_seconds: int = DEFAULT_STEP_DEADLINE_SECONDS


@workflow.defn
class TrajectoryErasureWorkflow:
    @workflow.run
    async def run(self, input: TrajectoryErasureInput) -> dict[str, Any]:
        project_id = input.project_id
        deadline = timedelta(seconds=input.step_deadline_seconds)

        await self._step(ACTIVITY_GATE, project_id, deadline)
        await self._step(ACTIVITY_INVALIDATE, project_id, deadline)
        await workflow.sleep(timedelta(seconds=input.ingest_wait_seconds))
        await self._step(ACTIVITY_CLEAR_SEEN, project_id, deadline)
        first = await self._step(ACTIVITY_DELETE_ITEMS, project_id, deadline)

        # A span written before ingest stopped may land late; check again.
        await workflow.sleep(timedelta(seconds=input.recheck_seconds))
        await self._step(ACTIVITY_GATE, project_id, deadline)
        await self._step(ACTIVITY_CLEAR_SEEN, project_id, deadline)
        second = await self._step(ACTIVITY_DELETE_ITEMS, project_id, deadline)
        receipt = await self._step(ACTIVITY_VERIFY, project_id, deadline)
        return {
            "project_id": project_id,
            "first_pass_rows": (first or {}).get("rows", 0),
            "second_pass_rows": (second or {}).get("rows", 0),
            "receipt": receipt,
        }

    async def _step(self, name: str, project_id: str, deadline: timedelta) -> Any:
        try:
            output = await workflow.execute_activity(
                name,
                {"args": [project_id], "kwargs": {}},
                task_queue=ERASURE_QUEUE,
                start_to_close_timeout=STEP_ATTEMPT_TIMEOUT[name],
                schedule_to_close_timeout=deadline,
                retry_policy=STEP_RETRY,
            )
        except ActivityError:
            # Out of retries: alert, then fail so the run is visible.
            await workflow.execute_activity(
                ACTIVITY_STALLED,
                {"args": [project_id, name], "kwargs": {}},
                task_queue=ERASURE_QUEUE,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            raise
        if isinstance(output, dict):
            return output.get("result")
        return output


__all__ = [
    "ACTIVITY_CLEAR_SEEN",
    "ACTIVITY_DELETE_ITEMS",
    "ACTIVITY_GATE",
    "ACTIVITY_INVALIDATE",
    "ACTIVITY_STALLED",
    "ACTIVITY_VERIFY",
    "ERASURE_QUEUE",
    "TrajectoryErasureInput",
    "TrajectoryErasureWorkflow",
    "erasure_workflow_id",
]
