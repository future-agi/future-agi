"""Workspace access shared by metric recipes and their alert consumers."""

from rest_framework.permissions import BasePermission

from tfc.constants.levels import Level
from tfc.permissions.utils import get_effective_workspace_level, get_org_membership
from tracer.utils.workspace_scope import get_request_organization


class MetricWorkspacePermission(BasePermission):
    message = "An active workspace membership with the required access is needed."

    def has_permission(self, request, view):
        workspace = getattr(request, "workspace", None)
        organization = get_request_organization(request)
        if (
            not workspace
            or not request.user.is_active
            or workspace.deleted
            or not workspace.is_active
            or not organization
            or workspace.organization_id != organization.id
        ):
            return False
        membership = get_org_membership(request.user)
        if membership is None or membership.organization_id != organization.id:
            return False
        level = get_effective_workspace_level(request.user, workspace.id)
        required = (
            Level.WORKSPACE_VIEWER
            if request.method in {"GET", "HEAD", "OPTIONS"}
            else Level.WORKSPACE_MEMBER
        )
        return level is not None and level >= required


class MonitorWritePermission(MetricWorkspacePermission):
    """Do not let indirect monitor mutations bypass the metric member boundary.

    Existing monitor reads retain their canonical scope. All writes, including
    bulk mute/duplicate and log resolution, require workspace member access.
    """

    def has_permission(self, request, view):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return True
        return super().has_permission(request, view)
