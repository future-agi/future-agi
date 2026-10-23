import hmac

from rest_framework.permissions import BasePermission

from agentcc.admin_token import insecure_admin_token_allowed, usable_admin_token
from agentcc.services.gateway_client import AGENTCC_ADMIN_TOKEN
from tfc.constants.levels import Level

AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN = insecure_admin_token_allowed()

GATEWAY_ADMIN_MESSAGE = (
    "Only an organization Owner or Admin can change the gateway's configuration."
)


class IsAdminToken(BasePermission):
    """Authenticate requests using the gateway admin token.

    A token the gateway's policy refuses (empty, short, published, common, a
    placeholder, too few distinct characters) counts as no token, so every
    request is denied. Only the dev-only opt-in
    (``AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN``) accepts such a token, and never an
    empty one.
    """

    def has_permission(self, request, view):
        expected = usable_admin_token(
            AGENTCC_ADMIN_TOKEN, AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN
        )
        if not expected:
            return False
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            return bool(token) and hmac.compare_digest(token, expected)
        return False


# The role check (GatewayAdminActionsMixin): the gateway view's config writes
# and the write actions of the org-config version, provider-credential,
# routing-policy, guardrail-policy and shadow-experiment views are an Owner's
# or an Admin's of the organization the request acts on; reads are open to
# every member.


def is_gateway_admin(request) -> bool:
    """Whether the user is an Owner or Admin of the organization the request
    acts on (``request.organization``), not of their home organization."""
    user = getattr(request, "user", None)
    organization = getattr(request, "organization", None)
    if user is None or not user.is_authenticated or organization is None:
        return False
    level = user.get_membership_level(organization)
    return level is not None and level >= Level.ADMIN


class IsOrgGatewayAdmin(BasePermission):
    """Org Owner or Admin: may change what the gateway enforces."""

    message = GATEWAY_ADMIN_MESSAGE

    def has_permission(self, request, view):
        return is_gateway_admin(request)


class GatewayAdminActionsMixin:
    """Adds ``IsOrgGatewayAdmin`` to the ViewSet actions named in
    ``gateway_admin_actions``; every other action keeps the view's
    ``permission_classes``."""

    gateway_admin_actions: frozenset[str] = frozenset()

    def get_permissions(self):
        permissions = super().get_permissions()
        # Schema generation builds views without an action.
        if getattr(self, "action", None) in self.gateway_admin_actions:
            permissions.append(IsOrgGatewayAdmin())
        return permissions
