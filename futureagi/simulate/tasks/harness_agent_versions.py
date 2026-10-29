from __future__ import annotations

from typing import Any

from django.db import close_old_connections

from tfc.temporal.drop_in import temporal_activity

__all__ = ["preflight_harness_agent_version", "schedule_agent_preflight"]


@temporal_activity(time_limit=600, max_retries=3, queue="simulation_runner")
def preflight_harness_agent_version(version_id: str) -> str:
    """Check one agent version; the input is its id only, values are read here."""
    from simulate.models import AgentVersion
    from simulate.services.harness_agent_versions import preflight_agent_version

    close_old_connections()
    try:
        version = AgentVersion._base_manager.filter(id=version_id).first()
        if version is None:
            return "missing"
        return preflight_agent_version(version).status
    finally:
        close_old_connections()


def schedule_agent_preflight(version_id: Any):
    """Start the check; requests for one version coalesce while one is running."""
    from temporalio.common import WorkflowIDConflictPolicy

    return preflight_harness_agent_version.apply_async(
        args=[str(version_id)],
        task_id=f"harness-agent-preflight-{version_id}",
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
