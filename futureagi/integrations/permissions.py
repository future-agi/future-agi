"""Who may add, change and remove an integration.

The console offers Add Integration to an organization Owner or Admin and to an
Admin of the current workspace (``RolePermission.API_KEYS`` create in
``frontend/src/utils/rolePermissionMapping.js``). The API grants the same
roles, so every control the console shows does what it says, and every other
member keeps read access.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.utils import get_request_organization
from tfc.constants.levels import Level

INTEGRATION_ADMIN_MESSAGE = (
    "Only an organization Owner or Admin, or an Admin of this workspace, "
    "can add, change or remove an integration."
)


def can_manage_integrations(user, organization, workspace=None) -> bool:
    """Whether ``user`` may add, change or remove ``organization``'s
    integrations in ``workspace``.

    The organization is passed in, never read from the request's thread-local
    context, so the Slack OAuth callback (which has no session) decides with
    the organization its signed state names.
    """
    if (
        user is None
        or not getattr(user, "is_authenticated", False)
        or not getattr(user, "is_active", False)
        or organization is None
    ):
        return False
    level = user.get_membership_level(organization)
    if level is None:
        return False
    if level >= Level.ADMIN:
        return True
    if workspace is None or workspace.organization_id != organization.id:
        return False

    from accounts.models.workspace import WorkspaceMembership

    membership = WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace, is_active=True
    ).first()
    return (
        membership is not None and membership.level_or_legacy >= Level.WORKSPACE_ADMIN
    )


class CanManageIntegrations(BasePermission):
    """Reads stay open to every member; the writes a view names in
    ``integration_admin_actions`` (or, for a view without actions, every
    unsafe method) need ``can_manage_integrations``."""

    message = INTEGRATION_ADMIN_MESSAGE

    def has_permission(self, request, view):
        guarded = getattr(view, "integration_admin_actions", None)
        if guarded is not None:
            if getattr(view, "action", None) not in guarded:
                return True
        elif request.method in SAFE_METHODS:
            return True
        return can_manage_integrations(
            request.user,
            get_request_organization(request),
            getattr(request, "workspace", None),
        )
