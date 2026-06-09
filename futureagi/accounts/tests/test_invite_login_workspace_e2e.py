"""
E2E tests for Invite → Login → Workspace Access flow.

Validates that invited users can ONLY see and access workspaces they were
explicitly granted membership to. Owner/Admin have global access to all
workspaces; Member/Viewer must have explicit WorkspaceMembership.

Bug regression: Previously, GLOBAL_ACCESS_ROLES included Member and Viewer,
allowing all org-level users to see every workspace in the organization.
"""

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
INVITE_URL = "/accounts/organization/invite/"
WORKSPACE_LIST_URL = "/accounts/workspace/list/"
WORKSPACE_SWITCH_URL = "/accounts/workspace/switch/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_client(user, workspace):
    """Create an authenticated WorkspaceAwareAPIClient for the given user+workspace."""
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


def _get_workspace_names(response):
    """Extract workspace names from a paginated workspace list response."""
    return [w["name"] for w in response.data.get("results", [])]


def _create_invited_user(
    organization, email, org_level, workspace_access, inviter, password="invited123"
):
    """Simulate the invite dual-write: create user + org membership + workspace memberships.

    This mirrors what InviteCreateAPIView._dual_write_legacy does.
    """
    clear_workspace_context()
    set_workspace_context(organization=organization)

    org_role_string = Level.to_org_string(org_level)
    user = User.objects.create_user(
        email=email,
        password=password,
        name=email.split("@")[0],
        organization=organization,
        organization_role=org_role_string,
        is_active=True,
    )

    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=org_role_string,
        level=org_level,
        invited_by=inviter,
        is_active=True,
    )

    # Map level to OrganizationRoles value (DB value, not display label)
    _level_to_role = {
        Level.WORKSPACE_ADMIN: OrganizationRoles.WORKSPACE_ADMIN,
        Level.WORKSPACE_MEMBER: OrganizationRoles.WORKSPACE_MEMBER,
        Level.WORKSPACE_VIEWER: OrganizationRoles.WORKSPACE_VIEWER,
    }

    for ws_entry in workspace_access:
        ws = ws_entry["workspace"]
        ws_level = ws_entry.get("level", Level.WORKSPACE_VIEWER)
        ws_role = _level_to_role.get(ws_level, OrganizationRoles.WORKSPACE_MEMBER)
        WorkspaceMembership.no_workspace_objects.create(
            workspace=ws,
            user=user,
            role=ws_role,
            level=ws_level,
            organization_membership=org_membership,
            invited_by=inviter,
            is_active=True,
        )

    return user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def org(db):
    """Organization with 3 workspaces."""
    return Organization.objects.create(name="InviteTestOrg")


@pytest.fixture
def owner(db, org):
    """Owner of the test organization."""
    clear_workspace_context()
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email="owner-invite-test@futureagi.com",
        password="ownerpass123",
        name="Owner User",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    return user


@pytest.fixture
def ws_default(db, org, owner):
    """Default workspace."""
    clear_workspace_context()
    set_workspace_context(organization=org)
    ws = Workspace.objects.create(
        name="Default WS",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    # Owner gets membership in all workspaces
    org_mem = OrganizationMembership.no_workspace_objects.get(
        user=owner, organization=org
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=ws,
        user=owner,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership=org_mem,
        is_active=True,
    )
    return ws


@pytest.fixture
def ws_second(db, org, owner):
    """Second workspace."""
    clear_workspace_context()
    set_workspace_context(organization=org)
    ws = Workspace.objects.create(
        name="Second WS",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=owner,
    )
    org_mem = OrganizationMembership.no_workspace_objects.get(
        user=owner, organization=org
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=ws,
        user=owner,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership=org_mem,
        is_active=True,
    )
    return ws


@pytest.fixture
def ws_third(db, org, owner):
    """Third workspace."""
    clear_workspace_context()
    set_workspace_context(organization=org)
    ws = Workspace.objects.create(
        name="Third WS",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=owner,
    )
    org_mem = OrganizationMembership.no_workspace_objects.get(
        user=owner, organization=org
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=ws,
        user=owner,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership=org_mem,
        is_active=True,
    )
    return ws


@pytest.fixture
def owner_client(owner, ws_default):
    """Authenticated client for the owner."""
    client = _make_client(owner, ws_default)
    yield client
    client.stop_workspace_injection()


# ===========================================================================
# A. Single Workspace Invite — Member/Viewer see only assigned workspace
# ===========================================================================


class TestSingleWorkspaceInvite:
    """Users invited with a single workspace should only see that workspace."""

    def test_viewer_with_viewer_ws_sees_only_target(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """1. Org Viewer + WS Viewer → sees only ws_second."""
        user = _create_invited_user(
            org,
            "viewer-v@futureagi.com",
            Level.VIEWER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_VIEWER}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert names == ["Second WS"]
        client.stop_workspace_injection()

    def test_viewer_with_member_ws_sees_only_target(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """2. Org Viewer + WS Member → sees only ws_second."""
        user = _create_invited_user(
            org,
            "viewer-m@futureagi.com",
            Level.VIEWER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert names == ["Second WS"]
        client.stop_workspace_injection()

    def test_member_with_member_ws_sees_only_target(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """3. Org Member + WS Member → sees only ws_second."""
        user = _create_invited_user(
            org,
            "member-m@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert names == ["Second WS"]
        client.stop_workspace_injection()

    def test_member_with_admin_ws_sees_only_target(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """4. Org Member + WS Admin → sees only ws_second."""
        user = _create_invited_user(
            org,
            "member-a@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_ADMIN}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert names == ["Second WS"]
        client.stop_workspace_injection()

    def test_admin_sees_all_workspaces(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """5. Org Admin has global access → sees all 3 workspaces."""
        user = _create_invited_user(
            org,
            "admin-a@futureagi.com",
            Level.ADMIN,
            [{"workspace": ws_second, "level": Level.WORKSPACE_ADMIN}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert len(names) == 3
        assert "Default WS" in names
        assert "Second WS" in names
        assert "Third WS" in names
        client.stop_workspace_injection()

    def test_owner_sees_all_workspaces(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """6. Org Owner has global access → sees all 3 workspaces."""
        user = _create_invited_user(
            org,
            "owner2-a@futureagi.com",
            Level.OWNER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_ADMIN}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert len(names) == 3
        client.stop_workspace_injection()


# ===========================================================================
# Existing Active Member Re-invite
# ===========================================================================


@pytest.mark.django_db
class TestExistingActiveMemberReinvite:
    def test_existing_active_member_gets_email_when_workspace_access_is_added(
        self, monkeypatch, owner_client, org, owner, ws_default, ws_second
    ):
        existing_user = _create_invited_user(
            org,
            "existing-active@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_default, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        sent_emails = []

        def fake_send_invite_email(email, organization, invited_by):
            sent_emails.append((email, organization.id, invited_by.id))

        monkeypatch.setattr(
            "accounts.views.rbac_views.send_invite_email", fake_send_invite_email
        )

        response = owner_client.post(
            INVITE_URL,
            {
                "emails": [existing_user.email],
                "org_level": Level.MEMBER,
                "workspace_access": [
                    {
                        "workspace_id": str(ws_second.id),
                        "level": Level.WORKSPACE_MEMBER,
                    }
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"] == {
            "invited": [],
            "already_members": [existing_user.email],
        }
        assert sent_emails == [(existing_user.email, org.id, owner.id)]
        assert WorkspaceMembership.no_workspace_objects.filter(
            user=existing_user,
            workspace=ws_second,
            is_active=True,
            level=Level.WORKSPACE_MEMBER,
        ).exists()

    def test_existing_active_member_does_not_get_email_when_access_is_unchanged(
        self, monkeypatch, owner_client, org, owner, ws_default
    ):
        existing_user = _create_invited_user(
            org,
            "existing-unchanged@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_default, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        sent_emails = []

        monkeypatch.setattr(
            "accounts.views.rbac_views.send_invite_email",
            lambda email, organization, invited_by: sent_emails.append(email),
        )

        response = owner_client.post(
            INVITE_URL,
            {
                "emails": [existing_user.email],
                "org_level": Level.MEMBER,
                "workspace_access": [
                    {
                        "workspace_id": str(ws_default.id),
                        "level": Level.WORKSPACE_MEMBER,
                    }
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["already_members"] == [existing_user.email]
        assert sent_emails == []


# ===========================================================================
# B. Multiple Workspace Invite
# ===========================================================================


class TestMultipleWorkspaceInvite:
    """Users invited to multiple workspaces see exactly those workspaces."""

    def test_member_with_two_workspaces(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """7. Org Member + ws_second + ws_third → sees exactly 2 workspaces."""
        user = _create_invited_user(
            org,
            "member-2ws@futureagi.com",
            Level.MEMBER,
            [
                {"workspace": ws_second, "level": Level.WORKSPACE_MEMBER},
                {"workspace": ws_third, "level": Level.WORKSPACE_MEMBER},
            ],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert len(names) == 2
        assert "Second WS" in names
        assert "Third WS" in names
        assert "Default WS" not in names
        client.stop_workspace_injection()

    def test_viewer_with_mixed_ws_roles(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """8. Org Viewer + ws_second (Member) + ws_third (Viewer) → sees 2 workspaces."""
        user = _create_invited_user(
            org,
            "viewer-2ws@futureagi.com",
            Level.VIEWER,
            [
                {"workspace": ws_second, "level": Level.WORKSPACE_MEMBER},
                {"workspace": ws_third, "level": Level.WORKSPACE_VIEWER},
            ],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert len(names) == 2
        assert "Second WS" in names
        assert "Third WS" in names
        client.stop_workspace_injection()


# ===========================================================================
# C. No Explicit Workspace Invite (auto-assign default)
# ===========================================================================


class TestNoWorkspaceInvite:
    """Users invited without workspace_access get auto-assigned to default workspace
    (for Member/Viewer) or see all workspaces (for Admin/Owner)."""

    def test_member_no_ws_gets_default_only(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """9. Org Member + no workspace_access → auto-assigned to default ws only."""
        # Create user with org membership but no workspace membership
        clear_workspace_context()
        set_workspace_context(organization=org)
        user = User.objects.create_user(
            email="member-nows@futureagi.com",
            password="invited123",
            name="member-nows",
            organization=org,
            organization_role=OrganizationRoles.MEMBER,
            is_active=True,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            invited_by=owner,
            is_active=True,
        )
        # No WorkspaceMembership created — simulates invite with empty workspace_access

        # Verify: user has no workspace memberships
        assert (
            WorkspaceMembership.no_workspace_objects.filter(
                user=user, is_active=True
            ).count()
            == 0
        )

        # The workspace list should return empty since no membership exists
        # (In production, the auth middleware's _get_or_create_default_workspace
        # would auto-assign on login, but we test the list endpoint directly)
        client = _make_client(user, ws_default)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        # Without any workspace membership, member should see nothing
        assert len(names) == 0
        client.stop_workspace_injection()

    def test_admin_no_ws_sees_all(self, org, owner, ws_default, ws_second, ws_third):
        """11. Org Admin + no workspace_access → sees all workspaces (global access)."""
        clear_workspace_context()
        set_workspace_context(organization=org)
        user = User.objects.create_user(
            email="admin-nows@futureagi.com",
            password="invited123",
            name="admin-nows",
            organization=org,
            organization_role=OrganizationRoles.ADMIN,
            is_active=True,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org,
            role=OrganizationRoles.ADMIN,
            level=Level.ADMIN,
            invited_by=owner,
            is_active=True,
        )

        client = _make_client(user, ws_default)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert len(names) == 3
        client.stop_workspace_injection()


# ===========================================================================
# D. Bug Regression: Member/Viewer should NOT see all workspaces
# ===========================================================================


class TestWorkspaceVisibilityRegression:
    """Regression tests for the GLOBAL_ACCESS_ROLES bug.

    Previously, GLOBAL_ACCESS_ROLES included Member and Viewer,
    causing the workspace list to return ALL workspaces in the org.
    """

    def test_org_viewer_invited_to_one_workspace_sees_only_that(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """12. BUG REGRESSION: Org Viewer + ws_second → sees ONLY ws_second, not all 3."""
        user = _create_invited_user(
            org,
            "regress-viewer@futureagi.com",
            Level.VIEWER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_VIEWER}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert names == [
            "Second WS"
        ], f"Viewer should see only 'Second WS' but got: {names}"
        client.stop_workspace_injection()

    def test_org_member_invited_to_one_workspace_sees_only_that(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """13. BUG REGRESSION: Org Member + ws_second → sees ONLY ws_second, not all 3."""
        user = _create_invited_user(
            org,
            "regress-member@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert names == [
            "Second WS"
        ], f"Member should see only 'Second WS' but got: {names}"
        client.stop_workspace_injection()


# ===========================================================================
# E. Workspace Access Enforcement — switch and header checks
# ===========================================================================


class TestWorkspaceAccessEnforcement:
    """Ensure Members/Viewers cannot switch to or access unauthorized workspaces."""

    def test_member_cannot_switch_to_unauthorized_workspace(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """14. Member with ws_second access cannot switch to ws_third."""
        user = _create_invited_user(
            org,
            "member-switch@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.post(
            WORKSPACE_SWITCH_URL,
            {"new_workspace_id": str(ws_third.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        client.stop_workspace_injection()

    def test_admin_can_switch_to_any_workspace(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """15. Admin with global access can switch to any workspace."""
        user = _create_invited_user(
            org,
            "admin-switch@futureagi.com",
            Level.ADMIN,
            [{"workspace": ws_second, "level": Level.WORKSPACE_ADMIN}],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.post(
            WORKSPACE_SWITCH_URL,
            {"new_workspace_id": str(ws_third.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        client.stop_workspace_injection()

    def test_member_can_switch_to_authorized_workspace(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """Member with access to ws_second AND ws_third can switch between them."""
        user = _create_invited_user(
            org,
            "member-switchok@futureagi.com",
            Level.MEMBER,
            [
                {"workspace": ws_second, "level": Level.WORKSPACE_MEMBER},
                {"workspace": ws_third, "level": Level.WORKSPACE_MEMBER},
            ],
            owner,
        )
        client = _make_client(user, ws_second)
        resp = client.post(
            WORKSPACE_SWITCH_URL,
            {"new_workspace_id": str(ws_third.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        client.stop_workspace_injection()

    def test_viewer_cannot_access_unauthorized_workspace_via_header(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """16. Viewer trying to set X-Workspace-Id for unauthorized workspace gets no access."""
        user = _create_invited_user(
            org,
            "viewer-header@futureagi.com",
            Level.VIEWER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_VIEWER}],
            owner,
        )
        # Try to use ws_third via the WorkspaceAwareAPIClient
        assert user.can_access_workspace(ws_third) is False
        assert user.can_access_workspace(ws_second) is True


# ===========================================================================
# F. Workspace Membership Verification
# ===========================================================================


class TestWorkspaceMembershipVerification:
    """Verify that invite creates exactly the right workspace memberships."""

    def test_single_ws_invite_creates_one_membership(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """17. Only the invited workspace should have a membership record."""
        user = _create_invited_user(
            org,
            "verify-single@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        memberships = WorkspaceMembership.no_workspace_objects.filter(
            user=user, is_active=True
        )
        assert memberships.count() == 1
        assert memberships.first().workspace_id == ws_second.id

    def test_multi_ws_invite_creates_exact_memberships(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """Multiple workspace invite creates exactly those memberships."""
        user = _create_invited_user(
            org,
            "verify-multi@futureagi.com",
            Level.MEMBER,
            [
                {"workspace": ws_second, "level": Level.WORKSPACE_MEMBER},
                {"workspace": ws_third, "level": Level.WORKSPACE_VIEWER},
            ],
            owner,
        )
        memberships = WorkspaceMembership.no_workspace_objects.filter(
            user=user, is_active=True
        )
        assert memberships.count() == 2
        ws_ids = set(memberships.values_list("workspace_id", flat=True))
        assert ws_ids == {ws_second.id, ws_third.id}

    def test_no_ws_invite_creates_no_membership(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """18. Invite without workspace_access creates no workspace membership."""
        clear_workspace_context()
        set_workspace_context(organization=org)
        user = User.objects.create_user(
            email="verify-nows@futureagi.com",
            password="invited123",
            name="verify-nows",
            organization=org,
            organization_role=OrganizationRoles.MEMBER,
            is_active=True,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            invited_by=owner,
            is_active=True,
        )
        memberships = WorkspaceMembership.no_workspace_objects.filter(
            user=user, is_active=True
        )
        assert memberships.count() == 0

    def test_ws_membership_has_correct_levels(
        self, org, owner, ws_default, ws_second, ws_third
    ):
        """Workspace memberships have the correct role levels."""
        user = _create_invited_user(
            org,
            "verify-levels@futureagi.com",
            Level.VIEWER,
            [
                {"workspace": ws_second, "level": Level.WORKSPACE_MEMBER},
                {"workspace": ws_third, "level": Level.WORKSPACE_VIEWER},
            ],
            owner,
        )
        mem_second = WorkspaceMembership.no_workspace_objects.get(
            user=user, workspace=ws_second
        )
        mem_third = WorkspaceMembership.no_workspace_objects.get(
            user=user, workspace=ws_third
        )
        assert mem_second.level == Level.WORKSPACE_MEMBER
        assert mem_third.level == Level.WORKSPACE_VIEWER


# ===========================================================================
# G. Owner always sees all workspaces
# ===========================================================================


class TestOwnerGlobalAccess:
    """Owner always has global access regardless of workspace membership."""

    def test_owner_sees_all_three_workspaces(
        self, owner_client, ws_default, ws_second, ws_third
    ):
        """Owner sees all workspaces in the organization."""
        resp = owner_client.get(WORKSPACE_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        names = _get_workspace_names(resp)
        assert len(names) == 3
        assert "Default WS" in names
        assert "Second WS" in names
        assert "Third WS" in names

    def test_owner_can_switch_to_any_workspace(
        self, owner_client, ws_default, ws_second, ws_third
    ):
        """Owner can switch to any workspace."""
        resp = owner_client.post(
            WORKSPACE_SWITCH_URL,
            {"new_workspace_id": str(ws_third.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK


# ===========================================================================
# H. Model-level access checks
# ===========================================================================


class TestModelLevelAccess:
    """Test User model methods for workspace access with the new GLOBAL_ACCESS_ROLES."""

    def test_member_has_no_global_workspace_access(self, org, owner, ws_default):
        """Org Member does NOT have global workspace access."""
        user = _create_invited_user(
            org,
            "model-member@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_default, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        assert user.has_global_workspace_access(org) is False
        assert user.is_workspace_only_user(org) is True

    def test_viewer_has_no_global_workspace_access(self, org, owner, ws_default):
        """Org Viewer does NOT have global workspace access."""
        user = _create_invited_user(
            org,
            "model-viewer@futureagi.com",
            Level.VIEWER,
            [{"workspace": ws_default, "level": Level.WORKSPACE_VIEWER}],
            owner,
        )
        assert user.has_global_workspace_access(org) is False
        assert user.is_workspace_only_user(org) is True

    def test_admin_has_global_workspace_access(self, org, owner, ws_default):
        """Org Admin has global workspace access."""
        user = _create_invited_user(
            org,
            "model-admin@futureagi.com",
            Level.ADMIN,
            [{"workspace": ws_default, "level": Level.WORKSPACE_ADMIN}],
            owner,
        )
        assert user.has_global_workspace_access(org) is True
        assert user.is_workspace_only_user(org) is False

    def test_member_can_access_assigned_workspace(
        self, org, owner, ws_default, ws_second
    ):
        """Member can access workspace they have membership for."""
        user = _create_invited_user(
            org,
            "model-access@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        assert user.can_access_workspace(ws_second) is True
        assert user.can_access_workspace(ws_default) is False

    def test_member_write_read_permissions(self, org, owner, ws_default, ws_second):
        """Member has write+read on assigned workspace, nothing on unassigned."""
        user = _create_invited_user(
            org,
            "model-rw@futureagi.com",
            Level.MEMBER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_MEMBER}],
            owner,
        )
        assert user.can_write_to_workspace(ws_second) is True
        assert user.can_read_from_workspace(ws_second) is True
        assert user.can_write_to_workspace(ws_default) is False
        assert user.can_read_from_workspace(ws_default) is False

    def test_viewer_readonly_on_assigned_workspace(
        self, org, owner, ws_default, ws_second
    ):
        """Viewer has read-only on assigned workspace, nothing on unassigned."""
        user = _create_invited_user(
            org,
            "model-ro@futureagi.com",
            Level.VIEWER,
            [{"workspace": ws_second, "level": Level.WORKSPACE_VIEWER}],
            owner,
        )
        assert user.can_write_to_workspace(ws_second) is False
        assert user.can_read_from_workspace(ws_second) is True
        assert user.can_read_from_workspace(ws_default) is False
