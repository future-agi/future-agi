"""Authentication-strength permissions used by account-management endpoints."""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission


class RequiresIndependentAuth(BasePermission):
    """Disallow credentials bound to a SAML organization from widening access."""

    message = "Sign in with your password or OAuth to manage authentication factors or connected apps"

    def has_permission(self, request, view):
        if getattr(request, "auth_scope", None) is not None:
            raise PermissionDenied(self.message, code="saml_scoped_session")
        return True


class RequiresPersonalKeySession(BasePermission):
    def has_permission(self, request, view):
        from accounts.api_key_policy import require_personal_key_session

        require_personal_key_session(request)
        return True
