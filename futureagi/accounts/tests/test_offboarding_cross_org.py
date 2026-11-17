"""Tenant offboarding must never delete or deactivate a shared identity.

Membership writers do not share an identity lock. Exercise the explicitly safe
fallback: retain even a zero-membership identity, with no access to the old org.
"""

import socket
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from accounts.api_key_policy import admit_api_key
from accounts.authentication import (
    APIKeyAuthentication,
    decode_token,
    generate_encrypted_message,
)
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from ee.falcon_ai.models import FalconMemory
from model_hub.models.develop_dataset import Dataset
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.tenant_scope import acts_in_organization, user_organization

pytestmark = pytest.mark.django_db
ENDPOINTS = ["bulk", "team", "legacy"]


@pytest.fixture(autouse=True)
def local_only(settings, monkeypatch):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()
    original_connect = socket.socket.connect

    def connect(sock, address):
        if sock.family == socket.AF_UNIX:
            return original_connect(sock, address)
        raise AssertionError("Offboarding tests forbid network egress")

    monkeypatch.setattr(socket.socket, "connect", connect)
    # Revocation delivery is external; keep the real DB revocation logic.
    with (
        patch("accounts.api_key_revocation.publish_key_revocation"),
        patch("agentcc.services.auth_bridge.revoke_key"),
        patch("accounts.services.service_grants.publish_grant_revocation"),
    ):
        yield
    cache.clear()


@pytest.fixture
def target(organization, workspace, user):
    member = User.objects.create_user(
        email="offboard-target@futureagi.com",
        password="synthetic-test-password",
        name="Offboarding target",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
    )
    second = Workspace.no_workspace_objects.create(
        name="Second acting-org workspace", organization=organization, created_by=user
    )
    for ws, linked in ((workspace, membership), (second, None)):
        WorkspaceMembership.no_workspace_objects.create(
            user=member,
            workspace=ws,
            organization_membership=linked,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
        )
    return member


@pytest.fixture
def other_org_data(target):
    org = Organization.objects.create(name="Surviving organization")
    membership = OrganizationMembership.no_workspace_objects.create(
        user=target,
        organization=org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
    )
    ws = Workspace.no_workspace_objects.create(
        name="Target-created foreign workspace",
        organization=org,
        created_by=target,
    )
    grant = WorkspaceMembership.no_workspace_objects.create(
        user=target,
        workspace=ws,
        organization_membership=membership,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
    )
    memory = FalconMemory.no_workspace_objects.create(
        organization=org,
        workspace=ws,
        created_by=target,
        key="offboarding-regression",
        value="Foreign data must survive",
    )
    dataset = Dataset.no_workspace_objects.create(
        name="Foreign dataset",
        organization=org,
        workspace=ws,
        user=target,
    )
    return membership, ws, grant, memory, dataset


def set_actor_level(user, organization, level):
    role = OrganizationRoles.OWNER if level == Level.OWNER else OrganizationRoles.ADMIN
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=level, role=role)
    user.organization_role = role
    user.save(update_fields=["organization_role"])


def remove_member(client, endpoint, member_id):
    if endpoint == "member_remove":
        return client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member_id)},
            format="json",
        )
    if endpoint == "deactivate":
        return client.post(
            "/accounts/user/deactivate/", {"user_id": str(member_id)}, format="json"
        )
    if endpoint == "bulk":
        return client.delete(
            "/accounts/delete-users/", {"user_ids": [str(member_id)]}, format="json"
        )
    if endpoint == "legacy":
        # This endpoint uses ambient workspace context, not a query parameter.
        with patch(
            "accounts.views.workspace_management.get_current_workspace",
            return_value=None,
        ):
            return client.post(
                "/accounts/user/delete/", {"user_id": str(member_id)}, format="json"
            )
    return client.delete(f"/accounts/team/users/{member_id}/", format="json")


def assert_success(response, endpoint, member_id):
    assert response.status_code == 200, response.data
    if endpoint in {"member_remove", "deactivate"}:
        assert response.data["result"]["user_id"] == str(member_id)
    elif endpoint == "bulk":
        assert response.data == [
            {"user_id": str(member_id), "message": "User deleted successfully."}
        ]
    elif endpoint == "team":
        assert response.data["result"] == {
            "message": "Member removed from all workspaces and organization successfully.",
            "removed_from": "all_workspaces_and_organization",
        }
    else:
        assert response.data["result"] == {
            "message": "User deleted from organization successfully",
            "user_id": str(member_id),
            "level": "organization",
        }


def credentials(user, org):
    tokens = []
    for kind in (AuthTokenType.ACCESS, AuthTokenType.REFRESH):
        token = AuthToken.objects.create(
            user=user,
            auth_type=kind,
            scoped_organization=org,
            last_used_at=timezone.now(),
        )
        bearer = generate_encrypted_message(
            {"user_id": str(user.pk), "id": str(token.pk)}
        )
        cache.set(
            f"{kind}_token_{token.pk}",
            {"token": bearer, "user": user, "non_saml_v1": True},
        )
        tokens.append((token, bearer))
    return tokens


def assert_removed(target, organization):
    target.refresh_from_db()
    assert (
        target.is_active
    )  # Safe fallback: never make an unserialized zero-org decision.
    assert not acts_in_organization(target, organization)
    assert not OrganizationMembership._base_manager.filter(
        user=target, organization=organization, is_active=True, deleted=False
    ).exists()
    assert not WorkspaceMembership._base_manager.filter(
        user=target, workspace__organization=organization, is_active=True, deleted=False
    ).exists()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("other_state", ["active", "absent", "inactive", "deleted"])
def test_offboarding_preserves_foreign_data_and_credentials(
    auth_client,
    organization,
    target,
    other_org_data,
    endpoint,
    other_state,
    django_capture_on_commit_callbacks,
):
    membership, ws, grant, memory, dataset = other_org_data
    if other_state == "absent":
        # A real removed membership, not merely an inactive one. Keep its data.
        OrganizationMembership._base_manager.filter(pk=membership.pk).delete()
    elif other_state != "active":
        OrganizationMembership._base_manager.filter(pk=membership.pk).update(
            is_active=other_state != "inactive", deleted=other_state == "deleted"
        )
    own_tokens = credentials(target, organization)
    foreign_tokens = credentials(target, ws.organization)
    unscoped_tokens = credentials(target, None)
    own_key = OrgApiKey._base_manager.create(user=target, organization=organization)
    foreign_key = OrgApiKey._base_manager.create(
        user=target, organization=ws.organization, workspace=ws
    )
    pending = OrganizationInvite.objects.create(
        organization=organization, target_email=target.email, level=Level.MEMBER
    )
    with django_capture_on_commit_callbacks(execute=True):
        response = remove_member(auth_client, endpoint, target.pk)
    assert_success(response, endpoint, target.pk)
    for row in (ws, memory, dataset):
        row.refresh_from_db()
        assert not row.deleted
    assert ws.created_by_id == target.pk and ws.is_active
    assert memory.value == "Foreign data must survive"
    assert dataset.workspace_id == ws.pk
    assert_removed(target, organization)
    pending.refresh_from_db()
    assert pending.status == InviteStatus.CANCELLED
    for token, bearer in own_tokens:
        token.refresh_from_db()
        assert not token.is_active
        assert cache.get(f"{token.auth_type}_token_{token.pk}") is None
        if token.auth_type == AuthTokenType.ACCESS:
            with pytest.raises(AuthenticationFailed):
                decode_token(bearer)
    own_key.refresh_from_db()
    assert not own_key.enabled
    with pytest.raises(AuthenticationFailed):
        admit_api_key(own_key)
    # Even when O2 membership is inactive/absent, O1 cannot revoke its credentials.
    for token, bearer in foreign_tokens + unscoped_tokens:
        token.refresh_from_db()
        assert token.is_active
        assert cache.get(f"{token.auth_type}_token_{token.pk}")["token"] == bearer
    foreign_key.refresh_from_db()
    assert foreign_key.enabled
    if other_state == "active":
        membership.refresh_from_db()
        grant.refresh_from_db()
        assert membership.is_active and not membership.deleted
        assert grant.is_active and not grant.deleted
        authenticated, _, _ = decode_token(foreign_tokens[0][1])
        assert authenticated.pk == target.pk
        assert acts_in_organization(authenticated, ws.organization)
        assert not acts_in_organization(authenticated, organization)
        assert admit_api_key(foreign_key).pk == foreign_key.pk
        for selected_org in (ws.organization, organization):
            request = Request(
                APIRequestFactory().get(
                    "/accounts/workspaces/",
                    HTTP_AUTHORIZATION=f"Bearer {unscoped_tokens[0][1]}",
                    HTTP_X_ORGANIZATION_ID=str(selected_org.pk),
                    HTTP_X_WORKSPACE_ID=str(ws.pk),
                )
            )
            assert APIKeyAuthentication().authenticate(request)[0].pk == target.pk
            # A stale O1 selector may fall back to O2, but must never serve O1.
            assert request.organization.pk == ws.organization_id
            assert request.workspace.pk == ws.pk
    else:
        assert user_organization(target) is None


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_zero_membership_identity_can_be_reinvited(
    auth_client,
    api_client,
    organization,
    workspace,
    target,
    endpoint,
):
    assert_success(remove_member(auth_client, endpoint, target.pk), endpoint, target.pk)
    assert_removed(target, organization)
    assert user_organization(target) is None
    # Existing login explicitly permits an org-less identity, without old-org access.
    with patch("accounts.views.user.track_mixpanel_event"):
        response = api_client.post(
            "/accounts/token/",
            {"email": target.email, "password": "synthetic-test-password"},
            format="json",
        )
    assert response.status_code == 200, response.data
    assert response.data["requires_org_setup"] is True
    # Existing invite endpoint reactivates the same identity and membership.
    with patch("accounts.views.rbac_views.send_invite_email"):
        invited = auth_client.post(
            "/accounts/organization/invite/",
            {
                "emails": [target.email],
                "org_level": Level.MEMBER,
                "workspace_access": [
                    {"workspace_id": str(workspace.pk), "level": Level.WORKSPACE_MEMBER}
                ],
            },
            format="json",
        )
    assert invited.status_code == 200, invited.data
    assert target.email in invited.data["result"]["invited"]
    assert User.objects.get(email=target.email).pk == target.pk
    target.refresh_from_db()
    assert target.is_active and acts_in_organization(target, organization)
    assert WorkspaceMembership._base_manager.filter(
        user=target, workspace=workspace, is_active=True, deleted=False
    ).exists()


@pytest.mark.parametrize("target_level", [Level.ADMIN, Level.OWNER])
def test_bulk_actor_level_refusal_is_unchanged(
    auth_client, user, organization, target, target_level
):
    set_actor_level(user, organization, Level.ADMIN)
    OrganizationMembership.no_workspace_objects.filter(
        user=target, organization=organization
    ).update(level=target_level)
    response = remove_member(auth_client, "bulk", target.pk)
    assert response.status_code == 200
    assert response.data == [
        {
            "user_id": str(target.pk),
            "error": "You cannot delete a user at or above your own level.",
        }
    ]
    assert acts_in_organization(target, organization)


def test_team_still_requires_owner(auth_client, user, organization, target):
    set_actor_level(user, organization, Level.ADMIN)
    assert remove_member(auth_client, "team", target.pk).status_code == 403
    assert acts_in_organization(target, organization)


@pytest.mark.parametrize("endpoint", ["bulk", "legacy"])
def test_admin_can_offboard_member(auth_client, user, organization, target, endpoint):
    set_actor_level(user, organization, Level.ADMIN)
    assert_success(remove_member(auth_client, endpoint, target.pk), endpoint, target.pk)
    assert_removed(target, organization)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_legacy_account_gets_inactive_membership_tombstone(
    auth_client, target, organization, endpoint
):
    OrganizationMembership._base_manager.filter(user=target).delete()
    assert acts_in_organization(target, organization)
    assert_success(remove_member(auth_client, endpoint, target.pk), endpoint, target.pk)
    assert_removed(target, organization)
    assert user_organization(target) is None
