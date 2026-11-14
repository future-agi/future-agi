"""Permissions for organization-scoped SAML configuration."""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from tfc.permissions.rbac import IsOrganizationAdmin, IsOrganizationMember


class SAMLConfigPermission(BasePermission):
    """Members may read their IdP configuration; admins may change it."""

    def has_permission(self, request, view):
        permission = (
            IsOrganizationMember()
            if request.method in SAFE_METHODS
            else IsOrganizationAdmin()
        )
        return permission.has_permission(request, view)
