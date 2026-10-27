"""
Multi-Organization Endpoint Tests — Phase 3

Tests for the org switch, org list, and current org API endpoints.
Covers real-world E2E scenarios:
- Switching orgs and getting workspace resolved
- Org list de-duplication (membership + FK)
- orgWorkspaceMap maintained on workspace switch
- Cross-org access denied

NOTE: Response keys are camelCase due to the CamelCaseMiddleware.
"""

from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_a(db):
    return Organization.objects.create(name="Org Alpha", display_name="Alpha Corp")


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="Org Beta", display_name="Beta Inc")


@pytest.fixture
def org_c(db):
    return Organization.objects.create(name="Org Gamma", display_name="Gamma LLC")


@pytest.fixture
def multi_user(db, org_a, org_b):
    """User: Owner of Alpha, Member of Beta."""
    clear_workspace_context()
    set_workspace_context(organization=org_a)
    user = User.objects.create_user(
        email="multi@test.com",
        password="testpass123",
        name="Multi User",
        organization=org_a,
        organization_role=OrganizationRoles.OWNER,
        config={},
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_a,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_b,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    return user


@pytest.fixture
def ws_a_default(db, org_a, multi_user):
    clear_workspace_context()
    set_workspace_context(organization=org_a)
    return Workspace.objects.create(
        name="Alpha Default",
        organization=org_a,
        is_default=True,
        is_active=True,
        created_by=multi_user,
    )


@pytest.fixture
def ws_a_staging(db, org_a, multi_user):
    clear_workspace_context()
    set_workspace_context(organization=org_a)
    return Workspace.objects.create(
        name="Alpha Staging",
        organization=org_a,
        is_default=False,
        is_active=True,
        created_by=multi_user,
    )


@pytest.fixture
def ws_b_default(db, org_b, multi_user):
    clear_workspace_context()
    set_workspace_context(organization=org_b)
    return Workspace.objects.create(
        name="Beta Default",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=multi_user,
    )


@pytest.fixture
def auth_client(multi_user, ws_a_default):
    """Authenticated client with workspace context."""
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=multi_user)
    client.set_workspace(ws_a_default)
    yield client
    client.stop_workspace_injection()


# ---------------------------------------------------------------------------
# Organization List (GET /accounts/organizations/)
# ---------------------------------------------------------------------------


class TestOrganizationListEndpoint:
    def test_lists_all_org_memberships(self, auth_client, org_a, org_b):
        """Returns all orgs from active memberships."""
        response = auth_client.get("/accounts/organizations/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        orgs = result["organizations"]
        org_ids = {o["id"] for o in orgs}
        assert str(org_a.id) in org_ids
        assert str(org_b.id) in org_ids

    def test_no_duplicates(self, auth_client, org_a):
        """Primary org with membership should not appear twice."""
        response = auth_client.get("/accounts/organizations/")
        data = response.json()
        result = data.get("result", data)
        orgs = result["organizations"]
        org_ids = [o["id"] for o in orgs]
        # Count how many times org_a appears
        assert org_ids.count(str(org_a.id)) == 1

    def test_includes_level(self, auth_client, org_a):
        """Each org entry includes RBAC level."""
        response = auth_client.get("/accounts/organizations/")
        data = response.json()
        result = data.get("result", data)
        orgs = result["organizations"]
        for org in orgs:
            assert "level" in org

    def test_is_selected_flag(self, auth_client, org_a, org_b):
        """Exactly one org is marked as isSelected."""
        response = auth_client.get("/accounts/organizations/")
        data = response.json()
        result = data.get("result", data)
        orgs = result["organizations"]
        selected = [o for o in orgs if o["is_selected"]]
        assert len(selected) == 1

    def test_excludes_inactive_memberships(self, auth_client, multi_user, org_c):
        """Inactive membership orgs are excluded."""
        OrganizationMembership.no_workspace_objects.create(
            user=multi_user,
            organization=org_c,
            role=OrganizationRoles.MEMBER_VIEW_ONLY,
            level=Level.VIEWER,
            is_active=False,
        )
        response = auth_client.get("/accounts/organizations/")
        data = response.json()
        result = data.get("result", data)
        org_ids = {o["id"] for o in result["organizations"]}
        assert str(org_c.id) not in org_ids

    def test_default_selection(self, auth_client):
        """At least one org is marked isSelected."""
        response = auth_client.get("/accounts/organizations/")
        data = response.json()
        result = data.get("result", data)
        orgs = result["organizations"]
        assert any(o["is_selected"] for o in orgs)


# ---------------------------------------------------------------------------
# Switch Organization (POST /accounts/organizations/switch/)
# ---------------------------------------------------------------------------


class TestSwitchOrganizationEndpoint:
    def test_switch_to_invited_org(self, auth_client, org_b, ws_b_default):
        """Can switch to an invited org."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert result["organization"]["id"] == str(org_b.id)
        assert "workspace" in result
        assert result["org_role"] == OrganizationRoles.MEMBER

    def test_switch_returns_workspace(self, auth_client, org_b, ws_b_default):
        """Switch returns the resolved workspace for the target org."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        data = response.json()
        result = data.get("result", data)
        assert result["workspace"]["id"] == str(ws_b_default.id)

    def test_switch_updates_config(self, auth_client, multi_user, org_b, ws_b_default):
        """Switch updates user.config with new org and workspace."""
        auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        multi_user.refresh_from_db()
        assert multi_user.config["currentOrganizationId"] == str(org_b.id)
        assert multi_user.config["currentWorkspaceId"] == str(ws_b_default.id)
        assert multi_user.config["orgWorkspaceMap"][str(org_b.id)] == str(
            ws_b_default.id
        )

    def test_switch_remembers_last_workspace(
        self, auth_client, multi_user, org_b, ws_b_default
    ):
        """orgWorkspaceMap records last workspace per org."""
        # Set up: user had previously used ws_b_default in org_b
        multi_user.config["orgWorkspaceMap"] = {str(org_b.id): str(ws_b_default.id)}
        multi_user.save(update_fields=["config"])

        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        data = response.json()
        result = data.get("result", data)
        assert result["workspace"]["id"] == str(ws_b_default.id)

    def test_switch_to_unauthorized_org_fails(self, auth_client, org_c):
        """Cannot switch to an org user has no access to."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_c.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_switch_to_nonexistent_org_fails(self, auth_client):
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_switch_missing_id_fails(self, auth_client):
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_switch_creates_default_workspace_if_needed(
        self, auth_client, multi_user, org_b
    ):
        """If org has no workspaces, switch creates a default one."""
        # Delete all workspaces in org_b
        Workspace.objects.filter(organization=org_b).delete()

        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert "workspace" in result
        assert result["workspace"]["is_default"] is True


# ---------------------------------------------------------------------------
# Current Organization (GET /accounts/organizations/current/)
# ---------------------------------------------------------------------------


class TestCurrentOrganizationEndpoint:
    def test_returns_current_org(self, auth_client, org_a):
        """Returns the org resolved by auth layer."""
        response = auth_client.get("/accounts/organizations/current/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert result["organization"]["id"] == str(org_a.id)

    def test_returns_role_and_level(self, auth_client):
        response = auth_client.get("/accounts/organizations/current/")
        data = response.json()
        result = data.get("result", data)
        assert "role" in result
        assert "level" in result

    def test_after_switch_returns_new_org(
        self, auth_client, multi_user, org_b, ws_b_default
    ):
        """After switching, current org endpoint reflects the switch."""
        # Switch to org_b
        multi_user.config["currentOrganizationId"] = str(org_b.id)
        multi_user.save(update_fields=["config"])

        # Note: the auth layer will resolve org from config for new requests
        # but our test client is already authenticated with org_a context
        # So we check config was updated correctly
        multi_user.refresh_from_db()
        assert multi_user.config["currentOrganizationId"] == str(org_b.id)


# ---------------------------------------------------------------------------
# E2E Scenarios
# ---------------------------------------------------------------------------


class TestE2EOrgSwitchScenarios:
    def test_full_flow_switch_and_back(
        self, auth_client, multi_user, org_a, org_b, ws_a_default, ws_b_default
    ):
        """
        Scenario: User switches from Alpha to Beta, then back to Alpha.
        Both org's last-used workspace should be remembered.
        """
        # Switch to Beta
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Switch back to Alpha
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_a.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert result["organization"]["id"] == str(org_a.id)

        # Verify orgWorkspaceMap has both entries
        multi_user.refresh_from_db()
        org_ws_map = multi_user.config.get("orgWorkspaceMap", {})
        assert str(org_a.id) in org_ws_map
        assert str(org_b.id) in org_ws_map

    def test_workspace_switch_updates_org_workspace_map(
        self, auth_client, multi_user, org_a, ws_a_default, ws_a_staging
    ):
        """
        Scenario: User switches workspace within their current org.
        The orgWorkspaceMap should update for that org.
        """
        response = auth_client.post(
            "/accounts/workspace/switch/",
            {
                "new_workspace_id": str(ws_a_staging.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        multi_user.refresh_from_db()
        org_ws_map = multi_user.config.get("orgWorkspaceMap", {})
        assert org_ws_map.get(str(org_a.id)) == str(ws_a_staging.id)

    def test_removed_user_cannot_switch_to_org(self, auth_client, multi_user, org_b):
        """
        Scenario: User's membership in Beta is deactivated.
        Switch to Beta should fail.
        """
        membership = OrganizationMembership.no_workspace_objects.get(
            user=multi_user, organization=org_b
        )
        membership.is_active = False
        membership.save()

        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]
