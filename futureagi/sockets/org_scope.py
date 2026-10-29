"""Organization resolution for WebSocket consumers.

SAML credentials are bound to one organization. Other WebSocket sessions retain
the legacy membership-based resolution until that behavior is addressed
separately.
"""

from channels.db import database_sync_to_async


async def resolve_socket_organization(scope, user):
    """Return the organization allowed by this socket's credential."""

    auth_scope = scope.get("auth_scope")
    if auth_scope is not None:
        return auth_scope.org_id
    return await _resolve_legacy_socket_organization(user)


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
