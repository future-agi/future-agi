from __future__ import annotations

import hashlib

from django.db import close_old_connections

from tfc.temporal.drop_in import temporal_activity

__all__ = [
    "observe_harness_github_push",
    "poll_harness_github_heads",
    "schedule_github_push",
]


@temporal_activity(time_limit=300, max_retries=0, queue="default")
def poll_harness_github_heads() -> dict[str, int]:
    """Read the heads of watched public repositories; new commits make versions."""
    from simulate.services.harness_agent_changes import poll_repository_heads

    close_old_connections()
    try:
        return poll_repository_heads()
    finally:
        close_old_connections()


@temporal_activity(time_limit=120, max_retries=2, queue="default")
def observe_harness_github_push(
    repository: str,
    ref: str,
    default_branch: str = "",
    repository_id: int | None = None,
) -> dict[str, int]:
    """A signed push named this repository and ref; its payload is never passed."""
    from simulate.services.harness_agent_changes import receive_push

    close_old_connections()
    try:
        return receive_push(repository, ref, default_branch or None, repository_id)
    finally:
        close_old_connections()


def schedule_github_push(
    repository: str, ref: str, default_branch: str, repository_id: int | None = None
) -> None:
    """Pushes to one branch coalesce while one is observed; the poller picks up the
    rest."""
    from temporalio.common import WorkflowIDConflictPolicy

    digest = hashlib.sha256(
        f"{repository.lower()}:{ref}:{repository_id}".encode()
    ).hexdigest()[:32]
    observe_harness_github_push.apply_async(
        args=[repository, ref, default_branch, repository_id],
        task_id=f"harness-github-push-{digest}",
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
