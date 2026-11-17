"""Who may do what (SPEC §5.1, §10). Every permission raises a HubError with its own code."""

from __future__ import annotations

from django.contrib.auth.models import Permission
from rest_framework.permissions import SAFE_METHODS, AllowAny, BasePermission

from omega_hub import conf
from omega_hub.errors import HubError
from tfc.constants.levels import Level

MAINTAINER_CODENAME = "hub_maintainer"
MAINTAINER_PERM = f"omega_hub.{MAINTAINER_CODENAME}"


def maintainer_permission() -> Permission:
    return Permission.objects.get(content_type__app_label="omega_hub", codename=MAINTAINER_CODENAME)


def has_explicit_grant(user) -> bool:
    """The permission granted to the user or one of its groups. Superuser status does not count."""
    if not getattr(user, "pk", None):
        return False
    perms = Permission.objects.filter(content_type__app_label="omega_hub", codename=MAINTAINER_CODENAME)
    return perms.filter(user=user).exists() or perms.filter(group__user=user).exists()


def org_level(user, organization_id) -> int:
    from accounts.models.organization_membership import OrganizationMembership

    if not organization_id or not getattr(user, "pk", None):
        return 0
    membership = OrganizationMembership.no_workspace_objects.filter(
        user=user, organization_id=organization_id, is_active=True
    ).first()
    return membership.level_or_legacy if membership else 0


def is_maintainer(user) -> bool:
    """Both hold at request time: the explicit grant, and Admin or Owner of the maintainer org."""
    if not getattr(user, "is_active", False) or not getattr(user, "pk", None):
        return False
    org_id = conf.maintainer_org_id()
    if not org_id:
        return False
    return has_explicit_grant(user) and org_level(user, org_id) >= Level.ADMIN


def can_act_for_publisher(user, publisher) -> bool:
    if not getattr(user, "pk", None):
        return False
    if publisher.kind == "user":
        return publisher.owner_user_id == user.pk
    return org_level(user, publisher.owner_org_id) >= Level.ADMIN


def is_hub_token(request) -> bool:
    from omega_hub.models import HubToken

    return isinstance(getattr(request, "auth", None), HubToken)


def uses_org_api_key(request) -> bool:
    """The request authenticated with an org `X-Api-Key` + `X-Secret-Key` pair.

    `APIKeyAuthentication` marks those requests with `request.org_api_key`. Such a pair is not a person:
    a `system` key authenticates as the organisation's first user by `created_at`, and any member can
    read it from `GET /accounts/keys/`. The Hub therefore never lets one act (SPEC §9.3: signed in means
    a Future AGI session or a Hub token).
    """
    return getattr(request, "org_api_key", None) is not None


def refuse_org_api_key(request, permissions) -> None:
    """Raise unless the request needs no identity: a safe-method request the view lets anyone make."""
    if not uses_org_api_key(request):
        return
    public_read = request.method in SAFE_METHODS and any(isinstance(p, AllowAny) for p in permissions)
    if not public_read:
        raise HubError("OMEGA_HUB_SESSION_REQUIRED", 403)


def require_publisher(request, publisher) -> None:
    """The caller acts for `publisher`: its owner, or a token bound to it."""
    if is_hub_token(request):
        if request.auth.publisher_id != publisher.pk:
            raise HubError("OMEGA_HUB_TOKEN_SCOPE", 403, "This Hub token belongs to another publisher.")
        return
    if not can_act_for_publisher(request.user, publisher):
        raise HubError("OMEGA_HUB_NOT_PUBLISHER_OWNER", 403)


class HubAuthenticated(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            raise HubError("OMEGA_HUB_UNAUTHENTICATED", 401)
        return True


class IsHubMaintainer(BasePermission):
    def has_permission(self, request, view):
        if not is_maintainer(request.user):
            raise HubError("OMEGA_HUB_NOT_MAINTAINER", 403)
        return True


class IsSessionUser(BasePermission):
    """A browser session (JWT): not a Hub token and not an org API key pair (SPEC §6.1)."""

    def has_permission(self, request, view):
        if is_hub_token(request) or uses_org_api_key(request):
            raise HubError("OMEGA_HUB_LIKE_NEEDS_SESSION", 403)
        if not (request.user and request.user.is_authenticated):
            raise HubError("OMEGA_HUB_UNAUTHENTICATED", 401)
        return True


class IsChecker(BasePermission):
    def has_permission(self, request, view):
        if request.auth != "checker":
            raise HubError("OMEGA_HUB_CHECKER_AUTH", 401)
        return True


class IsMetrics(BasePermission):
    def has_permission(self, request, view):
        if request.auth != "metrics":
            raise HubError("OMEGA_HUB_METRICS_AUTH", 401)
        return True


class TokenScope(BasePermission):
    """Appended to every Hub view: a Hub token reaches only views marked `token_allowed`."""

    def has_permission(self, request, view):
        if is_hub_token(request) and not getattr(view, "token_allowed", False):
            raise HubError("OMEGA_HUB_TOKEN_SCOPE", 403)
        return True
