"""Organization-key policy shared by account views and key tools."""

from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from accounts.models import Organization, OrgApiKey, User
from accounts.services.workspace_membership import resolve_org_membership
from tfc.constants.levels import Level


def request_key_organization(request):
    # An explicitly unresolved organization must not fall back to a stale FK.
    return getattr(request, "organization", request.user.organization)


def key_org_level(user, organization):
    """Use only an active, non-deleted membership in the selected organization."""
    if not user or not user.is_authenticated or not user.is_active:
        return None
    membership = resolve_org_membership(user, organization)
    return membership.level_or_legacy if membership else None


def can_manage_system_keys(user, organization):
    return (key_org_level(user, organization) or 0) >= Level.ADMIN


def visible_api_keys(user, organization):
    """Admins manage org keys; other members see only their personal keys."""
    keys = OrgApiKey.no_workspace_objects.filter(organization=organization).exclude(
        type="ingest"
    )
    level = key_org_level(user, organization)
    if level is None:
        return keys.none()
    if level < Level.ADMIN:
        return keys.filter(user=user, type__in=("user", "mcp"))
    return keys


def bootstrap_api_key(user, organization):
    level = key_org_level(user, organization)
    if level is None:
        raise PermissionDenied("An active organization membership is required.")

    # Serialize first-time bootstrap so concurrent SDK/UI requests reuse one key.
    with transaction.atomic():
        if level >= Level.ADMIN:
            Organization.objects.select_for_update().get(pk=organization.pk)
            key, _ = OrgApiKey.no_workspace_objects.get_or_create(
                organization=organization, type="system"
            )
            if not key.enabled:
                raise PermissionDenied("The organization API key is disabled.")
            return key

        User.objects.select_for_update().get(pk=user.pk)
        keys = OrgApiKey.no_workspace_objects.filter(
            organization=organization, user=user, type="user", enabled=True
        )
        key = keys.order_by("created_at", "id").first()
        if key is not None:
            return key
        return OrgApiKey.no_workspace_objects.create(
            organization=organization, user=user, type="user", name="personal_api_key"
        )
