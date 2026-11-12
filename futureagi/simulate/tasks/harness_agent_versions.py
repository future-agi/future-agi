from __future__ import annotations

from typing import Any

from django.db import close_old_connections

from simulate.temporal.constants import READING_ACTIVITY_TIMEOUT_SECONDS
from tfc.temporal.drop_in import temporal_activity

__all__ = [
    "preflight_harness_agent_version",
    "read_harness_agent_version_document",
    "schedule_agent_document_reading",
    "schedule_agent_preflight",
]


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


@temporal_activity(
    time_limit=READING_ACTIVITY_TIMEOUT_SECONDS,
    max_retries=1,
    queue="simulation_runner",
)
def read_harness_agent_version_document(version_id: str) -> str:
    """Read an environment-made agent version only to store its document; the
    version's check and its environments never wait for it."""
    from simulate.services.agent_reading_sandbox import read_version

    close_old_connections()
    try:
        return read_version(version_id, document_only=True).status
    finally:
        close_old_connections()


def schedule_agent_document_reading(version_id: Any):
    """Start the reading; one runs per version at a time."""
    from temporalio.common import WorkflowIDConflictPolicy

    return read_harness_agent_version_document.apply_async(
        args=[str(version_id)],
        task_id=f"harness-agent-document-{version_id}",
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
