from __future__ import annotations

from typing import Any

from django.db import close_old_connections

from tfc.temporal.drop_in import temporal_activity

__all__ = ["scan_agent_source", "schedule_agent_scan"]


@temporal_activity(time_limit=2400, max_retries=2, queue="simulation_runner")
def scan_agent_source(scan_id: str) -> str:
    """Find the agents in one scan's source; the input is its id only."""
    from simulate.models import AgentScan
    from simulate.services.agent_reading_sandbox import scan_source

    close_old_connections()
    try:
        scan_source(scan_id)
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
