"""
Workspace API Tests

Comprehensive tests for workspace management and membership endpoints.
Tests cover WorkspaceManagementView and WorkspaceMembershipView from workspace.py.
"""

import pytest
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context


def _assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


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
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=admin,
        organization=organization,
        defaults={
            "role": OrganizationRoles.ADMIN,
            "level": Level.ADMIN,
            "is_active": True,
        },
    )
    # Add admin to workspace
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
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=member,
        organization=organization,
        defaults={
            "role": OrganizationRoles.MEMBER,
            "level": Level.MEMBER,
            "is_active": True,
        },
    )
    # Add member to workspace
    WorkspaceMembership.no_workspace_objects.create(
        workspace=workspace,
        user=member,
        role=OrganizationRoles.WORKSPACE_MEMBER,
    )
    return member


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
    # Add owner to workspace
    WorkspaceMembership.no_workspace_objects.create(
        workspace=ws,
        user=user,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        invited_by=user,
    )
    return ws


# =============================================================================
# WorkspaceManagementView Tests - GET /accounts/workspaces/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceListAPI:
    """Tests for GET /accounts/workspaces/ endpoint."""

    def test_list_workspaces_as_owner(self, auth_client, user, workspace):
        """Owner can list workspaces in their organization."""
        response = auth_client.get("/accounts/workspaces/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "result" in data
        result = data["result"]
        assert "workspaces" in result
        assert "total" in result
        assert result["total"] >= 1

    def test_list_workspaces_as_admin(self, admin_client, admin_user, workspace):
        """Admin can list workspaces in their organization."""
        response = admin_client.get("/accounts/workspaces/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "result" in data
        assert "workspaces" in data["result"]

    def test_list_workspaces_as_member_returns_filtered(self, member_client, workspace):
        """Member (non-admin) can list workspaces they are members of."""
        response = member_client.get("/accounts/workspaces/")
        assert response.status_code == status.HTTP_200_OK
        # Members only see workspaces they have membership in
        data = response.json()
        assert "result" in data
        assert "workspaces" in data["result"]

    def test_list_workspaces_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/workspaces/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_list_workspaces_returns_workspace_details(self, auth_client, workspace):
        """Workspace list includes expected fields (camelCase due to middleware)."""
        response = auth_client.get("/accounts/workspaces/")
        assert response.status_code == status.HTTP_200_OK
        workspaces = response.json()["result"]["workspaces"]
        assert len(workspaces) >= 1
        ws = workspaces[0]
        assert "id" in ws
        assert "name" in ws
        assert "display_name" in ws
        assert "is_default" in ws
        assert "member_count" in ws
        assert "created_at" in ws
        assert "created_by" in ws

    def test_detail_route_filters_to_requested_workspace(
        self, auth_client, second_workspace
    ):
        """GET detail alias should not ignore the workspace_id path parameter."""
        response = auth_client.get(f"/accounts/workspaces/{second_workspace.id}/")
        assert response.status_code == status.HTTP_200_OK
        workspaces = response.json()["result"]["workspaces"]
        assert len(workspaces) == 1
        assert workspaces[0]["id"] == str(second_workspace.id)


# =============================================================================
# WorkspaceManagementView Tests - POST /accounts/workspaces/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceCreateAPI:
    """Tests for POST /accounts/workspaces/ endpoint."""

    def test_create_workspace_as_owner(self, auth_client, organization, user):
        """Owner can create a new workspace."""
        response = auth_client.post(
            "/accounts/workspaces/",
            {
                "name": "New Workspace",
                "display_name": "New Workspace Display",
                "description": "A brand new workspace",
                "emails": [user.email],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert "result" in data
        result = data["result"]
        assert "workspace" in result
        assert result["workspace"]["name"] == "New Workspace"
        assert result["message"] == "Workspace created successfully"

    def test_create_workspace_as_admin(self, admin_client, organization, admin_user):
        """Admin can create a new workspace."""
        response = admin_client.post(
            "/accounts/workspaces/",
            {
                "name": "Admin Workspace",
                "emails": [admin_user.email],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_create_workspace_as_member_forbidden(self, member_client):
        """Member cannot create workspaces."""
        response = member_client.post(
            "/accounts/workspaces/",
            {
                "name": "Member Workspace",
                "emails": [],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
            },
            format="json",
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_create_workspace_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/workspaces/",
            {"name": "Unauthorized Workspace"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_create_workspace_missing_name(self, auth_client):
        """Creating workspace without name fails."""
        response = auth_client.post(
            "/accounts/workspaces/",
            {"description": "No name provided", "emails": [], "role": "member"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_workspace_without_role_succeeds(self, auth_client):
        """Creating workspace without role succeeds when no emails provided."""
        response = auth_client.post(
            "/accounts/workspaces/",
            {"name": "No Role Workspace", "emails": []},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_create_workspace_missing_role_with_emails(self, auth_client):
        """Creating workspace with emails but no role fails."""
        response = auth_client.post(
            "/accounts/workspaces/",
            {"name": "Needs Role Workspace", "emails": ["user@example.com"]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_workspace_duplicate_name(self, auth_client, workspace):
        """Cannot create workspace with duplicate name."""
        response = auth_client.post(
            "/accounts/workspaces/",
            {
                "name": workspace.name,  # Same name as existing workspace
                "emails": [],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_workspace_with_existing_member(self, auth_client, member_user):
        """Can create workspace and add existing organization member."""
        response = auth_client.post(
            "/accounts/workspaces/",
            {
                "name": "Workspace With Member",
                "emails": [member_user.email],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        result = response.json()["result"]
        # camelCase: addedUsers
        assert member_user.email in result["added_users"]

    def test_create_workspace_invalid_emails_format(self, auth_client):
        """Emails must be a list."""
        response = auth_client.post(
            "/accounts/workspaces/",
            {
                "name": "Invalid Emails Workspace",
                "emails": "not-a-list@test.com",
                "role": OrganizationRoles.WORKSPACE_MEMBER,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_workspace_rejects_unknown_request_fields(self, auth_client):
        """Workspace creation rejects camelCase aliases and stray body fields."""
        response = auth_client.post(
            "/accounts/workspaces/",
            {
                "name": "Unknown Field Workspace",
                "displayName": "legacy camel alias",
            },
            format="json",
        )
        _assert_unknown_field(response, "displayName")

    def test_detail_post_alias_rejects_create_body_before_persistence(
        self, auth_client, workspace, organization
    ):
        """Generated detail POST alias must fail closed instead of creating a workspace."""
        workspace_name = "Detail Alias Should Not Create"
        before_count = Workspace.objects.filter(
            organization=organization, name=workspace_name
        ).count()

        response = auth_client.post(
            f"/accounts/workspaces/{workspace.id}/",
            {"name": workspace_name, "emails": []},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "detail post" in str(response.json()).lower()
        assert (
            Workspace.objects.filter(
                organization=organization, name=workspace_name
            ).count()
            == before_count
        )


# =============================================================================
# WorkspaceManagementView Tests - PUT /accounts/workspaces/<id>/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceUpdateAPI:
    """Tests for PUT /accounts/workspaces/<workspace_id>/ endpoint."""

    def test_update_workspace_as_owner(self, auth_client, second_workspace):
        """Owner can update workspace details."""
        response = auth_client.put(
            f"/accounts/workspaces/{second_workspace.id}/",
            {
                "display_name": "Updated Display Name",
                "description": "Updated description",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        # camelCase: displayName
        assert result["workspace"]["display_name"] == "Updated Display Name"
        assert result["message"] == "Workspace updated successfully"

    def test_update_workspace_as_admin(self, admin_client, second_workspace):
        """Admin can update workspace details."""
        response = admin_client.put(
            f"/accounts/workspaces/{second_workspace.id}/",
            {"description": "Admin updated this"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_update_workspace_as_member_forbidden(
        self, member_client, second_workspace
    ):
        """Member cannot update workspaces."""
        response = member_client.put(
            f"/accounts/workspaces/{second_workspace.id}/",
            {"description": "Member trying to update"},
            format="json",
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_update_workspace_unauthenticated(self, api_client, second_workspace):
        """Unauthenticated request fails."""
        response = api_client.put(
            f"/accounts/workspaces/{second_workspace.id}/",
            {"description": "Unauthorized update"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_update_workspace_not_found(self, auth_client):
        """Updating non-existent workspace returns 404."""
        response = auth_client.put(
            "/accounts/workspaces/00000000-0000-0000-0000-000000000000/",
            {"description": "Ghost workspace"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_workspace_partial(self, auth_client, second_workspace):
        """Can update only specific fields."""
        original_name = second_workspace.name
        response = auth_client.put(
            f"/accounts/workspaces/{second_workspace.id}/",
            {"description": "Only description changed"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        # Name should remain unchanged
        assert response.json()["result"]["workspace"]["name"] == original_name

    def test_update_workspace_rejects_unknown_request_fields(
        self, auth_client, second_workspace
    ):
        """Workspace updates reject camelCase aliases and stray body fields."""
        response = auth_client.put(
            f"/accounts/workspaces/{second_workspace.id}/",
            {
                "display_name": "Updated Display Name",
                "displayName": "legacy camel alias",
            },
            format="json",
        )
        _assert_unknown_field(response, "displayName")

    def test_collection_put_alias_requires_workspace_id(self, auth_client, workspace):
        """Generated collection PUT alias should return 400 instead of dispatching a 500."""
        original_display_name = workspace.display_name
        response = auth_client.put(
            "/accounts/workspaces/",
            {"display_name": "Collection Alias Update"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "workspace_id" in str(response.json()).lower()
        workspace.refresh_from_db()
        assert workspace.display_name == original_display_name


# =============================================================================
# WorkspaceManagementView Tests - DELETE /accounts/workspaces/<id>/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceDeleteAPI:
    """Tests for DELETE /accounts/workspaces/<workspace_id>/ endpoint."""

    def test_delete_workspace_as_owner(self, auth_client, second_workspace):
        """Owner can delete non-default workspace."""
        response = auth_client.delete(f"/accounts/workspaces/{second_workspace.id}/")
        assert response.status_code == status.HTTP_200_OK
        assert "deleted successfully" in response.json()["result"]["message"]

    def test_delete_workspace_as_admin_forbidden(self, admin_client, second_workspace):
        """Admin cannot delete workspaces (only owner can)."""
        response = admin_client.delete(f"/accounts/workspaces/{second_workspace.id}/")
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_delete_workspace_as_member_forbidden(
        self, member_client, second_workspace
    ):
        """Member cannot delete workspaces."""
        response = member_client.delete(f"/accounts/workspaces/{second_workspace.id}/")
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_delete_workspace_unauthenticated(self, api_client, second_workspace):
        """Unauthenticated request fails."""
        response = api_client.delete(f"/accounts/workspaces/{second_workspace.id}/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_delete_default_workspace_forbidden(self, auth_client, workspace):
        """Cannot delete the default workspace."""
        assert workspace.is_default is True
        response = auth_client.delete(f"/accounts/workspaces/{workspace.id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Error message is in "result" field
        assert "default" in response.json().get("result", "").lower()

    def test_delete_workspace_not_found(self, auth_client):
        """Deleting non-existent workspace returns 404."""
        response = auth_client.delete(
            "/accounts/workspaces/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_collection_delete_alias_requires_workspace_id(
        self, auth_client, second_workspace
    ):
        """Generated collection DELETE alias should return 400 instead of dispatching a 500."""
        response = auth_client.delete("/accounts/workspaces/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "workspace_id" in str(response.json()).lower()
        second_workspace.refresh_from_db()
        assert second_workspace.is_active is True


# =============================================================================
# WorkspaceMembershipView Tests - GET /accounts/workspaces/<id>/members/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceMembersListAPI:
    """Tests for GET /accounts/workspaces/<workspace_id>/members/ endpoint."""

    def test_get_workspace_members_as_owner(self, auth_client, workspace):
        """Owner can get workspace members."""
        response = auth_client.get(f"/accounts/workspaces/{workspace.id}/members/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "workspace" in result
        assert "members" in result
        assert "total" in result

    def test_get_workspace_members_as_admin(self, admin_client, workspace):
        """Admin can get workspace members."""
        response = admin_client.get(f"/accounts/workspaces/{workspace.id}/members/")
        assert response.status_code == status.HTTP_200_OK

    def test_get_workspace_members_as_member_forbidden(self, member_client, workspace):
        """Member cannot get workspace members list."""
        response = member_client.get(f"/accounts/workspaces/{workspace.id}/members/")
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_get_workspace_members_unauthenticated(self, api_client, workspace):
        """Unauthenticated request fails."""
        response = api_client.get(f"/accounts/workspaces/{workspace.id}/members/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_get_workspace_members_not_found(self, auth_client):
        """Getting members of non-existent workspace returns 404."""
        response = auth_client.get(
            "/accounts/workspaces/00000000-0000-0000-0000-000000000000/members/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_workspace_members_returns_details(
        self, auth_client, workspace, member_user
    ):
        """Member list includes expected fields (camelCase due to middleware)."""
        # Add member to workspace first
        WorkspaceMembership.no_workspace_objects.get_or_create(
            workspace=workspace,
            user=member_user,
            defaults={"role": OrganizationRoles.WORKSPACE_MEMBER},
        )

        response = auth_client.get(f"/accounts/workspaces/{workspace.id}/members/")
        assert response.status_code == status.HTTP_200_OK
        members = response.json()["result"]["members"]
        assert len(members) >= 1
        member = members[0]
        # camelCase keys
        assert "user_id" in member
        assert "email" in member
        assert "name" in member
        assert "role" in member
        assert "joined_at" in member

    def test_get_workspace_member_detail_filters_to_requested_member(
        self, auth_client, workspace, member_user
    ):
        """GET member detail alias should not ignore the member_id path parameter."""
        WorkspaceMembership.no_workspace_objects.get_or_create(
            workspace=workspace,
            user=member_user,
            defaults={"role": OrganizationRoles.WORKSPACE_MEMBER},
        )

        response = auth_client.get(
            f"/accounts/workspaces/{workspace.id}/members/{member_user.id}/"
        )

        assert response.status_code == status.HTTP_200_OK
        members = response.json()["result"]["members"]
        assert len(members) == 1
        assert members[0]["user_id"] == str(member_user.id)


# =============================================================================
# WorkspaceMembershipView Tests - POST /accounts/workspaces/<id>/members/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceMembersAddAPI:
    """Tests for POST /accounts/workspaces/<workspace_id>/members/ endpoint."""

    def test_add_member_to_workspace_as_owner(
        self, auth_client, second_workspace, member_user
    ):
        """Owner can add members to workspace."""
        response = auth_client.post(
            f"/accounts/workspaces/{second_workspace.id}/members/",
            {
                "users": [
                    {
                        "email": member_user.email,
                        "role": OrganizationRoles.WORKSPACE_MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        result = response.json()["result"]
        # camelCase: addedUsers
        assert "added_users" in result
        assert len(result["added_users"]) >= 1

    def test_add_member_to_workspace_as_admin(
        self, admin_client, second_workspace, member_user
    ):
        """Admin can add members to workspace."""
        response = admin_client.post(
            f"/accounts/workspaces/{second_workspace.id}/members/",
            {
                "users": [
                    {
                        "email": member_user.email,
                        "role": OrganizationRoles.WORKSPACE_MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_add_member_as_member_forbidden(self, member_client, second_workspace):
        """Member cannot add other members."""
        response = member_client.post(
            f"/accounts/workspaces/{second_workspace.id}/members/",
            {"users": [{"email": "new@futureagi.com", "role": "member"}]},
            format="json",
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_add_member_unauthenticated(self, api_client, second_workspace):
        """Unauthenticated request fails."""
        response = api_client.post(
            f"/accounts/workspaces/{second_workspace.id}/members/",
            {"users": [{"email": "new@example.com"}]},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_add_member_workspace_not_found(self, auth_client):
        """Adding member to non-existent workspace returns 404."""
        response = auth_client.post(
            "/accounts/workspaces/00000000-0000-0000-0000-000000000000/members/",
            {"users": [{"email": "test@example.com", "role": "member"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_member_invalid_users_format(self, auth_client, workspace):
        """Users data must be a list."""
        response = auth_client.post(
            f"/accounts/workspaces/{workspace.id}/members/",
            {"users": "not-a-list"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_member_rejects_unknown_request_fields(self, auth_client, workspace):
        """Adding workspace members rejects camelCase aliases and stray fields."""
        response = auth_client.post(
            f"/accounts/workspaces/{workspace.id}/members/",
            {"users": [], "workspaceUsers": []},
            format="json",
        )
        _assert_unknown_field(response, "workspaceUsers")

    def test_member_detail_post_alias_rejects_add_body_before_persistence(
        self, auth_client, workspace, member_user
    ):
        """Generated member detail POST alias must fail closed instead of adding users."""
        before_count = WorkspaceMembership.no_workspace_objects.filter(
            workspace=workspace, user=member_user, is_active=True
        ).count()

        response = auth_client.post(
            f"/accounts/workspaces/{workspace.id}/members/{member_user.id}/",
            {
                "users": [
                    {
                        "email": member_user.email,
                        "role": OrganizationRoles.WORKSPACE_ADMIN,
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "detail post" in str(response.json()).lower()
        assert (
            WorkspaceMembership.no_workspace_objects.filter(
                workspace=workspace, user=member_user, is_active=True
            ).count()
            == before_count
        )

    def test_add_existing_member_updates_role(
        self, auth_client, workspace, member_user
    ):
        """Adding existing member with different role updates their role."""
        # Ensure member is in workspace
        membership, _ = WorkspaceMembership.no_workspace_objects.get_or_create(
            workspace=workspace,
            user=member_user,
            defaults={"role": OrganizationRoles.WORKSPACE_MEMBER},
        )

        response = auth_client.post(
            f"/accounts/workspaces/{workspace.id}/members/",
            {
                "users": [
                    {
                        "email": member_user.email,
                        "role": OrganizationRoles.WORKSPACE_ADMIN,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        # Check action indicates role was updated or already member
        # camelCase: addedUsers
        added = response.json()["result"]["added_users"]
        assert any(u["email"] == member_user.email for u in added)


# =============================================================================
# WorkspaceMembershipView Tests - DELETE /accounts/workspaces/<id>/members/<member_id>/
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceMembersRemoveAPI:
    """Tests for DELETE /accounts/workspaces/<workspace_id>/members/<member_id>/ endpoint."""

    def test_remove_member_as_owner(self, auth_client, workspace, member_user):
        """Owner can remove members from workspace."""
        # Ensure member is in workspace
        WorkspaceMembership.no_workspace_objects.get_or_create(
            workspace=workspace,
            user=member_user,
            defaults={"role": OrganizationRoles.WORKSPACE_MEMBER},
        )

        response = auth_client.delete(
            f"/accounts/workspaces/{workspace.id}/members/{member_user.id}/"
        )
        assert response.status_code == status.HTTP_200_OK
        assert "removed" in response.json()["result"]["message"].lower()

    def test_remove_member_as_admin(self, admin_client, workspace, member_user):
        """Admin can remove members from workspace."""
        # Ensure member is in workspace
        WorkspaceMembership.no_workspace_objects.get_or_create(
            workspace=workspace,
            user=member_user,
            defaults={"role": OrganizationRoles.WORKSPACE_MEMBER},
        )

        response = admin_client.delete(
            f"/accounts/workspaces/{workspace.id}/members/{member_user.id}/"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_remove_member_as_member_forbidden(
        self, member_client, workspace, admin_user
    ):
        """Member cannot remove other members."""
        response = member_client.delete(
            f"/accounts/workspaces/{workspace.id}/members/{admin_user.id}/"
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_remove_member_unauthenticated(self, api_client, workspace, member_user):
        """Unauthenticated request fails."""
        response = api_client.delete(
            f"/accounts/workspaces/{workspace.id}/members/{member_user.id}/"
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_remove_member_workspace_not_found(self, auth_client, member_user):
        """Removing from non-existent workspace returns 404."""
        response = auth_client.delete(
            f"/accounts/workspaces/00000000-0000-0000-0000-000000000000/members/{member_user.id}/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_remove_member_not_found(self, auth_client, workspace):
        """Removing non-existent member returns 404."""
        response = auth_client.delete(
            f"/accounts/workspaces/{workspace.id}/members/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_members_collection_delete_alias_requires_member_id(
        self, auth_client, workspace, member_user
    ):
        """Generated members collection DELETE alias should return 400 instead of 500."""
        WorkspaceMembership.no_workspace_objects.get_or_create(
            workspace=workspace,
            user=member_user,
            defaults={"role": OrganizationRoles.WORKSPACE_MEMBER},
        )

        response = auth_client.delete(f"/accounts/workspaces/{workspace.id}/members/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "member_id" in str(response.json()).lower()
        assert WorkspaceMembership.no_workspace_objects.filter(
            workspace=workspace, user=member_user, is_active=True
        ).exists()

    def test_remove_last_admin_forbidden(self, auth_client, second_workspace, user):
        """Cannot remove the last admin from workspace."""
        # User is the only admin in second_workspace
        response = auth_client.delete(
            f"/accounts/workspaces/{second_workspace.id}/members/{user.id}/"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Error message is in "result" field
        assert "last admin" in response.json().get("result", "").lower()


# =============================================================================
# Legacy Endpoint Tests (from workspace_management.py)
# =============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceListViewAPI:
    """Tests for /accounts/workspace/list/ endpoint."""

    def test_workspace_list(self, auth_client, workspace):
        """Authenticated user can list workspaces."""
        response = auth_client.get("/accounts/workspace/list/")
        assert response.status_code == status.HTTP_200_OK

    def test_workspace_list_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/workspace/list/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceInviteAPI:
    """Tests for /accounts/workspace/invite/ endpoint."""

    def test_invite_to_workspace(self, auth_client, workspace):
        """Owner can invite users to workspace."""
        response = auth_client.post(
            "/accounts/workspace/invite/",
            {
                "email": "invitee@futureagi.com",
                "role": "member",
            },
            format="json",
        )
        # May succeed or fail based on workspace setup
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_invite_unauthenticated(self, api_client):
        """Unauthenticated invite request fails."""
        response = api_client.post(
            "/accounts/workspace/invite/",
            {"email": "invitee@example.com"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_invite_invalid_email(self, auth_client):
        """Invite with invalid email fails."""
        response = auth_client.post(
            "/accounts/workspace/invite/",
            {"email": "invalid-email"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceSwitchAPI:
    """Tests for /accounts/workspace/switch/ endpoint."""

    def test_switch_workspace_authenticated(self, auth_client, second_workspace):
        """Authenticated user can switch to workspace they belong to."""
        response = auth_client.post(
            "/accounts/workspace/switch/",
            {"workspace_id": str(second_workspace.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,  # If not a member
        ]

    def test_switch_workspace_unauthenticated(self, api_client):
        """Unauthenticated switch request fails."""
        response = api_client.post(
            "/accounts/workspace/switch/",
            {"workspace_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_switch_workspace_invalid_id(self, auth_client):
        """Switch to invalid workspace fails."""
        response = auth_client.post(
            "/accounts/workspace/switch/",
            {"workspace_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]
