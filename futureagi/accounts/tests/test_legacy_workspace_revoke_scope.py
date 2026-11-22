"""NULL-linked legacy admins retain the same grant and revoke authority."""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_invite import OrganizationInvite
from accounts.models.workspace import WorkspaceMembership
from accounts.services.member_role_service import update_member_role
from accounts.tests.test_shared_workspace_grant_authority import state  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("legacy_null", [False, True])
@pytest.mark.parametrize("transport", ["service", "http"])
def test_admin_revokes_only_owned_workspace_for_member_and_invite(state, pending, legacy_null, transport):
    org, actor, target, am, tm, own, sibling, _, aw = state
    if legacy_null:
        aw.organization_membership = None
        aw.save(update_fields=["organization_membership"])
    for ws in (own, sibling):
        WorkspaceMembership.no_workspace_objects.create(
            user=target, organization_membership=tm, workspace=ws,
            role="workspace_member", level=3, is_active=True,
        )
    invite = None
    if pending:
        tm.is_active = False
        tm.save(update_fields=["is_active"])
        invite = OrganizationInvite.objects.create(
            organization=org, target_email=target.email, invited_by=actor, level=1,
            workspace_access=[{"workspace_id": str(ws.pk), "level": 3} for ws in (own, sibling)],
        )
    kwargs = {"org_level": 1, "workspace_access": []}
    if transport == "service":
        update_member_role(organization=org, actor=actor, target_user_id=target.pk,
                           workspace_access_provided=True, **kwargs)
    else:
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION="Bearer " + generate_encrypted_message({
                "user_id": str(actor.pk),
                "id": str(AuthToken.objects.create(
                    user=actor, auth_type=AuthTokenType.ACCESS, last_used_at=timezone.now(),
                ).pk),
            }),
            HTTP_X_ORGANIZATION_ID=str(org.pk), HTTP_X_WORKSPACE_ID=str(own.pk),
        )
        response = client.post("/accounts/organization/members/role/", {
            "user_id": str(target.pk), **kwargs,
        }, format="json")
        assert response.status_code == 200, response.data
    owned = WorkspaceMembership._base_manager.get(user=target, workspace=own)
    other = WorkspaceMembership._base_manager.get(user=target, workspace=sibling)
    assert not owned.is_active and owned.deleted
    assert other.is_active and not other.deleted
    if invite:
        invite.refresh_from_db()
        assert invite.workspace_access == [{"workspace_id": str(sibling.pk), "level": 3}]
        # Invite acceptance has no selected-workspace context; the invite
        # itself supplies its organization and explicit workspace list.
        from tfc.middleware.workspace_context import clear_workspace_context

        clear_workspace_context()
        invite.accept(target)
        owned.refresh_from_db()
        other.refresh_from_db()
        assert not owned.is_active and owned.deleted
        assert other.is_active and not other.deleted
