"""Authoritative workspace bindings for server-issued prompt job targets."""

from typing import Literal
from uuid import uuid4

import structlog
from channels.db import database_sync_to_async
from django.core.cache import cache

from sockets.targets import normalize_socket_target

logger = structlog.get_logger(__name__)
# Seven days covers execution and retries without retaining a key for every past job.
PROMPT_JOB_BINDING_TTL_SECONDS = 604_800
JOB_UNAVAILABLE = "This result is unavailable. Reopen it to try again."


def is_prompt_job(target) -> bool:
    return isinstance(target, str) and target.startswith(("generate_", "improve_"))


def _binding_key(target: str) -> str:
    return f"socket:prompt-job:v1:{normalize_socket_target(target)}"


def create_prompt_job(
    kind: Literal["generate", "improve"], workspace, organization_id
) -> str:
    """Bind before exposing the ID or handing it to any worker."""
    if (
        workspace is None
        or workspace.id is None
        or organization_id is None
        or str(workspace.organization_id) != str(organization_id)
    ):
        raise ValueError("Prompt job requires a workspace in the selected organization")
    target = f"{kind}_{uuid4()}"
    if not cache.add(
        _binding_key(target),
        (str(organization_id), str(workspace.id)),
        timeout=PROMPT_JOB_BINDING_TTL_SECONDS,
    ):
        raise RuntimeError("Prompt job binding unavailable")
    return target


def _workspace_access(user_id, organization_id, workspace_id) -> bool:
    from accounts.models import User, Workspace

    workspace = (
        Workspace.no_workspace_objects.filter(
            pk=workspace_id,
            organization_id=organization_id,
            is_active=True,
            deleted=False,
        )
        .select_related("organization")
        .first()
    )
    # User caches role decisions, so never reuse the socket's User instance.
    user = User.objects.filter(pk=user_id, is_active=True).first()
    return (
        workspace is not None
        and user is not None
        and user.can_access_workspace(workspace)
    )


@database_sync_to_async
def prompt_job_access(user_id, organization_id, target: str) -> bool:
    try:
        binding = cache.get(_binding_key(target))
        if binding is None:
            return False
        org_id, workspace_id = binding
        return org_id == str(organization_id) and _workspace_access(
            user_id, org_id, workspace_id
        )
    except Exception:
        logger.exception("prompt_job_check_failed")
        return False
