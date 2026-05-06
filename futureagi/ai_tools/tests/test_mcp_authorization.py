"""
Regression tests for MCP tool authorization.

Validates that the MCP tool layer enforces proper RBAC:
- Org Members cannot escalate to workspace admin
- Org Members cannot see private workspaces they have no access to
- Org Members cannot invite users to workspaces they don't admin
- Role hierarchy is enforced (cannot grant >= own level)

Ref: Security report "MCP Tool Authorization: Org Members Can Escalate to Workspace Admin"
"""

import uuid

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import OrganizationRoles, Workspace, WorkspaceMembership
from ai_tools.base import ToolContext
from ai_tools.tests.conftest import run_tool
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


@pytest.fixture
def private_workspace(db, user):
    """A private workspace in the same org that the member user has NO access to."""
    org = user.organization
    return Workspace.objects.create(
        name="Private Workspace",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=user,
    )


@pytest.fixture
def member_user(db, user, workspace):
    """An org-level Member with access only to the default workspace."""
    from accounts.models.user import User

    org = user.organization
    member = User.objects.create_user(
        email=f"member-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Member User",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )

    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )

    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=org_membership,
    )

    return member


@pytest.fixture
def member_context(member_user, workspace):
    """ToolContext for the member user."""
    org = member_user.organization
    set_workspace_context(workspace=workspace, organization=org, user=member_user)
    yield ToolContext(user=member_user, organization=org, workspace=workspace)
    clear_workspace_context()


@pytest.fixture
def ws_admin_user(db, user, workspace):
    """A user who is Workspace Admin in the default workspace but Org Member."""
    from accounts.models.user import User

    org = user.organization
    ws_admin = User.objects.create_user(
        email=f"wsadmin-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="WS Admin User",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )

    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=ws_admin,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )

    WorkspaceMembership.no_workspace_objects.create(
        user=ws_admin,
        workspace=workspace,
        role="workspace_admin",
        level=Level.WORKSPACE_ADMIN,
        is_active=True,
        organization_membership=org_membership,
    )

    return ws_admin


@pytest.fixture
def ws_admin_context(ws_admin_user, workspace):
    """ToolContext for the workspace admin user."""
    org = ws_admin_user.organization
    set_workspace_context(workspace=workspace, organization=org, user=ws_admin_user)
    yield ToolContext(user=ws_admin_user, organization=org, workspace=workspace)
    clear_workspace_context()


# ===================================================================
# add_workspace_member authorization
# ===================================================================


class TestAddWorkspaceMemberPermissions:
    """Regression: Org Member must NOT be able to add themselves to any workspace."""

    def test_org_member_cannot_add_self_to_other_workspace(
        self, member_context, private_workspace
    ):
        """P0 regression: Member denied adding to workspace they have no admin access to."""
        result = run_tool(
            "add_workspace_member",
            {
                "workspace_id": str(private_workspace.id),
                "user_id": str(member_context.user.id),
                "role": "workspace_admin",
            },
            member_context,
        )
        assert result.is_error
        assert result.error_code == "PERMISSION_DENIED"

    def test_org_member_cannot_add_self_as_admin_in_own_workspace(
        self, member_context, workspace
    ):
        """P0 regression: Member cannot grant workspace_admin even in own workspace."""
        result = run_tool(
            "add_workspace_member",
            {
                "workspace_id": str(workspace.id),
                "user_id": str(member_context.user.id),
                "role": "workspace_admin",
            },
            member_context,
        )
        assert result.is_error
        assert result.error_code == "PERMISSION_DENIED"

    def test_ws_admin_can_add_member_to_own_workspace(
        self, ws_admin_context, workspace, member_user
    ):
        """Workspace Admin CAN add workspace_member to their workspace."""
        result = run_tool(
            "add_workspace_member",
            {
                "workspace_id": str(workspace.id),
                "user_id": str(member_user.id),
                "role": "workspace_member",
            },
            ws_admin_context,
        )
        assert not result.is_error

    def test_ws_admin_cannot_add_to_other_workspace(
        self, ws_admin_context, private_workspace, member_user
    ):
        """Workspace Admin CANNOT add members to a workspace they don't admin."""
        result = run_tool(
            "add_workspace_member",
            {
                "workspace_id": str(private_workspace.id),
                "user_id": str(member_user.id),
                "role": "workspace_member",
            },
            ws_admin_context,
        )
        assert result.is_error
        assert result.error_code == "PERMISSION_DENIED"

    def test_org_admin_can_add_workspace_admin(
        self, tool_context, workspace, member_user
    ):
        """Org Owner/Admin CAN grant workspace_admin."""
        result = run_tool(
            "add_workspace_member",
            {
                "workspace_id": str(workspace.id),
                "user_id": str(member_user.id),
                "role": "workspace_admin",
            },
            tool_context,
        )
        assert not result.is_error
        assert result.data["role"] == "workspace_admin"

    def test_invalid_role_rejected(self, tool_context, workspace, member_user):
        """Invalid role strings are rejected with VALIDATION_ERROR."""
        result = run_tool(
            "add_workspace_member",
            {
                "workspace_id": str(workspace.id),
                "user_id": str(member_user.id),
                "role": "superadmin",
            },
            tool_context,
        )
        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"


# ===================================================================
# list_workspaces authorization
# ===================================================================


class TestListWorkspacesPermissions:
    """Regression: Org Member must NOT see private workspace IDs."""

    def test_org_member_only_sees_accessible_workspaces(
        self, member_context, private_workspace
    ):
        """P0 regression: Member does NOT see workspaces they have no membership in."""
        result = run_tool("list_workspaces", {}, member_context)
        assert not result.is_error
        workspace_ids = [ws["id"] for ws in result.data["workspaces"]]
        assert str(private_workspace.id) not in workspace_ids

    def test_org_admin_sees_all_workspaces(self, tool_context, private_workspace):
        """Org Owner/Admin sees all workspaces including private ones."""
        result = run_tool("list_workspaces", {}, tool_context)
        assert not result.is_error
        workspace_ids = [ws["id"] for ws in result.data["workspaces"]]
        assert str(private_workspace.id) in workspace_ids


# ===================================================================
# invite_users authorization
# ===================================================================


class TestInviteUsersPermissions:
    """Regression: Org Member must NOT invite users to workspaces they don't admin."""

    def test_org_member_cannot_invite_to_other_workspace(
        self, member_context, private_workspace
    ):
        """P0 regression: Member denied inviting to a workspace they don't admin."""
        result = run_tool(
            "invite_users",
            {
                "emails": ["victim@example.com"],
                "role": "workspace_member",
                "workspace_ids": [str(private_workspace.id)],
            },
            member_context,
        )
        assert result.is_error
        assert result.error_code == "PERMISSION_DENIED"

    def test_org_member_cannot_invite_with_admin_role(self, member_context, workspace):
        """Member cannot assign workspace_admin role even in own workspace."""
        result = run_tool(
            "invite_users",
            {
                "emails": ["newuser@example.com"],
                "role": "workspace_admin",
                "workspace_ids": [str(workspace.id)],
            },
            member_context,
        )
        assert result.is_error
        assert result.error_code == "PERMISSION_DENIED"
