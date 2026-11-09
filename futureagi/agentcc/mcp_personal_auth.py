"""Individual authentication for the account-connect routes only.

Keep the platform's bearer sessions and person-owned user/MCP keys, but never
let authentication provision access or substitute an organization for a person.
Shared authentication and its other consumers are deliberately unchanged.
"""

from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.api_key_lookup import find_api_key
from accounts.authentication import APIKeyAuthentication
from accounts.models import OrgApiKey, User
from accounts.models.organization_membership import OrganizationMembership

PERSON_KEY_TYPES = frozenset({"user", "mcp"})


def is_individual(user):
    """A persisted, currently active person, not a service-shaped principal."""
    return (
        isinstance(user, User)
        and User.objects.filter(pk=user.pk, is_active=True).exists()
    )


def is_individual_member(user, org, *, allow_legacy=True):
    if org is None or not is_individual(user):
        return False
    # Include soft-deleted history: a removed member is not a legacy account.
    memberships = OrganizationMembership.all_objects.filter(
        user_id=user.pk, organization_id=org.pk
    )
    if memberships.filter(is_active=True, deleted=False).exists():
        return True
    return bool(
        allow_legacy
        and not memberships.exists()
        and User.objects.filter(pk=user.pk, organization_id=org.pk).exists()
    )


class PersonalConnectionAuthentication(APIKeyAuthentication):
    """Validate keys BEFORE the shared authenticator can select a person or
    resolve a workspace. Bearer validation retains the platform's revocation
    and SAML rules. Neither path creates workspaces or memberships here.
    """

    def authenticate(self, request):
        # Match the existing bearer precedence, including an empty Bearer value.
        bearer = (
            request.META.get("HTTP_AUTHORIZATION", "")
            .replace("Bearer", "")
            .replace(" ", "")
        )
        if bearer:
            return super().authenticate(request)
        api_key = request.headers.get("X-Api-Key")
        secret = request.headers.get("X-Secret-Key")
        if not api_key and not secret:
            return None
        # Secrets are sealed at rest: verify the pair with the platform's
        # constant-time lookup (accounts/api_key_lookup.py), as the shared
        # key authentication and the MCP service provisioning do. A missing,
        # empty, over-long or wrong secret finds no key.
        key = find_api_key(
            OrgApiKey.no_workspace_objects.select_related(
                "user", "organization"
            ).filter(enabled=True, deleted=False),
            api_key,
            secret,
        )
        if key is None or key.type not in PERSON_KEY_TYPES:
            raise AuthenticationFailed(
                "Sign in as an individual to manage an account.",
                code="individual_required",
            )
        user = key.user
        if not is_individual_member(user, key.organization, allow_legacy=False):
            raise PermissionDenied(
                "Not a member of this organization.", code="not_member"
            )
        # Do not invoke the shared key branch: it may substitute a system key's
        # organization for a human. This branch only ever uses the key's user.
        request.org_api_key = key
        self._bind_user_context(user)
        self._set_workspace_context(request, user)
        return user, None

    def _set_workspace_context(self, request, user):
        # Only the two own-account writes skip workspace DATA-write permission.
        # Shared mode and every other route retain the platform check. Invalid
        # selectors still go through normal request validation, never a write.
        from agentcc.views.mcp_connect import MCPConnectStartView, MCPDisconnectView
        from tfc.middleware.workspace_context import set_workspace_context

        view = (getattr(request, "parser_context", None) or {}).get("view")
        own_start = (
            isinstance(view, MCPConnectStartView)
            and request.method == "POST"
            and isinstance(request.data, dict)
            and request.data.get("mode", "user") == "user"
        )
        own_disconnect = False
        if isinstance(view, MCPDisconnectView) and request.method == "DELETE":
            from agentcc.serializers.mcp_connect import MCPDisconnectQuerySerializer

            selector = MCPDisconnectQuerySerializer(data=request.query_params)
            # Defer malformed selectors to the view's 400 response. Raising
            # here is wrapped as an invalid bearer token by platform auth.
            # They cannot reach a write because the view validates again.
            own_disconnect = (
                not selector.is_valid() or selector.validated_data["mode"] == "user"
            )
        if not (own_start or own_disconnect):
            return super()._set_workspace_context(request, user)
        organization = self._resolve_organization(request, user)
        workspace = self._get_requested_workspace(request, user, organization)
        if not workspace:
            workspace = self._get_scoped_default_workspace(user, organization)
        if workspace and not self._user_has_workspace_access(user, workspace):
            raise PermissionDenied("Access denied to this workspace")
        request.workspace = workspace
        request.organization = organization
        set_workspace_context(workspace=workspace, organization=organization, user=user)

    def _resolve_organization(self, request, user):
        if not is_individual(user):
            raise PermissionDenied(
                "An individual is required.", code="individual_required"
            )
        key = getattr(request, "org_api_key", None)
        # Recheck the key actually selected by authentication, not just preflight.
        if key is not None and (
            key.type not in PERSON_KEY_TYPES or key.user_id != user.pk
        ):
            raise PermissionDenied(
                "An individual is required.", code="individual_required"
            )
        org = super()._resolve_organization(request, user)
        requested = request.headers.get("X-Organization-Id") or request.GET.get(
            "organization_id"
        )
        if requested and (org is None or str(org.pk) != requested):
            raise PermissionDenied(
                "Not a member of this organization.", code="not_member"
            )
        if not is_individual_member(user, org, allow_legacy=key is None):
            raise PermissionDenied(
                "Not a member of this organization.", code="not_member"
            )
        return org

    def _get_or_create_default_workspace(self, user, organization):
        # Keep existing preference and workspace-role checks, without onboarding.
        return self._get_scoped_default_workspace(user, organization)


class IsIndividualPrincipal(BasePermission):
    message = "Sign in as an individual to manage an account."
    code = "individual_required"

    def has_permission(self, request, view):
        key = getattr(request, "org_api_key", None)
        return is_individual(request.user) and (
            key is None
            or (key.type in PERSON_KEY_TYPES and key.user_id == request.user.pk)
        )
