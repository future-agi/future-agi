"""Service layer for ``MemberRoleUpdateAPIView``."""

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
    """Domain error raised by ``update_member_role``.

    ``code`` is the ``error_codes`` key the caller maps to its transport.
    ``status_code`` is the HTTP status to use (400 for input/state errors,
    403 for authz).
    """

    def __init__(self, code: str, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


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
    """Apply an org-level and/or workspace-level role update for a target user.

    ``workspace_access_provided`` distinguishes "key omitted" from "key sent
    empty"; the DRF serializer defaults to ``[]`` so the caller has to tell us.

    Returns a ``changes`` dict for audit log + response.
    """
    try:
        target_membership = OrganizationMembership.objects.get(
            user_id=target_user_id,
            organization=organization,
        )
    except OrganizationMembership.DoesNotExist as exc:
        raise MemberRoleUpdateError("MEMBER_NOT_IN_ORG") from exc

    if not target_membership.is_active and not _has_pending_invite(
        organization, target_user_id
    ):
        raise MemberRoleUpdateError("MEMBER_DEACTIVATED_ROLE_UPDATE")

    _validate_workspace_access_in_org(workspace_access, organization)
    if ws_level is not None and workspace_id is not None:
        _validate_workspace_in_org(workspace_id, organization)

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


def _has_pending_invite(organization: Organization, target_user_id: UUID) -> bool:
    target_user = User.objects.filter(id=target_user_id).first()
    if not target_user:
        return False
    return OrganizationInvite.objects.filter(
        organization=organization,
        target_email__iexact=target_user.email,
        status=InviteStatus.PENDING,
    ).exists()


def _validate_workspace_access_in_org(
    workspace_access: Optional[list[dict]], organization: Organization
) -> None:
    """Reject cross-org workspace_ids; without this the writes below silently
    create a row pointing at a foreign workspace."""
    if not workspace_access:
        return
    ws_ids = [
        entry.get("workspace_id")
        for entry in workspace_access
        if entry.get("workspace_id")
    ]
    if not ws_ids:
        return
    valid_count = Workspace.objects.filter(
        id__in=ws_ids, organization=organization
    ).count()
    if valid_count != len(set(ws_ids)):
        raise MemberRoleUpdateError("WS_NOT_IN_ORG")


def _validate_workspace_in_org(workspace_id: UUID, organization: Organization) -> None:
    """Reject a direct ``workspace_id`` (the ``ws_level`` path) that is not in
    the actor's org. Without this the ``_apply_ws_level_change`` write below
    would silently create a membership in a foreign workspace — the same
    privilege-boundary gap ``_validate_workspace_access_in_org`` closes for the
    ``workspace_access`` list."""
    if not Workspace.objects.filter(
        id=workspace_id, organization=organization
    ).exists():
        raise MemberRoleUpdateError("WS_NOT_IN_ORG")


def _invite_workspace_access(
    *,
    organization: Organization,
    new_level: int,
    workspace_access: list[dict],
    workspace_access_provided: bool,
) -> Optional[list[dict]]:
    """The ``workspace_access`` to persist onto a pending invite, mirroring the
    access applied to an active membership for the same ``new_level``.

    Returns ``None`` when the invite's existing access must be left untouched —
    a single-workspace edit that omitted the ``workspace_access`` key (the
    "key omitted" semantics; we don't know the desired set).
    """
    if new_level >= Level.ADMIN:
        # Admin gets every workspace on accept, mirroring
        # _promote_to_workspace_admin_everywhere on the active path.
        return [
            {"workspace_id": str(ws_id), "level": Level.WORKSPACE_ADMIN}
            for ws_id in Workspace.objects.filter(
                organization=organization
            ).values_list("id", flat=True)
        ]
    if not workspace_access_provided:
        return None
    default_ws_level = (
        Level.WORKSPACE_MEMBER if new_level >= Level.MEMBER else Level.WORKSPACE_VIEWER
    )
    return [
        {
            "workspace_id": str(entry["workspace_id"]),
            "level": entry.get("level", default_ws_level),
        }
        for entry in workspace_access
        if entry.get("workspace_id")
    ]


def _merge_invite_workspace_access(
    *,
    existing: Optional[list[dict]],
    desired: list[dict],
    revoke_scope: Optional[set[UUID]],
) -> list[dict]:
    """The ``workspace_access`` to write onto a pending invite, honoring the
    actor's revoke scope (the same rule the active-membership revoke uses).

    - ``revoke_scope is None`` → org-wide authority: ``desired`` is authoritative.
    - otherwise the actor may only drop workspaces they administer, so existing
      invite entries for workspaces **outside** that scope (and not re-granted by
      ``desired``) are preserved rather than revoked on the invite path.
    """
    if revoke_scope is None:
        return desired
    scope_strs = {str(w) for w in revoke_scope}
    desired_ids = {entry["workspace_id"] for entry in desired}
    preserved = [
        entry
        for entry in (existing or [])
        if entry.get("workspace_id")
        and str(entry["workspace_id"]) not in desired_ids
        and str(entry["workspace_id"]) not in scope_strs
    ]
    return desired + preserved


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
    old_level = target_membership.level_or_legacy

    actor_membership = get_org_membership(actor)
    actor_level = actor_membership.level_or_legacy if actor_membership else 0
    if not can_invite_at_level(actor_level, new_level):
        raise MemberRoleUpdateError("ROLE_ASSIGN_FORBIDDEN", status_code=403)

    if old_level >= Level.OWNER and new_level < Level.OWNER:
        _enforce_not_last_owner(organization)

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

    User.objects.filter(id=target_user_id).update(
        organization_role=Level.to_org_string(new_level)
    )

    target_user = User.objects.filter(id=target_user_id).first()
    if target_user:
        pending_invites = OrganizationInvite.objects.filter(
            organization=organization,
            target_email__iexact=target_user.email,
            status=InviteStatus.PENDING,
        )
        # Also persist the authoritative workspace_access: OrganizationInvite.accept()
        # grants memberships from invite.workspace_access, so a stale value would
        # re-grant the workspaces this update just revoked once the invite is
        # accepted (the revocation-not-sticking bug, on the invite path).
        desired = _invite_workspace_access(
            organization=organization,
            new_level=new_level,
            workspace_access=workspace_access,
            workspace_access_provided=workspace_access_provided,
        )
        if desired is None:
            # Single-workspace edit that omitted workspace_access — touch level only.
            pending_invites.update(level=new_level)
        else:
            # The invite rewrite carries the same revoke scope as the active path:
            # a workspace-admin org-member may only drop workspaces they administer,
            # so out-of-scope entries already on the invite are preserved rather
            # than silently revoked on the invite path. (≤1 pending invite per
            # org+email by unique constraint, so per-row save is cheap.)
            revoke_scope = _revocable_workspace_ids(organization, actor)
            for invite in pending_invites:
                invite.level = new_level
                invite.workspace_access = _merge_invite_workspace_access(
                    existing=invite.workspace_access,
                    desired=desired,
                    revoke_scope=revoke_scope,
                )
                invite.save(update_fields=["level", "workspace_access"])


def _enforce_not_last_owner(organization: Organization) -> None:
    """``select_for_update`` so a concurrent demote can't push the org below
    one owner."""
    owner_count = (
        OrganizationMembership.objects.select_for_update()
        .filter(organization=organization, is_active=True, level__gte=Level.OWNER)
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
        raise MemberRoleUpdateError("LAST_OWNER_DEMOTE")


def _active_ws_membership_defaults(
    level: int, target_membership: OrganizationMembership, actor: User
) -> dict[str, Any]:
    """``defaults`` for an ``update_or_create`` that grants/refreshes an active
    workspace membership at ``level`` (un-soft-deletes if it was revoked)."""
    return {
        "level": level,
        "role": Level.to_ws_role(level),
        "organization_membership": target_membership,
        "granted_by": actor,
        "is_active": True,
        "deleted": False,
        "deleted_at": None,
    }


def _promote_to_workspace_admin_everywhere(
    *,
    organization: Organization,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
) -> None:
    for ws in Workspace.objects.filter(organization=organization):
        WorkspaceMembership._base_manager.update_or_create(
            workspace=ws,
            user_id=target_user_id,
            defaults=_active_ws_membership_defaults(
                Level.WORKSPACE_ADMIN, target_membership, actor
            ),
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
                defaults=_active_ws_membership_defaults(
                    ws_level, target_membership, actor
                ),
            )

    if not workspace_access_provided:
        return

    desired_ws_ids: set = {
        entry.get("workspace_id")
        for entry in workspace_access
        if entry.get("workspace_id")
    }
    # Block 2's workspace_id would be re-activated by _apply_ws_level_change
    # below anyway; keep it in the desired set to avoid a revoke + resurrect
    # in the same transaction.
    if also_keep_ws_id is not None:
        desired_ws_ids.add(also_keep_ws_id)

    revoke_qs = WorkspaceMembership._base_manager.filter(
        user_id=target_user_id,
        workspace__organization=organization,
        is_active=True,
    ).exclude(workspace_id__in=desired_ws_ids)

    # The revoke is org-wide for org admins/owners, but an org member who is
    # only a workspace admin may revoke access *only within the workspaces they
    # administer* — never strip a user out of workspaces they don't manage.
    revoke_scope = _revocable_workspace_ids(organization, actor)
    if revoke_scope is not None:
        revoke_qs = revoke_qs.filter(workspace_id__in=revoke_scope)

    revoked = revoke_qs.update(is_active=False, deleted=True, deleted_at=timezone.now())
    if revoked:
        changes["revoked_workspaces"] = revoked


def _revocable_workspace_ids(
    organization: Organization, actor: User
) -> Optional[set[UUID]]:
    """Workspace ids the ``actor`` is authorized to revoke access within.

    - Org admins/owners (``level >= ADMIN``) may revoke org-wide → ``None``
      (no scope restriction; owners can revoke anyone, admins are already
      limited to managing members/viewers by ``CanManageTargetUser``).
    - Everyone else (an org member/viewer who happens to be a workspace admin)
      may revoke only within the workspaces they themselves administer.
    """
    actor_membership = get_org_membership(actor)
    actor_level = actor_membership.level_or_legacy if actor_membership else 0
    if actor_level >= Level.ADMIN:
        return None

    memberships = WorkspaceMembership.objects.filter(
        user=actor,
        workspace__organization=organization,
        is_active=True,
    ).only("workspace_id", "level", "role")
    return {
        m.workspace_id
        for m in memberships
        if m.level_or_legacy >= Level.WORKSPACE_ADMIN
    }


def _apply_ws_level_change(
    *,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
    workspace_id: UUID,
    ws_level: int,
    changes: dict[str, Any],
) -> None:
    # `_base_manager` (not `all_objects`, which still applies the current-workspace
    # context filter): when the request context is workspace A but `workspace_id`
    # is B, the scoped manager hides B's existing membership, so update_or_create
    # would try to INSERT a duplicate (workspace, user) row and 500 on the unique
    # constraint instead of updating it. Matches `_apply_workspace_access`.
    existing_ws = WorkspaceMembership._base_manager.filter(
        workspace_id=workspace_id,
        user_id=target_user_id,
    ).first()
    old_ws = existing_ws.level_or_legacy if existing_ws else None

    WorkspaceMembership._base_manager.update_or_create(
        workspace_id=workspace_id,
        user_id=target_user_id,
        defaults=_active_ws_membership_defaults(ws_level, target_membership, actor),
    )
    changes["ws_level"] = {"old": old_ws, "new": ws_level}
