"""Resolve the browser's selected organization without widening its credential.

Without an explicit selection, a socket follows the user's current
OrganizationMembership rows, as HTTP authentication does. The legacy
``user.organization`` FK only identifies accounts that predate membership rows:
a removed member keeps that FK next to a deactivated membership and must not
regain the former organization's broadcast or targeted groups through it.
"""

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


def membership_organization(user):
    """Return the user's current organization from the membership model.

    Mirrors ``APIKeyAuthentication._resolve_organization``: the first active
    membership wins, and the legacy FK is used only when the user has no
    membership rows at all. Synchronous; call it from a database thread.
    """

    if user is None:
        return None
    from accounts.models.organization_membership import OrganizationMembership

    memberships = OrganizationMembership.no_workspace_objects.filter(user=user)
    membership = (
        memberships.filter(is_active=True).select_related("organization").first()
    )
    if membership is not None:
        return membership.organization
    if getattr(user, "organization_id", None) is not None and not memberships.exists():
        return user.organization
    return None


def organization_membership_is_active(user_id, organization_id):
    """Return whether the user still belongs to the socket's organization.

    The rule connect applies: an active membership row for the organization,
    or the legacy FK for an account with no membership rows at all. An
    inactive user belongs to no organization. Synchronous; call it from a
    database thread.
    """

    if user_id is None or organization_id is None:
        return False
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User

    user = (
        User.objects.filter(pk=user_id, is_active=True)
        .values("organization_id")
        .first()
    )
    if user is None:
        return False
    memberships = OrganizationMembership.no_workspace_objects.filter(user_id=user_id)
    if memberships.filter(organization_id=organization_id, is_active=True).exists():
        return True
    return (
        user["organization_id"] is not None
        and str(user["organization_id"]) == str(organization_id)
        and not memberships.exists()
    )


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
    organization = membership_organization(user)
    return organization.id if organization is not None else None
