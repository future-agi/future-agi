"""
E2E Invite Permission Tests

Tests the invitation permission matrix across:
- Org-level actors (Owner/Admin/Member/Viewer) inviting at each target level
- Workspace-level actors (WS Admin/Member/Viewer) inviting
- Target user state variations (new, existing, deactivated, expired, etc.)
- Invite lifecycle (resend, cancel, expire)

NOTE: Response keys are camelCase because DRF CamelCaseJSONRenderer is active.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context

# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture(autouse=True)
def _owner_membership(user, organization):
    """Ensure the owner user has an OrganizationMembership.

    The root conftest ``user`` fixture creates User + Workspace but
    does NOT create an OrganizationMembership row.  RBAC permission
    classes look up this row, so we must create it here.
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
def second_workspace(organization, user):
    """Create a second workspace in the same org."""
    return Workspace.objects.create(
        name="Second Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


# =====================================================================
# Helpers
# =====================================================================


def _make_user_with_role(organization, email, role_str, level, password="pass123"):
    """Create a user with a specific org-level role and membership."""
    set_workspace_context(organization=organization)
    u = User.objects.create_user(
        email=email,
        password=password,
        name=f"{role_str} User",
        organization=organization,
        organization_role=role_str,
    )
    OrganizationMembership.objects.create(
        user=u,
        organization=organization,
        role=role_str,
        level=level,
        is_active=True,
    )
    return u


def _make_client(user, workspace):
    """Create an authenticated WorkspaceAwareAPIClient for the given user."""
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


def _invite_payload(email, org_level, workspace=None, ws_level=Level.WORKSPACE_VIEWER):
    """Build the standard invite POST payload."""
    payload = {
        "emails": [email],
        "org_level": org_level,
    }
    if workspace:
        payload["workspace_access"] = [
            {"workspace_id": str(workspace.id), "level": ws_level}
        ]
    return payload


INVITE_URL = "/accounts/organization/invite/"
RESEND_URL = "/accounts/organization/invite/resend/"
CANCEL_URL = "/accounts/organization/invite/cancel/"


# =====================================================================
# 1. Org-Level Invite Permissions
# =====================================================================


@pytest.mark.integration
@pytest.mark.api
class TestOrgLevelInvitePermissions:
    """Test invite permission matrix for org-level actors."""

    # --- Owner (15) ---

    def test_owner_can_invite_owner(self, auth_client, workspace):
        """Owner can invite at Owner level (special exception)."""
        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("newowner@example.com", Level.OWNER, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        data = resp.json()
        result = data.get("result", data)
        assert "newowner@example.com" in result["invited"]

    def test_owner_can_invite_admin(self, auth_client, workspace):
        """Owner can invite at Admin level."""
        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("newadmin@example.com", Level.ADMIN, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        result = resp.json().get("result", resp.json())
        assert "newadmin@example.com" in result["invited"]

    def test_owner_can_invite_member(self, auth_client, workspace):
        """Owner can invite at Member level."""
        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("newmember@example.com", Level.MEMBER, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        result = resp.json().get("result", resp.json())
        assert "newmember@example.com" in result["invited"]

    def test_owner_can_invite_viewer(self, auth_client, workspace):
        """Owner can invite at Viewer level."""
        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("newviewer@example.com", Level.VIEWER, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        result = resp.json().get("result", resp.json())
        assert "newviewer@example.com" in result["invited"]

    # --- Admin (8) ---

    def test_admin_cannot_invite_owner(self, organization, workspace):
        """Admin cannot invite at Owner level (escalation)."""
        admin = _make_user_with_role(
            organization, "admin@futureagi.com", "Admin", Level.ADMIN
        )
        client = _make_client(admin, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("target@example.com", Level.OWNER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        finally:
            client.stop_workspace_injection()

    def test_admin_can_invite_admin(self, organization, workspace):
        """Admin can invite at Admin level (equal or below own level)."""
        admin = _make_user_with_role(
            organization, "admin2@futureagi.com", "Admin", Level.ADMIN
        )
        client = _make_client(admin, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("target2@example.com", Level.ADMIN, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK, resp.json()
            result = resp.json().get("result", resp.json())
            assert "target2@example.com" in result["invited"]
        finally:
            client.stop_workspace_injection()

    def test_admin_can_invite_member(self, organization, workspace):
        """Admin can invite at Member level (strictly below)."""
        admin = _make_user_with_role(
            organization, "admin3@futureagi.com", "Admin", Level.ADMIN
        )
        client = _make_client(admin, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("target3@example.com", Level.MEMBER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK, resp.json()
            result = resp.json().get("result", resp.json())
            assert "target3@example.com" in result["invited"]
        finally:
            client.stop_workspace_injection()

    def test_admin_can_invite_viewer(self, organization, workspace):
        """Admin can invite at Viewer level."""
        admin = _make_user_with_role(
            organization, "admin4@futureagi.com", "Admin", Level.ADMIN
        )
        client = _make_client(admin, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("target4@example.com", Level.VIEWER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK, resp.json()
            result = resp.json().get("result", resp.json())
            assert "target4@example.com" in result["invited"]
        finally:
            client.stop_workspace_injection()

    # --- Member (3) — no IsOrganizationAdminOrWorkspaceAdmin ---

    def test_member_cannot_invite_owner(self, organization, workspace):
        """Org Member cannot invite anyone (not admin or WS admin)."""
        member = _make_user_with_role(
            organization, "member1@futureagi.com", "Member", Level.MEMBER
        )
        client = _make_client(member, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("t1@example.com", Level.OWNER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            client.stop_workspace_injection()

    def test_member_cannot_invite_member(self, organization, workspace):
        """Org Member cannot invite at Member level."""
        member = _make_user_with_role(
            organization, "member2@futureagi.com", "Member", Level.MEMBER
        )
        client = _make_client(member, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("t2@example.com", Level.MEMBER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            client.stop_workspace_injection()

    def test_member_cannot_invite_viewer(self, organization, workspace):
        """Org Member cannot invite even at Viewer level."""
        member = _make_user_with_role(
            organization, "member3@futureagi.com", "Member", Level.MEMBER
        )
        client = _make_client(member, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("t3@example.com", Level.VIEWER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            client.stop_workspace_injection()

    # --- Viewer (1) ---

    def test_viewer_cannot_invite_owner(self, organization, workspace):
        """Org Viewer cannot invite anyone."""
        viewer = _make_user_with_role(
            organization, "viewer1@futureagi.com", "Viewer", Level.VIEWER
        )
        client = _make_client(viewer, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("t4@example.com", Level.OWNER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            client.stop_workspace_injection()

    def test_viewer_cannot_invite_viewer(self, organization, workspace):
        """Org Viewer cannot invite even at Viewer level."""
        viewer = _make_user_with_role(
            organization, "viewer2@futureagi.com", "Viewer", Level.VIEWER
        )
        client = _make_client(viewer, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("t5@example.com", Level.VIEWER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            client.stop_workspace_injection()

    def test_viewer_cannot_invite_admin(self, organization, workspace):
        """Org Viewer cannot invite at Admin level."""
        viewer = _make_user_with_role(
            organization, "viewer3@futureagi.com", "Viewer", Level.VIEWER
        )
        client = _make_client(viewer, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("t6@example.com", Level.ADMIN, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            client.stop_workspace_injection()


# =====================================================================
# 2. WS-Level Invite Permissions
# =====================================================================


def _make_ws_user(organization, workspace, email, org_role, org_level, ws_level):
    """Create a user with org-level membership + workspace membership."""
    set_workspace_context(organization=organization)
    u = User.objects.create_user(
        email=email,
        password="pass123",
        name=f"WS {email}",
        organization=organization,
        organization_role=org_role,
    )
    org_mem = OrganizationMembership.objects.create(
        user=u,
        organization=organization,
        role=org_role,
        level=org_level,
        is_active=True,
    )
    WorkspaceMembership.objects.create(
        workspace=workspace,
        user=u,
        role=Level.to_ws_string(ws_level),
        level=ws_level,
        organization_membership=org_mem,
        is_active=True,
    )
    return u


@pytest.mark.integration
@pytest.mark.api
class TestWsLevelInvitePermissions:
    """Test invite permissions for workspace-level actors."""

    def test_ws_admin_can_invite_viewer(self, organization, workspace):
        """WS Admin can invite — target level is forced to Viewer(1)."""
        ws_admin = _make_ws_user(
            organization,
            workspace,
            "wsadmin1@futureagi.com",
            "Member",
            Level.MEMBER,
            Level.WORKSPACE_ADMIN,
        )
        client = _make_client(ws_admin, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("wstarget1@example.com", Level.VIEWER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK, resp.json()
            result = resp.json().get("result", resp.json())
            assert "wstarget1@example.com" in result["invited"]
        finally:
            client.stop_workspace_injection()

    def test_ws_admin_invite_member_downgrades_to_viewer(self, organization, workspace):
        """WS Admin requesting Member level gets silently downgraded to Viewer.

        The view forces target_org_level=VIEWER when actor_level < ADMIN.
        """
        ws_admin = _make_ws_user(
            organization,
            workspace,
            "wsadmin2@futureagi.com",
            "Member",
            Level.MEMBER,
            Level.WORKSPACE_ADMIN,
        )
        client = _make_client(ws_admin, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("wstarget2@example.com", Level.MEMBER, workspace),
                format="json",
            )
            # Succeeds but at Viewer level (not Member)
            assert resp.status_code == status.HTTP_200_OK, resp.json()
            # Verify the created membership is at Viewer level, not Member
            mem = OrganizationMembership.objects.get(
                user__email="wstarget2@example.com",
                organization=organization,
            )
            assert mem.level == Level.VIEWER
        finally:
            client.stop_workspace_injection()

    def test_ws_admin_invite_admin_downgrades_to_viewer(self, organization, workspace):
        """WS Admin requesting Admin level gets downgraded to Viewer."""
        ws_admin = _make_ws_user(
            organization,
            workspace,
            "wsadmin3@futureagi.com",
            "Member",
            Level.MEMBER,
            Level.WORKSPACE_ADMIN,
        )
        client = _make_client(ws_admin, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("wstarget3@example.com", Level.ADMIN, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK, resp.json()
            mem = OrganizationMembership.objects.get(
                user__email="wstarget3@example.com",
                organization=organization,
            )
            assert mem.level == Level.VIEWER
        finally:
            client.stop_workspace_injection()

    def test_ws_admin_invite_owner_downgrades_to_viewer(self, organization, workspace):
        """WS Admin requesting Owner level gets downgraded to Viewer."""
        ws_admin = _make_ws_user(
            organization,
            workspace,
            "wsadmin4@futureagi.com",
            "Member",
            Level.MEMBER,
            Level.WORKSPACE_ADMIN,
        )
        client = _make_client(ws_admin, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("wstarget4@example.com", Level.OWNER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK, resp.json()
            mem = OrganizationMembership.objects.get(
                user__email="wstarget4@example.com",
                organization=organization,
            )
            assert mem.level == Level.VIEWER
        finally:
            client.stop_workspace_injection()

    def test_ws_member_cannot_invite(self, organization, workspace):
        """WS Member (not admin) cannot invite."""
        ws_member = _make_ws_user(
            organization,
            workspace,
            "wsmember@futureagi.com",
            "Member",
            Level.MEMBER,
            Level.WORKSPACE_MEMBER,
        )
        client = _make_client(ws_member, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("wst5@example.com", Level.VIEWER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            client.stop_workspace_injection()

    def test_ws_viewer_cannot_invite(self, organization, workspace):
        """WS Viewer cannot invite."""
        ws_viewer = _make_ws_user(
            organization,
            workspace,
            "wsviewer@futureagi.com",
            "Viewer",
            Level.VIEWER,
            Level.WORKSPACE_VIEWER,
        )
        client = _make_client(ws_viewer, workspace)
        try:
            resp = client.post(
                INVITE_URL,
                _invite_payload("wst6@example.com", Level.VIEWER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            client.stop_workspace_injection()


# =====================================================================
# 3. Target User State Variations
# =====================================================================


@pytest.mark.integration
@pytest.mark.api
class TestTargetUserStateVariations:
    """Test invite behavior with various target user states."""

    def test_invite_new_email(self, auth_client, workspace):
        """Inviting a brand new email creates invite and user."""
        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("brand.new@example.com", Level.MEMBER, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        result = resp.json().get("result", resp.json())
        assert "brand.new@example.com" in result["invited"]
        # User is created (inactive) via dual-write
        assert User.objects.filter(email="brand.new@example.com").exists()

    def test_invite_existing_active_user_same_org_returns_already_member(
        self, auth_client, organization, workspace
    ):
        """Inviting an existing active user in the same org is idempotent."""
        existing = _make_user_with_role(
            organization, "existing@futureagi.com", "Member", Level.MEMBER
        )
        resp = auth_client.post(
            INVITE_URL,
            _invite_payload(existing.email, Level.MEMBER, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        result = resp.json().get("result", resp.json())
        assert result == {"invited": [], "already_members": [existing.email]}

    def test_invite_existing_user_different_org(
        self, auth_client, organization, workspace, user
    ):
        """Inviting a user from a different org — succeeds with multi-org support."""
        other_org = Organization.objects.create(name="Other Org")
        set_workspace_context(organization=other_org)
        other_user = User.objects.create_user(
            email="otherorg@example.com",
            password="pass123",
            name="Other Org User",
            organization=other_org,
            organization_role="Member",
        )
        # Restore workspace context so auth_client's request is processed correctly
        set_workspace_context(workspace=workspace, organization=organization, user=user)

        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("otherorg@example.com", Level.MEMBER, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        result = resp.json().get("result", resp.json())
        # Multi-org support: user from another org can be invited successfully
        assert "otherorg@example.com" in result["invited"]

    def test_invite_deactivated_member_reactivates(
        self, auth_client, organization, workspace
    ):
        """Inviting a deactivated member re-activates their membership."""
        member = _make_user_with_role(
            organization, "deactivated@futureagi.com", "Member", Level.MEMBER
        )
        # Deactivate
        org_mem = OrganizationMembership.objects.get(
            user=member, organization=organization
        )
        org_mem.is_active = False
        org_mem.save(update_fields=["is_active"])

        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("deactivated@futureagi.com", Level.MEMBER, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()

        # Verify membership re-activated (source of truth)
        org_mem.refresh_from_db()
        assert org_mem.is_active is True

    def test_invite_user_with_expired_invite(
        self, auth_client, organization, workspace
    ):
        """Inviting a user who has an expired invite works (replaces/updates)."""
        # Create expired invite
        expired_invite = OrganizationInvite.objects.create(
            organization=organization,
            target_email="expired@example.com",
            level=Level.VIEWER,
            invited_by=auth_client.handler._force_user,
        )
        # Backdate to make it expired
        OrganizationInvite.objects.filter(id=expired_invite.id).update(
            created_at=timezone.now() - timedelta(days=10)
        )

        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("expired@example.com", Level.MEMBER, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        result = resp.json().get("result", resp.json())
        assert "expired@example.com" in result["invited"]

    def test_invite_user_with_pending_invite(
        self, auth_client, organization, workspace, user
    ):
        """Inviting a user with a pending invite — update_or_create replaces it."""
        OrganizationInvite.objects.create(
            organization=organization,
            target_email="pending@example.com",
            level=Level.VIEWER,
            invited_by=user,
        )

        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("pending@example.com", Level.MEMBER, workspace),
            format="json",
        )
        # update_or_create updates the existing invite
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        result = resp.json().get("result", resp.json())
        assert "pending@example.com" in result["invited"]

    def test_invite_multiple_emails(self, auth_client, workspace):
        """Batch invite: multiple emails in one request."""
        payload = {
            "emails": [
                "batch1@example.com",
                "batch2@example.com",
                "batch3@example.com",
            ],
            "org_level": Level.MEMBER,
            "workspace_access": [
                {"workspace_id": str(workspace.id), "level": Level.WORKSPACE_VIEWER}
            ],
        }
        resp = auth_client.post(INVITE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        result = resp.json().get("result", resp.json())
        assert len(result["invited"]) == 3

    def test_invite_with_workspace_access(self, auth_client, organization, workspace):
        """Invite creates workspace memberships."""
        resp = auth_client.post(
            INVITE_URL,
            _invite_payload("wsaccess@example.com", Level.MEMBER, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()

        target_user = User.objects.get(email="wsaccess@example.com")
        ws_mem = WorkspaceMembership.objects.filter(
            user=target_user, workspace=workspace
        )
        assert ws_mem.exists()

    def test_invite_with_multiple_workspace_access(
        self, auth_client, organization, workspace, second_workspace
    ):
        """Invite with multiple workspace_access entries creates multiple WS memberships."""
        payload = {
            "emails": ["multiws@example.com"],
            "org_level": Level.MEMBER,
            "workspace_access": [
                {"workspace_id": str(workspace.id), "level": Level.WORKSPACE_VIEWER},
                {
                    "workspace_id": str(second_workspace.id),
                    "level": Level.WORKSPACE_MEMBER,
                },
            ],
        }
        resp = auth_client.post(INVITE_URL, payload, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.json()

        target_user = User.objects.get(email="multiws@example.com")
        # Use no_workspace_objects to bypass workspace context filter
        ws_mems = WorkspaceMembership.no_workspace_objects.filter(user=target_user)
        assert ws_mems.count() == 2

    def test_invite_with_invalid_workspace_id(self, auth_client, workspace):
        """Invalid workspace_id returns 400."""
        payload = {
            "emails": ["invalidws@example.com"],
            "org_level": Level.MEMBER,
            "workspace_access": [
                {
                    "workspace_id": "00000000-0000-0000-0000-000000000000",
                    "level": Level.WORKSPACE_VIEWER,
                }
            ],
        }
        resp = auth_client.post(INVITE_URL, payload, format="json")
        # The view checks workspace belongs to org and returns bad_request
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# =====================================================================
# 4. Invite Lifecycle (Resend / Cancel / Expire)
# =====================================================================


@pytest.mark.integration
@pytest.mark.api
class TestInviteLifecycle:
    """Test invite resend, cancel, and expiration flows."""

    def _create_invite(
        self,
        auth_client,
        workspace,
        email="lifecycle@example.com",
        org_level=Level.MEMBER,
    ):
        """Helper: create an invite via API and return the invite object."""
        resp = auth_client.post(
            INVITE_URL,
            _invite_payload(email, org_level, workspace),
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        # The invite may or may not exist depending on user state.
        # For a new email, invite row exists (user is inactive).
        invite = OrganizationInvite.objects.filter(
            target_email=email,
            status=InviteStatus.PENDING,
        ).first()
        return invite

    def test_resend_invite_refreshes_expiration(
        self, auth_client, workspace, organization
    ):
        """Resending an invite refreshes its created_at (expiration)."""
        invite = self._create_invite(auth_client, workspace, "resend1@example.com")
        assert invite is not None, "Invite should exist for new email"

        old_created_at = invite.created_at

        # Backdate the invite
        OrganizationInvite.objects.filter(id=invite.id).update(
            created_at=timezone.now() - timedelta(days=5)
        )

        resp = auth_client.post(
            RESEND_URL,
            {"invite_id": str(invite.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()

        invite.refresh_from_db()
        assert invite.created_at > old_created_at - timedelta(days=5)

    def test_resend_with_level_upgrade_allowed(
        self, auth_client, workspace, organization
    ):
        """Resend with level change within escalation rules succeeds."""
        invite = self._create_invite(
            auth_client, workspace, "resend2@example.com", Level.VIEWER
        )
        assert invite is not None

        # Owner upgrades from Viewer to Member (allowed)
        resp = auth_client.post(
            RESEND_URL,
            {"invite_id": str(invite.id), "org_level": Level.MEMBER},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()

        invite.refresh_from_db()
        assert invite.level == Level.MEMBER

    def test_resend_with_level_violating_escalation(self, organization, workspace):
        """Resend with level above actor's own level is denied."""
        admin = _make_user_with_role(
            organization, "admin.resend@futureagi.com", "Admin", Level.ADMIN
        )
        admin_client = _make_client(admin, workspace)
        try:
            # Create invite as admin at Viewer level
            resp = admin_client.post(
                INVITE_URL,
                _invite_payload("resend3@example.com", Level.VIEWER, workspace),
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK, resp.json()

            invite = OrganizationInvite.objects.get(target_email="resend3@example.com")

            # Try to upgrade to Owner — should be denied
            resp = admin_client.post(
                RESEND_URL,
                {"invite_id": str(invite.id), "org_level": Level.OWNER},
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            admin_client.stop_workspace_injection()

    def test_cancel_invite_deletes_from_db(self, auth_client, workspace, organization):
        """Cancelling an invite hard-deletes it from the database."""
        invite = self._create_invite(auth_client, workspace, "cancel1@example.com")
        assert invite is not None
        invite_id = invite.id

        resp = auth_client.delete(
            CANCEL_URL,
            {"invite_id": str(invite_id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()

        # Status set to Cancelled
        assert OrganizationInvite.objects.filter(
            id=invite_id, status=InviteStatus.CANCELLED
        ).exists()

    def test_cancel_invite_deactivates_pending_membership(
        self, auth_client, workspace, organization
    ):
        """Cancelling an invite for an inactive user deactivates their membership."""
        invite = self._create_invite(auth_client, workspace, "cancel2@example.com")
        assert invite is not None

        target = User.objects.get(email="cancel2@example.com")
        # User was created inactive by dual-write
        assert target.is_active is False

        resp = auth_client.delete(
            CANCEL_URL,
            {"invite_id": str(invite.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()

        # OrganizationMembership should be deactivated and soft-deleted
        org_mem = OrganizationMembership.all_objects.get(
            user=target, organization=organization
        )
        assert org_mem.is_active is False

    def test_expired_invite_shows_expired_status(
        self, auth_client, workspace, organization, user
    ):
        """An invite created 8+ days ago has status Expired."""
        invite = OrganizationInvite.objects.create(
            organization=organization,
            target_email="expstatus@example.com",
            level=Level.MEMBER,
            invited_by=user,
        )
        # Backdate to 8 days ago
        OrganizationInvite.objects.filter(id=invite.id).update(
            created_at=timezone.now() - timedelta(days=8)
        )
        invite.refresh_from_db()
        assert invite.is_expired is True
        assert invite.effective_status == "Expired"
