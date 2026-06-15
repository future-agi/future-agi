"""
Service layer for updating a member's org and workspace role assignments.

Per ``coding-standards/03-architecture-and-layers``, views own request
transport and (de)serialization; everything below — escalation guards,
last-owner enforcement, workspace grant + revoke math, downstream syncs —
lives in this service so any caller (DRF view, CLI, future Temporal
activity) can drive the same business rules.

Typed exceptions surface domain failures; the caller maps them to its own
transport (DRF view → HTTP 400/403). The service knows nothing about
``request``, status codes, or error-message strings.
"""

from typing import Any, Optional
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.permissions.utils import can_invite_at_level, get_org_membership


class MemberRoleUpdateError(Exception):
    """Base class for domain errors raised by update_member_role."""


class MemberNotInOrgError(MemberRoleUpdateError):
    """Target user has no OrganizationMembership for this organization."""


class MemberDeactivatedError(MemberRoleUpdateError):
    """Target member is deactivated and has no pending invite to re-activate."""


class RoleAssignForbiddenError(MemberRoleUpdateError):
    """Actor cannot assign the requested org level (escalation guard)."""


class LastOwnerDemoteError(MemberRoleUpdateError):
    """Cannot demote the last Owner of an organization."""


class WorkspaceNotInOrgError(MemberRoleUpdateError):
    """A workspace_id in workspace_access does not belong to this organization."""


def update_member_role(
    *,
    organization: Organization,
    actor: User,
    target_user_id: UUID,
    org_level: Optional[int] = None,
    ws_level: Optional[int] = None,
    workspace_id: Optional[UUID] = None,
    workspace_access: Optional[list[dict]] = None,
    workspace_access_provided: bool = False,
) -> dict[str, Any]:
    """
    Apply an org-level and/or workspace-level role update for a target user.

    Args:
        organization: org the target user belongs to.
        actor: user performing the update (for escalation guards + audit).
        target_user_id: user being updated.
        org_level: new integer org level (None = unchanged).
        ws_level: new integer ws level for ``workspace_id`` (None = unchanged).
        workspace_id: workspace the ``ws_level`` change targets.
        workspace_access: authoritative list of ``{workspace_id, level}`` grants
            when ``org_level`` is below Admin. Anything not in this list is
            revoked from the user's active workspace memberships.
        workspace_access_provided: True iff the caller explicitly sent the
            ``workspace_access`` key. Distinguishes "omitted, keep existing"
            from "sent empty list, revoke all" — the DRF serializer fills
            ``[]`` by default so the caller has to tell us.

    Returns:
        ``changes`` dict suitable for the audit log and API response:
        ``{"org_level": {"old": ..., "new": ...}, "ws_level": {...},
        "revoked_workspaces": N}``. Keys only appear for fields that
        actually changed.

    Raises:
        MemberNotInOrgError, MemberDeactivatedError, RoleAssignForbiddenError,
        LastOwnerDemoteError, WorkspaceNotInOrgError.
    """
    try:
        target_membership = OrganizationMembership.objects.get(
            user_id=target_user_id,
            organization=organization,
        )
    except OrganizationMembership.DoesNotExist as exc:
        raise MemberNotInOrgError() from exc

    if not target_membership.is_active:
        # Deactivated members are immutable unless they have a re-activatable
        # pending invite — that path is owned by the invite endpoint.
        target_user = User.objects.filter(id=target_user_id).first()
        has_pending_invite = bool(
            target_user
            and OrganizationInvite.objects.filter(
                organization=organization,
                target_email__iexact=target_user.email,
                status=InviteStatus.PENDING,
            ).exists()
        )
        if not has_pending_invite:
            raise MemberDeactivatedError()

    # Reject cross-org workspace_ids up front. Without this guard the code
    # below would write a WorkspaceMembership row pointing at a workspace the
    # actor's org does not own — silent privilege escalation.
    if workspace_access:
        provided_ws_ids = [
            entry.get("workspace_id")
            for entry in workspace_access
            if entry.get("workspace_id")
        ]
        if provided_ws_ids:
            valid_count = Workspace.objects.filter(
                id__in=provided_ws_ids, organization=organization
            ).count()
            if valid_count != len(set(provided_ws_ids)):
                raise WorkspaceNotInOrgError()

    changes: dict[str, Any] = {}

    with transaction.atomic():
        if org_level is not None:
            _apply_org_level_change(
                organization=organization,
                actor=actor,
                target_user_id=target_user_id,
                target_membership=target_membership,
                new_level=org_level,
                workspace_access=workspace_access or [],
                workspace_access_provided=workspace_access_provided,
                ws_level=ws_level,
                workspace_id=workspace_id,
                changes=changes,
            )

        if ws_level is not None and workspace_id is not None:
            _apply_ws_level_change(
                actor=actor,
                target_user_id=target_user_id,
                target_membership=target_membership,
                workspace_id=workspace_id,
                ws_level=ws_level,
                changes=changes,
            )

    return changes


def _apply_org_level_change(
    *,
    organization: Organization,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
    new_level: int,
    workspace_access: list[dict],
    workspace_access_provided: bool,
    ws_level: Optional[int],
    workspace_id: Optional[UUID],
    changes: dict[str, Any],
) -> None:
    """Apply ``org_level`` change + cascaded workspace grants/revocations."""
    old_level = target_membership.level_or_legacy

    actor_membership = get_org_membership(actor)
    actor_level = actor_membership.level_or_legacy if actor_membership else 0
    if not can_invite_at_level(actor_level, new_level):
        raise RoleAssignForbiddenError()

    # Race-safe last-owner check: count owners under select_for_update so a
    # concurrent demote can't push the org below one owner.
    if old_level >= Level.OWNER and new_level < Level.OWNER:
        owner_count = (
            OrganizationMembership.objects.select_for_update()
            .filter(
                organization=organization,
                is_active=True,
                level__gte=Level.OWNER,
            )
            .count()
        )
        legacy_owner_count = (
            OrganizationMembership.objects.select_for_update()
            .filter(
                organization=organization,
                is_active=True,
                level__isnull=True,
                role="Owner",
            )
            .count()
        )
        if (owner_count + legacy_owner_count) <= 1:
            raise LastOwnerDemoteError()

    target_membership.level = new_level
    target_membership.role = Level.to_org_string(new_level)
    target_membership.save(update_fields=["level", "role"])
    changes["org_level"] = {"old": old_level, "new": new_level}

    if new_level >= Level.ADMIN:
        _promote_to_workspace_admin_everywhere(
            organization=organization,
            actor=actor,
            target_user_id=target_user_id,
            target_membership=target_membership,
        )
    else:
        _apply_workspace_access(
            organization=organization,
            actor=actor,
            target_user_id=target_user_id,
            target_membership=target_membership,
            new_level=new_level,
            workspace_access=workspace_access,
            workspace_access_provided=workspace_access_provided,
            also_keep_ws_id=workspace_id if ws_level is not None else None,
            changes=changes,
        )

    # Mirror to legacy User.organization_role field (still read elsewhere
    # for backward compat).
    User.objects.filter(id=target_user_id).update(
        organization_role=Level.to_org_string(new_level)
    )

    # Update OrganizationInvite if user has a pending invite so a re-invite
    # accept lands on the new level.
    target_user = User.objects.filter(id=target_user_id).first()
    if target_user:
        OrganizationInvite.objects.filter(
            organization=organization,
            target_email__iexact=target_user.email,
            status=InviteStatus.PENDING,
        ).update(level=new_level)


def _promote_to_workspace_admin_everywhere(
    *,
    organization: Organization,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
) -> None:
    """Grant ``WORKSPACE_ADMIN`` in every workspace of the org for symmetry
    with the implicit "Org Admin sees all workspaces" rule."""
    org_workspaces = Workspace.objects.filter(organization=organization)
    for ws in org_workspaces:
        WorkspaceMembership._base_manager.update_or_create(
            workspace=ws,
            user_id=target_user_id,
            defaults={
                "level": Level.WORKSPACE_ADMIN,
                "role": Level.to_ws_role(Level.WORKSPACE_ADMIN),
                "organization_membership": target_membership,
                "granted_by": actor,
                "is_active": True,
                "deleted": False,
                "deleted_at": None,
            },
        )


def _apply_workspace_access(
    *,
    organization: Organization,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
    new_level: int,
    workspace_access: list[dict],
    workspace_access_provided: bool,
    also_keep_ws_id: Optional[UUID],
    changes: dict[str, Any],
) -> None:
    """Grant the workspaces listed in ``workspace_access`` and revoke any
    other active workspace memberships when the list is authoritative.
    """
    default_ws_level = (
        Level.WORKSPACE_MEMBER if new_level >= Level.MEMBER else Level.WORKSPACE_VIEWER
    )
    for ws_entry in workspace_access:
        ws_id = ws_entry.get("workspace_id")
        ws_level = ws_entry.get("level", default_ws_level)
        if ws_id:
            WorkspaceMembership._base_manager.update_or_create(
                workspace_id=ws_id,
                user_id=target_user_id,
                defaults={
                    "level": ws_level,
                    "role": Level.to_ws_role(ws_level),
                    "organization_membership": target_membership,
                    "granted_by": actor,
                    "is_active": True,
                    "deleted": False,
                    "deleted_at": None,
                },
            )

    if not workspace_access_provided:
        # Caller omitted the key — keep all other workspaces as they are.
        return

    desired_ws_ids: set = {
        entry.get("workspace_id")
        for entry in workspace_access
        if entry.get("workspace_id")
    }
    # If the same request is targeting a workspace via ws_level / workspace_id
    # below, treat it as part of the desired set so we don't revoke + resurrect
    # it in one transaction (no behavior change, no audit noise).
    if also_keep_ws_id is not None:
        desired_ws_ids.add(also_keep_ws_id)

    revoked = (
        WorkspaceMembership._base_manager.filter(
            user_id=target_user_id,
            workspace__organization=organization,
            is_active=True,
        )
        .exclude(workspace_id__in=desired_ws_ids)
        .update(
            is_active=False,
            deleted=True,
            deleted_at=timezone.now(),
        )
    )
    if revoked:
        changes["revoked_workspaces"] = revoked


def _apply_ws_level_change(
    *,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
    workspace_id: UUID,
    ws_level: int,
    changes: dict[str, Any],
) -> None:
    """Set a single workspace membership's level (Block 2 in the legacy
    request shape). Re-activates a soft-deleted row if one exists, so the
    DB unique constraint on ``(workspace_id, user_id)`` is respected."""
    existing_ws = WorkspaceMembership.all_objects.filter(
        workspace_id=workspace_id,
        user_id=target_user_id,
    ).first()
    old_ws = existing_ws.level_or_legacy if existing_ws else None

    WorkspaceMembership.all_objects.update_or_create(
        workspace_id=workspace_id,
        user_id=target_user_id,
        defaults={
            "level": ws_level,
            "role": Level.to_ws_role(ws_level),
            "organization_membership": target_membership,
            "granted_by": actor,
            "is_active": True,
            "deleted": False,
            "deleted_at": None,
        },
    )
    changes["ws_level"] = {"old": old_ws, "new": ws_level}
