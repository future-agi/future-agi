"""One access-checked landing policy for HTTP, SAML, invites and sockets."""

from uuid import UUID

from django.db.models import F, OuterRef, Subquery

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership


def resolve_user_workspace(
    user: User | None, organization: Organization | None
) -> Workspace | None:
    """Return the landing workspace in ``organization``, or ``None``.

    Eligible workspaces are exactly the active workspaces of ``organization``
    that ``User.can_access_workspace`` allows: an active WorkspaceMembership,
    or global workspace access through the org membership level (Owner and
    Admin need no WorkspaceMembership rows). Every candidate is accepted only
    through that helper, so this module never restates the access rule.

    Precedence over the eligible set: persisted per-org preference, legacy
    current preference, the configured ``defaultWorkspaceId`` (the default
    used for SAML/scoped sessions as well), the org's ``is_default``
    workspace, then a deterministic order: the user's earliest active
    membership first (workspaces without one last), with workspace UUID as
    the tiebreak. Workspace creation time is not an additional ordering key.

    Token-cached user.config is deliberately not authoritative. This is a read
    policy, never a membership grant: callers retain their explicit
    provisioning flows and can call again after creating a membership.
    """
    if user is None or organization is None:
        return None
    # can_access_workspace denies every workspace of an org the user cannot
    # access, so asking the same helper once up front only saves queries.
    if not user.can_access_organization(organization):
        return None
    workspaces = Workspace.no_workspace_objects.filter(
        organization=organization, is_active=True
    ).select_related("organization")

    def accessible(workspace: Workspace | None) -> Workspace | None:
        if workspace is not None and user.can_access_workspace(workspace):
            return workspace
        return None

    config = User.objects.filter(pk=user.pk).values_list("config", flat=True).first()
    if not isinstance(config, dict):
        config = {}
    org_map = config.get("orgWorkspaceMap")
    org_preference = (
        org_map.get(str(organization.pk)) if isinstance(org_map, dict) else None
    )
    for candidate in (
        org_preference,
        config.get("currentWorkspaceId"),
        config.get("defaultWorkspaceId"),
    ):
        try:
            workspace_id = UUID(str(candidate))
        except (ValueError, TypeError, AttributeError):
            continue
        workspace = accessible(workspaces.filter(pk=workspace_id).first())
        if workspace is not None:
            return workspace

    workspace = accessible(workspaces.filter(is_default=True).first())
    if workspace is not None:
        return workspace

    joined_at = (
        WorkspaceMembership.no_workspace_objects.filter(
            user=user, workspace=OuterRef("pk"), is_active=True
        )
        .order_by("created_at")
        .values("created_at")[:1]
    )
    ordered = workspaces.annotate(joined_at=Subquery(joined_at)).order_by(
        F("joined_at").asc(nulls_last=True), "id"
    )
    for workspace in ordered.iterator():
        if accessible(workspace) is not None:
            return workspace
    return None
