"""
Direct unit / functional tests for reusable RBAC permission primitives.

Targets:
    - tfc.permissions.rbac:  IsOrganizationMember, IsOrganizationOwner,
                              _get_request_workspace_id, CanManageTargetUser
    - tfc.permissions.permissions: APIKeyPermission (no-EE-safe subset)
    - tfc.permissions.utils: get_org_membership, can_invite_at_level,
                              get_effective_workspace_level

Exercises allow/deny boundaries and fallback/context behavior.
Uses RequestFactory / APIRequestFactory; no view wiring, no broad validation.
"""

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request as DRFRequest
from rest_framework.test import APIRequestFactory

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.permissions.permissions import APIKeyPermission
from tfc.permissions.rbac import (
    CanManageTargetUser,
    IsOrganizationMember,
    IsOrganizationOwner,
    _get_request_workspace_id,
)
from tfc.permissions.utils import (
    can_invite_at_level,
    get_effective_workspace_level,
    get_org_membership,
)

User = get_user_model()

factory = APIRequestFactory()


# ── Shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Test Org", display_name="Test Org")


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="Other Org", display_name="Other Org")


@pytest.fixture
def owner_user(db, org):
    user = User.objects.create_user(
        email="owner@test.com",
        password="testpass123",
        name="Owner",
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
def admin_user(db, org):
    user = User.objects.create_user(
        email="admin@test.com",
        password="testpass123",
        name="Admin",
        organization=org,
        organization_role=OrganizationRoles.ADMIN,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.ADMIN,
        level=Level.ADMIN,
        is_active=True,
    )
    return user


@pytest.fixture
def member_user(db, org):
    user = User.objects.create_user(
        email="member@test.com",
        password="testpass123",
        name="Member",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    return user


@pytest.fixture
def viewer_user(db, org):
    user = User.objects.create_user(
        email="viewer@test.com",
        password="testpass123",
        name="Viewer",
        organization=org,
        organization_role=OrganizationRoles.MEMBER_VIEW_ONLY,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.MEMBER_VIEW_ONLY,
        level=Level.VIEWER,
        is_active=True,
    )
    return user


@pytest.fixture
def no_membership_user(db, org):
    """User in the org FK but with no OrganizationMembership row."""
    return User.objects.create_user(
        email="orphan@test.com",
        password="testpass123",
        name="Orphan",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )


@pytest.fixture
def workspace(db, org, owner_user):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner_user,
    )


@pytest.fixture(autouse=True)
def _clear_ctx():
    """Ensure thread-local context is clean between tests."""
    clear_workspace_context()
    yield
    clear_workspace_context()


def _make_request(user, *, organization=None):
    """Create a DRF Request with the given user authenticated.

    Wraps the raw WSGIRequest from APIRequestFactory in DRF's Request
    wrapper so that ``request.user`` is available to permission classes.
    """
    wsgi_req = factory.get("/test/")
    drf_req = DRFRequest(wsgi_req)
    drf_req.user = user
    if organization is not None:
        set_workspace_context(organization=organization, user=user)
    return drf_req


# ── IsOrganizationMember ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestIsOrganizationMember:
    def test_allows_active_member(self, owner_user, org):
        req = _make_request(owner_user, organization=org)
        assert IsOrganizationMember().has_permission(req, None) is True

    def test_allows_viewer(self, viewer_user, org):
        req = _make_request(viewer_user, organization=org)
        assert IsOrganizationMember().has_permission(req, None) is True

    def test_denies_user_without_membership(self, no_membership_user, org):
        req = _make_request(no_membership_user, organization=org)
        assert IsOrganizationMember().has_permission(req, None) is False

    def test_denies_inactive_membership(self, member_user, org):
        member_user.organization_memberships.filter(organization=org).update(
            is_active=False
        )
        req = _make_request(member_user, organization=org)
        assert IsOrganizationMember().has_permission(req, None) is False

    def test_denies_user_from_different_org(self, owner_user, other_org):
        req = _make_request(owner_user, organization=other_org)
        assert IsOrganizationMember().has_permission(req, None) is False


# ── IsOrganizationOwner ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestIsOrganizationOwner:
    def test_allows_owner(self, owner_user, org):
        req = _make_request(owner_user, organization=org)
        assert IsOrganizationOwner().has_permission(req, None) is True

    def test_denies_admin(self, admin_user, org):
        req = _make_request(admin_user, organization=org)
        assert IsOrganizationOwner().has_permission(req, None) is False

    def test_denies_member(self, member_user, org):
        req = _make_request(member_user, organization=org)
        assert IsOrganizationOwner().has_permission(req, None) is False

    def test_denies_viewer(self, viewer_user, org):
        req = _make_request(viewer_user, organization=org)
        assert IsOrganizationOwner().has_permission(req, None) is False

    def test_denies_no_membership(self, no_membership_user, org):
        req = _make_request(no_membership_user, organization=org)
        assert IsOrganizationOwner().has_permission(req, None) is False

    def test_owner_level_boundary_at_15(self, owner_user, org):
        """Owner (15) is the exact threshold — should pass."""
        membership = owner_user.organization_memberships.get(organization=org)
        assert membership.level_or_legacy == Level.OWNER
        req = _make_request(owner_user, organization=org)
        assert IsOrganizationOwner().has_permission(req, None) is True


# ── _get_request_workspace_id ───────────────────────────────────────────────


class TestGetRequestWorkspaceId:
    def test_returns_id_when_workspace_set(self):
        ws_id = "12345678-1234-1234-1234-123456789abc"
        ws = SimpleNamespace(id=ws_id)
        req = SimpleNamespace(workspace=ws)
        assert _get_request_workspace_id(req) == ws_id

    def test_returns_none_when_no_workspace(self):
        req = SimpleNamespace(workspace=None)
        assert _get_request_workspace_id(req) is None

    def test_returns_none_when_attribute_missing(self):
        req = SimpleNamespace()
        assert _get_request_workspace_id(req) is None

    def test_converts_non_string_id_to_str(self):
        ws = SimpleNamespace(id=42)
        req = SimpleNamespace(workspace=ws)
        assert _get_request_workspace_id(req) == "42"
        assert isinstance(_get_request_workspace_id(req), str)


# ── APIKeyPermission ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestAPIKeyPermission:
    """Test the no-EE-safe behavior of APIKeyPermission.

    The permission reads os.getenv("API_KEY") and checks against the
    X-API-KEY request header.  It is used only by Appsmith internal
    tooling views (UserApiView, SOSLoginView) which are excluded from
    broader accounts test coverage by convention.
    """

    def test_raises_when_no_header(self):
        req = factory.get("/test/")
        with pytest.raises(AuthenticationFailed, match="No API key provided"):
            APIKeyPermission().has_permission(req, None)

    def test_raises_when_env_not_configured(self):
        req = factory.get("/test/", HTTP_X_API_KEY="some-key")
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("API_KEY", None)
            with pytest.raises(AuthenticationFailed, match="API key not configured"):
                APIKeyPermission().has_permission(req, None)

    def test_raises_when_wrong_key(self):
        req = factory.get("/test/", HTTP_X_API_KEY="wrong-key")
        with patch.dict(os.environ, {"API_KEY": "correct-key"}):
            with pytest.raises(AuthenticationFailed, match="Invalid API key"):
                APIKeyPermission().has_permission(req, None)

    def test_allows_correct_key(self):
        req = factory.get("/test/", HTTP_X_API_KEY="correct-key")
        with patch.dict(os.environ, {"API_KEY": "correct-key"}):
            assert APIKeyPermission().has_permission(req, None) is True


# ── get_org_membership ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestGetOrgMembership:
    """Test get_org_membership fallback and context behavior.

    Resolution order:
        1. Thread-local context var set via set_workspace_context(organization=…)
        2. Fallback to user.organization FK
        3. None when no org found
    """

    def test_returns_membership_when_context_set(self, member_user, org):
        set_workspace_context(organization=org, user=member_user)
        membership = get_org_membership(member_user)
        assert membership is not None
        assert membership.organization_id == org.id
        assert membership.level_or_legacy == Level.MEMBER

    def test_falls_back_to_user_org_when_context_unset(self, member_user, org):
        """No context var set → uses user.organization FK."""
        membership = get_org_membership(member_user)
        assert membership is not None
        assert membership.organization_id == org.id

    def test_returns_none_for_unauthenticated_user(self, db):
        anon = SimpleNamespace(is_authenticated=False)
        assert get_org_membership(anon) is None

    def test_returns_none_when_user_is_none(self):
        assert get_org_membership(None) is None

    def test_returns_none_when_no_org_found(self, db):
        user = User.objects.create_user(
            email="norg@test.com",
            password="testpass123",
            name="No Org",
            organization=None,
        )
        assert get_org_membership(user) is None

    def test_returns_none_when_membership_inactive(self, member_user, org):
        member_user.organization_memberships.filter(organization=org).update(
            is_active=False
        )
        set_workspace_context(organization=org, user=member_user)
        assert get_org_membership(member_user) is None

    def test_context_takes_precedence_over_user_fk(self, member_user, org, other_org):
        """When context var points to other_org but user belongs to org,
        the context var wins — and membership lookup uses other_org."""
        # Member only has membership in org, not other_org
        set_workspace_context(organization=other_org, user=member_user)
        # Should find nothing — user has no membership in other_org
        result = get_org_membership(member_user)
        assert result is None

    def test_returns_correct_membership_for_context_org(
        self, owner_user, org, other_org
    ):
        """Owner also gets membership in other_org → context picks the right one."""
        OrganizationMembership.no_workspace_objects.create(
            user=owner_user,
            organization=other_org,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )
        set_workspace_context(organization=other_org, user=owner_user)
        membership = get_org_membership(owner_user)
        assert membership is not None
        assert membership.organization_id == other_org.id
        assert membership.level_or_legacy == Level.MEMBER


# ── can_invite_at_level ─────────────────────────────────────────────────────


class TestCanInviteAtLevel:
    """Pure-function tests — no database needed.

    Rule: target level must be <= actor level.
    Exception: Owner (15) can invite another Owner (15).
    """

    # --- Owner actor ---
    def test_owner_can_invite_owner(self):
        assert can_invite_at_level(Level.OWNER, Level.OWNER) is True

    def test_owner_can_invite_admin(self):
        assert can_invite_at_level(Level.OWNER, Level.ADMIN) is True

    def test_owner_can_invite_member(self):
        assert can_invite_at_level(Level.OWNER, Level.MEMBER) is True

    def test_owner_can_invite_viewer(self):
        assert can_invite_at_level(Level.OWNER, Level.VIEWER) is True

    # --- Admin actor ---
    def test_admin_can_invite_admin(self):
        assert can_invite_at_level(Level.ADMIN, Level.ADMIN) is True

    def test_admin_can_invite_member(self):
        assert can_invite_at_level(Level.ADMIN, Level.MEMBER) is True

    def test_admin_can_invite_viewer(self):
        assert can_invite_at_level(Level.ADMIN, Level.VIEWER) is True

    def test_admin_cannot_invite_owner(self):
        assert can_invite_at_level(Level.ADMIN, Level.OWNER) is False

    # --- Member actor ---
    def test_member_can_invite_member(self):
        assert can_invite_at_level(Level.MEMBER, Level.MEMBER) is True

    def test_member_can_invite_viewer(self):
        assert can_invite_at_level(Level.MEMBER, Level.VIEWER) is True

    def test_member_cannot_invite_admin(self):
        assert can_invite_at_level(Level.MEMBER, Level.ADMIN) is False

    def test_member_cannot_invite_owner(self):
        assert can_invite_at_level(Level.MEMBER, Level.OWNER) is False

    # --- Viewer actor ---
    def test_viewer_can_invite_viewer(self):
        assert can_invite_at_level(Level.VIEWER, Level.VIEWER) is True

    def test_viewer_cannot_invite_member(self):
        assert can_invite_at_level(Level.VIEWER, Level.MEMBER) is False

    def test_viewer_cannot_invite_admin(self):
        assert can_invite_at_level(Level.VIEWER, Level.ADMIN) is False

    def test_viewer_cannot_invite_owner(self):
        assert can_invite_at_level(Level.VIEWER, Level.OWNER) is False

    # --- Boundary: level 0 (no membership) ---
    def test_level_zero_cannot_invite_anyone(self):
        assert can_invite_at_level(0, Level.VIEWER) is False

    # --- Self-assignment at every level ---
    def test_self_invite_allowed_at_all_levels(self):
        for level in (Level.VIEWER, Level.MEMBER, Level.ADMIN, Level.OWNER):
            assert can_invite_at_level(level, level) is True


# ── get_effective_workspace_level ───────────────────────────────────────────


@pytest.mark.django_db
class TestGetEffectiveWorkspaceLevel:
    """Verify effective level = max(org_level, ws_level) and auto-admin logic."""

    def test_org_admin_auto_gets_ws_admin(self, admin_user, workspace):
        """Org Admin (8) auto-gets WORKSPACE_ADMIN (8) even with no WS membership."""
        effective = get_effective_workspace_level(admin_user, workspace.id)
        assert effective == Level.WORKSPACE_ADMIN

    def test_org_owner_auto_gets_ws_admin(self, owner_user, workspace):
        effective = get_effective_workspace_level(owner_user, workspace.id)
        assert effective >= Level.WORKSPACE_ADMIN

    def test_member_with_no_ws_membership_returns_none(self, member_user, workspace):
        effective = get_effective_workspace_level(member_user, workspace.id)
        assert effective is None

    def test_member_with_ws_admin_membership(self, member_user, workspace):
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=member_user,
            role=OrganizationRoles.WORKSPACE_ADMIN,
            level=Level.WORKSPACE_ADMIN,
            is_active=True,
        )
        effective = get_effective_workspace_level(member_user, workspace.id)
        assert effective == Level.WORKSPACE_ADMIN

    def test_member_with_ws_member_membership(self, member_user, workspace):
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=member_user,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )
        effective = get_effective_workspace_level(member_user, workspace.id)
        # max(org_member=3, ws_member=3) = 3
        assert effective == Level.WORKSPACE_MEMBER

    def test_org_member_with_ws_viewer_gets_max(self, member_user, workspace):
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=member_user,
            role=OrganizationRoles.WORKSPACE_VIEWER,
            level=Level.WORKSPACE_VIEWER,
            is_active=True,
        )
        effective = get_effective_workspace_level(member_user, workspace.id)
        # max(org_member=3, ws_viewer=1) = 3 (org level wins)
        assert effective == Level.MEMBER

    def test_returns_none_for_inactive_ws_membership(self, member_user, workspace):
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=member_user,
            role=OrganizationRoles.WORKSPACE_ADMIN,
            level=Level.WORKSPACE_ADMIN,
            is_active=False,
        )
        effective = get_effective_workspace_level(member_user, workspace.id)
        assert effective is None


# ── CanManageTargetUser ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCanManageTargetUser:
    def test_owner_can_manage_admin(self, owner_user, admin_user, org):
        req = _make_request(owner_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(admin_user.id)})
        assert CanManageTargetUser().has_permission(req, view) is True

    def test_owner_can_manage_another_owner(self, owner_user, owner2, org):
        req = _make_request(owner_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(owner2.id)})
        assert CanManageTargetUser().has_permission(req, view) is True

    def test_admin_can_manage_member(self, admin_user, member_user, org):
        req = _make_request(admin_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(member_user.id)})
        assert CanManageTargetUser().has_permission(req, view) is True

    def test_admin_can_manage_viewer(self, admin_user, viewer_user, org):
        req = _make_request(admin_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(viewer_user.id)})
        assert CanManageTargetUser().has_permission(req, view) is True

    def test_admin_cannot_manage_owner(self, admin_user, owner_user, org):
        req = _make_request(admin_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(owner_user.id)})
        assert CanManageTargetUser().has_permission(req, view) is False

    def test_admin_cannot_manage_peer_admin(self, admin_user, admin_user2, org):
        req = _make_request(admin_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(admin_user2.id)})
        assert CanManageTargetUser().has_permission(req, view) is False

    def test_member_cannot_manage_admin(self, member_user, admin_user, org):
        req = _make_request(member_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(admin_user.id)})
        assert CanManageTargetUser().has_permission(req, view) is False

    def test_member_cannot_manage_peer_member(self, member_user, other_member, org):
        req = _make_request(member_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(other_member.id)})
        assert CanManageTargetUser().has_permission(req, view) is False

    def test_viewer_cannot_manage_member(self, viewer_user, member_user, org):
        req = _make_request(viewer_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(member_user.id)})
        assert CanManageTargetUser().has_permission(req, view) is False

    def test_no_membership_user_cannot_manage(
        self, no_membership_user, member_user, org
    ):
        req = _make_request(no_membership_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(member_user.id)})
        assert CanManageTargetUser().has_permission(req, view) is False

    def test_returns_false_when_no_target_user_id(self, admin_user, org):
        req = _make_request(admin_user, organization=org)
        view = SimpleNamespace(kwargs={})
        assert CanManageTargetUser().has_permission(req, view) is False

    def test_nonexistent_target_user_allowed(self, admin_user, org):
        """When target user has no membership, the permission returns True
        (defers to the view to return a 400)."""
        from uuid import uuid4

        req = _make_request(admin_user, organization=org)
        view = SimpleNamespace(kwargs={"user_id": str(uuid4())})
        assert CanManageTargetUser().has_permission(req, view) is True


# ── Additional fixtures for CanManageTargetUser ─────────────────────────────


@pytest.fixture
def owner2(db, org, owner_user):
    user = User.objects.create_user(
        email="owner2@test.com",
        password="testpass123",
        name="Owner Two",
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
def admin_user2(db, org):
    user = User.objects.create_user(
        email="admin2@test.com",
        password="testpass123",
        name="Admin Two",
        organization=org,
        organization_role=OrganizationRoles.ADMIN,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.ADMIN,
        level=Level.ADMIN,
        is_active=True,
    )
    return user


@pytest.fixture
def other_member(db, org):
    user = User.objects.create_user(
        email="othermember@test.com",
        password="testpass123",
        name="Other Member",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    return user
