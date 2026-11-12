"""Remove one tenant's access, never the shared person or their other tenants.

There is deliberately no zero-membership check here. Membership creation and
reactivation have multiple writers that do not share an identity lock; observing
zero rows is not proof that it is safe to deactivate the person. Retain the
identity even at zero (login then offers org setup), rather than racing another
organization's grant. Global identity erasure belongs to a separate platform flow.
"""

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services.personal_key_lifecycle import revoke_personal_keys
from tfc.routers import primary_required
from tfc.utils.audit import log_audit


@primary_required
@transaction.atomic
def offboard_organization_member(
    user, organization, *, actor=None, mark_removed=False
):
    """Atomically revoke membership, workspace grants and tenant credentials.

    Keep inactive membership rows: auth must not revive a legacy organization FK
    when the last live membership disappears. Query via the base manager so a
    selected workspace cannot hide other grants in the *same* organization.

    Only explicit member removal opts into retained-list visibility.
    Invitation cancellation and provisional access cleanup stay unmarked.
    """
    from accounts.services.team_members import active_owner_count
    from accounts.utils import resolve_org_role
    from tfc.constants.roles import OrganizationRoles

    # All lifecycle routes serialize the owner census with the same real org row.
    type(organization).objects.select_for_update().get(pk=organization.pk)
    if (
        resolve_org_role(user, organization) == OrganizationRoles.OWNER
        and active_owner_count(organization) <= 1
    ):
        raise ValueError("Cannot remove the last owner")
    memberships = OrganizationMembership._base_manager.filter(
        user_id=user.pk, organization_id=organization.pk
    )
    # Snapshot the departing grants before bulk updates bypass their signals.
    # Lock the rows, not the shared identity: foreign memberships stay writable.
    prior_membership = memberships.select_for_update().filter(deleted=False).first()
    workspaces = WorkspaceMembership._base_manager.filter(
        user_id=user.pk, workspace__organization_id=organization.pk
    )
    prior_workspaces = list(
        workspaces.select_for_update()
        .filter(is_active=True, deleted=False)
        .values("workspace_id", "role", "level")
    )
    removed_at = timezone.now()
    updates = {
        "is_active": False,
        "deleted": True,
        "deleted_at": removed_at,
    }
    if mark_removed:
        updates["removed_at"] = removed_at
    if not memberships.update(**updates):
        # Truly legacy accounts can have no membership row at all. A tombstone
        # disables the legacy fallback without mutating the shared User row.
        membership, _ = OrganizationMembership._base_manager.get_or_create(
            user_id=user.pk,
            organization_id=organization.pk,
            defaults={"is_active": False},
        )
        OrganizationMembership._base_manager.filter(pk=membership.pk).update(
            **updates
        )

    workspaces.update(is_active=False)
    OrganizationInvite._base_manager.filter(
        target_email__iexact=user.email,
        organization_id=organization.pk,
        status=InviteStatus.PENDING,
    ).update(status=InviteStatus.CANCELLED)

    # log_audit respects mute_audit_signals and falls back to the ambient actor.
    # HTTP callers pass their DRF-authenticated actor explicitly (middleware may
    # have run before token authentication). No foreign grants are logged.
    log_audit(
        organization=organization,
        action="member.removed",
        scope="organization",
        target_id=user.pk,
        changes={
            "role": prior_membership.role
            if prior_membership
            else user.organization_role,
            "level": prior_membership.level if prior_membership else None,
        },
        actor=actor,
    )
    for grant in prior_workspaces:
        log_audit(
            organization=organization,
            action="member.removed",
            scope="workspace",
            target_id=user.pk,
            changes={
                "workspace_id": str(grant["workspace_id"]),
                "role": grant["role"],
                "level": grant["level"],
            },
            actor=actor,
        )

    revoke_organization_credentials(user.pk, organization.pk)


@primary_required
@transaction.atomic
def revoke_organization_credentials(user_id, organization_id, *, using="default"):
    """Revoke only this tenant's personal/gateway/service keys and scoped sessions."""
    if organization_id is None:
        raise ValueError("Tenant revocation requires an organization")
    revoke_personal_keys(user_id, organization_id, using=using)
    tokens = AuthToken._base_manager.using(using).filter(
        user_id=user_id,
        scoped_organization_id=organization_id,
        auth_type__in=(AuthTokenType.ACCESS, AuthTokenType.REFRESH),
        is_active=True,
    )
    keys = [f"{kind}_token_{pk}" for pk, kind in tokens.values_list("pk", "auth_type")]
    tokens.update(is_active=False)
    if keys:
        # Do not evict any other tenant's tokens, or unscoped interactive tokens
        # used to switch between tenants. Auth checks live memberships on each
        # request, so those tokens cannot regain access to the departed org.
        # DB token liveness also denies a stale/repopulated cache after commit.
        transaction.on_commit(lambda: cache.delete_many(keys), using=using, robust=True)


@primary_required
@transaction.atomic
def reactivate_organization_member(user, organization, *, actor=None):
    """Restore tenant grants, not a platform-disabled identity or old credentials."""
    from accounts.models.workspace import Workspace
    from tfc.constants.levels import Level

    type(organization).objects.select_for_update().get(pk=organization.pk)
    membership = (
        OrganizationMembership._base_manager.select_for_update()
        .filter(user=user, organization=organization)
        .order_by("deleted", "-created_at", "-pk")
        .first()
    )
    if membership is None:
        raise ValueError("No membership to reactivate")
    membership.is_active = True
    membership.deleted = False
    membership.deleted_at = None
    membership.removed_at = None
    membership.save(
        update_fields=["is_active", "deleted", "deleted_at", "removed_at"]
    )
    restored = WorkspaceMembership._base_manager.filter(
        user=user, workspace__organization=organization, is_active=False, deleted=False
    ).update(is_active=True, organization_membership=membership)
    if not restored:
        workspace = Workspace.objects.filter(
            organization=organization, is_default=True, is_active=True
        ).first()
        if workspace:
            level = Level.get_default_ws_level(membership.level_or_legacy)
            WorkspaceMembership._base_manager.update_or_create(
                workspace=workspace,
                user=user,
                defaults={
                    "role": Level.to_ws_role(level),
                    "level": level,
                    "organization_membership": membership,
                    "is_active": True,
                    "deleted": False,
                    "deleted_at": None,
                },
            )
    revoke_organization_credentials(user.pk, organization.pk)
    log_audit(
        organization=organization,
        action="member.reactivated",
        scope="organization",
        target_id=user.pk,
        changes={"role": membership.role, "level": membership.level},
        actor=actor,
    )
    return membership


@primary_required
@transaction.atomic
def reinvite_removed_member(
    user, organization, *, role, workspace, workspace_role, actor
):
    """Reissue a pending invitation without overwriting a shared person's password."""
    from accounts.utils import persist_pending_org_invite
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    type(organization).objects.select_for_update().get(pk=organization.pk)
    membership = (
        OrganizationMembership._base_manager.select_for_update()
        .filter(user=user, organization=organization, deleted=True)
        .order_by("-created_at", "-pk")
        .first()
    )
    if membership is None or user.is_active:
        raise ValueError("Re-invitation requires a removed pending identity")
    # An invitation is not an active grant. Its requested privilege lives on
    # OrganizationInvite until the person accepts it.
    membership.role = OrganizationRoles.MEMBER_VIEW_ONLY
    membership.level = Level.VIEWER
    membership.is_active = False
    membership.deleted = False
    membership.deleted_at = None
    membership.removed_at = None
    membership.invited_by = actor
    membership.save(
        update_fields=[
            "role",
            "level",
            "is_active",
            "deleted",
            "deleted_at",
            "removed_at",
            "invited_by",
        ]
    )
    persist_pending_org_invite(
        organization=organization,
        target_email=user.email,
        org_role=role,
        workspace_role=workspace_role,
        workspaces=[workspace],
        invited_by=actor,
    )
    return user


@primary_required
@transaction.atomic
def readmit_retained_member(
    user, organization, *, role, workspace, workspace_role, actor
):
    """Restore only the explicitly granted org/workspace, never reset a password.

    Called only for an already-active retained identity with a departed membership.
    The old credential rows remain revoked; membership restoration never revives
    old API keys, sessions, or unrelated workspace grants.
    """
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    type(organization).objects.select_for_update().get(pk=organization.pk)
    rows = OrganizationMembership._base_manager.select_for_update().filter(
        user=user, organization=organization
    )
    membership = rows.order_by("deleted", "-created_at", "-pk").first()
    if not user.is_active or membership is None:
        raise ValueError("Re-admission requires a retained active identity")
    if membership.is_active and not membership.deleted:
        raise ValueError("User is already a member of this organization")
    org_role = role or OrganizationRoles.MEMBER_VIEW_ONLY
    membership.role = org_role
    membership.level = Level.STRING_TO_LEVEL[org_role]
    membership.invited_by = actor
    membership.is_active = True
    membership.deleted = False
    membership.deleted_at = None
    membership.removed_at = None
    membership.save(
        update_fields=[
            "role",
            "level",
            "invited_by",
            "is_active",
            "deleted",
            "deleted_at",
            "removed_at",
        ]
    )
    WorkspaceMembership._base_manager.update_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "organization_membership": membership,
            "role": workspace_role,
            "level": Level.STRING_TO_LEVEL[workspace_role],
            "invited_by": actor,
            "is_active": True,
            "deleted": False,
            "deleted_at": None,
        },
    )
    log_audit(
        organization=organization,
        action="member.reactivated",
        scope="organization",
        target_id=user.pk,
        changes={"role": membership.role, "level": membership.level},
        actor=actor,
    )
    return user
