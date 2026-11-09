"""Personal-key self-service is separate from gateway configuration authority."""

from uuid import UUID

from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import BasePermission

from accounts.authentication import APIKeyAuthentication
from agentcc.services.gateway_membership import live_gateway_memberships
from tfc.constants.levels import Level


def key_membership(request):
    user = getattr(request, "user", None)
    org = getattr(request, "organization", None)
    if user is None or not user.is_authenticated or org is None:
        return None
    return (
        live_gateway_memberships()
        .filter(user_id=user.pk, organization_id=org.pk)
        .first()
    )


def can_manage_gateway_keys(request):
    membership = key_membership(request)
    return membership is not None and membership.level_or_legacy >= Level.ADMIN


def has_personal_key_session(request):
    """Use authenticated provenance, not an Authorization header or user alone.

    On the pre-L1 gateway line APIKeyAuthentication returns a nonempty auth
    string ONLY after decode_token has validated a live browser access token.
    API-key/Basic authentication returns None. Restrict to that exact class:
    a service/grant subclass must not inherit this admission. L1's separate
    RequiresPersonalKeySession gate can compose with this without widening it.
    """
    if (
        getattr(request, "org_api_key", None) is not None
        or getattr(request, "auth_scope", None) is not None
    ):
        return False
    authenticator = getattr(request, "successful_authenticator", None)
    if type(authenticator) is SessionAuthentication:
        return True  # DRF already enforced CSRF on this session.
    return (
        type(authenticator) is APIKeyAuthentication
        and isinstance(request.auth, str)
        and bool(request.auth)
    )


def explicit_scope_matches(request):
    """Do not mint in a fallback org/workspace after an explicit scope was refused.

    The legacy account authenticator can fall back to a default for invalid
    headers. Keep its general behavior untouched, but fail closed on key APIs.
    Body user/org/workspace fields are never authority and are ignored.
    """
    for field, header in (
        ("organization", "X-Organization-Id"),
        ("workspace", "X-Workspace-Id"),
    ):
        resolved = getattr(request, field, None)
        for supplied in (
            request.headers.get(header),
            request.query_params.get(f"{field}_id"),
        ):
            if not supplied:
                continue
            try:
                if resolved is None or UUID(str(supplied)) != resolved.pk:
                    return False
            except (ValueError, TypeError, AttributeError):
                return False
    return True


def has_workspace_write_access(request):
    """Mirror the authenticator's data-write boundary: no landing means no write.

    Org-scoped personal-key self-service is exempted by exact action/method
    markers and PersonalGatewayKeyAccess, not by a global role or missing scope.
    Bound sessions also require a landing in the authenticator.
    """
    workspace = getattr(request, "workspace", None)
    return workspace is not None and request.user.can_write_to_workspace(workspace)


class PersonalGatewayKeyAccess(BasePermission):
    message = "Active organization membership in the requested scope is required."

    def has_permission(self, request, view):
        if not explicit_scope_matches(request) or key_membership(request) is None:
            return False
        if getattr(view, "action", None) == "create" and not has_personal_key_session(
            request
        ):
            self.message = "Sign in interactively to create your personal API key."
            return False
        # Only exact self-service actions from a proven personal session can
        # mutate org-wide keys without workspace-write authority. Ownership is
        # enforced by the queryset/object check, never by a client contact label.
        personal_write = (getattr(view, "action", None), request.method) in {
            ("create", "POST"),
            ("revoke", "POST"),
            ("destroy", "DELETE"),
        } and has_personal_key_session(request)
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and not personal_write
            and not has_workspace_write_access(request)
        ):
            self.message = "Write access denied to this workspace"
            return False
        return True

    def has_object_permission(self, request, view, obj):
        # Admin-only edits and sync have an additional permission on the view.
        return obj.user_id == request.user.pk or can_manage_gateway_keys(request)
