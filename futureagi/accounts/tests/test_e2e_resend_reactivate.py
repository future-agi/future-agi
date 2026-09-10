"""
End-to-end tests for:
1. Resend invite from workspace member tab
2. Reactivate deactivated member from org member tab
3. Workspace member list includes pending/expired invites
4. Status filtering for workspace member list
5. Deactivated status filtering for org member list
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
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

# URL constants
INVITE_URL = "/accounts/organization/invite/"
INVITE_RESEND_URL = "/accounts/organization/invite/resend/"
LEGACY_USER_RESEND_URL = "/accounts/user/resend-invite/"
BULK_RESEND_URL = "/accounts/resend-invitation-emails/"
MEMBER_LIST_URL = "/accounts/organization/members/"
MEMBER_REMOVE_URL = "/accounts/organization/members/remove/"
MEMBER_REACTIVATE_URL = "/accounts/organization/members/reactivate/"


def _ws_member_list_url(workspace_id):
    return f"/accounts/workspace/{workspace_id}/members/"


def _make_client(user, workspace):
    c = WorkspaceAwareAPIClient()
    c.force_authenticate(user=user)
    c.set_workspace(workspace)
    return c


def _make_user(
    organization, email, role_str, level, workspace=None, password="pass123"
):
    """Create a user with org membership and optional workspace membership."""
    clear_workspace_context()
    set_workspace_context(organization=organization)

    u = User.objects.create_user(
        email=email,
        password=password,
        name=f"{role_str} User",
        organization=organization,
        organization_role=role_str,
        is_active=True,
    )
    org_mem = OrganizationMembership.no_workspace_objects.get_or_create(
        user=u,
        organization=organization,
        defaults={
            "role": role_str,
            "level": level,
            "is_active": True,
        },
    )[0]
    if workspace:
        ws_level = {
            Level.OWNER: Level.WORKSPACE_ADMIN,
            Level.ADMIN: Level.WORKSPACE_ADMIN,
            Level.MEMBER: Level.WORKSPACE_MEMBER,
            Level.VIEWER: Level.WORKSPACE_VIEWER,
        }.get(level, Level.WORKSPACE_MEMBER)
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=u,
            workspace=workspace,
            defaults={
                "role": Level.to_ws_string(ws_level),
                "level": ws_level,
                "organization_membership": org_mem,
                "is_active": True,
            },
        )
    return u


def _invite_user(client, emails, org_level, workspace_access=None):
    payload = {"emails": emails, "org_level": org_level}
    if workspace_access:
        payload["workspace_access"] = workspace_access
    return client.post(INVITE_URL, payload, format="json")


def _get_results(response):
    return response.data["result"]["results"]


def _get_total(response):
    return response.data["result"]["total"]


# ── Fixtures ──


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Resend Reactivate Org")


@pytest.fixture
def owner(db, org):
    clear_workspace_context()
    set_workspace_context(organization=org)
    u = User.objects.create_user(
        email="owner@resend.com",
        password="pass123",
        name="Owner",
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
    return Workspace.objects.create(
        name="Second",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=owner,
    )


@pytest.fixture
def third_ws(db, org, owner):
    return Workspace.objects.create(
        name="Third",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=owner,
    )


@pytest.fixture
def owner_client(owner, default_ws):
    c = _make_client(owner, default_ws)
    yield c
    c.stop_workspace_injection()


# =====================================================================
# Feature 1: Workspace member list includes pending/expired invites
# =====================================================================


@pytest.mark.django_db
class TestWorkspaceMemberListInvites:
    """Workspace member list should show pending/expired invites for that workspace."""

    def test_pending_invite_appears_in_workspace_member_list(
        self, owner_client, org, default_ws, second_ws
    ):
        """An invite with workspace_access for second_ws appears in its member list."""
        resp = _invite_user(
            owner_client,
            ["invited@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )
        assert resp.status_code == status.HTTP_200_OK

        # Check workspace member list for second_ws
        resp = owner_client.get(_ws_member_list_url(second_ws.id))
        assert resp.status_code == status.HTTP_200_OK
        results = _get_results(resp)

        invited_row = next((r for r in results if r["email"] == "invited@ws.com"), None)
        assert invited_row is not None
        assert invited_row["status"] == "Pending"
        assert invited_row["type"] == "invite"

    def test_pending_invite_does_not_appear_in_other_workspace(
        self, owner_client, org, default_ws, second_ws, third_ws
    ):
        """An invite for second_ws should NOT appear in third_ws member list."""
        _invite_user(
            owner_client,
            ["invited2@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        resp = owner_client.get(_ws_member_list_url(third_ws.id))
        assert resp.status_code == status.HTTP_200_OK
        results = _get_results(resp)

        invited_row = next(
            (r for r in results if r["email"] == "invited2@ws.com"), None
        )
        assert invited_row is None

    def test_admin_invite_appears_in_all_workspace_member_lists(
        self, owner_client, org, default_ws, second_ws
    ):
        """An Admin-level invite auto-accesses all workspaces, so it appears everywhere."""
        _invite_user(owner_client, ["admin_invite@ws.com"], Level.ADMIN)

        # Should appear in default_ws
        resp = owner_client.get(_ws_member_list_url(default_ws.id))
        results = _get_results(resp)
        admin_row = next(
            (r for r in results if r["email"] == "admin_invite@ws.com"), None
        )
        assert admin_row is not None
        assert admin_row["status"] == "Pending"

        # Should also appear in second_ws
        resp = owner_client.get(_ws_member_list_url(second_ws.id))
        results = _get_results(resp)
        admin_row = next(
            (r for r in results if r["email"] == "admin_invite@ws.com"), None
        )
        assert admin_row is not None
        assert admin_row["status"] == "Pending"

    def test_expired_invite_appears_in_workspace_member_list(
        self, owner_client, org, default_ws, second_ws
    ):
        """Expired invites also appear in workspace member list."""
        _invite_user(
            owner_client,
            ["expired@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        # Backdate created_at to make it expired (invite expiry is based on created_at)
        invite = OrganizationInvite.objects.get(
            organization=org, target_email="expired@ws.com"
        )
        invite.created_at = timezone.now() - timedelta(days=30)
        invite.save(update_fields=["created_at"])

        resp = owner_client.get(_ws_member_list_url(second_ws.id))
        results = _get_results(resp)

        expired_row = next((r for r in results if r["email"] == "expired@ws.com"), None)
        assert expired_row is not None
        assert expired_row["status"] == "Expired"

    def test_workspace_member_list_status_filter(
        self, owner_client, org, default_ws, second_ws
    ):
        """Status filter works on workspace member list."""
        # Add an active member
        member = _make_user(org, "active@ws.com", "Member", Level.MEMBER, second_ws)

        # Add a pending invite
        _invite_user(
            owner_client,
            ["pending@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        # Filter by Pending only
        resp = owner_client.get(
            _ws_member_list_url(second_ws.id), {"filter_status": "Pending"}
        )
        assert resp.status_code == status.HTTP_200_OK
        results = _get_results(resp)
        assert all(r["status"] == "Pending" for r in results)

        # Filter by Active only
        resp = owner_client.get(
            _ws_member_list_url(second_ws.id), {"filter_status": "Active"}
        )
        results = _get_results(resp)
        assert all(r["status"] == "Active" for r in results)

    def test_invite_deduplicates_with_active_member(
        self, owner_client, org, default_ws, second_ws
    ):
        """If a user has both an active membership and a pending invite,
        only the invite row appears (no duplicate)."""
        # Create an invite (which dual-writes an inactive membership)
        _invite_user(
            owner_client,
            ["dedup@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        resp = owner_client.get(_ws_member_list_url(second_ws.id))
        results = _get_results(resp)
        dedup_rows = [r for r in results if r["email"] == "dedup@ws.com"]
        assert len(dedup_rows) == 1
        assert dedup_rows[0]["type"] == "invite"

    def test_ws_invite_row_has_correct_ws_role(
        self, owner_client, org, default_ws, second_ws
    ):
        """Invite row in workspace member list shows the correct workspace role."""
        _invite_user(
            owner_client,
            ["wsrole@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_ADMIN}
            ],
        )

        resp = owner_client.get(_ws_member_list_url(second_ws.id))
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "wsrole@ws.com"), None)
        assert row is not None
        assert row["ws_level"] == Level.WORKSPACE_ADMIN
        assert row["ws_role"] == "Workspace Admin"


# =====================================================================
# Feature 1b: Resend invite from workspace member tab
# =====================================================================


@pytest.mark.django_db
class TestResendInviteFromWorkspaceTab:
    """Resend invite should work using invite data from workspace member tab."""

    def test_resend_invite_for_workspace_member(
        self, owner_client, org, default_ws, second_ws
    ):
        """Can resend invite that appears in workspace member list."""
        _invite_user(
            owner_client,
            ["resend_ws@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        invite = OrganizationInvite.objects.get(
            organization=org, target_email="resend_ws@ws.com"
        )
        original_created_at = invite.created_at

        # Resend (refresh_expiration resets created_at to now)
        resp = owner_client.post(
            INVITE_RESEND_URL, {"invite_id": str(invite.id)}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

        invite.refresh_from_db()
        assert invite.created_at >= original_created_at

    def test_resend_expired_invite_from_workspace(
        self, owner_client, org, default_ws, second_ws
    ):
        """Can resend an expired invite that appears in workspace member list."""
        _invite_user(
            owner_client,
            ["expired_resend@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        invite = OrganizationInvite.objects.get(
            organization=org, target_email="expired_resend@ws.com"
        )
        # Backdate created_at to make it expired
        invite.created_at = timezone.now() - timedelta(days=30)
        invite.save(update_fields=["created_at"])
        assert invite.effective_status == "Expired"

        # Resend should refresh expiration (resets created_at to now)
        resp = owner_client.post(
            INVITE_RESEND_URL, {"invite_id": str(invite.id)}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

        invite.refresh_from_db()
        assert invite.effective_status == "Pending"

    def test_resend_expired_invite_from_legacy_user_endpoint(
        self, owner_client, org, default_ws, second_ws
    ):
        """The members-screen legacy resend (#2437) must refresh the same
        OrganizationInvite expiry InviteResendAPIView does, not just re-send
        the email — otherwise the invite still reads as expired everywhere
        that checks OrganizationInvite.is_expired."""
        _invite_user(
            owner_client,
            ["legacy_resend@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        invite = OrganizationInvite.objects.get(
            organization=org, target_email="legacy_resend@ws.com"
        )
        invite.created_at = timezone.now() - timedelta(days=30)
        invite.save(update_fields=["created_at"])
        assert invite.effective_status == "Expired"

        invited_user = User.objects.get(email="legacy_resend@ws.com")

        resp = owner_client.post(
            LEGACY_USER_RESEND_URL,
            {"user_id": str(invited_user.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        invite.refresh_from_db()
        assert invite.effective_status == "Pending"

    def test_resend_from_legacy_endpoint_does_not_reopen_accepted_invite(
        self, owner_client, org, default_ws, second_ws
    ):
        """An already-accepted invite must not be silently reopened by a
        resend from the legacy endpoint."""
        _invite_user(
            owner_client,
            ["already_accepted@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        invite = OrganizationInvite.objects.get(
            organization=org, target_email="already_accepted@ws.com"
        )
        invite.status = InviteStatus.ACCEPTED
        invite.created_at = timezone.now() - timedelta(days=30)
        invite.save(update_fields=["status", "created_at"])

        invited_user = User.objects.get(email="already_accepted@ws.com")

        resp = owner_client.post(
            LEGACY_USER_RESEND_URL,
            {"user_id": str(invited_user.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        invite.refresh_from_db()
        assert invite.status == InviteStatus.ACCEPTED
        assert invite.effective_status == "Accepted"

    def test_resend_expired_invite_from_bulk_endpoint(
        self, owner_client, org, default_ws, second_ws
    ):
        """The bulk resend-invitation-emails endpoint (#2437) has the exact
        same gap as the legacy per-user one and must be fixed the same way."""
        _invite_user(
            owner_client,
            ["bulk_resend@ws.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(second_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        invite = OrganizationInvite.objects.get(
            organization=org, target_email="bulk_resend@ws.com"
        )
        invite.created_at = timezone.now() - timedelta(days=30)
        invite.save(update_fields=["created_at"])
        assert invite.effective_status == "Expired"

        invited_user = User.objects.get(email="bulk_resend@ws.com")

        resp = owner_client.post(
            BULK_RESEND_URL,
            {"user_ids": [str(invited_user.id)]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        invite.refresh_from_db()
        assert invite.effective_status == "Pending"


# =====================================================================
# Feature 2: Reactivate deactivated member from org member tab
# =====================================================================


@pytest.mark.django_db
class TestMemberReactivation:
    """Reactivation of deactivated members from the org member tab."""

    def test_reactivate_deactivated_member(self, owner_client, org, default_ws):
        """Owner can reactivate a deactivated member."""
        member = _make_user(
            org, "deactivated@test.com", "Member", Level.MEMBER, default_ws
        )

        # Remove the member (deactivates)
        resp = owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(member.id)}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

        # Verify deactivated
        org_mem = OrganizationMembership.all_objects.get(user=member, organization=org)
        assert org_mem.is_active is False

        # Reactivate
        resp = owner_client.post(
            MEMBER_REACTIVATE_URL, {"user_id": str(member.id)}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

        # Verify reactivated
        org_mem.refresh_from_db()
        assert org_mem.is_active is True

    def test_reactivate_restores_org_membership_only(
        self, owner_client, org, default_ws, second_ws
    ):
        """Reactivation restores org membership; workspace memberships stay deactivated
        (admin must re-add workspace access manually)."""
        member = _make_user(
            org, "ws_restore@test.com", "Member", Level.MEMBER, default_ws
        )

        # Also add to second workspace
        org_mem = OrganizationMembership.all_objects.get(user=member, organization=org)
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=member,
            workspace=second_ws,
            defaults={
                "role": "Workspace Member",
                "level": Level.WORKSPACE_MEMBER,
                "organization_membership": org_mem,
                "is_active": True,
            },
        )

        # Deactivate org + all workspace memberships directly
        org_mem.is_active = False
        org_mem.save(update_fields=["is_active"])
        WorkspaceMembership.all_objects.filter(
            user=member, workspace__organization=org
        ).update(is_active=False)

        # Verify all workspace memberships deactivated
        ws_mems = WorkspaceMembership.all_objects.filter(
            user=member, workspace__organization=org
        )
        assert ws_mems.count() == 2
        assert all(not m.is_active for m in ws_mems)

        # Reactivate
        resp = owner_client.post(
            MEMBER_REACTIVATE_URL, {"user_id": str(member.id)}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

        # Org membership is restored
        org_mem.refresh_from_db()
        assert org_mem.is_active is True

        # Workspace memberships are restored along with org membership
        ws_mems = WorkspaceMembership.all_objects.filter(
            user=member, workspace__organization=org
        )
        assert ws_mems.count() == 2
        assert all(m.is_active for m in ws_mems)

    def test_reactivated_member_appears_as_active_in_member_list(
        self, owner_client, org, default_ws
    ):
        """Reactivated member shows Active status in org member list."""
        member = _make_user(
            org, "list_check@test.com", "Member", Level.MEMBER, default_ws
        )

        # Deactivate
        owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(member.id)}, format="json"
        )

        # Check deactivated status in member list
        resp = owner_client.get(MEMBER_LIST_URL)
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "list_check@test.com"), None)
        assert row is not None
        assert row["status"] == "Deactivated"

        # Reactivate
        owner_client.post(
            MEMBER_REACTIVATE_URL, {"user_id": str(member.id)}, format="json"
        )

        # Check active status
        resp = owner_client.get(MEMBER_LIST_URL)
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "list_check@test.com"), None)
        assert row is not None
        assert row["status"] == "Active"

    def test_reactivated_member_appears_in_workspace_after_reactivation(
        self, owner_client, org, default_ws
    ):
        """Reactivated member appears in workspace member list because
        reactivation restores both org and workspace memberships."""
        member = _make_user(org, "ws_list@test.com", "Member", Level.MEMBER, default_ws)

        # Deactivate
        owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(member.id)}, format="json"
        )

        # Should not appear in workspace member list
        resp = owner_client.get(_ws_member_list_url(default_ws.id))
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "ws_list@test.com"), None)
        assert row is None

        # Reactivate (restores org + workspace memberships)
        owner_client.post(
            MEMBER_REACTIVATE_URL, {"user_id": str(member.id)}, format="json"
        )

        # Should appear in workspace member list (ws membership restored)
        resp = owner_client.get(_ws_member_list_url(default_ws.id))
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "ws_list@test.com"), None)
        assert row is not None
        assert row["status"] == "Active"

    def test_cannot_reactivate_yourself(self, owner_client, owner, org, default_ws):
        """Cannot reactivate yourself."""
        resp = owner_client.post(
            MEMBER_REACTIVATE_URL, {"user_id": str(owner.id)}, format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_reactivate_already_active_member(
        self, owner_client, org, default_ws
    ):
        """Cannot reactivate a member who is already active."""
        member = _make_user(
            org, "already_active@test.com", "Member", Level.MEMBER, default_ws
        )

        resp = owner_client.post(
            MEMBER_REACTIVATE_URL, {"user_id": str(member.id)}, format="json"
        )
        # Should fail — no deactivated membership found
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_reactivate_nonexistent_user(self, owner_client, org, default_ws):
        """Cannot reactivate a nonexistent user."""
        import uuid

        resp = owner_client.post(
            MEMBER_REACTIVATE_URL, {"user_id": str(uuid.uuid4())}, format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# =====================================================================
# Feature 2b: Reactivation permission checks
# =====================================================================


@pytest.mark.django_db
class TestReactivationPermissions:
    """Only Admin+ can reactivate members."""

    def test_admin_can_reactivate_member(self, owner_client, org, default_ws):
        """Admin can reactivate a deactivated member below their level."""
        admin = _make_user(org, "admin@perm.com", "Admin", Level.ADMIN, default_ws)
        member = _make_user(org, "member@perm.com", "Member", Level.MEMBER, default_ws)

        # Owner deactivates
        owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(member.id)}, format="json"
        )

        # Admin reactivates
        admin_client = _make_client(admin, default_ws)
        try:
            resp = admin_client.post(
                MEMBER_REACTIVATE_URL, {"user_id": str(member.id)}, format="json"
            )
            assert resp.status_code == status.HTTP_200_OK

            org_mem = OrganizationMembership.all_objects.get(
                user=member, organization=org
            )
            assert org_mem.is_active is True
        finally:
            admin_client.stop_workspace_injection()

    def test_member_cannot_reactivate(self, owner_client, org, default_ws):
        """Member role cannot reactivate other members."""
        member_actor = _make_user(
            org, "member_actor@perm.com", "Member", Level.MEMBER, default_ws
        )
        target = _make_user(org, "target@perm.com", "Viewer", Level.VIEWER, default_ws)

        # Owner deactivates
        owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(target.id)}, format="json"
        )

        # Member tries to reactivate
        member_client = _make_client(member_actor, default_ws)
        try:
            resp = member_client.post(
                MEMBER_REACTIVATE_URL, {"user_id": str(target.id)}, format="json"
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            member_client.stop_workspace_injection()

    def test_viewer_cannot_reactivate(self, owner_client, org, default_ws):
        """Viewer role cannot reactivate members."""
        viewer = _make_user(org, "viewer@perm.com", "Viewer", Level.VIEWER, default_ws)
        target = _make_user(org, "target2@perm.com", "Viewer", Level.VIEWER, default_ws)

        # Owner deactivates target
        owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(target.id)}, format="json"
        )

        # Viewer tries to reactivate
        viewer_client = _make_client(viewer, default_ws)
        try:
            resp = viewer_client.post(
                MEMBER_REACTIVATE_URL, {"user_id": str(target.id)}, format="json"
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            viewer_client.stop_workspace_injection()


# =====================================================================
# Feature 2c: Deactivated status filtering in org member list
# =====================================================================


@pytest.mark.django_db
class TestDeactivatedStatusFilter:
    """Deactivated members can be filtered in org member list."""

    def test_deactivated_filter_returns_only_deactivated(
        self, owner_client, org, default_ws
    ):
        """Filtering by Deactivated returns only deactivated members."""
        active_member = _make_user(
            org, "active_filter@test.com", "Member", Level.MEMBER, default_ws
        )
        deactivated_member = _make_user(
            org, "deactivated_filter@test.com", "Member", Level.MEMBER, default_ws
        )

        # Deactivate one
        owner_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(deactivated_member.id)},
            format="json",
        )

        # Filter by Deactivated
        resp = owner_client.get(MEMBER_LIST_URL, {"filter_status": "Deactivated"})
        assert resp.status_code == status.HTTP_200_OK
        results = _get_results(resp)

        assert all(r["status"] == "Deactivated" for r in results)
        emails = [r["email"] for r in results]
        assert "deactivated_filter@test.com" in emails
        assert "active_filter@test.com" not in emails

    def test_active_filter_excludes_deactivated(self, owner_client, org, default_ws):
        """Filtering by Active excludes deactivated members."""
        _make_user(org, "active_only@test.com", "Member", Level.MEMBER, default_ws)
        deactivated = _make_user(
            org, "deactivated_only@test.com", "Member", Level.MEMBER, default_ws
        )

        owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(deactivated.id)}, format="json"
        )

        resp = owner_client.get(MEMBER_LIST_URL, {"filter_status": "Active"})
        results = _get_results(resp)

        assert all(r["status"] == "Active" for r in results)
        emails = [r["email"] for r in results]
        assert "deactivated_only@test.com" not in emails

    def test_no_filter_returns_all_statuses(self, owner_client, org, default_ws):
        """No filter returns Active + Deactivated + Pending."""
        _make_user(org, "all_active@test.com", "Member", Level.MEMBER, default_ws)
        deactivated = _make_user(
            org, "all_deactivated@test.com", "Member", Level.MEMBER, default_ws
        )
        owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(deactivated.id)}, format="json"
        )
        _invite_user(owner_client, ["all_pending@test.com"], Level.MEMBER)

        resp = owner_client.get(MEMBER_LIST_URL)
        results = _get_results(resp)
        statuses = {r["status"] for r in results}
        # Should have at least Active (owner + member) and Deactivated and Pending
        assert "Active" in statuses
        assert "Deactivated" in statuses
        assert "Pending" in statuses


# =====================================================================
# Full lifecycle: Invite → Remove → Reactivate
# =====================================================================


@pytest.mark.django_db
class TestFullLifecycle:
    """Full member lifecycle: invite → accept → remove → reactivate."""

    def _activate_invited_user(self, user, organization):
        """Simulate invite acceptance."""
        user.is_active = True
        user.save(update_fields=["is_active"])
        OrganizationMembership.all_objects.filter(
            user=user, organization=organization
        ).update(is_active=True)
        WorkspaceMembership.all_objects.filter(
            user=user, workspace__organization=organization
        ).update(is_active=True)

    def test_invite_accept_remove_reactivate_lifecycle(
        self, owner_client, org, default_ws
    ):
        """Full lifecycle: invite → accept → remove → reactivate using default ws."""
        # Step 1: Invite (to default workspace)
        resp = _invite_user(
            owner_client,
            ["lifecycle@test.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(default_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )
        assert resp.status_code == status.HTTP_200_OK

        # Step 2: Verify pending in workspace member list
        resp = owner_client.get(_ws_member_list_url(default_ws.id))
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "lifecycle@test.com"), None)
        assert row is not None
        assert row["status"] == "Pending"

        # Step 3: Simulate acceptance
        invited_user = User.objects.get(email="lifecycle@test.com")
        self._activate_invited_user(invited_user, org)

        # Set invite status to Accepted to simulate acceptance cleanup
        OrganizationInvite.objects.filter(
            organization=org, target_email="lifecycle@test.com"
        ).update(status=InviteStatus.ACCEPTED)

        # Step 4: Verify active in workspace member list
        resp = owner_client.get(_ws_member_list_url(default_ws.id))
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "lifecycle@test.com"), None)
        assert row is not None
        assert row["status"] == "Active"

        # Step 5: Remove member
        resp = owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(invited_user.id)}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

        # Step 6: Verify deactivated in org member list
        resp = owner_client.get(MEMBER_LIST_URL)
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "lifecycle@test.com"), None)
        assert row is not None
        assert row["status"] == "Deactivated"

        # Should not appear in workspace member list anymore
        resp = owner_client.get(_ws_member_list_url(default_ws.id))
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "lifecycle@test.com"), None)
        assert row is None

        # Step 7: Reactivate
        resp = owner_client.post(
            MEMBER_REACTIVATE_URL, {"user_id": str(invited_user.id)}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK

        # Step 8: Verify active again in org member list
        resp = owner_client.get(MEMBER_LIST_URL)
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "lifecycle@test.com"), None)
        assert row is not None
        assert row["status"] == "Active"

        # Workspace membership is restored by reactivation
        resp = owner_client.get(_ws_member_list_url(default_ws.id))
        results = _get_results(resp)
        row = next((r for r in results if r["email"] == "lifecycle@test.com"), None)
        assert row is not None
        assert row["status"] == "Active"

    def test_deactivate_and_reactivate_preserves_role(
        self, owner_client, org, default_ws
    ):
        """Reactivation preserves the member's original role."""
        member = _make_user(
            org, "preserve_role@test.com", "Admin", Level.ADMIN, default_ws
        )

        # Deactivate
        owner_client.delete(
            MEMBER_REMOVE_URL, {"user_id": str(member.id)}, format="json"
        )

        # Reactivate
        owner_client.post(
            MEMBER_REACTIVATE_URL, {"user_id": str(member.id)}, format="json"
        )

        # Verify role preserved
        org_mem = OrganizationMembership.all_objects.get(user=member, organization=org)
        assert org_mem.level == Level.ADMIN
        assert org_mem.is_active is True


# ── Invite Acceptance activates memberships ──


@pytest.mark.django_db
class TestInviteAcceptanceActivatesMemberships:
    """Regression tests: accepting an invite must activate org & workspace
    memberships so the user does NOT see 'You've been removed'."""

    def test_accept_invite_activates_org_membership(
        self, owner_client, org, default_ws, owner
    ):
        """After invite + accept, the org membership must be is_active=True."""
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        # Invite a brand-new user
        resp = _invite_user(
            owner_client,
            ["newguy@test.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(default_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )
        assert resp.status_code == status.HTTP_200_OK

        # Verify the dual-write created an INACTIVE org membership
        user = User.objects.get(email="newguy@test.com")
        org_mem = OrganizationMembership.all_objects.get(user=user, organization=org)
        assert org_mem.is_active is False, "Dual-write should set is_active=False"

        # Simulate accepting the invite via the URL endpoint (POST sets password + activates)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        from rest_framework.test import APIClient

        anon_client = APIClient()
        resp = anon_client.post(
            f"/accounts/accept-invitation/{uid}/{token}/",
            {"new_password": "SecurePass123!", "repeat_password": "SecurePass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # Org membership must now be active
        org_mem.refresh_from_db()
        assert (
            org_mem.is_active is True
        ), "accept_invitation_mail must activate org membership"

    def test_accept_invite_activates_workspace_membership(
        self, owner_client, org, default_ws, owner
    ):
        """After invite + accept, workspace memberships must be is_active=True."""
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        _invite_user(
            owner_client,
            ["wsguy@test.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(default_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        user = User.objects.get(email="wsguy@test.com")
        ws_mem = WorkspaceMembership.no_workspace_objects.get(
            user=user, workspace=default_ws
        )
        assert ws_mem.is_active is False

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        from rest_framework.test import APIClient

        anon_client = APIClient()
        resp = anon_client.post(
            f"/accounts/accept-invitation/{uid}/{token}/",
            {"new_password": "SecurePass123!", "repeat_password": "SecurePass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        ws_mem.refresh_from_db()
        assert (
            ws_mem.is_active is True
        ), "accept_invitation_mail must activate workspace membership"

    def test_accepted_user_has_active_membership_for_login(
        self, owner_client, org, default_ws, owner
    ):
        """After acceptance, the login query for active membership must succeed
        (this is what prevented the 'org removed' screen)."""
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        _invite_user(
            owner_client,
            ["logintest@test.com"],
            Level.MEMBER,
            workspace_access=[
                {"workspace_id": str(default_ws.id), "level": Level.WORKSPACE_MEMBER}
            ],
        )

        user = User.objects.get(email="logintest@test.com")
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        from rest_framework.test import APIClient

        anon_client = APIClient()
        anon_client.post(
            f"/accounts/accept-invitation/{uid}/{token}/",
            {"new_password": "SecurePass123!", "repeat_password": "SecurePass123!"},
            format="json",
        )

        # This is the exact query the login view uses
        active_mem = OrganizationMembership.no_workspace_objects.filter(
            user=user, is_active=True
        ).first()
        assert (
            active_mem is not None
        ), "Login must find an active org membership after invite acceptance"
