from __future__ import annotations

from typing import Any

from django.db import close_old_connections

from simulate.temporal.constants import READING_ACTIVITY_TIMEOUT_SECONDS
from tfc.logging.temporal.context import try_activity_info
from tfc.temporal.drop_in import temporal_activity

__all__ = ["scan_agent_source", "schedule_agent_scan"]


SCAN_TIME_LIMIT_SECONDS = READING_ACTIVITY_TIMEOUT_SECONDS
SCAN_MAX_RETRIES = 2
SCAN_DEADLINE_SECONDS = SCAN_TIME_LIMIT_SECONDS * (SCAN_MAX_RETRIES + 1) + 300


def _last_attempt() -> bool:
    """Whether Temporal runs this scan no more after the current attempt; a direct
    call is never retried."""
    info = try_activity_info()
    if info is None:
        return True
    policy = info.retry_policy
    limit = SCAN_MAX_RETRIES + 1 if policy is None else policy.maximum_attempts
    return limit > 0 and info.attempt >= limit


@temporal_activity(
    time_limit=SCAN_TIME_LIMIT_SECONDS,
    max_retries=SCAN_MAX_RETRIES,
    queue="simulation_runner",
)
def scan_agent_source(scan_id: str) -> str:
    """Find the agents in one scan's source; the input is its id only."""
    from simulate.models import AgentScan
    from simulate.services.agent_reading_sandbox import fail_scan, scan_source

    close_old_connections()
    try:
        try:
            scan_source(scan_id)
        except Exception:
            if _last_attempt():
                fail_scan(scan_id)
            raise
        return str(
            AgentScan.no_workspace_objects.filter(id=scan_id)
            .values_list("status", flat=True)
            .first()
            or "missing"
        )
    finally:
        close_old_connections()


def schedule_agent_scan(scan_id: Any):
    """Start the scan; a scan asked for twice runs once."""
    from temporalio.common import WorkflowIDConflictPolicy

    return scan_agent_source.apply_async(
        args=[str(scan_id)],
        task_id=f"agent-scan-{scan_id}",
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
