import hmac

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from agentcc.admin_token import insecure_admin_token_allowed, usable_admin_token
from agentcc.services.gateway_client import AGENTCC_ADMIN_TOKEN
from tfc.constants.levels import Level

GATEWAY_ADMIN_MESSAGE = (
    "Only an organization Owner or Admin can change the gateway's configuration."
)

AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN = insecure_admin_token_allowed()


class IsAdminToken(BasePermission):
    """Authenticate requests using the gateway admin token.

    A token the gateway's policy refuses (empty, short, published, a
    placeholder) counts as no token, so every request is denied.
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


def is_gateway_admin(request) -> bool:
    """Whether the user is an Owner or Admin of the organization the request
    acts on (``request.organization``), not of their home organization."""
    user = getattr(request, "user", None)
    organization = getattr(request, "organization", None)
    if user is None or not user.is_authenticated or organization is None:
        return False
    level = user.get_membership_level(organization)
    return level is not None and level >= Level.ADMIN


def require_gateway_admin(request) -> None:
    """Raise 403 unless ``is_gateway_admin(request)``; for a view that checks
    inside an action rather than through ``permission_classes``."""
    if not is_gateway_admin(request):
        raise PermissionDenied(GATEWAY_ADMIN_MESSAGE)


class IsOrgGatewayAdmin(BasePermission):
    """Org Owner or Admin: may change what the gateway spends, routes or
    enforces. The console shows these controls to the same roles."""

    message = GATEWAY_ADMIN_MESSAGE

    def has_permission(self, request, view):
        return is_gateway_admin(request)


class IsStaffOrAdminToken(BasePermission):
    """A Future AGI staff user, or a caller holding the gateway admin token.

    The token is read from the ``Authorization`` header, which the default
    authentication class reads as a user token first: a view that takes the
    admin token sets ``authentication_classes`` that leave the header alone.
    """

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_staff:
            return True
        return IsAdminToken().has_permission(request, view)


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
