"""
Member Removal & Soft-Delete Tests

Tests the complete user-removal lifecycle:
- Admin removes member (soft-deactivate)
- Deactivated member appears in member list
- Removed user can still login (gets requires_org_setup)
- Removed user can create a new organization
- Removed user can be re-invited to the original org
"""

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture(autouse=True)
def _owner_membership(user, organization):
    """Ensure the owner user has an OrganizationMembership.

    The root conftest ``user`` fixture creates User + Workspace but
    does NOT create an OrganizationMembership row.  RBAC permission
    classes (``IsOrganizationAdmin``) look up this row, so we must
    create it here for all tests in this module.
    """
    OrganizationMembership.objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": "Owner",
            "level": Level.OWNER,
            "is_active": True,
        },
    )


@pytest.fixture
def member_user(db, organization, user):
    """Create a second user (member) in the same org, with workspace membership."""
    from tfc.middleware.workspace_context import set_workspace_context

    set_workspace_context(organization=organization)

    member = User.objects.create_user(
        email="member@futureagi.com",
        password="memberpass123",
        name="Member User",
        organization=organization,
        organization_role="Member",
    )

    org_mem = OrganizationMembership.objects.create(
        user=member,
        organization=organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )

    workspace = Workspace.objects.get(organization=organization, is_default=True)
    WorkspaceMembership.objects.create(
        workspace=workspace,
        user=member,
        role="Workspace Viewer",
        level=Level.WORKSPACE_VIEWER,
        is_active=True,
        organization_membership=org_mem,
    )

    return member


def _remove_member(auth_client, member_user):
    """Helper: remove a member via the API and assert success."""
    resp = auth_client.delete(
        "/accounts/organization/members/remove/",
        {"user_id": str(member_user.id)},
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK, resp.json()
    return resp


# ---------------------------------------------------------------------------
# 1. Soft-delete removal
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestMemberRemoval:
    """Tests for DELETE /accounts/organization/members/remove/"""

    def test_remove_member_soft_deactivates(
        self, auth_client, member_user, organization
    ):
        """Removing a member soft-deactivates their memberships (not hard delete)."""
        _remove_member(auth_client, member_user)

        # Org membership still exists but is_active=False
        org_mem = OrganizationMembership.objects.get(
            user=member_user, organization=organization
        )
        assert org_mem.is_active is False

        # Workspace memberships deactivated
        ws_mems = WorkspaceMembership.objects.filter(
            user=member_user, workspace__organization=organization
        )
        for ws_mem in ws_mems:
            assert ws_mem.is_active is False

        # User has no active org membership
        member_user.refresh_from_db()
        assert not OrganizationMembership.objects.filter(
            user=member_user, organization=organization, is_active=True
        ).exists()

    def test_remove_member_preserves_data(self, auth_client, member_user, organization):
        """Soft-delete preserves the membership row for audit/re-invite."""
        _remove_member(auth_client, member_user)

        # Row still exists (not deleted from DB)
        assert OrganizationMembership.objects.filter(
            user=member_user, organization=organization
        ).exists()

        # User account still exists and is active
        member_user.refresh_from_db()
        assert member_user.is_active is True  # Account alive, just no org

    def test_cannot_remove_last_owner(self, auth_client, user):
        """Cannot remove the only owner of the organization."""
        response = auth_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(user.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_remove_nonexistent_member(self, auth_client):
        """Removing a non-member returns error."""
        response = auth_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_remove_requires_auth(self, api_client, member_user):
        """Unauthenticated removal fails."""
        response = api_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member_user.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ---------------------------------------------------------------------------
# 2. Deactivated member in member list
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestDeactivatedMemberList:
    """Tests for GET /accounts/organization/members/ showing deactivated users."""

    def test_deactivated_member_shown_in_list(
        self, auth_client, member_user, organization
    ):
        """After removal, member appears with status=Deactivated."""
        _remove_member(auth_client, member_user)

        response = auth_client.get("/accounts/organization/members/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        results = data.get("result", {}).get("results", [])

        # Find the removed member
        member_entry = next(
            (r for r in results if r.get("email") == member_user.email), None
        )
        assert member_entry is not None
        assert member_entry["status"] == "Deactivated"

    def test_active_member_shown_as_active(self, auth_client, member_user):
        """Active member appears with status=Active."""
        response = auth_client.get("/accounts/organization/members/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        results = data.get("result", {}).get("results", [])

        member_entry = next(
            (r for r in results if r.get("email") == member_user.email), None
        )
        assert member_entry is not None
        assert member_entry["status"] == "Active"


# ---------------------------------------------------------------------------
# 3. Removed user login flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestRemovedUserLogin:
    """Tests for login when user has no organization."""

    def test_orgless_user_login_returns_requires_org_setup(
        self,
        api_client,
        member_user,
        auth_client,
    ):
        """User removed from org gets requires_org_setup=true on login."""
        _remove_member(auth_client, member_user)

        # Login as removed user
        response = api_client.post(
            "/accounts/token/",
            {"email": member_user.email, "password": "memberpass123"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data.get("requires_org_setup") is True
        assert "access" in data
        assert "refresh" in data

    def test_orgless_user_info_returns_requires_org_setup(
        self,
        api_client,
        member_user,
        auth_client,
    ):
        """user-info endpoint returns requires_org_setup for org-less user."""
        _remove_member(auth_client, member_user)

        # Login to get token
        login_resp = api_client.post(
            "/accounts/token/",
            {"email": member_user.email, "password": "memberpass123"},
            format="json",
        )
        access_token = login_resp.json()["access"]

        # Call user-info
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        response = api_client.get("/accounts/user-info/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data.get("requires_org_setup") is True
        assert data.get("default_workspace_id") is None


# ---------------------------------------------------------------------------
# 4. Organization creation for removed users
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestOrganizationCreate:
    """Tests for POST /accounts/organizations/create/"""

    def _assert_unknown_field(self, response, field_name):
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field_name in response.json()["details"]

    def _get_orgless_client(self, api_client, member_user, auth_client):
        """Helper: remove member and return an authenticated client for them."""
        _remove_member(auth_client, member_user)
        login_resp = api_client.post(
            "/accounts/token/",
            {"email": member_user.email, "password": "memberpass123"},
            format="json",
        )
        access_token = login_resp.json()["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        return api_client

    def test_orgless_user_can_create_org(self, api_client, member_user, auth_client):
        """Removed user can create a new organization."""
        client = self._get_orgless_client(api_client, member_user, auth_client)

        response = client.post(
            "/accounts/organizations/create/",
            {"organization_name": "My New Org"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        result = data.get("result", data)
        assert result.get("organization_name") == "My New Org"
        assert result.get("organization_id") is not None
        assert result.get("workspace_id") is not None

        # Verify user now has an active org membership
        new_membership = (
            OrganizationMembership.objects.filter(user=member_user, is_active=True)
            .select_related("organization")
            .first()
        )
        assert new_membership is not None
        assert new_membership.organization.name == "My New Org"
        assert new_membership.role == "Owner"

    def test_create_org_auto_name_from_email(
        self, api_client, member_user, auth_client
    ):
        """Org name auto-derived from email domain when not provided."""
        client = self._get_orgless_client(api_client, member_user, auth_client)

        response = client.post(
            "/accounts/organizations/create/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        result = data.get("result", data)
        # member@futureagi.com → "futureagi"
        assert result.get("organization_name") == "futureagi"

    def test_create_org_creates_default_workspace(
        self, api_client, member_user, auth_client
    ):
        """Creating org also creates a default workspace."""
        client = self._get_orgless_client(api_client, member_user, auth_client)

        response = client.post(
            "/accounts/organizations/create/",
            {"organization_name": "Fresh Org"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        result = response.json().get("result", response.json())

        org_id = result["organization_id"]
        ws = Workspace.objects.filter(organization_id=org_id, is_default=True)
        assert ws.exists()

    def test_user_with_org_cannot_create_another(self, auth_client):
        """User who already has an org is rejected."""
        response = auth_client.post(
            "/accounts/organizations/create/",
            {"organization_name": "Another Org"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_org_requires_auth(self, api_client):
        """Unauthenticated request is rejected."""
        response = api_client.post(
            "/accounts/organizations/create/",
            {"organization_name": "Test Org"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_create_org_rejects_unknown_request_fields(
        self, api_client, member_user, auth_client
    ):
        """Org-less create accepts only the documented snake_case request body."""
        client = self._get_orgless_client(api_client, member_user, auth_client)

        response = client.post(
            "/accounts/organizations/create/",
            {
                "organization_name": "My New Org",
                "organizationName": "legacy camel alias",
            },
            format="json",
        )
        self._assert_unknown_field(response, "organizationName")


# ---------------------------------------------------------------------------
# 5. Re-invite flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestReInviteDeactivatedMember:
    """Tests for re-inviting a previously removed member."""

    def test_reinvite_restores_membership(
        self, auth_client, member_user, organization, workspace
    ):
        """Re-inviting a deactivated member restores their memberships."""
        _remove_member(auth_client, member_user)

        # Verify deactivated
        org_mem = OrganizationMembership.objects.get(
            user=member_user, organization=organization
        )
        assert org_mem.is_active is False

        # Re-invite
        response = auth_client.post(
            "/accounts/organization/invite/",
            {
                "emails": [member_user.email],
                "org_level": Level.MEMBER,
                "workspace_access": [
                    {
                        "workspace_id": str(workspace.id),
                        "level": Level.WORKSPACE_VIEWER,
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()

        # Verify membership restored
        org_mem.refresh_from_db()
        assert org_mem.is_active is True

        # Verify user's primary org restored
        member_user.refresh_from_db()
        assert member_user.organization_id == organization.id


# ---------------------------------------------------------------------------
# 6. End-to-end lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestFullRemovalLifecycle:
    """End-to-end test: remove → login → create org → verify."""

    def test_full_removal_and_recovery_lifecycle(
        self,
        api_client,
        auth_client,
        member_user,
    ):
        """
        Complete lifecycle:
        1. Admin removes member
        2. Member logs in → gets requires_org_setup
        3. Member creates new org
        4. Member can access user-info with new org
        """
        # Step 1: Admin removes member
        _remove_member(auth_client, member_user)

        # Step 2: Member logs in
        login_resp = api_client.post(
            "/accounts/token/",
            {"email": member_user.email, "password": "memberpass123"},
            format="json",
        )
        assert login_resp.status_code == status.HTTP_200_OK
        assert login_resp.json().get("requires_org_setup") is True
        access_token = login_resp.json()["access"]

        # Step 3: Member creates new org
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        create_resp = api_client.post(
            "/accounts/organizations/create/",
            {"organization_name": "Recovery Org"},
            format="json",
        )
        assert create_resp.status_code == status.HTTP_201_CREATED

        # Step 4: Member can now access user-info with new org
        info_resp = api_client.get("/accounts/user-info/")
        assert info_resp.status_code == status.HTTP_200_OK
        data = info_resp.json()
        # Should no longer require org setup
        assert data.get("requires_org_setup") is not True
        assert data.get("default_workspace_id") is not None
