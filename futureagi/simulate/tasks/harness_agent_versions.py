from __future__ import annotations

from typing import Any

from django.db import close_old_connections

from simulate.temporal.constants import READING_ACTIVITY_TIMEOUT_SECONDS
from tfc.temporal.drop_in import temporal_activity

__all__ = [
    "preflight_harness_agent_version",
    "read_agent_for_design_review",
    "read_harness_agent_version_document",
    "schedule_agent_document_reading",
    "schedule_agent_preflight",
    "schedule_design_reading",
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
    version's check and its environments never wait for it. A version on its
    parent's code takes the parent's document instead of a reading. Every check
    start schedules it while no document is stored; each schedule is a new
    workflow run, which its retry and any request while it runs share. The
    reading's charge is keyed by that run, as a check's is by its own."""
    from simulate.services.agent_reading_sandbox import read_version
    from simulate.services.agent_version_steps import reuse_parent_reading
    from tfc.logging.temporal.context import try_activity_info

    close_old_connections()
    try:
        if reuse_parent_reading(version_id) is not None:
            return "reused"
        info = try_activity_info()
        return read_version(
            version_id,
            document_only=True,
            check_id=info.workflow_run_id if info else None,
        ).status
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


_DESIGN_READING_ATTEMPTS = 2


def _retries_left(info) -> bool:
    return info is not None and info.attempt < _DESIGN_READING_ATTEMPTS


@temporal_activity(
    time_limit=READING_ACTIVITY_TIMEOUT_SECONDS,
    max_retries=_DESIGN_READING_ATTEMPTS - 1,
    queue="simulation_runner",
)
def read_agent_for_design_review(job_id: str) -> str:
    from simulate.services.agent_reading_sandbox import CRASHED, read_version_contract
    from simulate.services.harness_design_review import (
        open_review_after_reading,
        reading_version_id,
    )
    from tfc.logging.temporal.context import try_activity_info

    close_old_connections()
    try:
        version_id = reading_version_id(job_id)
        if version_id is None:
            return "closed"
        info = try_activity_info()
        try:
            outcome = read_version_contract(
                version_id, check_id=info.workflow_run_id if info else None
            )
        except Exception as exc:
            if _retries_left(info):
                raise
            return open_review_after_reading(
                job_id,
                code=str(getattr(exc, "code", "") or CRASHED),
                message="Reading the agent stopped before it finished. Build again.",
                retryable=True,
            )
        if outcome.status == "failed" and outcome.retryable and _retries_left(info):
            raise RuntimeError(outcome.code)
        return open_review_after_reading(
            job_id,
            code=outcome.code if outcome.status == "failed" else "",
            message=outcome.message if outcome.status == "failed" else "",
            retryable=outcome.retryable,
            commit_sha=outcome.commit_sha,
        )
    finally:
        close_old_connections()


def schedule_design_reading(job_id: Any):
    from temporalio.common import WorkflowIDConflictPolicy

    return read_agent_for_design_review.apply_async(
        args=[str(job_id)],
        task_id=f"harness-design-reading-{job_id}",
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
