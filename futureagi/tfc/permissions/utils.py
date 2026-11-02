"""
RBAC utility functions.

Single source of truth for permission resolution.
All permission checks should go through these functions.
"""

from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
)

VIEWER_READ_ONLY_CODE = "viewer_read_only"


def viewer_denied_message(action=None):
    action = action or "make changes in this workspace"
    return f"Viewers can't {action}. Ask a workspace admin for the Member role."


def is_read_only_user(user=None, workspace=None):
    user = user or get_current_user()
    if not user or not user.is_authenticated:
        return False
    workspace = workspace or get_current_workspace()
    if workspace is not None:
        return not user.can_write_to_workspace(workspace)
    membership = get_org_membership(user)
    return membership is None or membership.level_or_legacy < Level.MEMBER


def cached_is_read_only_user(context):
    if "read_only_user" not in context:
        context["read_only_user"] = is_read_only_user()
    return context["read_only_user"]


def get_org_membership(user):
    """
    Get the user's OrganizationMembership for the current organization.

    Returns the membership instance or None.
    Uses thread-local organization context (set by authentication).
    """
    if not user or not user.is_authenticated:
        return None

    org = get_current_organization()
    if not org:
        # Fallback to user's organization
        org = getattr(user, "organization", None)
    if not org:
        return None

    from accounts.models.organization_membership import OrganizationMembership

    try:
        return OrganizationMembership.objects.get(
            user=user,
            organization=org,
            is_active=True,
        )
    except OrganizationMembership.DoesNotExist:
        return None


def get_effective_workspace_level(user, workspace_id):
    """
    Resolve the effective workspace level for a user.

    effective_level = max(org_level, ws_level)

    - Org Admins+ automatically get WORKSPACE_ADMIN in every workspace.
    - Returns the integer level, or None if no access.
    """
    org_membership = get_org_membership(user)
    if org_membership is None:
        return None

    org_level = org_membership.level_or_legacy

    # Org Admin/Owner auto-gets workspace admin
    if org_level >= Level.ADMIN:
        return max(org_level, Level.WORKSPACE_ADMIN)

    from accounts.models.workspace import WorkspaceMembership

    try:
        ws_membership = WorkspaceMembership.objects.get(
            workspace_id=workspace_id,
            user=user,
            is_active=True,
        )
    except WorkspaceMembership.DoesNotExist:
        return None

    ws_level = ws_membership.level_or_legacy

    return max(org_level, ws_level)


def can_invite_at_level(actor_level, target_level):
    """
    Check if an actor can invite someone at a given level.

    Rule: target level must be at or below actor level.
    Exception: Owner (15) can invite another Owner (15).
    """
    if actor_level >= Level.OWNER:
        return target_level <= Level.OWNER
    return target_level <= actor_level
