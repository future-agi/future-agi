"""
Comprehensive RBAC Tests

Tests for organization-level and workspace-level role-based access control,
including all edge cases, multi-org support, and permission boundaries.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

User = get_user_model()


# Workspace role constants (defined in OrganizationRoles)
class WorkspaceRoles:
    WORKSPACE_ADMIN = OrganizationRoles.WORKSPACE_ADMIN
    WORKSPACE_MEMBER = OrganizationRoles.WORKSPACE_MEMBER
    WORKSPACE_VIEWER = OrganizationRoles.WORKSPACE_VIEWER


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def org_a(db):
    """Create Organization A for multi-org tests."""
    return Organization.objects.create(name="Organization A", display_name="Org A")


@pytest.fixture
def org_b(db):
    """Create Organization B for multi-org tests."""
    return Organization.objects.create(name="Organization B", display_name="Org B")


@pytest.fixture
def owner1(db, org_a):
    """Create primary owner in Organization A."""
    user = User.objects.create_user(
        email="owner1@futureagi.com",
        password="password123",
        name="Owner One",
        organization=org_a,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_a,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    return user


@pytest.fixture
def owner2(db, org_a):
    """Create secondary owner in Organization A."""
    user = User.objects.create_user(
        email="owner2@futureagi.com",
        password="password123",
        name="Owner Two",
        organization=org_a,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_a,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    return user


@pytest.fixture
def admin_user(db, org_a):
    """Create admin user in Organization A."""
    user = User.objects.create_user(
        email="admin@futureagi.com",
        password="password123",
        name="Admin User",
        organization=org_a,
        organization_role=OrganizationRoles.ADMIN,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_a,
        role=OrganizationRoles.ADMIN,
        level=Level.ADMIN,
        is_active=True,
    )
    return user


@pytest.fixture
def member_user(db, org_a):
    """Create member user in Organization A."""
    user = User.objects.create_user(
        email="member@futureagi.com",
        password="password123",
        name="Member User",
        organization=org_a,
        organization_role=OrganizationRoles.MEMBER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_a,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    return user


@pytest.fixture
def viewer_user(db, org_a):
    """Create viewer user in Organization A."""
    user = User.objects.create_user(
        email="viewer@futureagi.com",
        password="password123",
        name="Viewer User",
        organization=org_a,
        organization_role=OrganizationRoles.MEMBER_VIEW_ONLY,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_a,
        role=OrganizationRoles.MEMBER_VIEW_ONLY,
        level=Level.VIEWER,
        is_active=True,
    )
    return user


@pytest.fixture
def workspace_a(db, org_a, owner1):
    """Create workspace in Organization A."""
    return Workspace.objects.create(
        name="Workspace A",
        organization=org_a,
        is_default=True,
        is_active=True,
        created_by=owner1,
    )


# =============================================================================
# TEST CLASS 1: MULTI-ORG SUPPORT
# =============================================================================


@pytest.mark.django_db
class TestMultiOrgSupport:
    """Test that users can belong to multiple organizations."""

    def test_user_can_be_invited_to_multiple_orgs(
        self, api_client, owner1, org_a, org_b
    ):
        """User can be invited to multiple organizations (multi-org support)."""
        from conftest import WorkspaceAwareAPIClient

        # Create owner for org_b
        owner_b = User.objects.create_user(
            email="ownerb@futureagi.com",
            password="password123",
            name="Owner B",
            organization=org_b,
            organization_role=OrganizationRoles.OWNER,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=owner_b,
            organization=org_b,
            role=OrganizationRoles.OWNER,
            level=Level.OWNER,
            is_active=True,
        )

        # Workspace for org_b
        workspace_b = Workspace.objects.create(
            name="Workspace B",
            organization=org_b,
            is_default=True,
            is_active=True,
            created_by=owner_b,
        )

        # Owner B invites Owner1 to org_b as Member
        client_b = WorkspaceAwareAPIClient()
        client_b.force_authenticate(user=owner_b)
        client_b.set_workspace(workspace_b)

        response = client_b.post(
            "/accounts/organization/invite/",
            {
                "emails": ["owner1@futureagi.com"],
                "org_level": Level.MEMBER,
            },
            format="json",
        )

        # Should succeed - multi-org is supported
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]
        assert response.data["status"] is True

        # Verify owner1 now has 2 org memberships
        memberships = OrganizationMembership.no_workspace_objects.filter(
            user=owner1
        ).count()
        assert memberships == 2

        client_b.stop_workspace_injection()

    def test_user_has_different_roles_in_different_orgs(self, owner1, org_a, org_b):
        """User can have different roles in different organizations."""
        # owner1 is Owner in org_a
        membership_a = OrganizationMembership.no_workspace_objects.get(
            user=owner1, organization=org_a
        )
        assert membership_a.role == OrganizationRoles.OWNER

        # Add owner1 to org_b as Member
        OrganizationMembership.no_workspace_objects.create(
            user=owner1,
            organization=org_b,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )

        membership_b = OrganizationMembership.no_workspace_objects.get(
            user=owner1, organization=org_b
        )
        assert membership_b.role == OrganizationRoles.MEMBER

        # Verify different roles
        assert membership_a.role != membership_b.role


# =============================================================================
# TEST CLASS 2: ORGANIZATION OWNER PERMISSIONS
# =============================================================================


@pytest.mark.django_db
class TestOrganizationOwnerPermissions:
    """Test that Owners have full permissions."""

    def test_owner_can_invite_any_role(self, auth_client, org_a):
        """Owner can invite users at any role level."""
        levels_to_test = [
            Level.OWNER,
            Level.ADMIN,
            Level.MEMBER,
            Level.VIEWER,
        ]

        for idx, level in enumerate(levels_to_test):
            response = auth_client.post(
                "/accounts/organization/invite/",
                {
                    "emails": [f"test{idx}@futureagi.com"],
                    "org_level": level,
                },
                format="json",
            )
            assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

    def test_owner_can_change_any_user_role(
        self, api_client, owner1, member_user, workspace_a
    ):
        """Owner can change any user's role."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=owner1)
        client.set_workspace(workspace_a)

        # Change member to admin
        response = client.post(
            "/accounts/organization/members/role/",
            {
                "user_id": str(member_user.id),
                "org_level": Level.ADMIN,
            },
            format="json",
        )

        # Should succeed
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        # Verify role changed
        membership = OrganizationMembership.no_workspace_objects.get(
            user=member_user, organization=owner1.organization
        )
        assert membership.level == Level.ADMIN

        client.stop_workspace_injection()

    def test_owner_can_remove_non_owner_users(
        self, api_client, owner1, member_user, workspace_a
    ):
        """Owner can remove non-owner users from organization."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=owner1)
        client.set_workspace(workspace_a)

        response = client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member_user.id)},
            format="json",
        )

        # Should succeed
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT]

        # Verify user removed (soft deactivate)
        membership = OrganizationMembership.all_objects.get(
            user=member_user, organization=owner1.organization
        )
        assert membership.is_active is False

        client.stop_workspace_injection()


# =============================================================================
# TEST CLASS 3: MULTIPLE OWNERS & LAST OWNER PROTECTION
# =============================================================================


@pytest.mark.django_db
class TestMultipleOwnersAndLastOwnerProtection:
    """Test multiple owner scenarios and last owner protection."""

    def test_multiple_owners_can_exist(self, owner1, owner2, org_a):
        """Organization can have multiple owners."""
        owners = OrganizationMembership.no_workspace_objects.filter(
            organization=org_a, role=OrganizationRoles.OWNER
        )
        assert owners.count() >= 2

    def test_owner_can_demote_another_owner_when_multiple_exist(
        self, api_client, owner1, owner2, workspace_a
    ):
        """Owner can demote another owner when multiple owners exist."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=owner1)
        client.set_workspace(workspace_a)

        # Owner1 demotes Owner2 to Admin
        response = client.post(
            "/accounts/organization/members/role/",
            {
                "user_id": str(owner2.id),
                "org_level": Level.ADMIN,
            },
            format="json",
        )

        # Should succeed (still have owner1 as owner)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        membership = OrganizationMembership.no_workspace_objects.get(
            user=owner2, organization=owner1.organization
        )
        assert membership.level == Level.ADMIN

        client.stop_workspace_injection()

    def test_cannot_demote_last_owner(self, api_client, owner1, workspace_a):
        """Cannot demote the last owner of an organization."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=owner1)
        client.set_workspace(workspace_a)

        # Ensure owner1 is the only owner
        other_owners = OrganizationMembership.no_workspace_objects.filter(
            organization=owner1.organization, role=OrganizationRoles.OWNER
        ).exclude(user=owner1)
        for membership in other_owners:
            membership.role = OrganizationRoles.ADMIN
            membership.save()

        # Verify owner1 is now the only owner
        owner_count = OrganizationMembership.no_workspace_objects.filter(
            organization=owner1.organization,
            role=OrganizationRoles.OWNER,
            deleted=False,
        ).count()
        assert owner_count == 1

        # Try to demote owner1
        response = client.post(
            "/accounts/organization/members/role/",
            {
                "user_id": str(owner1.id),
                "org_level": Level.ADMIN,
            },
            format="json",
        )

        # Should fail
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

        # Verify role unchanged
        membership = OrganizationMembership.no_workspace_objects.get(
            user=owner1, organization=owner1.organization
        )
        assert membership.level == Level.OWNER

        client.stop_workspace_injection()

    def test_cannot_remove_last_owner(self, api_client, owner1, workspace_a):
        """Cannot remove the last owner of an organization."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=owner1)
        client.set_workspace(workspace_a)

        # Ensure owner1 is the only owner
        other_owners = OrganizationMembership.no_workspace_objects.filter(
            organization=owner1.organization, role=OrganizationRoles.OWNER
        ).exclude(user=owner1)
        for membership in other_owners:
            membership.delete()

        owner_count = OrganizationMembership.no_workspace_objects.filter(
            organization=owner1.organization,
            role=OrganizationRoles.OWNER,
            deleted=False,
        ).count()
        assert owner_count == 1

        # Try to remove owner1
        response = client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(owner1.id)},
            format="json",
        )

        # Should fail
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

        # Verify not deactivated
        membership = OrganizationMembership.no_workspace_objects.get(
            user=owner1, organization=owner1.organization
        )
        assert membership.is_active is True

        client.stop_workspace_injection()


# =============================================================================
# TEST CLASS 4: ADMIN PERMISSIONS & RESTRICTIONS
# =============================================================================


@pytest.mark.django_db
class TestAdminPermissionsAndRestrictions:
    """Test that Admins have limited permissions."""

    def test_admin_can_invite_member_and_viewer(
        self, api_client, admin_user, workspace_a, org_a
    ):
        """Admin can invite users as Member or Viewer."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=admin_user)
        client.set_workspace(workspace_a)

        for idx, level in enumerate([Level.MEMBER, Level.VIEWER]):
            response = client.post(
                "/accounts/organization/invite/",
                {
                    "emails": [f"admintest{idx}@futureagi.com"],
                    "org_level": level,
                },
                format="json",
            )
            # Should succeed
            assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        client.stop_workspace_injection()

    def test_admin_cannot_invite_owner(
        self, api_client, admin_user, workspace_a, org_a
    ):
        """Admin cannot invite users as Owner."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=admin_user)
        client.set_workspace(workspace_a)

        response = client.post(
            "/accounts/organization/invite/",
            {
                "emails": ["newowner@futureagi.com"],
                "org_level": Level.OWNER,
            },
            format="json",
        )

        # Should fail
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

        client.stop_workspace_injection()

    def test_admin_can_invite_admin(
        self, api_client, admin_user, workspace_a, org_a
    ):
        """Admin can invite other admins at their own level."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=admin_user)
        client.set_workspace(workspace_a)

        response = client.post(
            "/accounts/organization/invite/",
            {
                "emails": ["newadmin@futureagi.com"],
                "org_level": Level.ADMIN,
            },
            format="json",
        )

        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        client.stop_workspace_injection()

    def test_admin_cannot_change_owner_role(
        self, api_client, admin_user, owner1, workspace_a
    ):
        """Admin cannot change owner's role."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=admin_user)
        client.set_workspace(workspace_a)

        membership = OrganizationMembership.no_workspace_objects.get(
            user=owner1, organization=admin_user.organization
        )

        response = client.post(
            "/accounts/organization/members/role/",
            {
                "member_id": str(membership.id),
                "role": OrganizationRoles.MEMBER,
            },
            format="json",
        )

        # Should fail
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

        # Verify role unchanged
        membership.refresh_from_db()
        assert membership.role == OrganizationRoles.OWNER

        client.stop_workspace_injection()

    def test_admin_cannot_remove_owner(
        self, api_client, admin_user, owner1, workspace_a
    ):
        """Admin cannot remove owners."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=admin_user)
        client.set_workspace(workspace_a)

        membership = OrganizationMembership.no_workspace_objects.get(
            user=owner1, organization=admin_user.organization
        )

        response = client.post(
            "/accounts/organization/members/remove/",
            {"member_id": str(membership.id)},
            format="json",
        )

        # Should fail
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

        # Verify not deleted
        membership.refresh_from_db()
        assert membership.deleted is False

        client.stop_workspace_injection()


# =============================================================================
# TEST CLASS 5: MEMBER & VIEWER RESTRICTIONS
# =============================================================================


@pytest.mark.django_db
class TestMemberAndViewerRestrictions:
    """Test that Members and Viewers have read-only permissions."""

    def test_member_cannot_invite_users(
        self, api_client, member_user, workspace_a, org_a
    ):
        """Member cannot invite users."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=member_user)
        client.set_workspace(workspace_a)

        response = client.post(
            "/accounts/organization/invite/",
            {
                "orgName": org_a.name,
                "members": [
                    {
                        "email": "newuser@futureagi.com",
                        "name": "New User",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ],
            },
            format="json",
        )

        # Should fail
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

        client.stop_workspace_injection()

    def test_viewer_cannot_invite_users(
        self, api_client, viewer_user, workspace_a, org_a
    ):
        """Viewer cannot invite users."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=viewer_user)
        client.set_workspace(workspace_a)

        response = client.post(
            "/accounts/organization/invite/",
            {
                "orgName": org_a.name,
                "members": [
                    {
                        "email": "newuser@futureagi.com",
                        "name": "New User",
                        "organization_role": OrganizationRoles.MEMBER_VIEW_ONLY,
                    }
                ],
            },
            format="json",
        )

        # Should fail
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

        client.stop_workspace_injection()

    def test_member_cannot_view_organization_members(
        self, api_client, member_user, workspace_a
    ):
        """Member cannot view organization members list (admin-only)."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=member_user)
        client.set_workspace(workspace_a)

        response = client.get("/accounts/organization/members/")

        # Should fail - members don't have access to member list
        assert response.status_code == status.HTTP_403_FORBIDDEN

        client.stop_workspace_injection()


# =============================================================================
# TEST CLASS 6: WORKSPACE-LEVEL RBAC
# =============================================================================


@pytest.mark.django_db
class TestWorkspaceLevelRBAC:
    """Test workspace-specific roles and permissions."""

    def test_org_member_with_workspace_admin_can_invite(
        self, api_client, org_a, owner1
    ):
        """Org member with workspace admin permissions can invite to their workspace."""
        from conftest import WorkspaceAwareAPIClient

        # Create workspace with owner1 as creator
        workspace = Workspace.objects.create(
            name="Test WS",
            organization=org_a,
            is_active=True,
            created_by=owner1,
        )

        # Create user who is org-level MEMBER but workspace admin in this workspace
        ws_admin = User.objects.create_user(
            email="wsadmin@futureagi.com",
            password="password123",
            name="WS Admin",
            organization=org_a,
            organization_role=OrganizationRoles.MEMBER,
        )
        org_membership = OrganizationMembership.no_workspace_objects.create(
            user=ws_admin,
            organization=org_a,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )
        WorkspaceMembership.objects.create(
            user=ws_admin,
            workspace=workspace,
            organization_membership=org_membership,
            role=WorkspaceRoles.WORKSPACE_ADMIN,
            level=Level.WORKSPACE_ADMIN,
            is_active=True,
        )

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=ws_admin)
        client.set_workspace(workspace)

        # Org member (workspace admin) invites user
        response = client.post(
            "/accounts/organization/invite/",
            {
                "emails": ["newwsmember@futureagi.com"],
                "org_level": Level.MEMBER,
                "workspace_access": [
                    {
                        "workspace_id": str(workspace.id),
                        "level": Level.WORKSPACE_MEMBER,
                    }
                ],
            },
            format="json",
        )

        # Should succeed - org members can invite
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        client.stop_workspace_injection()

    def test_user_has_different_roles_in_different_workspaces(
        self, api_client, org_a, owner1
    ):
        """User can have different roles in different workspaces."""
        # Create two workspaces
        ws1 = Workspace.objects.create(
            name="Workspace 1", organization=org_a, is_active=True, created_by=owner1
        )
        ws2 = Workspace.objects.create(
            name="Workspace 2", organization=org_a, is_active=True, created_by=owner1
        )

        # Create user
        user = User.objects.create_user(
            email="multiws@futureagi.com",
            password="password123",
            name="Multi WS User",
            organization=org_a,
            organization_role=OrganizationRoles.MEMBER,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_a,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )

        # Add user to ws1 as admin
        WorkspaceMembership.objects.create(
            user=user,
            workspace=ws1,
            role=WorkspaceRoles.WORKSPACE_ADMIN,
            level=15,
            is_active=True,
        )

        # Add user to ws2 as viewer
        WorkspaceMembership.objects.create(
            user=user,
            workspace=ws2,
            role=WorkspaceRoles.WORKSPACE_VIEWER,
            level=5,
            is_active=True,
        )

        # Verify different roles
        ws1_membership = WorkspaceMembership.objects.get(user=user, workspace=ws1)
        ws2_membership = WorkspaceMembership.objects.get(user=user, workspace=ws2)

        assert ws1_membership.role == WorkspaceRoles.WORKSPACE_ADMIN
        assert ws2_membership.role == WorkspaceRoles.WORKSPACE_VIEWER
        assert ws1_membership.role != ws2_membership.role


# =============================================================================
# TEST CLASS 7: EDGE CASES & RACE CONDITIONS
# =============================================================================


@pytest.mark.django_db
class TestEdgeCasesAndRaceConditions:
    """Test edge cases and potential race conditions."""

    def test_cannot_invite_user_with_invalid_email(
        self, api_client, owner1, workspace_a, org_a
    ):
        """Cannot invite user with invalid email format."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=owner1)
        client.set_workspace(workspace_a)

        response = client.post(
            "/accounts/organization/invite/",
            {
                "orgName": org_a.name,
                "members": [
                    {
                        "email": "not-an-email",
                        "name": "Invalid",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ],
            },
            format="json",
        )

        # Should fail
        assert response.status_code in [status.HTTP_400_BAD_REQUEST]

        client.stop_workspace_injection()

    def test_cannot_invite_duplicate_email_to_same_org(
        self, api_client, owner1, member_user, workspace_a, org_a
    ):
        """Cannot invite user who is already a member."""
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=owner1)
        client.set_workspace(workspace_a)

        response = client.post(
            "/accounts/organization/invite/",
            {
                "orgName": org_a.name,
                "members": [
                    {
                        "email": member_user.email,
                        "name": "Duplicate",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ],
            },
            format="json",
        )

        # Should fail or return error in response
        if response.status_code == status.HTTP_200_OK:
            # Check for errors in response
            assert "errors" in response.data.get("result", {})
            assert len(response.data["result"]["errors"]) > 0
        else:
            assert response.status_code == status.HTTP_400_BAD_REQUEST

        client.stop_workspace_injection()

    def test_deleted_membership_can_be_recreated(self, api_client, owner1, org_a):
        """User who was removed can be re-invited (soft deactivate allows this)."""
        from conftest import WorkspaceAwareAPIClient

        # Create and remove a user
        user = User.objects.create_user(
            email="removable@futureagi.com",
            password="password123",
            name="Removable User",
            organization=org_a,
            organization_role=OrganizationRoles.MEMBER,
        )
        membership = OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_a,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )

        # Soft deactivate the membership
        membership.is_active = False
        membership.save()

        # Create workspace for context
        workspace = Workspace.objects.create(
            name="Test WS",
            organization=org_a,
            is_active=True,
            created_by=owner1,
        )

        # Re-invite the user
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=owner1)
        client.set_workspace(workspace)

        response = client.post(
            "/accounts/organization/invite/",
            {
                "emails": [user.email],
                "org_level": Level.MEMBER,
            },
            format="json",
        )

        # Should succeed (re-activates deactivated membership)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        # Verify membership restored
        membership.refresh_from_db()
        assert membership.is_active is True

        client.stop_workspace_injection()


# =============================================================================
# TEST CLASS 8: PERMISSION BOUNDARIES
# =============================================================================


@pytest.mark.django_db
class TestPermissionBoundaries:
    """Test that users cannot access resources outside their organization/workspace."""

    def test_user_cannot_access_another_orgs_resources(
        self, api_client, owner1, org_a, org_b
    ):
        """User cannot access resources from a different organization."""
        from conftest import WorkspaceAwareAPIClient

        # Create workspace in org_b
        owner_b = User.objects.create_user(
            email="ownerb2@futureagi.com",
            password="password123",
            name="Owner B2",
            organization=org_b,
            organization_role=OrganizationRoles.OWNER,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=owner_b,
            organization=org_b,
            role=OrganizationRoles.OWNER,
            level=Level.OWNER,
            is_active=True,
        )
        workspace_b = Workspace.objects.create(
            name="Workspace B",
            organization=org_b,
            is_active=True,
            created_by=owner_b,
        )

        # owner1 (from org_a) tries to access org_b's workspace
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=owner1)
        # Don't set workspace context - simulate cross-org access attempt

        response = client.get(f"/accounts/workspace/{workspace_b.id}/")

        # Should fail (forbidden or not found)
        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]

        client.stop_workspace_injection()

    def test_workspace_member_cannot_access_another_workspace(
        self, api_client, org_a, owner1
    ):
        """Workspace member cannot access workspaces they're not part of."""
        from conftest import WorkspaceAwareAPIClient

        # Create two workspaces
        ws1 = Workspace.objects.create(
            name="Workspace 1", organization=org_a, is_active=True, created_by=owner1
        )
        ws2 = Workspace.objects.create(
            name="Workspace 2", organization=org_a, is_active=True, created_by=owner1
        )

        # Create user only in ws1
        user = User.objects.create_user(
            email="ws1only@futureagi.com",
            password="password123",
            name="WS1 Only",
            organization=org_a,
            organization_role=OrganizationRoles.MEMBER,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_a,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )
        WorkspaceMembership.objects.create(
            user=user,
            workspace=ws1,
            role=WorkspaceRoles.WORKSPACE_MEMBER,
            level=10,
            is_active=True,
        )

        # User tries to access ws2
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(ws1)

        response = client.get(f"/accounts/workspace/{ws2.id}/")

        # Should fail (user not in ws2)
        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]

        client.stop_workspace_injection()
