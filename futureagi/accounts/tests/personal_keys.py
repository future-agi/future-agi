"""Shared fixture factory for explicitly person-owned API credentials."""

from accounts.models import OrgApiKey
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services.workspace_membership import create_workspace_membership
from tfc.constants.roles import OrganizationRoles


def personal_key_for(user, org, *, workspace=None, **fields):
    """Set up a test actor's memberships, then explicitly issue that actor's key.

    Do not use this helper in removal tests after revocation: those tests must
    assert that authentication itself never heals the actor's membership.
    """
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": user.organization_role or OrganizationRoles.MEMBER,
            "is_active": True,
        },
    )
    if (
        workspace is not None
        and not WorkspaceMembership.no_workspace_objects.filter(
            user=user, workspace=workspace, is_active=True
        ).exists()
    ):
        create_workspace_membership(
            user=user,
            workspace=workspace,
            organization_membership=membership,
            role=OrganizationRoles.WORKSPACE_MEMBER,
        )
    return OrgApiKey.no_workspace_objects.create(
        user=user, organization=org, workspace=workspace, type="user", **fields
    )


def session_client_for(user, org, workspace=None):
    """Exercise the same live access-session authenticator as the browser."""
    from django.utils import timezone
    from rest_framework.test import APIClient
    from accounts.authentication import generate_encrypted_message
    from accounts.models.auth_token import AuthToken, AuthTokenType

    row = AuthToken.objects.create(
        user=user, auth_type=AuthTokenType.ACCESS.value, last_used_at=timezone.now()
    )
    token = generate_encrypted_message({"user_id": str(user.pk), "id": str(row.pk)})
    client = APIClient()
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_X_ORGANIZATION_ID": str(org.pk),
    }
    if workspace is not None:
        headers["HTTP_X_WORKSPACE_ID"] = str(workspace.pk)
    client.credentials(**headers)
    return client
