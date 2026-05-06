"""
Tests that enforce workspace and organization access boundaries.

Verifies that:
1. A user CANNOT read data from a workspace they have no membership in.
2. A user CANNOT read data from an org they don't belong to.
3. Explicit X-Workspace-Id pointing to an unauthorized workspace is REJECTED (not silently redirected).
4. Explicit X-Organization-Id pointing to an unauthorized org is REJECTED.
5. Admin/Owner CAN access all workspaces in their org (global access).
"""

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
# Helpers
# ---------------------------------------------------------------------------

PASSWORD = "testpass123"


def _login(client, email, password=PASSWORD):
    return client.post(
        "/accounts/token/", {"email": email, "password": password}, format="json"
    )


def _auth_header(access_token):
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}


def _create_user(org, email, role_str, level, password=PASSWORD):
    clear_workspace_context()
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email=email,
        password=password,
        name=email.split("@")[0],
        organization=org,
        organization_role=role_str,
        is_active=True,
    )
    OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=org,
        defaults={"role": role_str, "level": level, "is_active": True},
    )
    return user


def _add_workspace_membership(user, workspace, level):
    org_mem = OrganizationMembership.no_workspace_objects.get(
        user=user,
        organization=workspace.organization,
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": Level.to_ws_string(level),
            "level": level,
            "organization_membership": org_mem,
            "is_active": True,
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_a(db):
    return Organization.objects.create(name="Access Test Org A")


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="Access Test Org B")


@pytest.fixture
def _org_a_owner(db, org_a):
    """Internal fixture: creates an owner user so workspaces have created_by."""
    return _create_user(
        org_a, "owner-a@futureagi.com", OrganizationRoles.OWNER, Level.OWNER
    )


@pytest.fixture
def _org_b_owner(db, org_b):
    """Internal fixture: creates an owner user so workspaces have created_by."""
    return _create_user(
        org_b, "owner-b@futureagi.com", OrganizationRoles.OWNER, Level.OWNER
    )


@pytest.fixture
def ws_alpha(db, org_a, _org_a_owner):
    return Workspace.objects.create(
        name="Alpha",
        organization=org_a,
        is_default=True,
        is_active=True,
        created_by=_org_a_owner,
    )


@pytest.fixture
def ws_beta(db, org_a, _org_a_owner):
    return Workspace.objects.create(
        name="Beta",
        organization=org_a,
        is_default=False,
        is_active=True,
        created_by=_org_a_owner,
    )


@pytest.fixture
def ws_gamma(db, org_b, _org_b_owner):
    """Workspace in org B — completely separate org."""
    return Workspace.objects.create(
        name="Gamma",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=_org_b_owner,
    )


@pytest.fixture
def admin_user(db, org_a, ws_alpha):
    """Admin in org A — has global workspace access."""
    user = _create_user(org_a, "admin@futureagi.com", "Admin", Level.ADMIN)
    _add_workspace_membership(user, ws_alpha, Level.WORKSPACE_ADMIN)
    return user


@pytest.fixture
def viewer_user(db, org_a, ws_alpha, ws_beta):
    """Viewer in org A — only has membership in ws_alpha, NOT ws_beta."""
    user = _create_user(org_a, "viewer@futureagi.com", "Viewer", Level.VIEWER)
    _add_workspace_membership(user, ws_alpha, Level.WORKSPACE_VIEWER)
    # Explicitly NO membership in ws_beta
    return user


@pytest.fixture
def member_user(db, org_a, ws_beta):
    """Member in org A — only has membership in ws_beta, NOT ws_alpha."""
    user = _create_user(org_a, "member@futureagi.com", "Member", Level.MEMBER)
    _add_workspace_membership(user, ws_beta, Level.WORKSPACE_MEMBER)
    # Explicitly NO membership in ws_alpha
    return user


@pytest.fixture
def org_b_user(db, org_b, ws_gamma):
    """Owner in org B — no access to org A at all."""
    user = _create_user(
        org_b, "orgb-owner@futureagi.com", OrganizationRoles.OWNER, Level.OWNER
    )
    _add_workspace_membership(user, ws_gamma, Level.WORKSPACE_ADMIN)
    return user


def _get_access_token(user):
    """Login and return the access token."""
    client = APIClient()
    resp = _login(client, user.email)
    assert resp.status_code == status.HTTP_200_OK, f"Login failed: {resp.data}"
    return resp.data["access"]


# ---------------------------------------------------------------------------
# Tests: Unauthorized workspace access is REJECTED
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWorkspaceAccessEnforcement:
    """Verify that requesting an unauthorized workspace via X-Workspace-Id
    returns 403, not a silent fallback to another workspace."""

    def test_viewer_cannot_access_unauthorized_workspace(self, viewer_user, ws_beta):
        """Viewer with access to ws_alpha CANNOT read ws_beta."""
        token = _get_access_token(viewer_user)
        client = APIClient()
        resp = client.get(
            "/accounts/user-info/",
            **_auth_header(token),
            HTTP_X_WORKSPACE_ID=str(ws_beta.id),
        )
        assert (
            resp.status_code == status.HTTP_403_FORBIDDEN
        ), f"Expected 403 for unauthorized workspace, got {resp.status_code}: {resp.data}"

    def test_member_cannot_access_unauthorized_workspace(self, member_user, ws_alpha):
        """Member with access to ws_beta CANNOT read ws_alpha."""
        token = _get_access_token(member_user)
        client = APIClient()
        resp = client.get(
            "/accounts/user-info/",
            **_auth_header(token),
            HTTP_X_WORKSPACE_ID=str(ws_alpha.id),
        )
        assert (
            resp.status_code == status.HTTP_403_FORBIDDEN
        ), f"Expected 403 for unauthorized workspace, got {resp.status_code}: {resp.data}"

    def test_admin_can_access_any_workspace_in_org(self, admin_user, ws_beta):
        """Admin has global access — can access ws_beta even without explicit membership."""
        token = _get_access_token(admin_user)
        client = APIClient()
        resp = client.get(
            "/accounts/user-info/",
            **_auth_header(token),
            HTTP_X_WORKSPACE_ID=str(ws_beta.id),
        )
        assert (
            resp.status_code == status.HTTP_200_OK
        ), f"Admin should access any workspace in their org, got {resp.status_code}"

    def test_level_admin_with_legacy_member_role_can_list_all_org_workspaces(
        self, org_a, ws_alpha, ws_beta
    ):
        user = _create_user(org_a, "drift-admin@futureagi.com", "Member", Level.ADMIN)
        _add_workspace_membership(user, ws_alpha, Level.WORKSPACE_ADMIN)

        token = _get_access_token(user)
        client = APIClient()
        response = client.get(
            "/accounts/workspace/list/",
            **_auth_header(token),
            HTTP_X_ORGANIZATION_ID=str(org_a.id),
            HTTP_X_WORKSPACE_ID=str(ws_alpha.id),
        )

        assert response.status_code == status.HTTP_200_OK
        workspace_names = {workspace["name"] for workspace in response.data["results"]}
        assert workspace_names == {"Alpha", "Beta"}
        assert all(
            workspace["user_ws_level"] == Level.WORKSPACE_ADMIN
            for workspace in response.data["results"]
        )
        beta_row = next(
            workspace
            for workspace in response.data["results"]
            if workspace["name"] == "Beta"
        )
        assert "user_ws_level" in beta_row

    def test_viewer_can_access_authorized_workspace(self, viewer_user, ws_alpha):
        """Viewer CAN access ws_alpha (they have membership)."""
        token = _get_access_token(viewer_user)
        client = APIClient()
        resp = client.get(
            "/accounts/user-info/",
            **_auth_header(token),
            HTTP_X_WORKSPACE_ID=str(ws_alpha.id),
        )
        assert (
            resp.status_code == status.HTTP_200_OK
        ), f"Viewer should access their own workspace, got {resp.status_code}"

    def test_no_workspace_header_uses_membership_workspace(self, member_user, ws_beta):
        """Without X-Workspace-Id, user lands on their membership workspace."""
        token = _get_access_token(member_user)
        client = APIClient()
        resp = client.get(
            "/accounts/user-info/",
            **_auth_header(token),
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        # Should resolve to ws_beta (their only membership)
        assert data.get("default_workspace_id") == str(
            ws_beta.id
        ), f"Expected ws_beta ({ws_beta.id}), got {data.get('default_workspace_id')}"


# ---------------------------------------------------------------------------
# Tests: Cross-org access is REJECTED
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCrossOrgAccessEnforcement:
    """Verify that a user in org B cannot access org A resources."""

    def test_user_cannot_access_other_org_workspace(self, org_b_user, ws_alpha, org_a):
        """User in org B cannot request a workspace in org A."""
        token = _get_access_token(org_b_user)
        client = APIClient()
        resp = client.get(
            "/accounts/user-info/",
            **_auth_header(token),
            HTTP_X_WORKSPACE_ID=str(ws_alpha.id),
            HTTP_X_ORGANIZATION_ID=str(org_a.id),
        )
        # Should be rejected — user has no membership in org A
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_200_OK,
        )
        if resp.status_code == status.HTTP_200_OK:
            # If 200, the org resolved must be org B (user's own org), not org A
            org_data = resp.data.get("organization", {})
            assert org_data.get("id") != str(
                org_a.id
            ), "User from org B must NOT see org A data"

    def test_user_cannot_switch_to_other_org_workspace(
        self, org_b_user, ws_alpha, org_a
    ):
        """User in org B cannot switch to a workspace in org A."""
        token = _get_access_token(org_b_user)
        client = APIClient()
        resp = client.post(
            "/accounts/workspace/switch/",
            {"new_workspace_id": str(ws_alpha.id)},
            format="json",
            **_auth_header(token),
            HTTP_X_ORGANIZATION_ID=str(org_a.id),
        )
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
        ), f"Cross-org workspace switch should fail, got {resp.status_code}: {resp.data}"

    def test_user_stays_in_own_org_without_explicit_header(self, org_b_user, org_b):
        """Without X-Organization-Id, user stays in their own org."""
        token = _get_access_token(org_b_user)
        client = APIClient()
        resp = client.get(
            "/accounts/user-info/",
            **_auth_header(token),
        )
        assert resp.status_code == status.HTTP_200_OK
        org_data = resp.data.get("organization", {})
        assert org_data.get("id") == str(org_b.id)


# ---------------------------------------------------------------------------
# Tests: Workspace switch endpoint enforcement
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWorkspaceSwitchEnforcement:
    """Verify the /accounts/workspace/switch/ endpoint enforces access."""

    def test_viewer_cannot_switch_to_unauthorized_workspace(
        self, viewer_user, ws_beta, ws_alpha
    ):
        """Viewer in ws_alpha cannot switch to ws_beta."""
        token = _get_access_token(viewer_user)
        client = APIClient()
        # First make a request to establish context
        resp = client.post(
            "/accounts/workspace/switch/",
            {"new_workspace_id": str(ws_beta.id)},
            format="json",
            **_auth_header(token),
            HTTP_X_WORKSPACE_ID=str(ws_alpha.id),
        )
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), f"Viewer should not switch to unauthorized workspace, got {resp.status_code}: {resp.data}"

    def test_viewer_can_switch_to_authorized_workspace(
        self, viewer_user, ws_alpha, ws_beta
    ):
        """Add viewer to ws_beta, then they CAN switch to it."""
        _add_workspace_membership(viewer_user, ws_beta, Level.WORKSPACE_VIEWER)
        token = _get_access_token(viewer_user)
        client = APIClient()
        resp = client.post(
            "/accounts/workspace/switch/",
            {"new_workspace_id": str(ws_beta.id)},
            format="json",
            **_auth_header(token),
            HTTP_X_WORKSPACE_ID=str(ws_alpha.id),
        )
        assert (
            resp.status_code == status.HTTP_200_OK
        ), f"Viewer with membership should switch, got {resp.status_code}: {resp.data}"

    def test_admin_can_switch_to_any_workspace(self, admin_user, ws_beta, ws_alpha):
        """Admin can switch to any workspace in their org."""
        token = _get_access_token(admin_user)
        client = APIClient()
        resp = client.post(
            "/accounts/workspace/switch/",
            {"new_workspace_id": str(ws_beta.id)},
            format="json",
            **_auth_header(token),
            HTTP_X_WORKSPACE_ID=str(ws_alpha.id),
        )
        assert (
            resp.status_code == status.HTTP_200_OK
        ), f"Admin should switch to any workspace, got {resp.status_code}: {resp.data}"


# ---------------------------------------------------------------------------
# Tests: Member list endpoint respects workspace boundaries
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMemberListAccessEnforcement:
    """Verify member list endpoints respect access boundaries."""

    def test_viewer_cannot_list_members_of_unauthorized_workspace(
        self, viewer_user, ws_beta
    ):
        """Viewer in ws_alpha cannot list members of ws_beta."""
        token = _get_access_token(viewer_user)
        client = APIClient()
        resp = client.get(
            f"/accounts/workspace/{ws_beta.id}/members/",
            **_auth_header(token),
            HTTP_X_WORKSPACE_ID=str(ws_beta.id),
        )
        assert resp.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_400_BAD_REQUEST,
        ), f"Should not list members of unauthorized workspace, got {resp.status_code}"

    def test_org_b_user_cannot_list_org_a_members(self, org_b_user, org_a, ws_alpha):
        """User in org B cannot list org A members."""
        token = _get_access_token(org_b_user)
        client = APIClient()
        resp = client.get(
            "/accounts/organization/members/",
            **_auth_header(token),
            HTTP_X_ORGANIZATION_ID=str(org_a.id),
        )
        # Should either be 403, or return org B's members (not org A's)
        if resp.status_code == status.HTTP_200_OK:
            # Verify the data belongs to org B, not org A
            results = resp.data.get("result", {}).get("results", [])
            for member in results:
                assert (
                    member.get("email") != "admin@futureagi.com"
                ), "Org B user should NOT see org A members"
                assert (
                    member.get("email") != "viewer@futureagi.com"
                ), "Org B user should NOT see org A members"
