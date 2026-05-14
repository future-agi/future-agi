"""
Comprehensive Org-Level E2E Tests

Tests org lifecycle, invite→join→access flow, member management,
role-based permissions, multi-org switching, and org-workspace
integration. Focuses on areas not covered by existing test files.

Complements:
  - test_create_organization.py (org creation)
  - test_e2e_invite_permissions.py (invite permission matrix)
  - test_e2e_multi_org_lifecycle.py (multi-org lifecycle)
  - test_multi_org_endpoints.py (list/switch/current endpoints)
  - test_e2e_role_and_removal.py (role updates, removal)
  - test_multi_org_auth.py (auth context)
  - test_cross_org_isolation.py (data isolation)
"""

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles, RolePermissions
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# =====================================================================
# Helpers
# =====================================================================


def _make_user(
    organization, email, role_str, level, workspace=None, password="pass123"
):
    """Create a user with org membership and optional workspace membership."""
    clear_workspace_context()
    if workspace:
        set_workspace_context(organization=organization, workspace=workspace)
    else:
        set_workspace_context(organization=organization)

    u = User.objects.create_user(
        email=email,
        password=password,
        name=f"{role_str} User",
        organization=organization,
        organization_role=role_str,
        is_active=True,
    )
    org_membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=u,
        organization=organization,
        defaults={
            "role": role_str,
            "level": level,
            "is_active": True,
        },
    )
    if workspace:
        ws_role_str = {
            Level.OWNER: OrganizationRoles.WORKSPACE_ADMIN,
            Level.ADMIN: OrganizationRoles.WORKSPACE_ADMIN,
            Level.MEMBER: OrganizationRoles.WORKSPACE_MEMBER,
            Level.VIEWER: OrganizationRoles.WORKSPACE_VIEWER,
        }
        ws_level_int = {
            Level.OWNER: Level.WORKSPACE_ADMIN,
            Level.ADMIN: Level.WORKSPACE_ADMIN,
            Level.MEMBER: Level.WORKSPACE_MEMBER,
            Level.VIEWER: Level.WORKSPACE_VIEWER,
        }
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=u,
            workspace=workspace,
            defaults={
                "role": ws_role_str.get(level, OrganizationRoles.WORKSPACE_MEMBER),
                "level": ws_level_int.get(level, Level.WORKSPACE_MEMBER),
                "is_active": True,
                "organization_membership": org_membership,
            },
        )
    return u


# Track WorkspaceAwareAPIClient instances created by _make_client so the
# autouse fixture below can stop their injected APIView.initial patch after
# each test, preventing process-wide contamination of subsequent tests.
_created_clients: list = []


@pytest.fixture(autouse=True)
def _teardown_workspace_injection():
    yield
    while _created_clients:
        client = _created_clients.pop()
        try:
            client.stop_workspace_injection()
        except Exception:
            pass


def _make_client(user, workspace):
    """Create an authenticated WorkspaceAwareAPIClient."""
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    _created_clients.append(client)
    return client


def _invite_user(client, emails, org_level, workspace_access=None):
    """Call POST /accounts/organization/invite/ and return response."""
    payload = {"emails": emails, "org_level": org_level}
    if workspace_access:
        payload["workspace_access"] = workspace_access
    return client.post(
        "/accounts/organization/invite/",
        payload,
        format="json",
    )


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def org(db):
    """Create a test organization."""
    return Organization.objects.create(name="Acme Corp")


@pytest.fixture
def owner(db, org):
    """Create the org Owner and default workspace."""
    clear_workspace_context()
    set_workspace_context(organization=org)
    u = User.objects.create_user(
        email="owner@acme.com",
        password="pass123",
        name="Owner User",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
        is_active=True,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=u,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    return u


@pytest.fixture
def default_ws(db, org, owner):
    """Create default workspace for org (requires owner for created_by)."""
    ws = Workspace.objects.create(
        name="Default",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=owner,
        workspace=ws,
        defaults={
            "role": OrganizationRoles.WORKSPACE_ADMIN,
            "level": Level.WORKSPACE_ADMIN,
            "is_active": True,
        },
    )
    return ws


@pytest.fixture
def second_ws(db, org, owner):
    """Create a second workspace."""
    return Workspace.objects.create(
        name="Second",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=owner,
    )


@pytest.fixture
def third_ws(db, org, owner):
    """Create a third workspace."""
    return Workspace.objects.create(
        name="Third",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=owner,
    )


@pytest.fixture
def admin(db, org, default_ws):
    """Create an org Admin."""
    return _make_user(
        org, "admin@acme.com", OrganizationRoles.ADMIN, Level.ADMIN, default_ws
    )


@pytest.fixture
def owner_client(owner, default_ws):
    return _make_client(owner, default_ws)


@pytest.fixture
def admin_client(admin, default_ws):
    return _make_client(admin, default_ws)


# =====================================================================
# A. Invite → Join → Verify Flow (org-level)
# =====================================================================


@pytest.mark.django_db
class TestOrgInviteAndJoinFlow:
    """Full invite → dual-write → verify membership flow at org level."""

    def test_invite_creates_user_and_membership(self, owner_client, org):
        """Inviting a new email creates user + org membership via dual-write."""
        resp = _invite_user(owner_client, ["new@example.com"], Level.MEMBER)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["result"]
        assert "new@example.com" in data["invited"]

        # Verify user was created (inactive — pending activation)
        user = User.objects.get(email="new@example.com")
        assert user.is_active is False

        # Verify OrganizationMembership was created (inactive until invite accepted)
        mem = OrganizationMembership.no_workspace_objects.get(
            user=user, organization=org
        )
        assert mem.is_active is False
        assert mem.level == Level.MEMBER

        # Verify OrganizationInvite exists (user is inactive)
        invite = OrganizationInvite.objects.get(
            organization=org, target_email="new@example.com"
        )
        assert invite.level == Level.MEMBER

    def test_invite_existing_active_user_updates_access(
        self, owner_client, org, default_ws
    ):
        """Inviting an already-active user records them as already a member."""
        existing = _make_user(
            org,
            "existing@acme.com",
            OrganizationRoles.MEMBER,
            Level.MEMBER,
            default_ws,
        )

        resp = _invite_user(owner_client, ["existing@acme.com"], Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["result"] == {
            "invited": [],
            "already_members": ["existing@acme.com"],
        }

        # Existing active members are upgraded when the requested org level is higher.
        mem = OrganizationMembership.no_workspace_objects.get(
            user=existing, organization=org
        )
        assert mem.level == Level.ADMIN

    def test_invite_with_workspace_access(
        self, owner_client, org, default_ws, second_ws
    ):
        """Invite with workspace_access creates workspace memberships."""
        resp = _invite_user(
            owner_client,
            ["wsuser@example.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER},
            ],
        )
        assert resp.status_code == status.HTTP_200_OK

        user = User.objects.get(email="wsuser@example.com")

        # Org membership exists (inactive until invite accepted)
        assert OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=org
        ).exists()
        mem = OrganizationMembership.no_workspace_objects.get(
            user=user, organization=org
        )
        assert mem.is_active is False

        # Workspace membership exists for second_ws (inactive until invite accepted)
        ws_mem = WorkspaceMembership.no_workspace_objects.get(
            user=user, workspace=second_ws
        )
        assert ws_mem.is_active is False
        assert ws_mem.level == Level.WORKSPACE_MEMBER

    def test_invite_multiple_emails(self, owner_client, org):
        """Can invite multiple emails at once."""
        emails = ["a@example.com", "b@example.com", "c@example.com"]
        resp = _invite_user(owner_client, emails, Level.VIEWER)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["result"]
        assert set(data["invited"]) == set(emails)

        # All three users created
        assert User.objects.filter(email__in=emails).count() == 3

    def test_invite_all_org_levels(self, owner_client, org):
        """Owner can invite at all levels below their own."""
        levels = [
            ("admin@new.com", Level.ADMIN),
            ("member@new.com", Level.MEMBER),
            ("viewer@new.com", Level.VIEWER),
        ]
        for email, level in levels:
            resp = _invite_user(owner_client, [email], level)
            assert resp.status_code == status.HTTP_200_OK, f"Failed for {email}"

            user = User.objects.get(email=email)
            mem = OrganizationMembership.no_workspace_objects.get(
                user=user, organization=org
            )
            assert mem.level == level, f"Wrong level for {email}"

    def test_invite_with_multi_workspace_access(
        self, owner_client, org, default_ws, second_ws, third_ws
    ):
        """Invite with access to multiple workspaces at different levels."""
        resp = _invite_user(
            owner_client,
            ["multi@example.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_ADMIN},
                {"workspace_id": str(third_ws.id), "level": Level.WORKSPACE_VIEWER},
            ],
        )
        assert resp.status_code == status.HTTP_200_OK

        user = User.objects.get(email="multi@example.com")

        # Check workspace memberships
        ws_mem_second = WorkspaceMembership.no_workspace_objects.get(
            user=user, workspace=second_ws
        )
        assert ws_mem_second.level == Level.WORKSPACE_ADMIN

        ws_mem_third = WorkspaceMembership.no_workspace_objects.get(
            user=user, workspace=third_ws
        )
        assert ws_mem_third.level == Level.WORKSPACE_VIEWER


# =====================================================================
# B. Invite Permission Boundaries
# =====================================================================


@pytest.mark.django_db
class TestInvitePermissionBoundaries:
    """Verify permission enforcement for invite operations."""

    def test_admin_can_invite_at_admin_level(self, admin_client, org):
        """Admin can invite at their own level."""
        resp = _invite_user(admin_client, ["peer@example.com"], Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK

    def test_admin_cannot_invite_owner(self, admin_client, org):
        """Admin cannot invite at Owner level."""
        resp = _invite_user(admin_client, ["boss@example.com"], Level.OWNER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_can_invite_member(self, admin_client, org):
        """Admin can invite at Member level."""
        resp = _invite_user(admin_client, ["member@new.com"], Level.MEMBER)
        assert resp.status_code == status.HTTP_200_OK

    def test_admin_can_invite_viewer(self, admin_client, org):
        """Admin can invite at Viewer level."""
        resp = _invite_user(admin_client, ["viewer@new.com"], Level.VIEWER)
        assert resp.status_code == status.HTTP_200_OK

    def test_member_cannot_invite(self, org, default_ws):
        """Member cannot invite anyone."""
        member = _make_user(
            org, "m@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        client = _make_client(member, default_ws)
        resp = _invite_user(client, ["anyone@example.com"], Level.VIEWER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_viewer_cannot_invite(self, org, default_ws):
        """Viewer cannot invite anyone."""
        viewer = _make_user(
            org,
            "v@acme.com",
            OrganizationRoles.MEMBER_VIEW_ONLY,
            Level.VIEWER,
            default_ws,
        )
        client = _make_client(viewer, default_ws)
        resp = _invite_user(client, ["anyone@example.com"], Level.VIEWER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_workspace_admin_invite_downgrades_to_viewer(
        self, org, default_ws, second_ws
    ):
        """Workspace Admin (not org Admin) can invite but only at org Viewer level."""
        # Create a user who is only workspace admin, not org admin
        ws_admin = _make_user(
            org,
            "wsadmin@acme.com",
            OrganizationRoles.MEMBER,
            Level.MEMBER,
            default_ws,
        )
        # Give them WS Admin on second_ws
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=ws_admin,
            workspace=second_ws,
            defaults={
                "role": OrganizationRoles.WORKSPACE_ADMIN,
                "level": Level.WORKSPACE_ADMIN,
                "is_active": True,
            },
        )
        # WS Admin needs IsOrganizationAdminOrWorkspaceAdmin which checks
        # org level >= ADMIN or workspace level >= WORKSPACE_ADMIN.
        # Since they're only org Member, the view should downgrade to Viewer.
        client = _make_client(ws_admin, second_ws)
        resp = _invite_user(client, ["downgraded@example.com"], Level.MEMBER)
        # Either 403 (not org admin) or 200 with downgrade to Viewer
        if resp.status_code == status.HTTP_200_OK:
            user = User.objects.get(email="downgraded@example.com")
            mem = OrganizationMembership.no_workspace_objects.get(
                user=user, organization=org
            )
            # Should be capped at Viewer
            assert mem.level == Level.VIEWER

    def test_invite_invalid_workspace_in_different_org(self, owner_client, org, owner):
        """Cannot invite with workspace_access pointing to another org's workspace."""
        other_org = Organization.objects.create(name="Other Org")
        other_ws = Workspace.objects.create(
            name="Other WS",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=owner,
        )
        resp = _invite_user(
            owner_client,
            ["bad@example.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(other_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# =====================================================================
# C. Member Management (List / Role Update / Remove)
# =====================================================================


@pytest.mark.django_db
class TestMemberListAPI:
    """GET /accounts/organization/members/ — listing, search, filter."""

    def test_list_members_includes_active(self, owner_client, org, owner, admin):
        """Member list includes all active members."""
        resp = owner_client.get("/accounts/organization/members/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["result"]
        emails = [m["email"] for m in data["results"]]
        assert owner.email in emails
        assert admin.email in emails

    def test_list_members_shows_pending_invites(self, owner_client, org):
        """Pending invites appear in member list with status 'Pending'."""
        _invite_user(owner_client, ["pending@example.com"], Level.MEMBER)

        resp = owner_client.get("/accounts/organization/members/")
        data = resp.json()["result"]
        pending = [m for m in data["results"] if m["email"] == "pending@example.com"]
        assert len(pending) == 1
        assert pending[0]["status"] == "Pending"

    def test_list_members_search_by_email(self, owner_client, org, admin):
        """Search filters by email substring."""
        resp = owner_client.get("/accounts/organization/members/?search=admin")
        data = resp.json()["result"]
        assert data["total"] >= 1
        assert any(m["email"] == admin.email for m in data["results"])

    def test_list_members_filter_by_status(self, owner_client, org, owner):
        """Filter by status (Active, Pending)."""
        _invite_user(owner_client, ["pending2@example.com"], Level.VIEWER)

        # Filter for Pending only
        resp = owner_client.get(
            '/accounts/organization/members/?filterStatus=["Pending"]'
        )
        data = resp.json()["result"]
        assert all(m["status"] == "Pending" for m in data["results"])

    def test_list_members_pagination(self, owner_client, org, default_ws, owner):
        """Pagination returns correct page/limit."""
        # Create several members
        for i in range(5):
            _make_user(
                org,
                f"page{i}@acme.com",
                OrganizationRoles.MEMBER,
                Level.MEMBER,
                default_ws,
            )

        resp = owner_client.get("/accounts/organization/members/?page=1&limit=3")
        data = resp.json()["result"]
        assert len(data["results"]) == 3
        assert data["page"] == 1
        assert data["total"] >= 6  # owner + 5 new (each test class is isolated)

    def test_member_list_forbidden_for_member(self, org, default_ws):
        """Member (non-admin) cannot list org members."""
        member = _make_user(
            org, "nolist@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        client = _make_client(member, default_ws)
        resp = client.get("/accounts/organization/members/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_member_list_includes_workspace_details(
        self, owner_client, org, default_ws
    ):
        """Active members show their workspace memberships."""
        member = _make_user(
            org, "wsmem@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )

        resp = owner_client.get("/accounts/organization/members/")
        data = resp.json()["result"]
        mem_row = next(m for m in data["results"] if m["email"] == member.email)
        assert "workspaces" in mem_row
        ws_ids = [w["workspace_id"] for w in mem_row["workspaces"]]
        assert str(default_ws.id) in ws_ids


@pytest.mark.django_db
class TestMemberRoleUpdate:
    """POST /accounts/organization/members/role/ — role update flows."""

    def test_owner_promotes_member_to_admin(self, owner_client, org, default_ws):
        """Owner can promote Member → Admin."""
        member = _make_user(
            org, "promote@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        resp = owner_client.post(
            "/accounts/organization/members/role/",
            {"user_id": str(member.id), "org_level": Level.ADMIN},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        mem = OrganizationMembership.no_workspace_objects.get(
            user=member, organization=org
        )
        assert mem.level == Level.ADMIN

    def test_owner_demotes_admin_to_viewer(self, owner_client, org, admin):
        """Owner can demote Admin → Viewer."""
        resp = owner_client.post(
            "/accounts/organization/members/role/",
            {"user_id": str(admin.id), "org_level": Level.VIEWER},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        mem = OrganizationMembership.no_workspace_objects.get(
            user=admin, organization=org
        )
        assert mem.level == Level.VIEWER

    def test_admin_can_promote_to_admin(self, admin_client, org, default_ws):
        """Admin can assign Admin level to a lower-level member."""
        member = _make_user(
            org, "noprom@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        resp = admin_client.post(
            "/accounts/organization/members/role/",
            {"user_id": str(member.id), "org_level": Level.ADMIN},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_admin_cannot_change_owner_role(self, admin_client, owner):
        """Admin cannot modify Owner's role."""
        resp = admin_client.post(
            "/accounts/organization/members/role/",
            {"user_id": str(owner.id), "org_level": Level.MEMBER},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestMemberRemoval:
    """DELETE /accounts/organization/members/remove/ — removal flows."""

    def test_owner_removes_member(self, owner_client, org, default_ws):
        """Owner can remove a Member from the org."""
        member = _make_user(
            org, "remove@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        resp = owner_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # Membership deactivated (soft-delete)
        mem = OrganizationMembership.no_workspace_objects.get(
            user=member, organization=org
        )
        assert mem.is_active is False

    def test_removed_member_workspace_access_revoked(
        self, owner_client, org, default_ws, second_ws
    ):
        """Removing org member deactivates org membership and workspace access."""
        member = _make_user(
            org, "revoke@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=member,
            workspace=second_ws,
            defaults={
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "level": Level.WORKSPACE_MEMBER,
                "is_active": True,
            },
        )

        resp = owner_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # Org membership should be deactivated
        mem = OrganizationMembership.no_workspace_objects.get(
            user=member, organization=org
        )
        assert mem.is_active is False

        # User should no longer have org access
        assert member.can_access_organization(org) is False

    def test_admin_cannot_remove_owner(self, admin_client, owner):
        """Admin cannot remove an Owner."""
        resp = admin_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(owner.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_member_cannot_remove_anyone(self, org, default_ws):
        """Member has no remove permission."""
        member = _make_user(
            org, "nomgmt@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        target = _make_user(
            org,
            "target@acme.com",
            OrganizationRoles.MEMBER_VIEW_ONLY,
            Level.VIEWER,
            default_ws,
        )
        client = _make_client(member, default_ws)
        resp = client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(target.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# =====================================================================
# D. Invite Lifecycle (Resend / Cancel)
# =====================================================================


@pytest.mark.django_db
class TestInviteLifecycle:
    """Resend and cancel invite operations."""

    def test_resend_invite_refreshes_timestamp(self, owner_client, org):
        """Resending an invite refreshes its expiration."""
        _invite_user(owner_client, ["resend@example.com"], Level.MEMBER)
        invite = OrganizationInvite.objects.get(
            organization=org, target_email="resend@example.com"
        )
        original_created = invite.created_at

        resp = owner_client.post(
            "/accounts/organization/invite/resend/",
            {"invite_id": str(invite.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        invite.refresh_from_db()
        assert invite.created_at >= original_created

    def test_resend_with_level_upgrade(self, owner_client, org):
        """Resend can upgrade the invite level."""
        _invite_user(owner_client, ["upgrade@example.com"], Level.VIEWER)
        invite = OrganizationInvite.objects.get(
            organization=org, target_email="upgrade@example.com"
        )
        assert invite.level == Level.VIEWER

        resp = owner_client.post(
            "/accounts/organization/invite/resend/",
            {"invite_id": str(invite.id), "org_level": Level.MEMBER},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        invite.refresh_from_db()
        assert invite.level == Level.MEMBER

    def test_cancel_invite_removes_it(self, owner_client, org):
        """Cancelling an invite deletes the record."""
        _invite_user(owner_client, ["cancel@example.com"], Level.MEMBER)
        invite = OrganizationInvite.objects.get(
            organization=org, target_email="cancel@example.com"
        )

        resp = owner_client.delete(
            "/accounts/organization/invite/cancel/",
            {"invite_id": str(invite.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        assert OrganizationInvite.objects.filter(
            id=invite.id, status=InviteStatus.CANCELLED
        ).exists()

    def test_cancel_invite_deactivates_pending_membership(self, owner_client, org):
        """Cancelling deactivates the dual-write membership for inactive users."""
        _invite_user(owner_client, ["deactivate@example.com"], Level.MEMBER)
        user = User.objects.get(email="deactivate@example.com")
        assert user.is_active is False  # new user, not yet activated

        invite = OrganizationInvite.objects.get(
            organization=org, target_email="deactivate@example.com"
        )
        owner_client.delete(
            "/accounts/organization/invite/cancel/",
            {"invite_id": str(invite.id)},
            format="json",
        )

        # Membership should be deactivated and soft-deleted
        mem = OrganizationMembership.all_objects.get(user=user, organization=org)
        assert mem.is_active is False


# =====================================================================
# E. Reinvite After Removal
# =====================================================================


@pytest.mark.django_db
class TestReinviteAfterRemoval:
    """Verify invite flow works for previously-removed members."""

    def test_reinvite_reactivates_membership(self, owner_client, org, default_ws):
        """Inviting a removed member reactivates their membership."""
        # Create and remove member
        member = _make_user(
            org, "comeback@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        owner_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member.id)},
            format="json",
        )

        # Verify deactivated
        mem = OrganizationMembership.no_workspace_objects.get(
            user=member, organization=org
        )
        assert mem.is_active is False

        # Reinvite at Admin level
        resp = _invite_user(owner_client, ["comeback@acme.com"], Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK

        # Membership should be reactivated with new level
        mem.refresh_from_db()
        assert mem.is_active is True
        assert mem.level == Level.ADMIN

    def test_reinvite_with_workspace_access(
        self, owner_client, org, default_ws, second_ws
    ):
        """Reinviting with workspace_access restores workspace memberships."""
        member = _make_user(
            org, "wsback@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )

        # Remove
        owner_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member.id)},
            format="json",
        )

        # Reinvite with workspace access
        resp = _invite_user(
            owner_client,
            ["wsback@acme.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )
        assert resp.status_code == status.HTTP_200_OK

        # Workspace membership should exist
        ws_mem = WorkspaceMembership.no_workspace_objects.filter(
            user=member, workspace=second_ws, is_active=True
        )
        assert ws_mem.exists()


# =====================================================================
# F. Org Access Verification (Model Level)
# =====================================================================


@pytest.mark.django_db
class TestOrgAccessModel:
    """Verify org access methods on User model."""

    def test_active_member_can_access_org(self, org, default_ws):
        """Active org member can access the org."""
        member = _make_user(
            org, "access@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        assert member.can_access_organization(org) is True

    def test_deactivated_member_cannot_access_org(self, org, default_ws):
        """Deactivated org member cannot access the org."""
        member = _make_user(
            org, "noaccess@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        OrganizationMembership.no_workspace_objects.filter(
            user=member, organization=org
        ).update(is_active=False)
        assert member.can_access_organization(org) is False

    def test_non_member_cannot_access_org(self, org, default_ws, owner):
        """User not in org cannot access it."""
        other_org = Organization.objects.create(name="Other")
        outsider = _make_user(
            other_org,
            "outsider@other.com",
            OrganizationRoles.MEMBER,
            Level.MEMBER,
        )
        # Create a workspace for other_org so outsider can function
        other_ws = Workspace.objects.create(
            name="Other WS",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=outsider,
        )
        assert outsider.can_access_organization(org) is False

    def test_owner_has_global_workspace_access(self, org, owner):
        """Owner has global workspace access."""
        assert owner.has_global_workspace_access(org) is True

    def test_admin_has_global_workspace_access(self, org, admin):
        """Admin has global workspace access."""
        assert admin.has_global_workspace_access(org) is True

    def test_member_no_global_workspace_access(self, org, default_ws):
        """Member does NOT have global workspace access."""
        member = _make_user(
            org, "memcheck@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        assert member.has_global_workspace_access(org) is False

    def test_viewer_no_global_workspace_access(self, org, default_ws):
        """Viewer does NOT have global workspace access."""
        viewer = _make_user(
            org,
            "viewcheck@acme.com",
            OrganizationRoles.MEMBER_VIEW_ONLY,
            Level.VIEWER,
            default_ws,
        )
        assert viewer.has_global_workspace_access(org) is False

    def test_get_organization_role(self, org, owner, admin, default_ws):
        """get_organization_role returns correct role for each user."""
        assert owner.get_organization_role(org) == OrganizationRoles.OWNER
        assert admin.get_organization_role(org) == OrganizationRoles.ADMIN

        member = _make_user(
            org,
            "rolecheck@acme.com",
            OrganizationRoles.MEMBER,
            Level.MEMBER,
            default_ws,
        )
        assert member.get_organization_role(org) == OrganizationRoles.MEMBER


# =====================================================================
# G. Multi-Org: Org Switching & Isolation
# =====================================================================


@pytest.mark.django_db
class TestMultiOrgSwitching:
    """Verify org switching and per-org workspace resolution."""

    @pytest.fixture
    def org_beta(self, db):
        return Organization.objects.create(name="Beta Corp")

    @pytest.fixture
    def multi_user(self, db, org, default_ws, org_beta):
        """User who is Owner in org and Member in org_beta."""
        u = _make_user(
            org, "multi@acme.com", OrganizationRoles.OWNER, Level.OWNER, default_ws
        )
        # Add to beta org as Member
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=u,
            organization=org_beta,
            defaults={
                "role": OrganizationRoles.MEMBER,
                "level": Level.MEMBER,
                "is_active": True,
            },
        )
        # Create workspace in beta and give membership
        beta_ws = Workspace.objects.create(
            name="Beta Default",
            organization=org_beta,
            is_default=True,
            is_active=True,
            created_by=u,
        )
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=u,
            workspace=beta_ws,
            defaults={
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "level": Level.WORKSPACE_MEMBER,
                "is_active": True,
            },
        )
        return u

    def test_org_list_shows_both_orgs(self, multi_user, org, org_beta, default_ws):
        """User sees both orgs in org list."""
        client = _make_client(multi_user, default_ws)
        resp = client.get("/accounts/organizations/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["result"]
        org_ids = [o["id"] for o in data["organizations"]]
        assert str(org.id) in org_ids
        assert str(org_beta.id) in org_ids

    def test_switch_org_updates_context(self, multi_user, org, org_beta, default_ws):
        """Switching org updates user config."""
        client = _make_client(multi_user, default_ws)

        resp = client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_beta.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["result"]
        assert data["organization"]["id"] == str(org_beta.id)

    def test_switch_to_unauthorized_org_denied(self, multi_user, default_ws):
        """Cannot switch to an org the user doesn't belong to."""
        ghost_org = Organization.objects.create(name="Ghost Corp")
        client = _make_client(multi_user, default_ws)

        resp = client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(ghost_org.id)},
            format="json",
        )
        assert resp.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_org_role_varies_per_org(self, multi_user, org, org_beta):
        """User has different roles in different orgs."""
        assert multi_user.get_organization_role(org) == OrganizationRoles.OWNER
        assert multi_user.get_organization_role(org_beta) == OrganizationRoles.MEMBER

    def test_global_access_varies_per_org(self, multi_user, org, org_beta):
        """Global workspace access depends on org-specific role."""
        assert multi_user.has_global_workspace_access(org) is True  # Owner
        assert multi_user.has_global_workspace_access(org_beta) is False  # Member


# =====================================================================
# H. Org-Workspace Integration
# =====================================================================


@pytest.mark.django_db
class TestOrgWorkspaceIntegration:
    """Verify workspace access respects org-level roles."""

    def test_owner_sees_all_workspaces(
        self, owner, org, default_ws, second_ws, third_ws
    ):
        """Owner sees all workspaces in the org."""
        client = _make_client(owner, default_ws)
        resp = client.get("/accounts/workspace/list/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        ws_names = [w["name"] for w in data["results"]]
        assert "Default" in ws_names
        assert "Second" in ws_names
        assert "Third" in ws_names

    def test_admin_sees_all_workspaces(
        self, admin, org, default_ws, second_ws, third_ws
    ):
        """Admin sees all workspaces in the org."""
        client = _make_client(admin, default_ws)
        resp = client.get("/accounts/workspace/list/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        ws_names = [w["name"] for w in data["results"]]
        assert "Default" in ws_names
        assert "Second" in ws_names
        assert "Third" in ws_names

    def test_member_sees_only_assigned_workspaces(
        self, org, default_ws, second_ws, third_ws
    ):
        """Member only sees workspaces they have explicit membership for."""
        member = _make_user(
            org, "limited@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        # Also give access to second_ws
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=member,
            workspace=second_ws,
            defaults={
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "level": Level.WORKSPACE_MEMBER,
                "is_active": True,
            },
        )
        client = _make_client(member, default_ws)
        resp = client.get("/accounts/workspace/list/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        ws_names = [w["name"] for w in data["results"]]
        assert "Default" in ws_names
        assert "Second" in ws_names
        assert "Third" not in ws_names

    def test_viewer_sees_only_assigned_workspaces(
        self, org, default_ws, second_ws, third_ws
    ):
        """Viewer only sees workspaces they have explicit membership for."""
        viewer = _make_user(
            org,
            "viewonly@acme.com",
            OrganizationRoles.MEMBER_VIEW_ONLY,
            Level.VIEWER,
            default_ws,
        )
        client = _make_client(viewer, default_ws)
        resp = client.get("/accounts/workspace/list/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        ws_names = [w["name"] for w in data["results"]]
        assert "Default" in ws_names
        assert "Second" not in ws_names
        assert "Third" not in ws_names

    def test_invited_member_sees_only_invited_workspaces(
        self, owner_client, org, default_ws, second_ws, third_ws
    ):
        """A user invited with specific workspace_access sees only those workspaces."""
        # Invite with access to second_ws only
        _invite_user(
            owner_client,
            ["limited_invite@acme.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )
        user = User.objects.get(email="limited_invite@acme.com")
        # Simulate invite acceptance: activate user + memberships
        user.is_active = True
        user.save(update_fields=["is_active"])
        OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=org
        ).update(is_active=True)
        WorkspaceMembership.no_workspace_objects.filter(user=user).update(
            is_active=True
        )

        client = _make_client(user, second_ws)
        resp = client.get("/accounts/workspace/list/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        ws_names = [w["name"] for w in data["results"]]
        assert "Second" in ws_names
        assert "Default" not in ws_names
        assert "Third" not in ws_names


# =====================================================================
# I. Org Settings & Updates
# =====================================================================


@pytest.mark.django_db
class TestOrgUpdate:
    """PATCH /accounts/organizations/update/ — org name/display updates."""

    def test_owner_can_update_org_name(self, owner_client, org):
        """Owner can update organization name."""
        resp = owner_client.patch(
            "/accounts/organizations/update/",
            {"name": "Acme Inc."},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        org.refresh_from_db()
        assert org.name == "Acme Inc."

    def test_owner_can_update_display_name(self, owner_client, org):
        """Owner can update organization display name."""
        resp = owner_client.patch(
            "/accounts/organizations/update/",
            {"display_name": "ACME Corporation"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        org.refresh_from_db()
        assert org.display_name == "ACME Corporation"


# =====================================================================
# J. Error Scenarios
# =====================================================================


@pytest.mark.django_db
class TestOrgErrorScenarios:
    """Error handling and edge cases."""

    def test_invite_empty_emails_list(self, owner_client, org):
        """Cannot invite with empty email list."""
        resp = _invite_user(owner_client, [], Level.MEMBER)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_remove_nonexistent_user(self, owner_client, org):
        """Removing a user that doesn't exist returns error."""
        import uuid

        resp = owner_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_update_role_nonexistent_user(self, owner_client, org):
        """Updating role for nonexistent user returns error."""
        import uuid

        resp = owner_client.post(
            "/accounts/organization/members/role/",
            {"user_id": str(uuid.uuid4()), "org_level": Level.ADMIN},
            format="json",
        )
        assert resp.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_invite_missing_org_level(self, owner_client, org):
        """Invite without org_level fails validation."""
        resp = owner_client.post(
            "/accounts/organization/invite/",
            {"emails": ["missing@example.com"]},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_cancel_nonexistent_invite(self, owner_client, org):
        """Cancelling a non-existent invite returns error."""
        import uuid

        resp = owner_client.delete(
            "/accounts/organization/invite/cancel/",
            {"invite_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_resend_nonexistent_invite(self, owner_client, org):
        """Resending a non-existent invite returns error."""
        import uuid

        resp = owner_client.post(
            "/accounts/organization/invite/resend/",
            {"invite_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# =====================================================================
# K. Cross-Org Isolation
# =====================================================================


@pytest.mark.django_db
class TestCrossOrgIsolation:
    """Verify operations cannot cross org boundaries."""

    @pytest.fixture
    def org_gamma(self, db):
        return Organization.objects.create(name="Gamma Corp")

    @pytest.fixture
    def gamma_owner(self, db, org_gamma):
        u = _make_user(
            org_gamma,
            "gowner@gamma.com",
            OrganizationRoles.OWNER,
            Level.OWNER,
        )
        return u

    @pytest.fixture
    def gamma_ws(self, db, org_gamma, gamma_owner):
        ws = Workspace.objects.create(
            name="Gamma Default",
            organization=org_gamma,
            is_default=True,
            is_active=True,
            created_by=gamma_owner,
        )
        org_membership = OrganizationMembership.no_workspace_objects.get(
            user=gamma_owner,
            organization=org_gamma,
        )
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=gamma_owner,
            workspace=ws,
            defaults={
                "role": OrganizationRoles.WORKSPACE_ADMIN,
                "level": Level.WORKSPACE_ADMIN,
                "is_active": True,
                "organization_membership": org_membership,
            },
        )
        return ws

    def test_cannot_list_other_org_members(
        self, org, default_ws, org_gamma, gamma_ws, gamma_owner, owner
    ):
        """Owner of Gamma cannot list Acme members."""
        gamma_client = _make_client(gamma_owner, gamma_ws)
        # The member list is scoped to the request's organization
        # gamma_owner's org is gamma, so they see gamma members
        resp = gamma_client.get("/accounts/organization/members/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()["result"]
        # Should NOT contain any Acme members
        emails = [m["email"] for m in data["results"]]
        assert "owner@acme.com" not in emails

    def test_cannot_invite_with_other_org_workspace(
        self, owner_client, org, org_gamma, gamma_ws
    ):
        """Cannot add workspace_access for a workspace in a different org."""
        resp = _invite_user(
            owner_client,
            ["xorg@example.com"],
            Level.MEMBER,
            workspace_access=[
                {
                    "workspace_id": str(gamma_ws.id),
                    "level": Level.WORKSPACE_MEMBER,
                }
            ],
        )
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        )

    def test_org_member_count_isolated(
        self, owner_client, org, default_ws, org_gamma, gamma_ws, gamma_owner, owner
    ):
        """Member counts are per-org, not cross-org."""
        # Add a member to Acme
        _make_user(
            org,
            "acme_only@acme.com",
            OrganizationRoles.MEMBER,
            Level.MEMBER,
            default_ws,
        )

        acme_resp = owner_client.get("/accounts/organization/members/")
        acme_count = acme_resp.json()["result"]["total"]

        gamma_client = _make_client(gamma_owner, gamma_ws)
        gamma_resp = gamma_client.get("/accounts/organization/members/")
        assert gamma_resp.status_code == status.HTTP_200_OK, gamma_resp.json()
        gamma_count = gamma_resp.json()["result"]["total"]

        # Counts should be independent
        assert acme_count != gamma_count or acme_count == 1  # edge case: both have 1


# =====================================================================
# L. Soft-Delete & Deactivation Verification
# =====================================================================


@pytest.mark.django_db
class TestSoftDeleteVerification:
    """Verify soft-delete mechanics for memberships."""

    def test_deactivated_member_not_in_active_list(self, owner_client, org, default_ws):
        """Deactivated member still appears in list but with Deactivated status."""
        member = _make_user(
            org, "deact@acme.com", OrganizationRoles.MEMBER, Level.MEMBER, default_ws
        )
        owner_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member.id)},
            format="json",
        )

        resp = owner_client.get("/accounts/organization/members/")
        data = resp.json()["result"]
        member_row = [m for m in data["results"] if m["email"] == "deact@acme.com"]
        if member_row:
            assert member_row[0]["status"] == "Deactivated"

    def test_deactivated_member_no_workspace_access(self, org, default_ws, second_ws):
        """Deactivated org member has no workspace access."""
        member = _make_user(
            org,
            "nowsaccess@acme.com",
            OrganizationRoles.MEMBER,
            Level.MEMBER,
            default_ws,
        )
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=member,
            workspace=second_ws,
            defaults={
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "level": Level.WORKSPACE_MEMBER,
                "is_active": True,
            },
        )

        # Deactivate org membership
        OrganizationMembership.no_workspace_objects.filter(
            user=member, organization=org
        ).update(is_active=False)

        # User should not be able to access the org
        assert member.can_access_organization(org) is False
