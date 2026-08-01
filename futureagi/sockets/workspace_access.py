"""Workspace access-gating for the prompt-stream WS protocol.

Pulled out of ``PromptStreamConsumer`` (TH-5944 follow-up — SRP review
comment on PR #959) so the access rules — "which workspace does this
request act on, and is the user allowed to use it" — can be unit-tested
without any Channels/consumer machinery, and so the consumer class is left
with just message routing + wiring frames/managers around this gate's
decisions.

Framework-agnostic by design: ``WorkspaceAccessGate`` takes plain values in
(``user``, ``workspace_id``) and returns a plain ``WorkspaceAccessResult``
out. It never calls ``send_json``/``close`` itself — the caller (the
consumer) decides how to surface ``result.message``/``result.reason`` on its
socket, including which WS close code to use. This keeps the gate reusable
for any future non-WS caller too (e.g. a plain HTTP view) without dragging
in Channels.
"""

from dataclasses import dataclass
from typing import Optional

from channels.db import database_sync_to_async
from django.core.exceptions import ValidationError

from accounts.models import Workspace

NOT_FOUND = "not_found"
PERMISSION_DENIED = "permission_denied"


@dataclass
class WorkspaceAccessResult:
    """Outcome of a :class:`WorkspaceAccessGate` resolve.

    ``reason`` is one of ``None`` (granted), :data:`NOT_FOUND`, or
    :data:`PERMISSION_DENIED` — the caller maps ``reason`` to its own
    transport-specific error code (e.g. a WS close code).
    """

    workspace: Optional[Workspace]
    org_id: Optional[str] = None
    reason: Optional[str] = None
    message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.workspace is not None


class WorkspaceAccessGate:
    """Resolves + gates ``(workspace, org)`` access for a user + workspace id.

    Single source of truth for TH-5944: the workspace the caller selected
    (not connect-time membership order) determines the org, and access is
    always re-checked against the *current* ``user``/``workspace_id`` rather
    than anything cached from an earlier point in the connection's lifetime.
    """

    def __init__(self, *, user, workspace_id):
        self.user = user
        self.workspace_id = workspace_id

    async def fetch_workspace(self) -> Optional[Workspace]:
        if not self.workspace_id:
            return None
        try:
            return await database_sync_to_async(
                lambda: Workspace.objects.get(id=self.workspace_id, is_active=True)
            )()
        except (Workspace.DoesNotExist, ValueError, ValidationError):
            return None

    async def resolve(self) -> WorkspaceAccessResult:
        """Full resolve for execute paths: fetch + access-check + derive org."""
        workspace = await self.fetch_workspace()
        if workspace is None:
            message = (
                "Workspace not found or inactive."
                if self.workspace_id
                else "workspace_id query param is required."
            )
            return WorkspaceAccessResult(
                workspace=None, reason=NOT_FOUND, message=message
            )

        can_access = await database_sync_to_async(self.user.can_access_workspace)(
            workspace
        )
        if not can_access:
            return WorkspaceAccessResult(
                workspace=None,
                reason=PERMISSION_DENIED,
                message="You do not have permission to use this workspace.",
            )

        return WorkspaceAccessResult(workspace=workspace, org_id=workspace.organization_id)

    async def resolve_org_for_stop(self) -> Optional[str]:
        """Best-effort org resolution for stop handlers.

        Stop is idempotent and best-effort — unlike :meth:`resolve`, this
        skips the access check entirely and just reports the org if the
        workspace exists, letting the caller decide how to handle a miss.
        """
        workspace = await self.fetch_workspace()
        return workspace.organization_id if workspace else None
