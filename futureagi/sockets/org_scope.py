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


async def resolve_socket_organization_ids(scope, user):
    """Every organization whose live updates this socket may receive.

    A SAML socket is bound to one organization. Any other socket belongs to
    its user, who may switch between the organizations they are an active
    member of, so all of those count.
    """

    auth_scope = scope.get("auth_scope")
    if auth_scope is not None:
        return frozenset({str(auth_scope.org_id)})
    return await _legacy_socket_organization_ids(user)


@database_sync_to_async
def _legacy_socket_organization_ids(user):
    if user is None:
        return frozenset()
    from accounts.models.organization_membership import OrganizationMembership

    org_ids = {
        str(org_id)
        for org_id in OrganizationMembership.objects.filter(
            user=user, is_active=True
        ).values_list("organization_id", flat=True)
    }
    # As _resolve_legacy_socket_organization: the user's own organization only
    # when they have no active membership.
    if not org_ids and getattr(user, "organization_id", None) is not None:
        org_ids.add(str(user.organization_id))
    return frozenset(org_ids)
