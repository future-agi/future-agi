"""Resolve the browser's selected organization without widening its credential."""

from urllib.parse import parse_qs
from uuid import UUID

from channels.db import database_sync_to_async


async def resolve_socket_organization(scope, user):
    """Return the organization allowed by this socket's credential."""

    try:
        requested = parse_qs(
            scope.get("query_string", b"").decode(), keep_blank_values=True
        ).get("organization_id")
        if requested is not None:
            if len(requested) != 1:
                return None
            requested = UUID(requested[0])
    except (UnicodeError, ValueError):
        return None

    auth_scope = scope.get("auth_scope")
    if auth_scope is not None:
        if requested is not None and str(requested) != str(auth_scope.org_id):
            return None
        return auth_scope.org_id
    if requested is not None:
        return await _resolve_requested_socket_organization(user, requested)
    return await _resolve_legacy_socket_organization(user)


@database_sync_to_async
def _resolve_requested_socket_organization(user, organization_id):
    from accounts.models import Organization

    if user is None:
        return None
    organization = Organization.objects.filter(id=organization_id).first()
    if organization is not None and user.can_access_organization(organization):
        return organization.id
    return None


@database_sync_to_async
def _resolve_legacy_socket_organization(user):
    if user is None:
        return None
    from accounts.models.organization_membership import OrganizationMembership

    membership = (
        OrganizationMembership.objects.filter(user=user, is_active=True)
        .select_related("organization")
        .first()
    )
    if membership is not None:
        return membership.organization_id
    return getattr(user, "organization_id", None)
