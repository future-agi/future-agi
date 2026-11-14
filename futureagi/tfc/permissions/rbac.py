"""
Composable DRF permission classes for RBAC.

Usage in views:
    permission_classes = [IsAuthenticated, IsOrganizationMember]
    permission_classes = [IsAuthenticated, IsOrganizationAdmin]
    permission_classes = [IsAuthenticated, IsOrganizationAdminOrWorkspaceAdmin]
"""

from rest_framework.permissions import BasePermission
from rest_framework.serializers import UUIDField

from tfc.constants.levels import Level
from tfc.permissions.utils import (
    get_effective_workspace_level,
    get_org_membership,
)


class IsOrganizationMember(BasePermission):
    """User has any active membership in the organization."""

    message = "You must be a member of this organization."

    def has_permission(self, request, view):
        membership = get_org_membership(request.user)
        return membership is not None


class IsOrganizationAdmin(BasePermission):
    """User is Admin or Owner in the organization (level >= ADMIN)."""

    message = "Organization admin access required."

    def has_permission(self, request, view):
        membership = get_org_membership(request.user)
        if membership is None:
            return False
        return membership.level_or_legacy >= Level.ADMIN


class IsOrganizationOwner(BasePermission):
    """User is Owner in the organization (level >= OWNER)."""

    message = "Organization owner access required."

    def has_permission(self, request, view):
        membership = get_org_membership(request.user)
        if membership is None:
            return False
        return membership.level_or_legacy >= Level.OWNER


class IsOrganizationAdminOrWorkspaceAdmin(BasePermission):
    """
    User is either:
    - Org Admin+ (level >= ADMIN), OR
    - Workspace Admin in the target workspace

    The target workspace is resolved from:
    1. request.data.get("workspace_id")
    2. request.headers.get("X-Workspace-Id")
    3. request.workspace
    """

    message = "Organization admin or workspace admin access required."

    def has_permission(self, request, view):
        # Check org-level first
        membership = get_org_membership(request.user)
        if membership is not None and membership.level_or_legacy >= Level.ADMIN:
            return True

        # Fallback: check workspace-level
        workspace_id = (
            request.data.get("workspace_id")
            or request.headers.get("X-Workspace-Id")
            or _get_request_workspace_id(request)
        )
        if not workspace_id:
            return False

        effective = get_effective_workspace_level(request.user, workspace_id)
        return effective is not None and effective >= Level.WORKSPACE_ADMIN


class CanManageTargetUser(BasePermission):
    """
    Actor's org level must be strictly above the target user's org level.
    Exception: Owner can manage other Owners.

    Expects `target_user_id` in request.data or view.kwargs.
    """

    message = "You cannot manage a user at or above your own level."

    def has_permission(self, request, view):
        actor_membership = get_org_membership(request.user)
        if actor_membership is None:
            return False

        target_user_id = request.data.get("user_id") or view.kwargs.get("user_id")
        if not target_user_id:
            return False

        # Permissions run before the view's serializer. Reject malformed IDs
        # as DRF validation errors rather than letting the ORM raise a 500.
        target_user_id = UUIDField().run_validation(target_user_id)

        from accounts.models.organization_membership import OrganizationMembership

        try:
            target_membership = OrganizationMembership.all_objects.get(
                user_id=target_user_id,
                organization=actor_membership.organization,
            )
        except OrganizationMembership.DoesNotExist:
            # Let the view handle non-existent users with a proper 400 error
            return True

        actor_level = actor_membership.level_or_legacy
        target_level = target_membership.level_or_legacy

        # Owner can manage other Owners
        if actor_level >= Level.OWNER:
            return True

        # Everyone else: strictly above
        return actor_level > target_level


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_request_workspace_id(request):
    ws = getattr(request, "workspace", None)
    return str(ws.id) if ws else None
