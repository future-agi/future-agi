"""
Multi-Organization Auth Tests — Phase 2

Tests for organization-aware authentication:
- X-Organization-Id header resolution
- Org resolution priority (header → config → FK → first membership)
- Workspace resolution within the resolved org
- orgWorkspaceMap per-org workspace memory
- Cross-org isolation (can't access org B's workspace via org A)

These are integration tests that hit the actual auth layer.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.exceptions import AuthenticationFailed

from accounts.authentication import APIKeyAuthentication, LangfuseBasicAuthentication
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_workspace,
    set_workspace_context,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_primary(db):
    return Organization.objects.create(name="Primary Org")


@pytest.fixture
def org_invited(db):
    return Organization.objects.create(name="Invited Org")


@pytest.fixture
def org_no_access(db):
    return Organization.objects.create(name="No Access Org")


@pytest.fixture
def auth_user(db, org_primary, org_invited):
    """User: Owner of Primary, Member of Invited."""
    clear_workspace_context()
    set_workspace_context(organization=org_primary)
    user = User.objects.create_user(
        email="authuser@test.com",
        password="testpass123",
        name="Auth User",
        organization=org_primary,
        organization_role=OrganizationRoles.OWNER,
        config={},
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_primary,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_invited,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    return user


@pytest.fixture
def ws_primary(db, org_primary, auth_user):
    clear_workspace_context()
    set_workspace_context(organization=org_primary)
    return Workspace.objects.create(
        name="Primary WS",
        organization=org_primary,
        is_default=True,
        is_active=True,
        created_by=auth_user,
    )


@pytest.fixture
def ws_primary_staging(db, org_primary, auth_user):
    clear_workspace_context()
    set_workspace_context(organization=org_primary)
    return Workspace.objects.create(
        name="Primary Staging",
        organization=org_primary,
        is_default=False,
        is_active=True,
        created_by=auth_user,
    )


@pytest.fixture
def ws_invited(db, org_invited, auth_user):
    clear_workspace_context()
    set_workspace_context(organization=org_invited)
    ws = Workspace.objects.create(
        name="Invited WS",
        organization=org_invited,
        is_default=True,
        is_active=True,
        created_by=auth_user,
    )
    org_mem = OrganizationMembership.no_workspace_objects.get(
        user=auth_user,
        organization=org_invited,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        workspace=ws,
        user=auth_user,
        defaults={
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
            "organization_membership": org_mem,
            "is_active": True,
        },
    )
    return ws


@pytest.fixture
def auth_instance():
    return APIKeyAuthentication()


def _make_request(headers=None, method="GET", path="/api/test/", query_params=None):
    """Build a mock request with the given headers and method."""
    request = MagicMock(spec=[])  # spec=[] prevents auto-attributes
    request.method = method
    request.path = path
    request.headers = headers or {}
    request.GET = query_params or {}
    request.META = {}
    return request


# ---------------------------------------------------------------------------
# authenticate tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAPIKeyAuthentication:
    def test_deleted_api_key_is_rejected(self, auth_instance, auth_user, org_primary):
        key = OrgApiKey.no_workspace_objects.create(
            name="Deleted API auth key",
            organization=org_primary,
            type="user",
            user=auth_user,
            deleted=True,
        )
        request = _make_request(
            headers={
                "X-Api-Key": key.api_key,
                "X-Secret-Key": key.secret_key,
            }
        )

        with pytest.raises(AuthenticationFailed):
            auth_instance.authenticate(request)

    def test_deleted_basic_api_key_is_rejected(self, auth_user, org_primary):
        key = OrgApiKey.no_workspace_objects.create(
            name="Deleted basic auth key",
            organization=org_primary,
            type="user",
            user=auth_user,
            deleted=True,
        )
        encoded = base64.b64encode(
            f"{key.api_key}:{key.secret_key}".encode("utf-8")
        ).decode("ascii")
        request = _make_request()
        request.META = {"HTTP_AUTHORIZATION": f"Basic {encoded}"}

        with pytest.raises(AuthenticationFailed):
            LangfuseBasicAuthentication().authenticate(request)


# ---------------------------------------------------------------------------
# _resolve_organization tests
# ---------------------------------------------------------------------------


class TestResolveOrganization:
    """Tests for APIKeyAuthentication._resolve_organization()."""

    def test_header_takes_priority(
        self, auth_instance, auth_user, org_primary, org_invited
    ):
        """X-Organization-Id header overrides everything."""
        # User's config points to Primary, but header says Invited
        auth_user.config["currentOrganizationId"] = str(org_primary.id)
        auth_user.save(update_fields=["config"])

        request = _make_request(headers={"X-Organization-Id": str(org_invited.id)})
        org = auth_instance._resolve_organization(request, auth_user)
        assert org.id == org_invited.id

    def test_config_used_when_no_header(self, auth_instance, auth_user, org_invited):
        """Falls back to user.config['currentOrganizationId'] when no header."""
        auth_user.config["currentOrganizationId"] = str(org_invited.id)
        auth_user.save(update_fields=["config"])

        request = _make_request()
        org = auth_instance._resolve_organization(request, auth_user)
        assert org.id == org_invited.id

    def test_first_membership_when_no_header_no_config(self, auth_instance, auth_user):
        """Falls back to first active membership when no header/config."""
        request = _make_request()
        org = auth_instance._resolve_organization(request, auth_user)
        # Should resolve to any active membership's org
        assert org is not None
        assert OrganizationMembership.no_workspace_objects.filter(
            user=auth_user, organization=org, is_active=True
        ).exists()

    def test_first_membership_when_no_fk(self, db, auth_instance, org_invited):
        """User with no FK gets first active membership's org."""
        clear_workspace_context()
        user = User.objects.create_user(
            email="nofk@test.com",
            password="testpass123",
            name="No FK User",
            organization=None,
            config={},
        )
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_invited,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )

        request = _make_request()
        org = auth_instance._resolve_organization(request, user)
        assert org.id == org_invited.id

    def test_returns_none_when_no_org_at_all(self, db, auth_instance):
        """User with no FK and no memberships gets None."""
        user = User.objects.create_user(
            email="orphan@test.com",
            password="testpass123",
            name="Orphan",
            config={},
        )
        request = _make_request()
        org = auth_instance._resolve_organization(request, user)
        assert org is None

    def test_invalid_header_falls_through(self, auth_instance, auth_user):
        """Invalid X-Organization-Id falls through to first active membership."""
        request = _make_request(
            headers={"X-Organization-Id": "00000000-0000-0000-0000-000000000000"}
        )
        org = auth_instance._resolve_organization(request, auth_user)
        # Should resolve to any active membership's org
        assert org is not None
        assert OrganizationMembership.no_workspace_objects.filter(
            user=auth_user, organization=org, is_active=True
        ).exists()

    def test_no_access_org_in_header_falls_through(
        self, auth_instance, auth_user, org_no_access
    ):
        """Org in header that user can't access falls through to first membership."""
        request = _make_request(headers={"X-Organization-Id": str(org_no_access.id)})
        org = auth_instance._resolve_organization(request, auth_user)
        # Should resolve to any active membership's org (not the no-access org)
        assert org is not None
        assert org.id != org_no_access.id
        assert OrganizationMembership.no_workspace_objects.filter(
            user=auth_user, organization=org, is_active=True
        ).exists()

    def test_stale_config_falls_through(self, auth_instance, auth_user):
        """Stale org in config (deleted) falls through to first membership."""
        auth_user.config["currentOrganizationId"] = (
            "00000000-0000-0000-0000-999999999999"
        )
        auth_user.save(update_fields=["config"])

        request = _make_request()
        org = auth_instance._resolve_organization(request, auth_user)
        # Should resolve to any active membership's org
        assert org is not None
        assert OrganizationMembership.no_workspace_objects.filter(
            user=auth_user, organization=org, is_active=True
        ).exists()

    def test_query_param_works(self, auth_instance, auth_user, org_invited):
        """organization_id query param is also supported."""
        request = _make_request(query_params={"organization_id": str(org_invited.id)})
        org = auth_instance._resolve_organization(request, auth_user)
        assert org.id == org_invited.id

    def test_api_key_org_takes_priority(self, auth_instance, auth_user, org_invited):
        """API key's organization overrides everything."""
        request = _make_request(headers={"X-Organization-Id": str(org_invited.id)})
        mock_api_key = MagicMock()
        mock_api_key.organization = org_invited
        request.org_api_key = mock_api_key

        org = auth_instance._resolve_organization(request, auth_user)
        assert org.id == org_invited.id


# ---------------------------------------------------------------------------
# _get_requested_workspace tests
# ---------------------------------------------------------------------------


class TestGetRequestedWorkspace:
    """Tests for _get_requested_workspace with org parameter."""

    def test_workspace_header_within_org(
        self, auth_instance, auth_user, org_primary, ws_primary
    ):
        """X-Workspace-Id resolves within the given org."""
        request = _make_request(headers={"X-Workspace-Id": str(ws_primary.id)})
        ws = auth_instance._get_requested_workspace(request, auth_user, org_primary)
        assert ws.id == ws_primary.id

    def test_workspace_header_wrong_org_returns_none(
        self, auth_instance, auth_user, org_primary, ws_invited
    ):
        """Workspace from a different org is rejected."""
        request = _make_request(headers={"X-Workspace-Id": str(ws_invited.id)})
        ws = auth_instance._get_requested_workspace(request, auth_user, org_primary)
        assert ws is None

    def test_no_header_returns_none(self, auth_instance, auth_user, org_primary):
        request = _make_request()
        ws = auth_instance._get_requested_workspace(request, auth_user, org_primary)
        assert ws is None

    def test_invalid_workspace_id_returns_none(
        self, auth_instance, auth_user, org_primary
    ):
        request = _make_request(
            headers={"X-Workspace-Id": "00000000-0000-0000-0000-000000000000"}
        )
        ws = auth_instance._get_requested_workspace(request, auth_user, org_primary)
        assert ws is None


# ---------------------------------------------------------------------------
# _get_user_default_workspace tests
# ---------------------------------------------------------------------------


class TestGetUserDefaultWorkspace:
    """Tests for _get_user_default_workspace with org parameter."""

    def test_org_workspace_map_preferred(
        self, auth_instance, auth_user, org_primary, ws_primary, ws_primary_staging
    ):
        """orgWorkspaceMap takes priority over legacy config."""
        auth_user.config["orgWorkspaceMap"] = {
            str(org_primary.id): str(ws_primary_staging.id)
        }
        auth_user.config["currentWorkspaceId"] = str(ws_primary.id)
        auth_user.save(update_fields=["config"])

        ws = auth_instance._get_user_default_workspace(auth_user, org_primary)
        assert ws.id == ws_primary_staging.id

    def test_legacy_config_fallback(
        self, auth_instance, auth_user, org_primary, ws_primary
    ):
        """Legacy currentWorkspaceId used when orgWorkspaceMap has no entry."""
        auth_user.config["currentWorkspaceId"] = str(ws_primary.id)
        auth_user.save(update_fields=["config"])

        ws = auth_instance._get_user_default_workspace(auth_user, org_primary)
        assert ws.id == ws_primary.id

    def test_legacy_config_wrong_org_ignored(
        self, auth_instance, auth_user, org_invited, ws_primary, ws_invited
    ):
        """Legacy workspace from different org is not used for this org."""
        auth_user.config["currentWorkspaceId"] = str(ws_primary.id)
        auth_user.save(update_fields=["config"])

        ws = auth_instance._get_user_default_workspace(auth_user, org_invited)
        # Should get invited org's default workspace, not primary's
        assert ws.id == ws_invited.id

    def test_creates_default_workspace_if_missing(
        self, auth_instance, auth_user, org_invited
    ):
        """Creates default workspace for org if none exists."""
        # org_invited has no workspaces
        ws = auth_instance._get_user_default_workspace(auth_user, org_invited)
        assert ws is not None
        assert ws.organization_id == org_invited.id
        assert ws.is_default is True

    def test_returns_none_when_no_org(self, auth_instance, auth_user):
        ws = auth_instance._get_user_default_workspace(auth_user, None)
        assert ws is None


# ---------------------------------------------------------------------------
# Full _set_workspace_context integration tests
# ---------------------------------------------------------------------------


class TestSetWorkspaceContextIntegration:
    """Integration tests for the full _set_workspace_context flow."""

    def test_org_header_sets_context(
        self, auth_instance, auth_user, org_invited, ws_invited
    ):
        """X-Organization-Id header correctly sets request.organization."""
        request = _make_request(
            headers={
                "X-Organization-Id": str(org_invited.id),
                "X-Workspace-Id": str(ws_invited.id),
            }
        )
        auth_instance._set_workspace_context(request, auth_user)

        assert request.organization.id == org_invited.id
        assert request.workspace.id == ws_invited.id

    def test_default_org_resolution(
        self, auth_instance, auth_user, org_primary, ws_primary
    ):
        """Config org → resolves to that org + default workspace."""
        # Set config to org_primary (as login flow would)
        auth_user.config["currentOrganizationId"] = str(org_primary.id)
        auth_user.save(update_fields=["config"])

        request = _make_request()
        auth_instance._set_workspace_context(request, auth_user)

        assert request.organization.id == org_primary.id
        assert request.workspace is not None

    def test_thread_local_matches_request(
        self, auth_instance, auth_user, org_invited, ws_invited
    ):
        """Thread-local context matches request attributes."""
        request = _make_request(
            headers={
                "X-Organization-Id": str(org_invited.id),
                "X-Workspace-Id": str(ws_invited.id),
            }
        )
        auth_instance._set_workspace_context(request, auth_user)

        assert get_current_organization().id == org_invited.id
        assert get_current_workspace().id == ws_invited.id

    def test_workspace_cross_org_rejected(
        self, auth_instance, auth_user, org_primary, ws_invited
    ):
        """Workspace from Org B rejected when Org A is current."""
        request = _make_request(
            headers={
                "X-Organization-Id": str(org_primary.id),
                "X-Workspace-Id": str(ws_invited.id),  # belongs to invited, not primary
            }
        )
        auth_instance._set_workspace_context(request, auth_user)

        assert request.organization.id == org_primary.id
        # Workspace should NOT be ws_invited (wrong org)
        assert request.workspace is None or request.workspace.id != ws_invited.id

    def test_orgless_user_gets_none(self, db, auth_instance):
        """User with no org at all gets None for both."""
        user = User.objects.create_user(
            email="noorg@test.com",
            password="testpass123",
            name="No Org",
            config={},
        )
        request = _make_request()
        auth_instance._set_workspace_context(request, user)

        assert request.organization is None
        assert request.workspace is None

    def test_config_org_switches_workspace_context(
        self, auth_instance, auth_user, org_invited, ws_invited
    ):
        """User with config pointing to Invited org gets Invited workspace."""
        auth_user.config["currentOrganizationId"] = str(org_invited.id)
        auth_user.config["orgWorkspaceMap"] = {str(org_invited.id): str(ws_invited.id)}
        auth_user.save(update_fields=["config"])

        request = _make_request()
        auth_instance._set_workspace_context(request, auth_user)

        assert request.organization.id == org_invited.id
        assert request.workspace.id == ws_invited.id


# ---------------------------------------------------------------------------
# E2E Scenarios
# ---------------------------------------------------------------------------


class TestE2EAuthScenarios:
    """End-to-end auth scenarios for multi-org."""

    def test_scenario_two_tabs_different_orgs(
        self, auth_instance, auth_user, org_primary, org_invited, ws_primary, ws_invited
    ):
        """
        Scenario: User has two browser tabs open.
        Tab 1 sends X-Organization-Id for Primary.
        Tab 2 sends X-Organization-Id for Invited.
        Each should resolve independently.
        """
        # Tab 1
        request1 = _make_request(
            headers={
                "X-Organization-Id": str(org_primary.id),
                "X-Workspace-Id": str(ws_primary.id),
            }
        )
        auth_instance._set_workspace_context(request1, auth_user)
        assert request1.organization.id == org_primary.id
        assert request1.workspace.id == ws_primary.id

        clear_workspace_context()

        # Tab 2
        request2 = _make_request(
            headers={
                "X-Organization-Id": str(org_invited.id),
                "X-Workspace-Id": str(ws_invited.id),
            }
        )
        auth_instance._set_workspace_context(request2, auth_user)
        assert request2.organization.id == org_invited.id
        assert request2.workspace.id == ws_invited.id

    def test_scenario_user_removed_during_session(
        self, auth_instance, auth_user, org_invited, org_primary, ws_primary
    ):
        """
        Scenario: User has header pointing to Invited org, but membership
        was just deactivated. Should fall through to FK org.
        """
        # Deactivate invited membership
        membership = OrganizationMembership.no_workspace_objects.get(
            user=auth_user, organization=org_invited
        )
        membership.is_active = False
        membership.save()

        request = _make_request(headers={"X-Organization-Id": str(org_invited.id)})
        auth_instance._set_workspace_context(request, auth_user)

        # Should fall back to primary org
        assert request.organization.id == org_primary.id

    def test_scenario_fresh_login_uses_last_org(
        self, auth_instance, auth_user, org_invited, ws_invited
    ):
        """
        Scenario: User logs in (no headers). Their config has
        currentOrganizationId from last session. Should use it.
        """
        auth_user.config["currentOrganizationId"] = str(org_invited.id)
        auth_user.config["orgWorkspaceMap"] = {str(org_invited.id): str(ws_invited.id)}
        auth_user.save(update_fields=["config"])

        request = _make_request()
        auth_instance._set_workspace_context(request, auth_user)

        assert request.organization.id == org_invited.id
        assert request.workspace.id == ws_invited.id

    def test_scenario_org_workspace_map_memory(
        self,
        auth_instance,
        auth_user,
        org_primary,
        org_invited,
        ws_primary,
        ws_primary_staging,
        ws_invited,
    ):
        """
        Scenario: User switches between orgs. Each org remembers
        the last-used workspace via orgWorkspaceMap.
        """
        auth_user.config["orgWorkspaceMap"] = {
            str(org_primary.id): str(ws_primary_staging.id),
            str(org_invited.id): str(ws_invited.id),
        }
        auth_user.save(update_fields=["config"])

        # Request to primary org
        request1 = _make_request(headers={"X-Organization-Id": str(org_primary.id)})
        auth_instance._set_workspace_context(request1, auth_user)
        assert request1.workspace.id == ws_primary_staging.id

        clear_workspace_context()

        # Request to invited org
        request2 = _make_request(headers={"X-Organization-Id": str(org_invited.id)})
        auth_instance._set_workspace_context(request2, auth_user)
        assert request2.workspace.id == ws_invited.id
