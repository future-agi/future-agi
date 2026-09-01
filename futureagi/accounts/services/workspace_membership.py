"""Workspace-membership write service.

Single create path for ``WorkspaceMembership`` so the ``organization_membership``
FK is *always* resolved and set. Scattered ``WorkspaceMembership.objects.create``
sites each hand-set (and several forgot) the FK, which is how the column drifted
to NULL and the workspace Members page lost its Org Role chip. Route every
create through ``create_workspace_membership`` and the invariant holds in one
place (a service, not ``save()`` which fires implicitly and can't be tested, nor
a manager which call sites bypass) — see
coding-standards/03-architecture-and-layers.
"""

import structlog

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership

logger = structlog.get_logger(__name__)


def resolve_org_membership(user, organization):
    """Return the active ``OrganizationMembership`` for ``(user, organization)``.

    Deterministic: filters to ``is_active=True`` (never attaches a soft-deleted /
    cancelled membership) and orders newest-first so a stable row is chosen when
    more than one survives. ``None`` when the user has no active org membership —
    the only case where the workspace FK legitimately stays NULL.
    """
    if user is None or organization is None:
        return None
    return (
        OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=organization, is_active=True
        )
        .order_by("-created_at", "-id")
        .first()
    )


def ensure_org_membership(user, organization, *, invited_by=None):
    """Return an active ``OrganizationMembership``, creating a minimal one if absent.

    Enforces the invariant "a workspace member belongs to the org" at the single
    create path, healing the drift that left workspace members with no
    ``OrganizationMembership`` row (TH-6156).

    Fail-closed on removal: if a row already exists but is *inactive* (the user
    was deliberately removed from the org), it is NOT resurrected — ``None`` is
    returned and the caller leaves the FK NULL. New rows are created at the
    minimal ``Viewer`` level, which grants org membership without global
    workspace access (that requires ``>= ADMIN``).
    """
    if user is None or organization is None:
        return None

    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    # The (user, organization) unique constraint (WHERE deleted=False) guarantees
    # at most one non-deleted row, so get_or_create resolves to exactly that row.
    # get_or_create also closes the race where two concurrent invite flows both
    # find no row: the loser's INSERT hits the unique constraint, Django catches
    # the IntegrityError and re-fetches the winner's row instead of raising.
    membership, _created = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.MEMBER_VIEW_ONLY,
            "level": Level.VIEWER,
            "invited_by": invited_by,
            "is_active": True,
        },
    )
    # Active → use it; inactive → the user was deliberately removed from the org,
    # so respect the removal (fail-closed) and leave the workspace FK NULL.
    return membership if membership.is_active else None


def create_workspace_membership(
    *,
    workspace,
    user,
    role,
    level=None,
    invited_by=None,
    granted_by=None,
    manager=None,
    **extra,
):
    """Create a ``WorkspaceMembership`` with ``organization_membership`` resolved.

    The single create path. Callers may pass ``organization_membership`` in
    ``extra`` to override resolution (rare); otherwise it is resolved from the
    workspace's organization. ``manager`` defaults to ``no_workspace_objects`` to
    match the existing create sites.
    """
    manager = manager or WorkspaceMembership.no_workspace_objects
    # Resolve lazily: only query when the caller didn't already pass the FK.
    # ``setdefault`` would evaluate ``resolve_org_membership`` unconditionally
    # (and fire a wasted query per call — N times inside bulk-invite loops).
    if "organization_membership" not in extra:
        org_membership = resolve_org_membership(user, workspace.organization)
        if org_membership is None:
            # No active org membership — create the minimal one so a workspace
            # member always belongs to the org (TH-6156). Returns None only when
            # an inactive (deliberately removed) membership exists.
            org_membership = ensure_org_membership(
                user, workspace.organization, invited_by=invited_by
            )
        extra["organization_membership"] = org_membership
    if extra["organization_membership"] is None:
        # Visibility into the only path that can still produce a NULL FK (user
        # with a deactivated org membership we won't resurrect) — distinct from
        # the old silent drift.
        logger.warning(
            "workspace_membership_created_without_org_fk",
            workspace_id=str(getattr(workspace, "id", "")),
            user_id=str(getattr(user, "id", "")),
        )
    fields = {"workspace": workspace, "user": user, "role": role, **extra}
    if level is not None:
        fields["level"] = level
    if invited_by is not None:
        fields["invited_by"] = invited_by
    if granted_by is not None:
        fields["granted_by"] = granted_by
    return manager.create(**fields)
