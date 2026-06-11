"""
Comprehensive E2E Login & RBAC Test Suite

Tests EVERY possible user journey through the system:
- Login scenarios (normal, org-less, new org, wrong credentials)
- Invite lifecycle (create → accept → verify access)
- Role updates + post-update access verification
- Member removal + post-removal state
- Workspace management (add, remove, last-workspace guard)
- Multi-org flows (switch, isolation, removal from one)
- Re-invite / reactivation of deactivated members
- Invite acceptance with set-password flow

"""

import pytest
from django.contrib.auth.hashers import make_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
LOGIN_URL = "/accounts/token/"
USER_INFO_URL = "/accounts/user-info/"
CREATE_ORG_URL = "/accounts/organizations/create/"
ORG_LIST_URL = "/accounts/organizations/"
ORG_SWITCH_URL = "/accounts/organizations/switch/"
INVITE_URL = "/accounts/organization/invite/"
INVITE_RESEND_URL = "/accounts/organization/invite/resend/"
INVITE_CANCEL_URL = "/accounts/organization/invite/cancel/"
MEMBER_LIST_URL = "/accounts/organization/members/"
MEMBER_ROLE_URL = "/accounts/organization/members/role/"
MEMBER_REMOVE_URL = "/accounts/organization/members/remove/"
MEMBER_REACTIVATE_URL = "/accounts/organization/members/reactivate/"
WS_MEMBER_LIST_URL = "/accounts/workspace/{ws_id}/members/"
WS_MEMBER_ROLE_URL = "/accounts/workspace/{ws_id}/members/role/"
WS_MEMBER_REMOVE_URL = "/accounts/workspace/{ws_id}/members/remove/"
ACCEPT_INVITE_URL = "/accounts/accept-invitation/{uid}/{token}/"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user(db, organization):
    """Override root user fixture to use deterministic email expected by tests."""
    clear_workspace_context()
    set_workspace_context(organization=organization)

    u = User.objects.create_user(
        email="test@futureagi.com",
        password="testpassword123",
        name="Test User",
        organization=organization,
        organization_role="Owner",
    )

    OrganizationMembership.objects.get_or_create(
        user=u,
        organization=organization,
        defaults={"role": "Owner", "level": Level.OWNER, "is_active": True},
    )

    workspace = Workspace.objects.create(
        name="Test Workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=u,
    )
    org_mem = OrganizationMembership.objects.get(user=u, organization=organization)
    WorkspaceMembership.objects.get_or_create(
        user=u,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": org_mem,
        },
    )

    set_workspace_context(workspace=workspace, organization=organization, user=u)
    return u


@pytest.fixture(autouse=True)
def _owner_membership(user, organization):
    OrganizationMembership.objects.get_or_create(
        user=user,
        organization=organization,
        defaults={"role": "Owner", "level": Level.OWNER, "is_active": True},
    )


def _make_user(organization, email, role_str, level, password="pass123"):
    """Create a user with org membership."""
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
    from conftest import WorkspaceAwareAPIClient

    c = WorkspaceAwareAPIClient()
    c.force_authenticate(user=user)
    c.set_workspace(workspace)
    _created_clients.append(c)
    return c


def _add_ws_membership(user, workspace, organization, ws_level):
    org_mem = OrganizationMembership.objects.get(
        user=user,
        organization=organization,
        is_active=True,
    )
    ws_mem, _ = WorkspaceMembership.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={
            "role": Level.to_ws_string(ws_level),
            "level": ws_level,
            "organization_membership": org_mem,
            "is_active": True,
        },
    )
    if ws_mem.level != ws_level:
        ws_mem.level = ws_level
        ws_mem.role = Level.to_ws_string(ws_level)
        ws_mem.save(update_fields=["level", "role"])
    return ws_mem


def _login(api_client, email, password):
    """Login and return the response."""
    return api_client.post(
        LOGIN_URL,
        {"email": email, "password": password},
        format="json",
    )


def _get_user_info(api_client, token):
    """Call user-info with the given access token."""
    return api_client.get(USER_INFO_URL, HTTP_AUTHORIZATION=f"Bearer {token}")


def _do_invite(client, emails, org_level, workspace, ws_level=None):
    """Send an invite via the API. Returns the response."""
    ws_level = ws_level if ws_level is not None else Level.WORKSPACE_VIEWER
    return client.post(
        INVITE_URL,
        {
            "emails": emails if isinstance(emails, list) else [emails],
            "org_level": org_level,
            "workspace_access": [
                {"workspace_id": str(workspace.id), "level": ws_level},
            ],
        },
        format="json",
    )


def _accept_invite(api_client, user_email, password="SecurePass123!"):
    """Generate token for user, POST to accept-invitation, return response."""
    user = User.objects.get(email=user_email)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    resp = api_client.post(
        ACCEPT_INVITE_URL.format(uid=uid, token=token),
        {"new_password": password, "repeat_password": password},
        format="json",
    )
    return resp, uid, token


def _login_and_create_org(api_client, email, password, org_name):
    """Login → get token → create org → return (login_resp, create_resp)."""
    login_resp = _login(api_client, email, password)
    token = login_resp.json()["access"]
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    create_resp = api_client.post(
        CREATE_ORG_URL,
        {"organization_name": org_name},
        format="json",
    )
    api_client.credentials()  # reset
    return login_resp, create_resp


# ============================================================================
# SUITE 1: LOGIN SCENARIOS
# ============================================================================


@pytest.mark.django_db
class TestLoginScenarios:
    """Every possible login outcome."""

    def test_normal_login_returns_tokens(self, api_client, user):
        """Active user with org → gets access + refresh tokens."""
        resp = _login(api_client, "test@futureagi.com", "testpassword123")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "access" in data
        assert "refresh" in data
        assert data.get("requires_org_setup") is not True

    def test_wrong_password_returns_error(self, api_client, user):
        """Wrong password → 400 with error message."""
        resp = _login(api_client, "test@futureagi.com", "wrongpassword")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_email_returns_error(self, api_client, user):
        """Non-existent email → 400."""
        resp = _login(api_client, "nobody@futureagi.com", "testpassword123")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_orgless_user_gets_requires_org_setup(self, api_client, user, organization):
        """User removed from their only org → requires_org_setup=true."""
        # Deactivate org membership
        OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
        ).update(is_active=False)

        resp = _login(api_client, "test@futureagi.com", "testpassword123")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data.get("requires_org_setup") is True
        assert "access" in data
        assert "refresh" in data

    def test_orgless_user_info_returns_requires_org_setup(
        self, api_client, user, organization
    ):
        """user-info for org-less user → requires_org_setup=true."""
        OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
        ).update(is_active=False)

        resp = _login(api_client, "test@futureagi.com", "testpassword123")
        token = resp.json()["access"]

        info = _get_user_info(api_client, token)
        assert info.status_code == status.HTTP_200_OK
        assert info.json().get("requires_org_setup") is True

    def test_inactive_user_cannot_login(self, api_client, user):
        """User with is_active=False on User model → cannot login."""
        user.is_active = False
        user.save(update_fields=["is_active"])

        resp = _login(api_client, "test@futureagi.com", "testpassword123")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_token_can_access_user_info(self, api_client, user):
        """Token from login → can call user-info successfully."""
        resp = _login(api_client, "test@futureagi.com", "testpassword123")
        token = resp.json()["access"]

        info = _get_user_info(api_client, token)
        assert info.status_code == status.HTTP_200_OK
        assert info.json()["email"] == "test@futureagi.com"


# ============================================================================
# SUITE 2: ORG CREATION (after removal)
# ============================================================================


@pytest.mark.django_db
class TestOrgCreationAfterRemoval:
    """Removed user creates a new org and starts fresh."""

    def test_orgless_user_can_create_org(self, api_client, user, organization):
        """Removed user → login → create org → gets new org."""
        OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
        ).update(is_active=False)

        _, create_resp = _login_and_create_org(
            api_client,
            "test@futureagi.com",
            "testpassword123",
            "My New Org",
        )
        assert create_resp.status_code == status.HTTP_201_CREATED
        data = create_resp.json()
        assert "result" in data
        assert "organization_id" in data["result"]

    def test_new_org_has_default_workspace(self, api_client, user, organization):
        """New org creation → default workspace auto-created."""
        OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
        ).update(is_active=False)

        _, create_resp = _login_and_create_org(
            api_client,
            "test@futureagi.com",
            "testpassword123",
            "Fresh Org",
        )
        org_id = create_resp.json()["result"]["organization_id"]
        new_org = Organization.objects.get(id=org_id)
        assert Workspace.objects.filter(organization=new_org, is_default=True).exists()

    def test_new_org_user_is_owner(self, api_client, user, organization):
        """After creating org, user is Owner of the new org."""
        OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
        ).update(is_active=False)

        _, create_resp = _login_and_create_org(
            api_client,
            "test@futureagi.com",
            "testpassword123",
            "Owner Test Org",
        )
        org_id = create_resp.json()["result"]["organization_id"]

        mem = OrganizationMembership.no_workspace_objects.get(
            user=user,
            organization_id=org_id,
            is_active=True,
        )
        assert mem.level == Level.OWNER

    def test_login_after_new_org_no_longer_requires_setup(
        self, api_client, user, organization
    ):
        """After creating new org → login no longer returns requires_org_setup."""
        OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
        ).update(is_active=False)

        _login_and_create_org(
            api_client,
            "test@futureagi.com",
            "testpassword123",
            "Recovery Org",
        )

        # Login again
        resp2 = _login(api_client, "test@futureagi.com", "testpassword123")
        assert resp2.json().get("requires_org_setup") is not True
        assert "access" in resp2.json()


# ============================================================================
# SUITE 3: FULL INVITE LIFECYCLE
# ============================================================================


@pytest.mark.django_db
class TestInviteLifecycle:
    """Invite → accept → verify access → verify correct role."""

    def test_invite_creates_pending_user(self, auth_client, organization, workspace):
        """Invite new email → User created (inactive), invite row created."""
        resp = _do_invite(
            auth_client,
            "newuser@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )
        assert resp.status_code == status.HTTP_200_OK

        # User exists but inactive
        new_user = User.objects.get(email="newuser@futureagi.com")
        assert new_user.is_active is False

        # Org membership exists but inactive
        org_mem = OrganizationMembership.all_objects.get(
            user=new_user,
            organization=organization,
        )
        assert org_mem.is_active is False
        assert org_mem.level == Level.MEMBER

        # Invite row exists with Pending status
        assert OrganizationInvite.objects.filter(
            target_email="newuser@futureagi.com",
            organization=organization,
            status=InviteStatus.PENDING,
        ).exists()

    def test_invite_accept_activates_memberships(
        self, auth_client, api_client, organization, workspace
    ):
        """Accept invite → user active, org membership active, ws membership active."""
        # Create invite
        _do_invite(
            auth_client,
            "accepttest@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )

        new_user = User.objects.get(email="accepttest@futureagi.com")
        uid = urlsafe_base64_encode(force_bytes(new_user.pk))
        token = default_token_generator.make_token(new_user)

        # GET to validate
        resp = api_client.get(ACCEPT_INVITE_URL.format(uid=uid, token=token))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["valid"] is True

        # POST to set password and activate
        resp = api_client.post(
            ACCEPT_INVITE_URL.format(uid=uid, token=token),
            {"new_password": "SecurePass123!", "repeat_password": "SecurePass123!"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "access" in data
        assert "refresh" in data

        # Verify user activated
        new_user.refresh_from_db()
        assert new_user.is_active is True

        # Verify org membership activated
        org_mem = OrganizationMembership.no_workspace_objects.get(
            user=new_user,
            organization=organization,
        )
        assert org_mem.is_active is True
        assert org_mem.level == Level.MEMBER

    def test_accepted_user_can_login(
        self, auth_client, api_client, organization, workspace
    ):
        """After accepting invite, user can login with their password."""
        _do_invite(
            auth_client,
            "logintest@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )

        _accept_invite(api_client, "logintest@futureagi.com", "MyPass123!")

        # Login with that password
        login_resp = _login(api_client, "logintest@futureagi.com", "MyPass123!")
        assert login_resp.status_code == status.HTTP_200_OK
        assert "access" in login_resp.json()

    def test_expired_invite_link_rejected(
        self, auth_client, api_client, organization, workspace
    ):
        """Invite link with wrong token → 400."""
        _do_invite(
            auth_client,
            "exptest@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )

        new_user = User.objects.get(email="exptest@futureagi.com")
        uid = urlsafe_base64_encode(force_bytes(new_user.pk))

        # Use a bogus token
        resp = api_client.get(
            ACCEPT_INVITE_URL.format(uid=uid, token="bad-token-value")
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_consumed_invite_link_rejected(
        self, auth_client, api_client, organization, workspace
    ):
        """After accepting, the same link should fail (token consumed by user.save)."""
        _do_invite(
            auth_client,
            "consumed@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )

        new_user = User.objects.get(email="consumed@futureagi.com")
        uid = urlsafe_base64_encode(force_bytes(new_user.pk))
        token = default_token_generator.make_token(new_user)

        # Accept
        api_client.post(
            ACCEPT_INVITE_URL.format(uid=uid, token=token),
            {"new_password": "SecurePass123!", "repeat_password": "SecurePass123!"},
            format="json",
        )

        # Same link again → invalid
        resp = api_client.get(ACCEPT_INVITE_URL.format(uid=uid, token=token))
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_invite_resend(self, auth_client, organization, workspace):
        """Resend invite → 200 (invite still exists)."""
        _do_invite(
            auth_client,
            "resendtest@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )

        invite = OrganizationInvite.objects.get(
            target_email="resendtest@futureagi.com",
            organization=organization,
        )
        resp = auth_client.post(
            INVITE_RESEND_URL,
            {"invite_id": str(invite.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_invite_cancel_removes_invite(self, auth_client, organization, workspace):
        """Cancel invite → invite deleted, user memberships deactivated."""
        _do_invite(
            auth_client,
            "canceltest@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )

        invite = OrganizationInvite.objects.get(
            target_email="canceltest@futureagi.com",
            organization=organization,
        )
        resp = auth_client.delete(
            INVITE_CANCEL_URL,
            {"invite_id": str(invite.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert OrganizationInvite.objects.filter(
            id=invite.id, status=InviteStatus.CANCELLED
        ).exists()


# ============================================================================
# SUITE 4: POST-INVITE ACCESS VERIFICATION
# ============================================================================


@pytest.mark.django_db
class TestPostInviteAccess:
    """After invite acceptance, verify the invited user can actually use the API."""

    def _invite_and_accept(
        self, auth_client, api_client, org, ws, email, level, ws_level
    ):
        """Helper: invite + accept + return (user, token)."""
        _do_invite(auth_client, email, level, ws, ws_level)

        resp, _, _ = _accept_invite(api_client, email)
        new_user = User.objects.get(email=email)
        return new_user, resp.json().get("access")

    def test_invited_admin_can_view_member_list(
        self, auth_client, api_client, organization, workspace
    ):
        """Invited Admin can call member list API (requires Admin+ perm)."""
        new_user, token = self._invite_and_accept(
            auth_client,
            api_client,
            organization,
            workspace,
            "admin-access@futureagi.com",
            Level.ADMIN,
            Level.WORKSPACE_ADMIN,
        )
        client = _make_client(new_user, workspace)
        resp = client.get(MEMBER_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_invited_viewer_cannot_invite(
        self, auth_client, api_client, organization, workspace
    ):
        """Invited Viewer cannot send invites (no permission)."""
        new_user, token = self._invite_and_accept(
            auth_client,
            api_client,
            organization,
            workspace,
            "viewer-noinvite@futureagi.com",
            Level.VIEWER,
            Level.WORKSPACE_VIEWER,
        )
        client = _make_client(new_user, workspace)
        resp = _do_invite(
            client,
            "target@futureagi.com",
            Level.VIEWER,
            workspace,
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_invited_admin_can_invite_member(
        self, auth_client, api_client, organization, workspace
    ):
        """Invited Admin can invite at Member level."""
        new_user, token = self._invite_and_accept(
            auth_client,
            api_client,
            organization,
            workspace,
            "admin-invite@futureagi.com",
            Level.ADMIN,
            Level.WORKSPACE_ADMIN,
        )
        client = _make_client(new_user, workspace)
        resp = _do_invite(
            client,
            "byadmin@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_invited_admin_can_invite_admin(
        self, auth_client, api_client, organization, workspace
    ):
        """Admin can invite at Admin level (equal to their own level)."""
        new_user, token = self._invite_and_accept(
            auth_client,
            api_client,
            organization,
            workspace,
            "admin-noadmin@futureagi.com",
            Level.ADMIN,
            Level.WORKSPACE_ADMIN,
        )
        client = _make_client(new_user, workspace)
        resp = _do_invite(
            client,
            "peer@futureagi.com",
            Level.ADMIN,
            workspace,
        )
        assert resp.status_code == status.HTTP_200_OK


# ============================================================================
# SUITE 5: MEMBER REMOVAL + POST-REMOVAL STATE
# ============================================================================


@pytest.mark.django_db
class TestMemberRemovalLifecycle:
    """Remove member → verify deactivation → verify login state → verify recovery."""

    def _setup_target(self, organization, workspace, email, level, ws_level):
        """Create a target user with org + ws membership."""
        target = _make_user(organization, email, Level.to_org_string(level), level)
        _add_ws_membership(target, workspace, organization, ws_level)
        return target

    def test_owner_removes_member(self, auth_client, organization, workspace):
        """Owner removes Member → org membership deactivated."""
        target = self._setup_target(
            organization,
            workspace,
            "rm-member@futureagi.com",
            Level.MEMBER,
            Level.WORKSPACE_MEMBER,
        )
        resp = auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(target.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        org_mem = OrganizationMembership.all_objects.get(
            user=target,
            organization=organization,
        )
        assert org_mem.is_active is False

    def test_removal_cascades_to_ws_memberships(
        self, auth_client, organization, workspace
    ):
        """Remove from org → ws memberships also deactivated."""
        target = self._setup_target(
            organization,
            workspace,
            "rm-cascade@futureagi.com",
            Level.MEMBER,
            Level.WORKSPACE_MEMBER,
        )
        auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(target.id)},
            format="json",
        )

        ws_mems = WorkspaceMembership.all_objects.filter(
            user=target,
            workspace__organization=organization,
        )
        for ws_mem in ws_mems:
            assert ws_mem.is_active is False

    def test_removed_user_login_returns_requires_org_setup(
        self, auth_client, api_client, organization, workspace
    ):
        """Removed member logs in → requires_org_setup=true."""
        target = self._setup_target(
            organization,
            workspace,
            "rm-login@futureagi.com",
            Level.MEMBER,
            Level.WORKSPACE_MEMBER,
        )
        auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(target.id)},
            format="json",
        )

        login_resp = _login(api_client, "rm-login@futureagi.com", "pass123")
        assert login_resp.status_code == status.HTTP_200_OK
        assert login_resp.json().get("requires_org_setup") is True

    def test_removed_user_creates_new_org_and_logs_in(
        self, auth_client, api_client, organization, workspace
    ):
        """Removed user → create new org → login works normally."""
        target = self._setup_target(
            organization,
            workspace,
            "rm-neworg@futureagi.com",
            Level.MEMBER,
            Level.WORKSPACE_MEMBER,
        )
        auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(target.id)},
            format="json",
        )

        # Login (gets requires_org_setup) → create org
        _, create_resp = _login_and_create_org(
            api_client,
            "rm-neworg@futureagi.com",
            "pass123",
            "Recovery Org",
        )
        assert create_resp.status_code == status.HTTP_201_CREATED

        # Login again — no requires_org_setup
        resp2 = _login(api_client, "rm-neworg@futureagi.com", "pass123")
        assert resp2.json().get("requires_org_setup") is not True

    def test_cannot_remove_last_owner(self, auth_client, organization, workspace, user):
        """Cannot remove the last owner of the org."""
        resp = auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(user.id)},
            format="json",
        )
        # Either "can't remove self" or "last owner" error
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_remove_self(self, auth_client, organization, workspace, user):
        """Cannot remove yourself."""
        # Add a second owner so "last owner" isn't the blocker
        _make_user(organization, "owner2@futureagi.com", "Owner", Level.OWNER)

        resp = auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(user.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_admin_cannot_remove_owner(
        self, auth_client, organization, workspace, user
    ):
        """Admin cannot remove Owner (8 < 15)."""
        admin = _make_user(
            organization,
            "admin-normo@futureagi.com",
            "Admin",
            Level.ADMIN,
        )
        _add_ws_membership(admin, workspace, organization, Level.WORKSPACE_ADMIN)
        admin_client = _make_client(admin, workspace)

        resp = admin_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(user.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_member_cannot_remove_anyone(self, auth_client, organization, workspace):
        """Member has no removal permission."""
        member = _make_user(
            organization,
            "member-normo@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(member, workspace, organization, Level.WORKSPACE_MEMBER)
        target = _make_user(
            organization,
            "target-normo@futureagi.com",
            "Viewer",
            Level.VIEWER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_VIEWER)

        member_client = _make_client(member, workspace)
        resp = member_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(target.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_member_list_shows_deactivated_status(
        self, auth_client, organization, workspace
    ):
        """After removal, member appears as 'Deactivated' in member list."""
        target = self._setup_target(
            organization,
            workspace,
            "rm-status@futureagi.com",
            Level.MEMBER,
            Level.WORKSPACE_MEMBER,
        )
        auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(target.id)},
            format="json",
        )

        resp = auth_client.get(MEMBER_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        # Response: {"status": true, "result": {"results": [...], ...}}
        data = resp.json()
        members = data.get("result", {}).get("results", [])
        target_entry = next(
            (m for m in members if m.get("email") == "rm-status@futureagi.com"),
            None,
        )
        assert target_entry is not None
        assert target_entry["status"] == "Deactivated"


# ============================================================================
# SUITE 6: RE-INVITE / REACTIVATION
# ============================================================================


@pytest.mark.django_db
class TestReinviteAndReactivation:
    """Re-invite deactivated member → membership restored."""

    def _remove_member(self, auth_client, user_id):
        return auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(user_id)},
            format="json",
        )

    def test_reinvite_deactivated_member_restores_membership(
        self, auth_client, api_client, organization, workspace
    ):
        """Remove → re-invite → org membership becomes active again."""
        target = _make_user(
            organization,
            "reinvite@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)

        # Remove
        self._remove_member(auth_client, target.id)
        assert not OrganizationMembership.objects.filter(
            user=target,
            organization=organization,
            is_active=True,
        ).exists()

        # Re-invite
        resp = _do_invite(
            auth_client,
            "reinvite@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )
        assert resp.status_code == status.HTTP_200_OK

        # Since user already exists and is active, membership should be restored
        org_mem = OrganizationMembership.no_workspace_objects.get(
            user=target,
            organization=organization,
        )
        assert org_mem.is_active is True

    def test_reactivate_deactivated_member(self, auth_client, organization, workspace):
        """Reactivate API restores org + ws memberships."""
        target = _make_user(
            organization,
            "reactivate@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)

        self._remove_member(auth_client, target.id)

        resp = auth_client.post(
            MEMBER_REACTIVATE_URL,
            {"user_id": str(target.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        org_mem = OrganizationMembership.no_workspace_objects.get(
            user=target,
            organization=organization,
        )
        assert org_mem.is_active is True

    def test_reactivated_member_can_login_normally(
        self, auth_client, api_client, organization, workspace
    ):
        """Reactivated member → login → no requires_org_setup."""
        target = _make_user(
            organization,
            "react-login@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)

        self._remove_member(auth_client, target.id)

        auth_client.post(
            MEMBER_REACTIVATE_URL,
            {"user_id": str(target.id)},
            format="json",
        )

        login_resp = _login(api_client, "react-login@futureagi.com", "pass123")
        assert login_resp.status_code == status.HTTP_200_OK
        assert login_resp.json().get("requires_org_setup") is not True


# ============================================================================
# SUITE 7: WORKSPACE MEMBER MANAGEMENT
# ============================================================================


@pytest.mark.django_db
class TestWorkspaceMemberManagement:
    """Workspace-level add/remove/role-change operations."""

    def test_cannot_remove_from_last_workspace(
        self, auth_client, organization, workspace
    ):
        """Member in only 1 workspace → cannot be removed from it."""
        target = _make_user(
            organization,
            "lastwsrm@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)

        url = WS_MEMBER_REMOVE_URL.format(ws_id=str(workspace.id))
        resp = auth_client.delete(
            url,
            {"user_id": str(target.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "only workspace" in resp.json().get("result", "").lower()

    def test_can_remove_from_workspace_if_has_another(
        self, auth_client, organization, workspace, user
    ):
        """Member in 2 workspaces → can be removed from one."""
        ws2 = Workspace.objects.create(
            name="Second WS",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        target = _make_user(
            organization,
            "multi-ws@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        _add_ws_membership(target, ws2, organization, Level.WORKSPACE_MEMBER)

        url = WS_MEMBER_REMOVE_URL.format(ws_id=str(workspace.id))
        resp = auth_client.delete(
            url,
            {"user_id": str(target.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # Still has ws2 membership
        assert WorkspaceMembership.objects.filter(
            user=target,
            workspace=ws2,
            is_active=True,
        ).exists()

    def test_cannot_remove_org_admin_from_workspace(
        self, auth_client, organization, workspace
    ):
        """Org Admin auto-accesses all workspaces → cannot be removed from ws."""
        admin = _make_user(
            organization,
            "ws-admin-protect@futureagi.com",
            "Admin",
            Level.ADMIN,
        )
        _add_ws_membership(admin, workspace, organization, Level.WORKSPACE_ADMIN)

        url = WS_MEMBER_REMOVE_URL.format(ws_id=str(workspace.id))
        resp = auth_client.delete(
            url,
            {"user_id": str(admin.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_ws_role_update(self, auth_client, organization, workspace):
        """Owner changes ws member's role from Member to Viewer."""
        target = _make_user(
            organization,
            "ws-roleup@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)

        url = WS_MEMBER_ROLE_URL.format(ws_id=str(workspace.id))
        resp = auth_client.post(
            url,
            {
                "user_id": str(target.id),
                "ws_level": Level.WORKSPACE_VIEWER,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        ws_mem = WorkspaceMembership.objects.get(
            user=target,
            workspace=workspace,
        )
        assert ws_mem.level == Level.WORKSPACE_VIEWER


# ============================================================================
# SUITE 8: MULTI-ORG FLOWS
# ============================================================================


@pytest.mark.django_db
class TestMultiOrgFlows:
    """User in multiple orgs — switching, isolation, removal from one."""

    @pytest.fixture
    def second_org(self, db, user):
        """Create a second org where the user is a member."""
        org2 = Organization.objects.create(name="Second Org")
        OrganizationMembership.objects.create(
            user=user,
            organization=org2,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )
        ws2 = Workspace.objects.create(
            name="Org2 Default WS",
            organization=org2,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        org_mem = OrganizationMembership.objects.get(
            user=user,
            organization=org2,
        )
        WorkspaceMembership.no_workspace_objects.create(
            workspace=ws2,
            user=user,
            role="Workspace Member",
            level=Level.WORKSPACE_MEMBER,
            organization_membership=org_mem,
            is_active=True,
        )
        return org2

    def test_user_with_two_orgs_can_login(
        self, api_client, user, organization, second_org
    ):
        """User in 2 orgs → login succeeds."""
        resp = _login(api_client, "test@futureagi.com", "testpassword123")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json().get("requires_org_setup") is not True

    def test_removed_from_one_org_still_has_other(
        self, auth_client, api_client, user, organization, workspace, second_org
    ):
        """Remove from org1 → login still works (org2 active)."""
        # Need a second owner so we can remove this user from org1
        owner2 = _make_user(
            organization,
            "owner2-multi@futureagi.com",
            "Owner",
            Level.OWNER,
        )
        _add_ws_membership(owner2, workspace, organization, Level.WORKSPACE_ADMIN)
        owner2_client = _make_client(owner2, workspace)

        owner2_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(user.id)},
            format="json",
        )

        # Login — should NOT get requires_org_setup (still in second_org)
        resp = _login(api_client, "test@futureagi.com", "testpassword123")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json().get("requires_org_setup") is not True

    def test_org_list_shows_both_orgs(
        self, auth_client, user, organization, second_org
    ):
        """Organization list API returns both orgs."""
        resp = auth_client.get(ORG_LIST_URL)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        # Response: {"status": true, "result": {"organizations": [...], ...}}
        orgs = data.get("result", {}).get("organizations", [])
        org_ids = {o["id"] for o in orgs}
        assert str(organization.id) in org_ids
        assert str(second_org.id) in org_ids


# ============================================================================
# SUITE 9: ROLE UPDATE PERMISSION MATRIX
# ============================================================================


@pytest.mark.django_db
class TestRoleUpdatePermissions:
    """Verify who can change whom to what."""

    def _update_role(self, client, target_id, new_level):
        return client.post(
            MEMBER_ROLE_URL,
            {"user_id": str(target_id), "org_level": new_level},
            format="json",
        )

    def test_owner_can_promote_member_to_admin(
        self, auth_client, organization, workspace
    ):
        target = _make_user(
            organization,
            "promo-to-admin@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        resp = self._update_role(auth_client, target.id, Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK

    def test_owner_can_promote_member_to_owner(
        self, auth_client, organization, workspace
    ):
        target = _make_user(
            organization,
            "promo-to-owner@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        resp = self._update_role(auth_client, target.id, Level.OWNER)
        assert resp.status_code == status.HTTP_200_OK

    def test_admin_can_change_member_to_viewer(
        self, auth_client, organization, workspace
    ):
        admin = _make_user(
            organization,
            "admin-chg@futureagi.com",
            "Admin",
            Level.ADMIN,
        )
        _add_ws_membership(admin, workspace, organization, Level.WORKSPACE_ADMIN)
        target = _make_user(
            organization,
            "m-to-v@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)

        admin_client = _make_client(admin, workspace)
        resp = self._update_role(admin_client, target.id, Level.VIEWER)
        assert resp.status_code == status.HTTP_200_OK

    def test_admin_can_promote_to_admin(self, auth_client, organization, workspace):
        admin = _make_user(
            organization,
            "admin-noadmin@futureagi.com",
            "Admin",
            Level.ADMIN,
        )
        _add_ws_membership(admin, workspace, organization, Level.WORKSPACE_ADMIN)
        target = _make_user(
            organization,
            "m-noadmin@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)

        admin_client = _make_client(admin, workspace)
        resp = self._update_role(admin_client, target.id, Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK

    def test_admin_cannot_change_owner_role(
        self, auth_client, organization, workspace, user
    ):
        admin = _make_user(
            organization,
            "admin-noown@futureagi.com",
            "Admin",
            Level.ADMIN,
        )
        _add_ws_membership(admin, workspace, organization, Level.WORKSPACE_ADMIN)

        admin_client = _make_client(admin, workspace)
        resp = self._update_role(admin_client, user.id, Level.MEMBER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_member_cannot_update_any_role(self, auth_client, organization, workspace):
        member = _make_user(
            organization,
            "member-norole@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(member, workspace, organization, Level.WORKSPACE_MEMBER)
        target = _make_user(
            organization,
            "target-norole@futureagi.com",
            "Viewer",
            Level.VIEWER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_VIEWER)

        member_client = _make_client(member, workspace)
        resp = self._update_role(member_client, target.id, Level.MEMBER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_viewer_cannot_update_any_role(self, auth_client, organization, workspace):
        viewer = _make_user(
            organization,
            "viewer-norole@futureagi.com",
            "Viewer",
            Level.VIEWER,
        )
        _add_ws_membership(viewer, workspace, organization, Level.WORKSPACE_VIEWER)
        target = _make_user(
            organization,
            "vtarget@futureagi.com",
            "Viewer",
            Level.VIEWER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_VIEWER)

        viewer_client = _make_client(viewer, workspace)
        resp = self._update_role(viewer_client, target.id, Level.MEMBER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ============================================================================
# SUITE 10: INVITE PERMISSION MATRIX
# ============================================================================


@pytest.mark.django_db
class TestInvitePermissions:
    """Who can invite at each level."""

    def test_owner_can_invite_owner(self, auth_client, organization, workspace):
        resp = _do_invite(
            auth_client,
            "inv-owner@futureagi.com",
            Level.OWNER,
            workspace,
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_owner_can_invite_admin(self, auth_client, organization, workspace):
        resp = _do_invite(
            auth_client,
            "inv-admin@futureagi.com",
            Level.ADMIN,
            workspace,
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_owner_can_invite_member(self, auth_client, organization, workspace):
        resp = _do_invite(
            auth_client,
            "inv-member@futureagi.com",
            Level.MEMBER,
            workspace,
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_owner_can_invite_viewer(self, auth_client, organization, workspace):
        resp = _do_invite(
            auth_client,
            "inv-viewer@futureagi.com",
            Level.VIEWER,
            workspace,
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_admin_cannot_invite_owner(self, auth_client, organization, workspace):
        admin = _make_user(
            organization,
            "admin-inv@futureagi.com",
            "Admin",
            Level.ADMIN,
        )
        _add_ws_membership(admin, workspace, organization, Level.WORKSPACE_ADMIN)
        admin_client = _make_client(admin, workspace)

        resp = _do_invite(
            admin_client,
            "adm-inv-own@futureagi.com",
            Level.OWNER,
            workspace,
        )
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_invite_admin(self, auth_client, organization, workspace):
        admin = _make_user(
            organization,
            "admin-inv2@futureagi.com",
            "Admin",
            Level.ADMIN,
        )
        _add_ws_membership(admin, workspace, organization, Level.WORKSPACE_ADMIN)
        admin_client = _make_client(admin, workspace)

        resp = _do_invite(
            admin_client,
            "adm-inv-adm@futureagi.com",
            Level.ADMIN,
            workspace,
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_admin_can_invite_member(self, auth_client, organization, workspace):
        admin = _make_user(
            organization,
            "admin-inv3@futureagi.com",
            "Admin",
            Level.ADMIN,
        )
        _add_ws_membership(admin, workspace, organization, Level.WORKSPACE_ADMIN)
        admin_client = _make_client(admin, workspace)

        resp = _do_invite(
            admin_client,
            "adm-inv-mem@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_member_cannot_invite_anyone(self, auth_client, organization, workspace):
        member = _make_user(
            organization,
            "member-inv@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(member, workspace, organization, Level.WORKSPACE_MEMBER)
        member_client = _make_client(member, workspace)

        resp = _do_invite(
            member_client,
            "mem-inv-v@futureagi.com",
            Level.VIEWER,
            workspace,
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_viewer_cannot_invite_anyone(self, auth_client, organization, workspace):
        viewer = _make_user(
            organization,
            "viewer-inv@futureagi.com",
            "Viewer",
            Level.VIEWER,
        )
        _add_ws_membership(viewer, workspace, organization, Level.WORKSPACE_VIEWER)
        viewer_client = _make_client(viewer, workspace)

        resp = _do_invite(
            viewer_client,
            "view-inv-v@futureagi.com",
            Level.VIEWER,
            workspace,
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ============================================================================
# SUITE 11: FULL LIFECYCLE SCENARIOS (complex multi-step)
# ============================================================================


@pytest.mark.django_db
class TestFullLifecycleScenarios:
    """Complex multi-step scenarios testing real-world user journeys."""

    def test_invite_accept_work_remove_recover(
        self, auth_client, api_client, organization, workspace
    ):
        """
        Full cycle:
        1. Owner invites Member
        2. Member accepts (set password)
        3. Member can access API (force_authenticate check)
        4. Owner removes Member
        5. Member login → requires_org_setup
        6. Member creates new org
        7. Member login → normal (new org)
        """
        # 1. Invite
        _do_invite(
            auth_client,
            "lifecycle@futureagi.com",
            Level.MEMBER,
            workspace,
            Level.WORKSPACE_MEMBER,
        )

        # 2. Accept
        target = User.objects.get(email="lifecycle@futureagi.com")
        uid = urlsafe_base64_encode(force_bytes(target.pk))
        token = default_token_generator.make_token(target)
        accept_resp = api_client.post(
            ACCEPT_INVITE_URL.format(uid=uid, token=token),
            {"new_password": "LifePass123!", "repeat_password": "LifePass123!"},
            format="json",
        )
        assert accept_resp.status_code == status.HTTP_200_OK

        # 3. Member can access API (using force_authenticate)
        target.refresh_from_db()
        target_client = _make_client(target, workspace)
        # Just verify the client works (e.g., user-info via force_auth)
        info_resp = target_client.get(USER_INFO_URL)
        assert info_resp.status_code == status.HTTP_200_OK

        # 4. Owner removes member
        auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(target.id)},
            format="json",
        )

        # 5. Login → requires_org_setup
        login1 = _login(api_client, "lifecycle@futureagi.com", "LifePass123!")
        assert login1.json().get("requires_org_setup") is True

        # 6. Create new org
        access_token = login1.json()["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        create_resp = api_client.post(
            CREATE_ORG_URL,
            {"organization_name": "Lifecycle Org"},
            format="json",
        )
        assert create_resp.status_code == status.HTTP_201_CREATED
        api_client.credentials()  # reset

        # 7. Login again → normal
        login2 = _login(api_client, "lifecycle@futureagi.com", "LifePass123!")
        assert login2.json().get("requires_org_setup") is not True

    def test_invite_promote_demote_remove_reinvite(
        self, auth_client, api_client, organization, workspace
    ):
        """
        1. Invite as Viewer
        2. Promote to Admin
        3. Demote to Member
        4. Remove from org
        5. Re-invite as Viewer
        6. Verify final state
        """
        # 1. Invite as Viewer
        _do_invite(
            auth_client,
            "churn@futureagi.com",
            Level.VIEWER,
            workspace,
            Level.WORKSPACE_VIEWER,
        )
        target = User.objects.get(email="churn@futureagi.com")
        uid = urlsafe_base64_encode(force_bytes(target.pk))
        tk = default_token_generator.make_token(target)
        api_client.post(
            ACCEPT_INVITE_URL.format(uid=uid, token=tk),
            {"new_password": "ChurnPass123!", "repeat_password": "ChurnPass123!"},
            format="json",
        )

        # 2. Promote to Admin
        resp = auth_client.post(
            MEMBER_ROLE_URL,
            {
                "user_id": str(target.id),
                "org_level": Level.ADMIN,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # 3. Demote to Member
        resp = auth_client.post(
            MEMBER_ROLE_URL,
            {
                "user_id": str(target.id),
                "org_level": Level.MEMBER,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # 4. Remove from org
        auth_client.delete(
            MEMBER_REMOVE_URL,
            {"user_id": str(target.id)},
            format="json",
        )

        # 5. Re-invite as Viewer
        resp = _do_invite(
            auth_client,
            "churn@futureagi.com",
            Level.VIEWER,
            workspace,
            Level.WORKSPACE_VIEWER,
        )
        assert resp.status_code == status.HTTP_200_OK

        # 6. Verify final state: active membership
        org_mem = OrganizationMembership.no_workspace_objects.get(
            user=target,
            organization=organization,
        )
        assert org_mem.is_active is True

    def test_two_workspaces_remove_from_one(
        self, auth_client, organization, workspace, user
    ):
        """
        1. Create second workspace
        2. Add member to both
        3. Remove from one → succeeds
        4. Try remove from last → blocked
        """
        ws2 = Workspace.objects.create(
            name="WS Two",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        target = _make_user(
            organization,
            "twows@futureagi.com",
            "Member",
            Level.MEMBER,
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        _add_ws_membership(target, ws2, organization, Level.WORKSPACE_MEMBER)

        # Remove from workspace 1 → OK
        url1 = WS_MEMBER_REMOVE_URL.format(ws_id=str(workspace.id))
        resp = auth_client.delete(url1, {"user_id": str(target.id)}, format="json")
        assert resp.status_code == status.HTTP_200_OK

        # Remove from workspace 2 (last one) → BLOCKED
        url2 = WS_MEMBER_REMOVE_URL.format(ws_id=str(ws2.id))
        resp = auth_client.delete(url2, {"user_id": str(target.id)}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
