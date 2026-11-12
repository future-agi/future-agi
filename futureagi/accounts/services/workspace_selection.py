"""Explicit selectors authorize in SQL, before workspace existence is observed."""

from django.db.models import Q

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level


def accessible_workspace_selections(user):
    """Active destinations matching User.can_access_workspace in one query.

    Both organization membership and (global level OR workspace membership)
    belong inside the selector lookup. Never fetch a destination and then branch
    on its existence to decide whether to query its membership. Integer levels
    override legacy roles, exactly as OrganizationMembership.level_or_legacy.
    """
    organizations = OrganizationMembership.no_workspace_objects.filter(
        user=user, is_active=True
    )
    global_organizations = organizations.filter(
        Q(level__gte=Level.ADMIN)
        | Q(
            level__isnull=True,
            role__in=[
                role
                for role, level in Level.STRING_TO_LEVEL.items()
                if level >= Level.ADMIN
            ],
        )
    )
    joined = WorkspaceMembership.no_workspace_objects.filter(user=user, is_active=True)
    return Workspace.no_workspace_objects.filter(
        Q(organization_id__in=global_organizations.values("organization_id"))
        | Q(pk__in=joined.values("workspace_id")),
        organization_id__in=organizations.values("organization_id"),
        is_active=True,
    ).select_related("organization")
