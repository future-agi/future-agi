"""Human organization authority only; no staff, workspace or SDK-key bypass."""

import os
from importlib import import_module
from importlib.util import find_spec
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.authentication import APIKeyAuthentication, decode_token
from accounts.models import User
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.ee_loader import is_cloud_env
from tfc.middleware.workspace_context import set_workspace_context
from tfc.utils.install_setup import current_setup


class HostUpdateAuthentication(APIKeyAuthentication):
    """Keep bearer decode/revocation/SAML; do not auto-provision workspaces.

    The normal APIKeyAuthentication default-workspace fallback can write a
    membership. Installation reporting needs only the existing org selection.
    Normal token liveness/cache maintenance remains owned by authentication.
    """

    def authenticate(self, request):
        if "X-Api-Key" in request.headers or "X-Secret-Key" in request.headers:
            raise PermissionDenied("Human authentication required.")
        authorization = request.META.get("HTTP_AUTHORIZATION", "").split()
        if not authorization:
            return None
        if len(authorization) != 2 or authorization[0].lower() != "bearer":
            raise AuthenticationFailed("Authentication required.")
        # Reuse the real revocation/refresh/SAML decoder, not the legacy
        # authenticate wrapper which prints raw exception tracebacks. The view
        # maps decoder failures to closed errors without exposing their text.
        user, token, scope = decode_token(authorization[1])
        if not user.is_active:
            raise AuthenticationFailed("Authentication required.")
        request.auth_scope = scope
        self._bind_user_context(user, scope)
        self._set_workspace_context(request, user)
        return user, token

    def _set_workspace_context(self, request, user):
        explicit = request.headers.get("X-Organization-Id")
        if explicit is not None:
            try:
                UUID(explicit)
            except (ValueError, TypeError, AttributeError):
                raise PermissionDenied(
                    "Organization selection is not authorized."
                ) from None
        scope = getattr(request, "auth_scope", None)
        # Query selectors do not select an org for this endpoint. Retain the
        # shared SAML conflict check, then let the view reject all queries only
        # after organization authorization. A malformed query UUID must not
        # change the authentication context or be treated as a bad bearer token.
        selection = SimpleNamespace(
            headers=request.headers,
            GET=request.GET if scope is not None else {},
            auth_scope=scope,
        )
        request.organization = self._resolve_organization(selection, user)
        request.workspace = None
        set_workspace_context(
            workspace=None, organization=request.organization, user=user
        )


class CanReadHostUpdateStatus(BasePermission):
    """Require an active authenticated human with an Owner/Admin org role.

    Use the application's real lifecycle predicates: User.is_active, an
    existing selected Organization, and an active, nondeleted organization
    membership. Organization has no is_active/deleted fields and User has no
    deleted field. Staff status, workspace roles and API keys grant no access.
    A numeric organization level is authoritative; only null levels fall back
    to the exact legacy Owner/Admin strings. Reporting never creates membership.
    """

    message = "Host update status is not authorized."

    def has_permission(self, request, view):
        user = request.user
        if (
            not user
            or not user.is_authenticated
            or not user.is_active
            or getattr(request, "org_api_key", None)
            or "X-Api-Key" in request.headers
            or "X-Secret-Key" in request.headers
        ):
            return False
        # Tokens can cache a user. Recheck active state rather than trusting it.
        if not User.objects.filter(pk=user.pk, is_active=True).exists():
            return False
        organization = getattr(request, "organization", None)
        if (
            organization is None
            or not Organization.objects.filter(pk=organization.pk).exists()
        ):
            return False
        # Organization currently has no is_active/deleted columns. Existence and
        # active, nondeleted membership are its supported lifecycle predicates.
        # An inaccessible explicit header must never inherit auth's fallback org.
        explicit = request.headers.get("X-Organization-Id")
        if explicit is not None and explicit != str(organization.pk):
            return False
        scope = getattr(request, "auth_scope", None)
        if scope is not None and str(scope.org_id) != str(organization.pk):
            return False
        if explicit is None and scope is None:
            config = (
                User.objects.filter(pk=user.pk).values_list("config", flat=True).first()
                or {}
            )
            selected = config.get("currentOrganizationId") or config.get(
                "selected_organization_id"
            )
            if selected and str(selected) != str(organization.pk):
                return False
        membership = OrganizationMembership.no_workspace_objects.filter(
            user_id=user.pk,
            organization_id=organization.pk,
            is_active=True,
            deleted=False,
        ).first()
        if membership is None:
            return False
        if membership.level is not None:
            return membership.level in (Level.ADMIN, Level.OWNER)
        return membership.role in (OrganizationRoles.OWNER, OrganizationRoles.ADMIN)


def supported_installation():
    """Return topology only for positively identified OSS Compose runtime.

    Never use is_oss(): its exception-to-OSS fallback is inappropriate here.
    A configured cloud region or license is denied before mode resolution.
    """
    try:
        if is_cloud_env(settings.CLOUD_DEPLOYMENT) or settings.EE_LICENSE_KEY:
            return None
        if "KUBERNETES_SERVICE_HOST" in os.environ:
            return None
        topology = current_setup()
        if topology not in ("standalone", "distributed"):
            return None
        if find_spec("ee") is not None:
            deployment = import_module("ee.usage.deployment")
            if deployment.DeploymentMode.get_mode() != "oss":
                return None
        return topology
    except Exception:
        # Broken optional imports/configuration must not expose the mount or
        # prevent normal routes from loading. Never render exception details.
        return None
