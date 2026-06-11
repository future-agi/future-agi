"""
E2E tests for role updates and member removal in the Django RBAC system.

Covers:
- Org-level role updates (Owner, Admin, Member, Viewer actors)
- Workspace-level role updates
- Org member removal (permission matrix)
- Post-removal state verification
- Workspace member removal

"""

import pytest
from rest_framework import status

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


@pytest.fixture(autouse=True)
def _owner_membership(user, organization):
    """Ensure the owner user has an OrganizationMembership."""
    OrganizationMembership.objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": "Owner",
            "level": Level.OWNER,
            "is_active": True,
        },
    )


@pytest.fixture(autouse=True)
def _bypass_plan_entitlement_check():
    """Bypass plan-gating so role update tests don't require a paid plan.

    MemberRoleUpdateAPIView checks ``Entitlements.check_feature("has_custom_roles")``
    which is only enabled on scale/enterprise plans. Test orgs default to free.
    Patch the check at its source so both org- and workspace-level role update
    endpoints see an allowed result.
    """
    from unittest.mock import patch

    try:
        from ee.usage.schemas.events import CheckResult
    except ImportError:
        CheckResult = None

    with patch(
        "ee.usage.services.entitlements.Entitlements.check_feature",
        return_value=CheckResult(allowed=True),
    ):
        yield


# Track WorkspaceAwareAPIClient instances created by _make_client so the
# autouse fixture below can tear down their injected APIView.initial patch
# after each test. Without this cleanup, the patch leaks into every
# subsequent test in the pytest process.
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


def _make_user(organization, email, role_str, level, password="pass123"):
    """Create a user with the given org role and membership."""
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
    """Create an authenticated API client for the given user.

    The client is registered with ``_created_clients`` so the autouse
    teardown fixture can stop its injected ``APIView.initial`` patch after
    the test completes (otherwise the patch leaks process-wide and
    contaminates subsequent tests).
    """
    from conftest import WorkspaceAwareAPIClient

    c = WorkspaceAwareAPIClient()
    c.force_authenticate(user=user)
    c.set_workspace(workspace)
    _created_clients.append(c)
    return c


def _add_ws_membership(user, workspace, organization, ws_level):
    """Add a workspace membership for the user."""
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


# ---------------------------------------------------------------------------
# Helper URLs
# ---------------------------------------------------------------------------

ORG_ROLE_URL = "/accounts/organization/members/role/"
ORG_REMOVE_URL = "/accounts/organization/members/remove/"


def _ws_role_url(workspace_id):
    return f"/accounts/workspace/{workspace_id}/members/role/"


def _ws_remove_url(workspace_id):
    return f"/accounts/workspace/{workspace_id}/members/remove/"


# ===================================================================
# TestOwnerRoleUpdates
# ===================================================================


@pytest.mark.integration
@pytest.mark.api
class TestOwnerRoleUpdates:
    """Owner changing org roles -- ALL should ALLOW."""

    def _update_role(self, auth_client, target_user, new_level):
        return auth_client.post(
            ORG_ROLE_URL,
            {"user_id": str(target_user.id), "org_level": new_level},
            format="json",
        )

    # -- Admin target --

    def test_owner_changes_admin_to_owner(self, auth_client, organization, workspace):
        target = _make_user(organization, "admin1@futureagi.com", "Admin", Level.ADMIN)
        resp = self._update_role(auth_client, target, Level.OWNER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_changes_admin_to_member(self, auth_client, organization, workspace):
        target = _make_user(organization, "admin2@futureagi.com", "Admin", Level.ADMIN)
        resp = self._update_role(auth_client, target, Level.MEMBER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_changes_admin_to_viewer(self, auth_client, organization, workspace):
        target = _make_user(organization, "admin3@futureagi.com", "Admin", Level.ADMIN)
        resp = self._update_role(auth_client, target, Level.VIEWER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    # -- Member target --

    def test_owner_changes_member_to_admin(self, auth_client, organization, workspace):
        target = _make_user(organization, "mem1@futureagi.com", "Member", Level.MEMBER)
        resp = self._update_role(auth_client, target, Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_changes_member_to_owner(self, auth_client, organization, workspace):
        target = _make_user(organization, "mem2@futureagi.com", "Member", Level.MEMBER)
        resp = self._update_role(auth_client, target, Level.OWNER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_changes_member_to_viewer(self, auth_client, organization, workspace):
        target = _make_user(organization, "mem3@futureagi.com", "Member", Level.MEMBER)
        resp = self._update_role(auth_client, target, Level.VIEWER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    # -- Viewer target --

    def test_owner_changes_viewer_to_member(self, auth_client, organization, workspace):
        target = _make_user(organization, "view1@futureagi.com", "Viewer", Level.VIEWER)
        resp = self._update_role(auth_client, target, Level.MEMBER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_changes_viewer_to_admin(self, auth_client, organization, workspace):
        target = _make_user(organization, "view2@futureagi.com", "Viewer", Level.VIEWER)
        resp = self._update_role(auth_client, target, Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_changes_viewer_to_owner(self, auth_client, organization, workspace):
        target = _make_user(organization, "view3@futureagi.com", "Viewer", Level.VIEWER)
        resp = self._update_role(auth_client, target, Level.OWNER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    # -- Owner target --

    def test_owner_changes_owner_to_admin(self, auth_client, organization, workspace):
        target = _make_user(organization, "own1@futureagi.com", "Owner", Level.OWNER)
        resp = self._update_role(auth_client, target, Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_changes_owner_to_member(self, auth_client, organization, workspace):
        target = _make_user(organization, "own2@futureagi.com", "Owner", Level.OWNER)
        resp = self._update_role(auth_client, target, Level.MEMBER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_changes_owner_to_viewer(self, auth_client, organization, workspace):
        target = _make_user(organization, "own3@futureagi.com", "Owner", Level.OWNER)
        resp = self._update_role(auth_client, target, Level.VIEWER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()


# ===================================================================
# TestAdminRoleUpdates
# ===================================================================


@pytest.mark.integration
@pytest.mark.api
class TestAdminRoleUpdates:
    """Admin changing org roles -- mix of ALLOW and DENY."""

    @pytest.fixture
    def admin_user(self, organization, workspace):
        u = _make_user(organization, "actor-admin@futureagi.com", "Admin", Level.ADMIN)
        return u

    @pytest.fixture
    def admin_client(self, admin_user, workspace):
        return _make_client(admin_user, workspace)

    def _update_role(self, client, target_user, new_level):
        return client.post(
            ORG_ROLE_URL,
            {"user_id": str(target_user.id), "org_level": new_level},
            format="json",
        )

    # ALLOW: Admin manages Member (8 > 3) and target stays below admin

    def test_admin_changes_member_to_viewer(
        self, admin_client, organization, workspace
    ):
        target = _make_user(organization, "m2v@futureagi.com", "Member", Level.MEMBER)
        resp = self._update_role(admin_client, target, Level.VIEWER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_admin_changes_viewer_to_member(
        self, admin_client, organization, workspace
    ):
        target = _make_user(organization, "v2m@futureagi.com", "Viewer", Level.VIEWER)
        resp = self._update_role(admin_client, target, Level.MEMBER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    # ALLOW: Admin may assign their own level to lower-level members.

    def test_admin_can_promote_member_to_admin(
        self, admin_client, organization, workspace
    ):
        target = _make_user(organization, "m2a@futureagi.com", "Member", Level.MEMBER)
        resp = self._update_role(admin_client, target, Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_admin_can_promote_viewer_to_admin(
        self, admin_client, organization, workspace
    ):
        target = _make_user(organization, "v2a@futureagi.com", "Viewer", Level.VIEWER)
        resp = self._update_role(admin_client, target, Level.ADMIN)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    # DENY: escalation -- can't promote above own level

    def test_admin_cannot_promote_member_to_owner(
        self, admin_client, organization, workspace
    ):
        target = _make_user(organization, "m2o@futureagi.com", "Member", Level.MEMBER)
        resp = self._update_role(admin_client, target, Level.OWNER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()

    def test_admin_cannot_promote_viewer_to_owner(
        self, admin_client, organization, workspace
    ):
        target = _make_user(organization, "v2o@futureagi.com", "Viewer", Level.VIEWER)
        resp = self._update_role(admin_client, target, Level.OWNER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()

    # DENY: can't manage higher level (Owner)

    def test_admin_cannot_change_owner_role(
        self, admin_client, organization, workspace
    ):
        target = _make_user(organization, "o2any@futureagi.com", "Owner", Level.OWNER)
        resp = self._update_role(admin_client, target, Level.MEMBER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()

    # DENY: can't manage same level (Admin)

    def test_admin_cannot_change_admin_role(
        self, admin_client, organization, workspace
    ):
        target = _make_user(organization, "a2any@futureagi.com", "Admin", Level.ADMIN)
        resp = self._update_role(admin_client, target, Level.MEMBER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()


# ===================================================================
# TestMemberViewerRoleUpdates
# ===================================================================


@pytest.mark.integration
@pytest.mark.api
class TestMemberViewerRoleUpdates:
    """Member and Viewer actors -- ALL DENY (403)."""

    def test_member_cannot_update_any_role(self, organization, workspace):
        actor = _make_user(
            organization, "actor-mem@futureagi.com", "Member", Level.MEMBER
        )
        target = _make_user(
            organization, "target-v@futureagi.com", "Viewer", Level.VIEWER
        )
        client = _make_client(actor, workspace)
        resp = client.post(
            ORG_ROLE_URL,
            {"user_id": str(target.id), "org_level": Level.MEMBER},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()

    def test_member_cannot_update_viewer_to_admin(self, organization, workspace):
        actor = _make_user(
            organization, "actor-mem2@futureagi.com", "Member", Level.MEMBER
        )
        target = _make_user(
            organization, "target-v2@futureagi.com", "Viewer", Level.VIEWER
        )
        client = _make_client(actor, workspace)
        resp = client.post(
            ORG_ROLE_URL,
            {"user_id": str(target.id), "org_level": Level.ADMIN},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()

    def test_viewer_cannot_update_any_role(self, organization, workspace):
        actor = _make_user(
            organization, "actor-view@futureagi.com", "Viewer", Level.VIEWER
        )
        target = _make_user(
            organization, "target-m@futureagi.com", "Member", Level.MEMBER
        )
        client = _make_client(actor, workspace)
        resp = client.post(
            ORG_ROLE_URL,
            {"user_id": str(target.id), "org_level": Level.VIEWER},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()

    def test_viewer_cannot_update_viewer_to_owner(self, organization, workspace):
        actor = _make_user(
            organization, "actor-view2@futureagi.com", "Viewer", Level.VIEWER
        )
        target = _make_user(
            organization, "target-v3@futureagi.com", "Viewer", Level.VIEWER
        )
        client = _make_client(actor, workspace)
        resp = client.post(
            ORG_ROLE_URL,
            {"user_id": str(target.id), "org_level": Level.OWNER},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()


# ===================================================================
# TestWorkspaceRoleUpdates
# ===================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceRoleUpdates:
    """Workspace-level role updates."""

    def _update_ws_role(self, client, workspace, target_user, new_level):
        return client.post(
            _ws_role_url(workspace.id),
            {"user_id": str(target_user.id), "ws_level": new_level},
            format="json",
        )

    # ALLOW: Org Owner changes WS roles

    def test_org_owner_changes_ws_member_to_ws_admin(
        self, auth_client, organization, workspace
    ):
        target = _make_user(organization, "wsm1@futureagi.com", "Member", Level.MEMBER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        resp = self._update_ws_role(
            auth_client, workspace, target, Level.WORKSPACE_ADMIN
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_org_owner_changes_ws_viewer_to_ws_member(
        self, auth_client, organization, workspace
    ):
        target = _make_user(organization, "wsv1@futureagi.com", "Viewer", Level.VIEWER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_VIEWER)
        resp = self._update_ws_role(
            auth_client, workspace, target, Level.WORKSPACE_MEMBER
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    # ALLOW: Org Admin changes WS roles

    def test_org_admin_changes_ws_viewer_to_ws_member(self, organization, workspace):
        admin = _make_user(organization, "wsadmin1@futureagi.com", "Admin", Level.ADMIN)
        target = _make_user(organization, "wsv2@futureagi.com", "Viewer", Level.VIEWER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_VIEWER)
        client = _make_client(admin, workspace)
        resp = self._update_ws_role(client, workspace, target, Level.WORKSPACE_MEMBER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        client.stop_workspace_injection()

    def test_org_admin_changes_ws_member_to_ws_viewer(self, organization, workspace):
        admin = _make_user(organization, "wsadmin2@futureagi.com", "Admin", Level.ADMIN)
        target = _make_user(organization, "wsm2@futureagi.com", "Member", Level.MEMBER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        client = _make_client(admin, workspace)
        resp = self._update_ws_role(client, workspace, target, Level.WORKSPACE_VIEWER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        client.stop_workspace_injection()

    # ALLOW: WS Admin (non-org-admin) changes WS roles

    def test_ws_admin_changes_ws_member_to_ws_viewer(self, organization, workspace):
        ws_admin = _make_user(
            organization, "wsonly-admin@futureagi.com", "Member", Level.MEMBER
        )
        _add_ws_membership(ws_admin, workspace, organization, Level.WORKSPACE_ADMIN)
        target = _make_user(organization, "wsm3@futureagi.com", "Member", Level.MEMBER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        client = _make_client(ws_admin, workspace)
        resp = self._update_ws_role(client, workspace, target, Level.WORKSPACE_VIEWER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        client.stop_workspace_injection()

    def test_ws_admin_changes_ws_viewer_to_ws_member(self, organization, workspace):
        ws_admin = _make_user(
            organization, "wsonly-admin2@futureagi.com", "Member", Level.MEMBER
        )
        _add_ws_membership(ws_admin, workspace, organization, Level.WORKSPACE_ADMIN)
        target = _make_user(organization, "wsv3@futureagi.com", "Viewer", Level.VIEWER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_VIEWER)
        client = _make_client(ws_admin, workspace)
        resp = self._update_ws_role(client, workspace, target, Level.WORKSPACE_MEMBER)
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        client.stop_workspace_injection()

    # DENY: WS Member cannot change roles

    def test_ws_member_cannot_change_roles(self, organization, workspace):
        ws_member = _make_user(
            organization, "wsmem-actor@futureagi.com", "Member", Level.MEMBER
        )
        _add_ws_membership(ws_member, workspace, organization, Level.WORKSPACE_MEMBER)
        target = _make_user(organization, "wsv4@futureagi.com", "Viewer", Level.VIEWER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_VIEWER)
        client = _make_client(ws_member, workspace)
        resp = self._update_ws_role(client, workspace, target, Level.WORKSPACE_MEMBER)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()

    # DENY: WS Viewer cannot change roles

    def test_ws_viewer_cannot_change_roles(self, organization, workspace):
        ws_viewer = _make_user(
            organization, "wsview-actor@futureagi.com", "Viewer", Level.VIEWER
        )
        _add_ws_membership(ws_viewer, workspace, organization, Level.WORKSPACE_VIEWER)
        target = _make_user(organization, "wsm4@futureagi.com", "Member", Level.MEMBER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        client = _make_client(ws_viewer, workspace)
        resp = self._update_ws_role(client, workspace, target, Level.WORKSPACE_ADMIN)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()

    # DENY: Cannot change Org Admin's WS role (auto-access)

    def test_cannot_change_org_admin_ws_role(
        self, auth_client, organization, workspace
    ):
        admin = _make_user(
            organization, "orgadmin-ws@futureagi.com", "Admin", Level.ADMIN
        )
        resp = self._update_ws_role(
            auth_client, workspace, admin, Level.WORKSPACE_VIEWER
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()


# ===================================================================
# TestOrgMemberRemoval
# ===================================================================


@pytest.mark.integration
@pytest.mark.api
class TestOrgMemberRemoval:
    """Org member removal permission matrix."""

    def _remove(self, client, target_user):
        return client.delete(
            ORG_REMOVE_URL,
            {"user_id": str(target_user.id)},
            format="json",
        )

    # ALLOW: Owner removes any role

    def test_owner_removes_owner(self, auth_client, organization, workspace):
        """Owner removes another Owner (second owner exists)."""
        target = _make_user(organization, "own-rm@futureagi.com", "Owner", Level.OWNER)
        resp = self._remove(auth_client, target)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_removes_admin(self, auth_client, organization, workspace):
        target = _make_user(organization, "adm-rm@futureagi.com", "Admin", Level.ADMIN)
        resp = self._remove(auth_client, target)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_removes_member(self, auth_client, organization, workspace):
        target = _make_user(
            organization, "mem-rm@futureagi.com", "Member", Level.MEMBER
        )
        resp = self._remove(auth_client, target)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    def test_owner_removes_viewer(self, auth_client, organization, workspace):
        target = _make_user(
            organization, "view-rm@futureagi.com", "Viewer", Level.VIEWER
        )
        resp = self._remove(auth_client, target)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    # ALLOW: Admin removes lower roles

    def test_admin_removes_member(self, organization, workspace):
        admin = _make_user(
            organization, "adm-actor-rm@futureagi.com", "Admin", Level.ADMIN
        )
        target = _make_user(
            organization, "mem-rm2@futureagi.com", "Member", Level.MEMBER
        )
        client = _make_client(admin, workspace)
        resp = self._remove(client, target)
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        client.stop_workspace_injection()

    def test_admin_removes_viewer(self, organization, workspace):
        admin = _make_user(
            organization, "adm-actor-rm2@futureagi.com", "Admin", Level.ADMIN
        )
        target = _make_user(
            organization, "view-rm2@futureagi.com", "Viewer", Level.VIEWER
        )
        client = _make_client(admin, workspace)
        resp = self._remove(client, target)
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        client.stop_workspace_injection()

    # DENY: Admin cannot remove same or higher level

    def test_admin_cannot_remove_admin(self, organization, workspace):
        admin_actor = _make_user(
            organization, "adm-a-rm@futureagi.com", "Admin", Level.ADMIN
        )
        admin_target = _make_user(
            organization, "adm-t-rm@futureagi.com", "Admin", Level.ADMIN
        )
        client = _make_client(admin_actor, workspace)
        resp = self._remove(client, admin_target)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()

    def test_admin_cannot_remove_owner(self, organization, workspace):
        admin = _make_user(
            organization, "adm-rm-own@futureagi.com", "Admin", Level.ADMIN
        )
        owner_target = _make_user(
            organization, "own-rm-tgt@futureagi.com", "Owner", Level.OWNER
        )
        client = _make_client(admin, workspace)
        resp = self._remove(client, owner_target)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()

    # DENY: Member / Viewer cannot remove anyone

    def test_member_cannot_remove_anyone(self, organization, workspace):
        member = _make_user(
            organization, "mem-actor-rm@futureagi.com", "Member", Level.MEMBER
        )
        target = _make_user(
            organization, "view-rm-tgt@futureagi.com", "Viewer", Level.VIEWER
        )
        client = _make_client(member, workspace)
        resp = self._remove(client, target)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()

    def test_viewer_cannot_remove_anyone(self, organization, workspace):
        viewer = _make_user(
            organization, "view-actor-rm@futureagi.com", "Viewer", Level.VIEWER
        )
        target = _make_user(
            organization, "mem-rm-tgt@futureagi.com", "Member", Level.MEMBER
        )
        client = _make_client(viewer, workspace)
        resp = self._remove(client, target)
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.json()
        client.stop_workspace_injection()

    # Edge: last owner guard

    def test_cannot_remove_last_owner(self, auth_client, user):
        """Cannot remove the sole owner."""
        resp = auth_client.delete(
            ORG_REMOVE_URL,
            {"user_id": str(user.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()

    # Edge: non-existent user

    def test_remove_nonexistent_user(self, auth_client):
        resp = auth_client.delete(
            ORG_REMOVE_URL,
            {"user_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()


# ===================================================================
# TestPostRemovalState
# ===================================================================


@pytest.mark.integration
@pytest.mark.api
class TestPostRemovalState:
    """Verify state after org member removal."""

    @pytest.fixture
    def member_user(self, organization, workspace, user):
        target = _make_user(
            organization, "post-rm@futureagi.com", "Member", Level.MEMBER
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        return target

    def _remove(self, auth_client, target):
        resp = auth_client.delete(
            ORG_REMOVE_URL,
            {"user_id": str(target.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        return resp

    def test_org_membership_deactivated(self, auth_client, member_user, organization):
        self._remove(auth_client, member_user)
        org_mem = OrganizationMembership.objects.get(
            user=member_user,
            organization=organization,
        )
        assert org_mem.is_active is False

    def test_ws_memberships_cascade_deactivated(
        self, auth_client, member_user, organization, workspace
    ):
        self._remove(auth_client, member_user)
        ws_mems = WorkspaceMembership.objects.filter(
            user=member_user,
            workspace__organization=organization,
        )
        for ws_mem in ws_mems:
            assert ws_mem.is_active is False

    def test_removed_user_can_login_requires_org_setup(
        self, auth_client, member_user, api_client
    ):
        self._remove(auth_client, member_user)
        resp = api_client.post(
            "/accounts/token/",
            {"email": member_user.email, "password": "pass123"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json().get("requires_org_setup") is True

    def test_removed_user_can_create_new_org(
        self, auth_client, member_user, api_client
    ):
        self._remove(auth_client, member_user)
        login = api_client.post(
            "/accounts/token/",
            {"email": member_user.email, "password": "pass123"},
            format="json",
        )
        token = login.json()["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = api_client.post(
            "/accounts/organizations/create/",
            {"organization_name": "Post Removal Org"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.json()

    def test_member_list_shows_deactivated_status(
        self, auth_client, member_user, organization
    ):
        self._remove(auth_client, member_user)
        resp = auth_client.get("/accounts/organization/members/")
        assert resp.status_code == status.HTTP_200_OK
        results = resp.json().get("result", {}).get("results", [])
        entry = next(
            (r for r in results if r.get("email") == member_user.email),
            None,
        )
        assert entry is not None
        assert entry["status"] == "Deactivated"

    def test_reinvite_restores_membership(
        self, auth_client, member_user, organization, workspace
    ):
        self._remove(auth_client, member_user)
        resp = auth_client.post(
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
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        org_mem = OrganizationMembership.objects.get(
            user=member_user,
            organization=organization,
        )
        assert org_mem.is_active is True

    def test_user_organization_membership_deactivated_after_removal(
        self, auth_client, member_user, organization
    ):
        self._remove(auth_client, member_user)
        assert not OrganizationMembership.objects.filter(
            user=member_user, organization=organization, is_active=True
        ).exists()


# ===================================================================
# TestWorkspaceMemberRemoval
# ===================================================================


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceMemberRemoval:
    """Workspace member removal tests."""

    def _ws_remove(self, client, workspace, target_user):
        return client.delete(
            _ws_remove_url(workspace.id),
            {"user_id": str(target_user.id)},
            format="json",
        )

    # ALLOW: Org Owner removes WS member (target has >1 workspace)

    def test_org_owner_removes_ws_member(
        self, auth_client, organization, workspace, user
    ):
        # Target must have a second workspace so removal from the first is allowed
        ws2 = Workspace.objects.create(
            name="WS RM1 Second",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        target = _make_user(organization, "wsrm1@futureagi.com", "Member", Level.MEMBER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        _add_ws_membership(target, ws2, organization, Level.WORKSPACE_MEMBER)
        resp = self._ws_remove(auth_client, workspace, target)
        assert resp.status_code == status.HTTP_200_OK, resp.json()

    # ALLOW: WS Admin removes WS Member (target has >1 workspace)

    def test_ws_admin_removes_ws_member(self, organization, workspace, user):
        ws2 = Workspace.objects.create(
            name="WS RM2 Second",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        ws_admin = _make_user(
            organization, "ws-adm-rm@futureagi.com", "Member", Level.MEMBER
        )
        _add_ws_membership(ws_admin, workspace, organization, Level.WORKSPACE_ADMIN)
        target = _make_user(organization, "wsrm2@futureagi.com", "Member", Level.MEMBER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        _add_ws_membership(target, ws2, organization, Level.WORKSPACE_MEMBER)
        client = _make_client(ws_admin, workspace)
        resp = self._ws_remove(client, workspace, target)
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        client.stop_workspace_injection()

    # ALLOW: WS Admin removes WS Viewer (target has >1 workspace)

    def test_ws_admin_removes_ws_viewer(self, organization, workspace, user):
        ws2 = Workspace.objects.create(
            name="WS RM3 Second",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        ws_admin = _make_user(
            organization, "ws-adm-rm2@futureagi.com", "Member", Level.MEMBER
        )
        _add_ws_membership(ws_admin, workspace, organization, Level.WORKSPACE_ADMIN)
        target = _make_user(organization, "wsrm3@futureagi.com", "Viewer", Level.VIEWER)
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_VIEWER)
        _add_ws_membership(target, ws2, organization, Level.WORKSPACE_VIEWER)
        client = _make_client(ws_admin, workspace)
        resp = self._ws_remove(client, workspace, target)
        assert resp.status_code == status.HTTP_200_OK, resp.json()
        client.stop_workspace_injection()

    # DENY: Cannot remove member from their ONLY workspace

    def test_cannot_remove_from_last_workspace(
        self, auth_client, organization, workspace
    ):
        target = _make_user(
            organization, "wsrm-last@futureagi.com", "Member", Level.MEMBER
        )
        _add_ws_membership(target, workspace, organization, Level.WORKSPACE_MEMBER)
        resp = self._ws_remove(auth_client, workspace, target)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()
        assert "only workspace" in resp.json()["result"].lower()

    # DENY: Cannot remove Org Admin from WS (auto-access)

    def test_cannot_remove_org_admin_from_ws(
        self, auth_client, organization, workspace
    ):
        admin = _make_user(
            organization, "orgadm-wsrm@futureagi.com", "Admin", Level.ADMIN
        )
        resp = self._ws_remove(auth_client, workspace, admin)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()

    # DENY: Cannot remove self from WS

    def test_cannot_remove_self_from_ws(self, auth_client, user, workspace):
        resp = self._ws_remove(auth_client, workspace, user)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.json()
