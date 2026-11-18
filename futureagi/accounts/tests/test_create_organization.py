"""
Tests for Create Additional Organization endpoint (POST /accounts/organizations/new/).

Covers:
- Authenticated user can create a new org
- User becomes Owner via OrganizationMembership
- Default workspace is created
- Primary org FK is NOT changed (user already has one)
- Org-less user gets active membership
- Validation: missing name
- Response structure
"""

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


def _assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def primary_org(db):
    return Organization.objects.create(name="Primary Org", display_name="Primary Org")


@pytest.fixture
def creator_user(db, primary_org):
    """User with an existing primary org."""
    clear_workspace_context()
    set_workspace_context(organization=primary_org)
    user = User.objects.create_user(
        email="creator@test.com",
        password="testpass123",
        name="Creator User",
        organization=primary_org,
        organization_role=OrganizationRoles.OWNER,
        config={},
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=primary_org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    return user


@pytest.fixture
def default_ws(db, primary_org, creator_user):
    clear_workspace_context()
    set_workspace_context(organization=primary_org)
    return Workspace.objects.create(
        name="Primary Default",
        organization=primary_org,
        is_default=True,
        is_active=True,
        created_by=creator_user,
    )


@pytest.fixture
def auth_client(creator_user, default_ws):
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=creator_user)
    client.set_workspace(default_ws)
    yield client
    client.stop_workspace_injection()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateAdditionalOrganization:
    def test_create_org_success(self, auth_client, creator_user):
        """Authenticated user can create a new organization."""
        response = auth_client.post(
            "/accounts/organizations/new/",
            {"name": "New Org", "display_name": "New Organization Inc"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        result = data.get("result", data)
        assert result["organization"]["name"] == "New Org"
        assert result["workspace"]["is_default"] is True
        assert "message" in result

    def test_creates_membership_as_owner(self, auth_client, creator_user):
        """User gets Owner membership in the new org."""
        response = auth_client.post(
            "/accounts/organizations/new/",
            {"name": "Membership Org"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        result = response.json().get("result", response.json())
        org_id = result["organization"]["id"]

        membership = OrganizationMembership.no_workspace_objects.get(
            user=creator_user, organization_id=org_id
        )
        assert membership.role == OrganizationRoles.OWNER
        assert membership.level == Level.OWNER
        assert membership.is_active is True

    def test_creates_default_workspace(self, auth_client, creator_user):
        """New org gets a default workspace."""
        response = auth_client.post(
            "/accounts/organizations/new/",
            {"name": "WS Org"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        result = response.json().get("result", response.json())
        org_id = result["organization"]["id"]

        ws = Workspace.objects.get(organization_id=org_id, is_default=True)
        assert ws.name == "Default Workspace"
        assert ws.is_active is True

    def test_creates_workspace_membership(self, auth_client, creator_user):
        """User gets workspace_admin membership in the default workspace."""
        response = auth_client.post(
            "/accounts/organizations/new/",
            {"name": "WS Membership Org"},
            format="json",
        )
        result = response.json().get("result", response.json())
        ws_id = result["workspace"]["id"]

        ws_membership = WorkspaceMembership.no_workspace_objects.get(
            user=creator_user, workspace_id=ws_id
        )
        assert ws_membership.role == OrganizationRoles.WORKSPACE_ADMIN

    def test_creates_system_api_key(self, auth_client, creator_user):
        """New org gets a system API key."""
        response = auth_client.post(
            "/accounts/organizations/new/",
            {"name": "API Key Org"},
            format="json",
        )
        result = response.json().get("result", response.json())
        org_id = result["organization"]["id"]

        assert OrgApiKey.no_workspace_objects.filter(
            organization_id=org_id, type="system"
        ).exists()

    def test_does_not_change_primary_org_fk(
        self, auth_client, creator_user, primary_org
    ):
        """Creating a new org does NOT change user.organization FK."""
        auth_client.post(
            "/accounts/organizations/new/",
            {"name": "Secondary Org"},
            format="json",
        )
        creator_user.refresh_from_db()
        assert creator_user.organization_id == primary_org.id

    def test_org_less_user_gets_membership(self, db):
        """If user has no primary org, creating one creates an active membership."""
        clear_workspace_context()
        # Create user and org without workspace context to avoid auto-assign signal
        temp_org = Organization.objects.create(name="Temp Org")
        set_workspace_context(organization=temp_org)
        orgless_user = User.objects.create_user(
            email="orgless@test.com",
            password="testpass123",
            name="Orgless User",
            organization=None,
            config={},
        )
        # Force-clear the FK in case a signal set it
        User.objects.filter(id=orgless_user.id).update(organization=None)
        orgless_user.refresh_from_db()
        assert orgless_user.organization is None

        ws = Workspace.objects.create(
            name="Temp WS",
            organization=temp_org,
            is_default=True,
            is_active=True,
            created_by=orgless_user,
        )
        from conftest import WorkspaceAwareAPIClient

        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=orgless_user)
        client.set_workspace(ws)

        response = client.post(
            "/accounts/organizations/new/",
            {"name": "First Org"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        result = response.json().get("result", response.json())
        org_id = result["organization"]["id"]
        # Verify membership created (source of truth, not FK)
        assert OrganizationMembership.no_workspace_objects.filter(
            user=orgless_user, organization_id=org_id, is_active=True
        ).exists()
        client.stop_workspace_injection()

    def test_missing_name_fails(self, auth_client):
        """Name is required."""
        response = auth_client.post(
            "/accounts/organizations/new/",
            {"name": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_name_key_fails(self, auth_client):
        """Empty body fails."""
        response = auth_client.post(
            "/accounts/organizations/new/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_rejects_unknown_request_fields(self, auth_client):
        """Additional org creation should only accept name/display_name."""
        response = auth_client.post(
            "/accounts/organizations/new/",
            {
                "name": "New Org",
                "displayName": "legacy camel alias",
            },
            format="json",
        )
        _assert_unknown_field(response, "displayName")

    def test_display_name_defaults_to_name(self, auth_client):
        """If display_name not provided, it defaults to name."""
        response = auth_client.post(
            "/accounts/organizations/new/",
            {"name": "My Org"},
            format="json",
        )
        result = response.json().get("result", response.json())
        assert result["organization"]["display_name"] == "My Org"

    def test_new_org_appears_in_org_list(self, auth_client, primary_org):
        """After creating, the new org appears in the org list endpoint."""
        auth_client.post(
            "/accounts/organizations/new/",
            {"name": "Listed Org"},
            format="json",
        )
        response = auth_client.get("/accounts/organizations/")
        result = response.json().get("result", response.json())
        org_names = [o["name"] for o in result["organizations"]]
        assert "Listed Org" in org_names

    def test_can_switch_to_new_org(self, auth_client, creator_user):
        """After creating, user can switch to the new org."""
        create_resp = auth_client.post(
            "/accounts/organizations/new/",
            {"name": "Switchable Org"},
            format="json",
        )
        result = create_resp.json().get("result", create_resp.json())
        new_org_id = result["organization"]["id"]

        switch_resp = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": new_org_id},
            format="json",
        )
        assert switch_resp.status_code == status.HTTP_200_OK
        switch_result = switch_resp.json().get("result", switch_resp.json())
        assert switch_result["organization"]["id"] == new_org_id
        assert "workspace" in switch_result

    def test_create_multiple_orgs(self, auth_client, creator_user):
        """User can create multiple additional organizations."""
        for i in range(3):
            response = auth_client.post(
                "/accounts/organizations/new/",
                {"name": f"Org {i}"},
                format="json",
            )
            assert response.status_code == status.HTTP_201_CREATED

        memberships = OrganizationMembership.no_workspace_objects.filter(
            user=creator_user, is_active=True
        )
        # Primary org + 3 new ones
        assert memberships.count() == 4
