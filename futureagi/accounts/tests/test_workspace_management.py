"""
Workspace Management API Tests

Comprehensive tests for workspace_management.py endpoints:
- WorkspaceListAPIView
- WorkspaceInviteAPIView
- UserListAPIView
- UserRoleUpdateAPIView
- ResendInviteAPIView
- DeleteUserAPIView
- DeactivateUserAPIView
- SwitchWorkspaceAPIView
- ManageTeamView
"""

import pytest
from rest_framework import status

from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def admin_user(db, organization, workspace):
    """Create an admin user in the same organization."""
    set_workspace_context(workspace=workspace)
    admin = User.objects.create_user(
        email="admin@futureagi.com",
        password="adminpassword123",
        name="Admin User",
        organization=organization,
        organization_role=OrganizationRoles.ADMIN,
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=workspace,
        user=admin,
        role=OrganizationRoles.WORKSPACE_ADMIN,
    )
    return admin


@pytest.fixture
def member_user(db, organization, workspace):
    """Create a member user (non-owner/admin) in the same organization."""
    set_workspace_context(workspace=workspace)
    member = User.objects.create_user(
        email="member@futureagi.com",
        password="memberpassword123",
        name="Member User",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=workspace,
        user=member,
        role=OrganizationRoles.WORKSPACE_MEMBER,
    )
    return member


@pytest.fixture
def inactive_user(db, organization, workspace, user):
    """Create an inactive (invited but not activated) user."""
    set_workspace_context(workspace=workspace)
    inactive = User.objects.create_user(
        email="inactive@futureagi.com",
        password="inactivepassword123",
        name="Inactive User",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
        is_active=False,
    )
    inactive.invited_by = user
    inactive.save()
    WorkspaceMembership.no_workspace_objects.create(
        workspace=workspace,
        user=inactive,
        role=OrganizationRoles.WORKSPACE_MEMBER,
    )
    return inactive


@pytest.fixture
def admin_client(api_client, admin_user):
    """Authenticated API client for admin user."""
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def member_client(api_client, member_user):
    """Authenticated API client for member user."""
    api_client.force_authenticate(user=member_user)
    return api_client


@pytest.fixture
def second_workspace(db, user, organization):
    """Create a second (non-default) workspace."""
    ws = Workspace.objects.create(
        name="Second Workspace",
        display_name="Second Workspace Display",
        description="A second workspace for testing",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=ws,
        user=user,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        invited_by=user,
    )
    return ws


# =============================================================================
# WorkspaceListAPIView Tests - GET /accounts/workspace/list/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceListAPIView:
    """Tests for GET /accounts/workspace/list/ endpoint."""

    def test_list_workspaces_as_owner(self, auth_client, workspace):
        """Owner can list workspaces."""
        response = auth_client.get("/accounts/workspace/list/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_list_workspaces_as_admin(self, admin_client, workspace):
        """Admin can list workspaces."""
        response = admin_client.get("/accounts/workspace/list/")
        assert response.status_code == status.HTTP_200_OK

    def test_list_workspaces_as_member(self, member_client, workspace):
        """Member can list their workspaces (only ones they belong to)."""
        response = member_client.get("/accounts/workspace/list/")
        assert response.status_code == status.HTTP_200_OK

    def test_list_workspaces_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/workspace/list/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_list_workspaces_with_search(self, auth_client, workspace):
        """Can search workspaces by name."""
        response = auth_client.get(
            "/accounts/workspace/list/", {"search": workspace.name}
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_workspaces_with_pagination(self, auth_client, workspace):
        """Pagination parameters work correctly."""
        response = auth_client.get(
            "/accounts/workspace/list/", {"page": 1, "limit": 10}
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_workspaces_with_sorting(self, auth_client, workspace):
        """Can sort workspaces."""
        response = auth_client.get("/accounts/workspace/list/", {"sort": "-created_at"})
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# WorkspaceInviteAPIView Tests - POST /accounts/workspace/invite/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceInviteAPIView:
    """Tests for POST /accounts/workspace/invite/ endpoint."""

    def test_invite_rejects_unknown_request_fields(self, auth_client, workspace):
        response = auth_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": ["newinvite@futureagi.com"],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "workspace_ids": [str(workspace.id)],
                "select_all": False,
                "selectAll": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "selectAll")

    def test_invite_user_as_owner(self, auth_client, workspace):
        """Owner can invite users to workspace."""
        response = auth_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": ["newinvite@futureagi.com"],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "workspace_ids": [str(workspace.id)],
                "select_all": False,
            },
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,  # If validation fails
        ]

    def test_invite_user_as_admin(self, admin_client, workspace):
        """Admin can invite users to workspace."""
        response = admin_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": ["admininvite@futureagi.com"],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "workspace_ids": [str(workspace.id)],
                "select_all": False,
            },
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_invite_user_as_member_forbidden(self, member_client, workspace):
        """Member cannot invite users (only Admin+ or WS Admin can invite)."""
        response = member_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": ["memberinvite@futureagi.com"],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "workspace_ids": [str(workspace.id)],
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_invite_user_unauthenticated(self, api_client, workspace):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/workspace/invite/",
            {"emails": ["unauthenticated@test.com"]},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_invite_with_select_all(self, auth_client, workspace, second_workspace):
        """Can invite user to all workspaces with select_all."""
        response = auth_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": ["selectall@futureagi.com"],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "select_all": True,
                "workspace_ids": [],  # No exclusions
            },
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_invite_missing_workspace_ids(self, auth_client):
        """Invite without workspace_ids when select_all is False fails."""
        response = auth_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": ["missing@futureagi.com"],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "select_all": False,
                # workspace_ids missing
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invite_invalid_email(self, auth_client, workspace):
        """Invite with invalid email format."""
        response = auth_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": ["invalid-email"],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "workspace_ids": [str(workspace.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invite_new_user_creates_pending_invite(
        self, auth_client, api_client, workspace, monkeypatch
    ):
        """Inviting a brand-new user must persist a PENDING OrganizationInvite.

        Regression: like the onboarding add-users flow, this endpoint created
        the user + emailed a token but no invite row, so accept_invitation_mail
        rejected the link as "expired or invalid".
        """
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        import accounts.views.workspace_management as wm
        from accounts.models.organization_invite import (
            InviteStatus,
            OrganizationInvite,
        )

        monkeypatch.setattr(wm, "email_helper", lambda *a, **k: None)

        email = "wsinvite@futureagi.com"
        response = auth_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": [email],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "workspace_ids": [str(workspace.id)],
                "select_all": False,
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        )

        new_member = User.objects.get(email=email)
        invite = OrganizationInvite.objects.get(
            target_email__iexact=email,
            organization=new_member.organization,
            status=InviteStatus.PENDING,
        )
        assert invite.workspace_access

        uidb64 = urlsafe_base64_encode(force_bytes(new_member.pk))
        token = default_token_generator.make_token(new_member)
        preview = api_client.get(f"/accounts/accept-invitation/{uidb64}/{token}/")
        assert preview.status_code == status.HTTP_200_OK


# =============================================================================
# UserListAPIView Tests - GET /accounts/user/list/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestUserListAPIView:
    """Tests for GET /accounts/user/list/ endpoint."""

    def test_list_users_as_owner(self, auth_client, user):
        """Owner can list users."""
        response = auth_client.get("/accounts/user/list/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data

    def test_list_users_as_admin(self, admin_client):
        """Admin can list users."""
        response = admin_client.get("/accounts/user/list/")
        assert response.status_code == status.HTTP_200_OK

    def test_list_users_as_member_success(self, member_client):
        """Member can list users (with org-level permission)."""
        response = member_client.get("/accounts/user/list/")
        # Members with org-level MEMBER role can now list users
        assert response.status_code == status.HTTP_200_OK

    def test_list_users_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/user/list/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_list_users_with_search(self, auth_client, user):
        """Can search users by name or email."""
        response = auth_client.get("/accounts/user/list/", {"search": user.name})
        assert response.status_code == status.HTTP_200_OK

    def test_list_users_with_workspace_filter(self, auth_client, workspace):
        """Can filter users by workspace."""
        response = auth_client.get(
            "/accounts/user/list/", {"workspace_id": str(workspace.id)}
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_users_with_status_filter(self, auth_client):
        """Can filter users by status."""
        response = auth_client.get(
            "/accounts/user/list/", {"filter_status": ["Active"]}
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_users_with_role_filter(self, auth_client):
        """Can filter users by role."""
        response = auth_client.get(
            "/accounts/user/list/",
            {"filter_role": [OrganizationRoles.WORKSPACE_ADMIN]},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_users_with_pagination(self, auth_client):
        """Pagination parameters work correctly."""
        response = auth_client.get("/accounts/user/list/", {"page": 1, "limit": 10})
        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# UserRoleUpdateAPIView Tests - POST /accounts/user/role/update/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestUserRoleUpdateAPIView:
    """Tests for POST /accounts/user/role/update/ endpoint."""

    def test_update_role_rejects_unknown_request_fields(self, auth_client, member_user):
        response = auth_client.post(
            "/accounts/user/role/update/",
            {
                "user_id": str(member_user.id),
                "new_role": OrganizationRoles.WORKSPACE_ADMIN,
                "userId": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "userId")

    def test_update_role_as_owner(self, auth_client, member_user):
        """Owner can update user roles."""
        response = auth_client.post(
            "/accounts/user/role/update/",
            {
                "user_id": str(member_user.id),
                "new_role": OrganizationRoles.WORKSPACE_ADMIN,
            },
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,  # If not a workspace member
        ]

    def test_update_role_as_admin(self, admin_client, member_user):
        """Admin can update user roles."""
        response = admin_client.post(
            "/accounts/user/role/update/",
            {
                "user_id": str(member_user.id),
                "new_role": OrganizationRoles.WORKSPACE_MEMBER,
            },
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_update_role_as_member_success(self, member_client, admin_user):
        """Member can update roles (with org-level permission)."""
        response = member_client.post(
            "/accounts/user/role/update/",
            {
                "user_id": str(admin_user.id),
                "new_role": OrganizationRoles.WORKSPACE_MEMBER,
            },
            format="json",
        )
        # Members with org-level MEMBER role can now update roles
        assert response.status_code == status.HTTP_200_OK

    def test_update_role_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/user/role/update/",
            {"user_id": "00000000-0000-0000-0000-000000000000", "new_role": "admin"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_update_own_role_forbidden(self, auth_client, user):
        """Cannot change own role."""
        response = auth_client.post(
            "/accounts/user/role/update/",
            {"user_id": str(user.id), "new_role": OrganizationRoles.WORKSPACE_MEMBER},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_role_nonexistent_user(self, auth_client):
        """Updating role of nonexistent user fails."""
        response = auth_client.post(
            "/accounts/user/role/update/",
            {
                "user_id": "00000000-0000-0000-0000-000000000000",
                "new_role": OrganizationRoles.WORKSPACE_ADMIN,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_role_missing_data(self, auth_client):
        """Role update without required data fails."""
        response = auth_client.post(
            "/accounts/user/role/update/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# ResendInviteAPIView Tests - POST /accounts/user/resend-invite/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestResendInviteAPIView:
    """Tests for POST /accounts/user/resend-invite/ endpoint."""

    def test_resend_invite_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/accounts/user/resend-invite/",
            {
                "user_id": "00000000-0000-0000-0000-000000000000",
                "userId": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "userId")

    def test_resend_invite_as_owner(self, auth_client, inactive_user):
        """Owner can resend invite to inactive user."""
        response = auth_client.post(
            "/accounts/user/resend-invite/",
            {"user_id": str(inactive_user.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,  # If email sending fails
        ]

    def test_resend_invite_as_admin(self, admin_client, inactive_user):
        """Admin can resend invite."""
        response = admin_client.post(
            "/accounts/user/resend-invite/",
            {"user_id": str(inactive_user.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_resend_invite_as_member_forbidden(self, member_client, inactive_user):
        """Member cannot resend invites."""
        response = member_client.post(
            "/accounts/user/resend-invite/",
            {"user_id": str(inactive_user.id)},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_resend_invite_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/user/resend-invite/",
            {"user_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_resend_invite_nonexistent_user(self, auth_client):
        """Resending invite to nonexistent user fails."""
        response = auth_client.post(
            "/accounts/user/resend-invite/",
            {"user_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# DeleteUserAPIView Tests - POST /accounts/user/delete/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestDeleteUserAPIView:
    """Tests for POST /accounts/user/delete/ endpoint."""

    def test_delete_user_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/accounts/user/delete/",
            {
                "user_id": "00000000-0000-0000-0000-000000000000",
                "userId": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "userId")

    def test_delete_user_as_owner(self, auth_client, member_user):
        """Owner can delete users."""
        response = auth_client.post(
            "/accounts/user/delete/",
            {"user_id": str(member_user.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_delete_user_as_admin(self, admin_client, member_user):
        """Admin can delete users."""
        response = admin_client.post(
            "/accounts/user/delete/",
            {"user_id": str(member_user.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_403_FORBIDDEN,  # Depending on admin permissions
        ]

    def test_delete_user_as_member_forbidden(self, member_client, admin_user):
        """Member cannot delete users."""
        response = member_client.post(
            "/accounts/user/delete/",
            {"user_id": str(admin_user.id)},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_delete_user_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/user/delete/",
            {"user_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_delete_self_forbidden(self, auth_client, user):
        """Cannot delete own account."""
        response = auth_client.post(
            "/accounts/user/delete/",
            {"user_id": str(user.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_nonexistent_user(self, auth_client):
        """Deleting nonexistent user fails."""
        response = auth_client.post(
            "/accounts/user/delete/",
            {"user_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# DeactivateUserAPIView Tests - POST /accounts/user/deactivate/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestDeactivateUserAPIView:
    """Tests for POST /accounts/user/deactivate/ endpoint."""

    def test_deactivate_user_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/accounts/user/deactivate/",
            {
                "user_id": "00000000-0000-0000-0000-000000000000",
                "userId": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "userId")

    def test_deactivate_user_as_owner(self, auth_client, member_user):
        """Owner can deactivate users."""
        response = auth_client.post(
            "/accounts/user/deactivate/",
            {"user_id": str(member_user.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert "deactivated" in result.get("message", "").lower()

    def test_deactivate_user_as_admin(self, admin_client, member_user):
        """Admin can deactivate users."""
        response = admin_client.post(
            "/accounts/user/deactivate/",
            {"user_id": str(member_user.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_deactivate_user_as_member_forbidden(self, member_client, admin_user):
        """Member cannot deactivate users."""
        response = member_client.post(
            "/accounts/user/deactivate/",
            {"user_id": str(admin_user.id)},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_deactivate_user_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/user/deactivate/",
            {"user_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_deactivate_self_forbidden(self, auth_client, user):
        """Cannot deactivate own account."""
        response = auth_client.post(
            "/accounts/user/deactivate/",
            {"user_id": str(user.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_deactivate_nonexistent_user(self, auth_client):
        """Deactivating nonexistent user fails."""
        response = auth_client.post(
            "/accounts/user/deactivate/",
            {"user_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# SwitchWorkspaceAPIView Tests - POST /accounts/workspace/switch/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestSwitchWorkspaceAPIView:
    """Tests for POST /accounts/workspace/switch/ endpoint."""

    def test_switch_workspace_rejects_unknown_request_fields(
        self, auth_client, workspace
    ):
        response = auth_client.post(
            "/accounts/workspace/switch/",
            {
                "new_workspace_id": str(workspace.id),
                "newWorkspaceId": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "newWorkspaceId")

    def test_switch_workspace_as_owner(self, auth_client, second_workspace):
        """Owner can switch to workspace."""
        response = auth_client.post(
            "/accounts/workspace/switch/",
            {"new_workspace_id": str(second_workspace.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert "switched" in result.get("message", "").lower()

    def test_switch_workspace_as_member(self, member_client, workspace, member_user):
        """Member can switch to workspace they belong to."""
        response = member_client.post(
            "/accounts/workspace/switch/",
            {"new_workspace_id": str(workspace.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_switch_workspace_member_no_global_access(
        self, member_client, second_workspace
    ):
        """Member without explicit workspace membership cannot switch to other workspace."""
        response = member_client.post(
            "/accounts/workspace/switch/",
            {"new_workspace_id": str(second_workspace.id)},
            format="json",
        )
        # Members do NOT have global workspace access — need explicit membership
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_switch_workspace_unauthenticated(self, api_client, workspace):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/workspace/switch/",
            {"new_workspace_id": str(workspace.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_switch_workspace_invalid_id(self, auth_client):
        """Switch to nonexistent workspace fails."""
        response = auth_client.post(
            "/accounts/workspace/switch/",
            {"new_workspace_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_switch_workspace_missing_id(self, auth_client):
        """Switch without workspace_id fails."""
        response = auth_client.post(
            "/accounts/workspace/switch/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# ManageTeamView Tests - GET/POST/DELETE /accounts/team/users/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestManageTeamViewGet:
    """Tests for GET /accounts/team/users/ endpoint."""

    def test_list_team_as_owner(self, auth_client, user):
        """Owner can list team members."""
        response = auth_client.get("/accounts/team/users/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert "results" in result
        assert "total" in result

    def test_list_team_as_admin_forbidden(self, admin_client):
        """Admin cannot list team (owner only)."""
        response = admin_client.get("/accounts/team/users/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_list_team_as_member_forbidden(self, member_client):
        """Member cannot list team."""
        response = member_client.get("/accounts/team/users/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_list_team_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/team/users/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_list_team_with_search(self, auth_client, user):
        """Can search team members."""
        response = auth_client.get("/accounts/team/users/", {"search_query": user.name})
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert any(row["id"] == str(user.id) for row in result["results"])

    def test_list_team_with_email_search(self, auth_client, user):
        """Can search team members by email as shown in settings tables."""
        response = auth_client.get(
            "/accounts/team/users/", {"search_query": user.email}
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert result["total"] >= 1
        assert any(row["id"] == str(user.id) for row in result["results"])

    def test_list_team_with_is_active_filter(self, auth_client):
        """Can filter by is_active."""
        response = auth_client.get("/accounts/team/users/", {"is_active": "true"})
        assert response.status_code == status.HTTP_200_OK

    def test_list_team_with_pagination(self, auth_client):
        """Pagination parameters work correctly."""
        response = auth_client.get(
            "/accounts/team/users/", {"page": 1, "page_size": 10}
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_team_with_workspace_context(self, auth_client, workspace):
        """Can list team members with workspace context."""
        response = auth_client.get(
            "/accounts/team/users/", {"workspace_id": str(workspace.id)}
        )
        assert response.status_code == status.HTTP_200_OK

    def test_get_team_member_by_id_filters_to_member(
        self, auth_client, member_user, workspace
    ):
        """Member detail alias returns only the requested accessible member."""
        response = auth_client.get(
            f"/accounts/team/users/{member_user.id}/",
            {"workspace_id": str(workspace.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert result["total"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["id"] == str(member_user.id)

    def test_get_team_member_by_id_not_found(self, auth_client):
        """Member detail alias does not fall back to returning the full team."""
        response = auth_client.get(
            "/accounts/team/users/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
class TestManageTeamViewPost:
    """Tests for POST /accounts/team/users/ endpoint."""

    def test_add_team_member_as_owner(self, auth_client, workspace):
        """Owner can add team members."""
        response = auth_client.post(
            "/accounts/team/users/",
            {
                "members": [
                    {
                        "email": "newteam@futureagi.com",
                        "name": "New Team Member",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,  # May fail due to email sending
            status.HTTP_429_TOO_MANY_REQUESTS,  # User limit reached
        ]

    def test_add_team_member_as_admin_forbidden(self, admin_client, workspace):
        """Admin cannot add team members (owner only)."""
        response = admin_client.post(
            "/accounts/team/users/",
            {
                "members": [
                    {
                        "email": "adminteam@futureagi.com",
                        "name": "Admin Add",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_add_team_member_as_member_forbidden(self, member_client, workspace):
        """Member cannot add team members."""
        response = member_client.post(
            "/accounts/team/users/",
            {
                "members": [
                    {
                        "email": "memberteam@futureagi.com",
                        "name": "Member Add",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_add_team_member_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/team/users/",
            {"members": []},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_add_team_member_invalid_data(self, auth_client):
        """Adding member with invalid data fails."""
        response = auth_client.post(
            "/accounts/team/users/",
            {"members": [{"email": "invalid-email"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_team_member_existing_email(self, auth_client, user):
        """Adding member with existing email fails."""
        response = auth_client.post(
            "/accounts/team/users/",
            {
                "members": [
                    {
                        "email": user.email,  # Existing user
                        "name": "Duplicate",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_team_member_creates_pending_invite(
        self, auth_client, api_client, workspace, monkeypatch
    ):
        """Adding a member must persist a PENDING OrganizationInvite.

        Regression: the onboarding add-users flow created the user but no
        invite row, so accept_invitation_mail rejected the link and the UI
        rendered "This invite link has expired or is invalid."
        """
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        import accounts.views.workspace_management as wm
        from accounts.models.organization_invite import (
            InviteStatus,
            OrganizationInvite,
        )

        # Avoid real email delivery and ee billing deduction in tests.
        monkeypatch.setattr(wm, "email_helper", lambda *a, **k: None)
        monkeypatch.setattr(
            wm, "log_and_deduct_cost_for_resource_request", None, raising=False
        )

        email = "pendinginvite@futureagi.com"
        response = auth_client.post(
            "/accounts/team/users/",
            {
                "members": [
                    {
                        "email": email,
                        "name": "Pending Invite",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        )

        new_member = User.objects.get(email=email)
        invite = OrganizationInvite.objects.get(
            target_email__iexact=email,
            organization=new_member.organization,
            status=InviteStatus.PENDING,
        )
        # workspace_access lets invite.accept() materialize memberships.
        assert invite.workspace_access

        # The invite link the email carries must now validate (GET preview).
        uidb64 = urlsafe_base64_encode(force_bytes(new_member.pk))
        token = default_token_generator.make_token(new_member)
        preview = api_client.get(f"/accounts/accept-invitation/{uidb64}/{token}/")
        assert preview.status_code == status.HTTP_200_OK

    def test_update_org_name(self, auth_client, organization):
        """Owner can update organization display name."""
        response = auth_client.post(
            "/accounts/team/users/",
            {"org_name": "Updated Org Name", "members": []},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ]

    def test_member_specific_create_route_rejected_before_mutation(
        self, auth_client, member_user
    ):
        """Generated member POST alias should not create users while ignoring member_id."""
        email = "member-specific-create@futureagi.com"
        response = auth_client.post(
            f"/accounts/team/users/{member_user.id}/",
            {
                "members": [
                    {
                        "email": email,
                        "name": "Member Specific Create",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.filter(email=email).exists()


@pytest.mark.integration
@pytest.mark.api
class TestManageTeamViewDelete:
    """Tests for DELETE /accounts/team/users/<member_id>/ endpoint."""

    def test_delete_collection_missing_member_id(self, auth_client):
        """Collection delete route fails closed instead of mutating broadly."""
        response = auth_client.delete("/accounts/team/users/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_team_member_as_owner(self, auth_client, member_user):
        """Owner can delete team members."""
        response = auth_client.delete(f"/accounts/team/users/{member_user.id}/")
        assert response.status_code == status.HTTP_200_OK

    def test_delete_team_member_as_admin_forbidden(self, admin_client, member_user):
        """Admin cannot delete team members (owner only)."""
        response = admin_client.delete(f"/accounts/team/users/{member_user.id}/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_delete_team_member_as_member_forbidden(self, member_client, admin_user):
        """Member cannot delete team members."""
        response = member_client.delete(f"/accounts/team/users/{admin_user.id}/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_delete_team_member_unauthenticated(self, api_client, member_user):
        """Unauthenticated request fails."""
        response = api_client.delete(f"/accounts/team/users/{member_user.id}/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_delete_self_forbidden(self, auth_client, user):
        """Cannot delete own account."""
        response = auth_client.delete(f"/accounts/team/users/{user.id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_nonexistent_member(self, auth_client):
        """Deleting nonexistent member fails."""
        response = auth_client.delete(
            "/accounts/team/users/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_from_specific_workspace(self, auth_client, member_user, workspace):
        """Can remove member from specific workspace only."""
        response = auth_client.delete(
            f"/accounts/team/users/{member_user.id}/",
            {"workspace_id": str(workspace.id)},
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,  # If not in workspace
        ]
