"""Authentication-strength permissions used by account-management endpoints."""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission


class HasKeyOrganizationMembership(BasePermission):
    """Key management requires current membership, not a legacy user role."""

    message = "An active organization membership is required."

    def has_permission(self, request, view):
        from accounts.services.api_keys import key_org_level, request_key_organization

        return (
            key_org_level(request.user, request_key_organization(request)) is not None
        )


class RequiresIndependentAuth(BasePermission):
    """Disallow credentials bound to a SAML organization from widening access."""

    message = "Sign in with your password or OAuth to manage authentication factors or connected apps"

    def has_permission(self, request, view):
        if getattr(request, "auth_scope", None) is not None:
            raise PermissionDenied(self.message, code="saml_scoped_session")
        return True
