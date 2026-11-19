import uuid

import pytest

from ai_tools.tests.conftest import run_tool

# ===================================================================
# READ TOOLS
# ===================================================================


class TestGetUserTool:
    def test_get_current_user(self, tool_context):
        result = run_tool(
            "get_user", {"user_id": str(tool_context.user.id)}, tool_context
        )

        assert not result.is_error
        assert "User Profile" in result.content
        assert tool_context.user.email in result.content
        assert result.data["user"]["id"] == str(tool_context.user.id)
        assert result.data["user"]["is_active"] is True

    def test_get_nonexistent_user(self, tool_context):
        result = run_tool("get_user", {"user_id": str(uuid.uuid4())}, tool_context)

        assert result.is_error
        assert "Not Found" in result.content

    def test_get_invalid_uuid(self, tool_context):
        result = run_tool("get_user", {"user_id": "not-a-uuid"}, tool_context)

        assert result.is_error


class TestGetOrganizationTool:
    def test_get_organization(self, tool_context):
        result = run_tool("get_organization", {}, tool_context)

        assert not result.is_error
        assert "Organization Details" in result.content
        assert result.data["organization_id"] == str(tool_context.organization.id)
        assert result.data["active_members"] >= 1  # At least the test user
        assert result.data["workspace_count"] >= 1  # At least the test workspace


class TestGetUserPermissionsTool:
    def test_get_permissions(self, tool_context):
        result = run_tool("get_user_permissions", {}, tool_context)

        assert not result.is_error
        assert result.data is not None


class TestListUsersTool:
    def test_list_users(self, tool_context):
        result = run_tool("list_users", {}, tool_context)

        assert not result.is_error
        assert result.data["total"] >= 1
        # The test user should be in the list
        user_ids = [u["id"] for u in result.data["users"]]
        assert str(tool_context.user.id) in user_ids

    def test_list_filter_by_status(self, tool_context):
        result = run_tool("list_users", {"status": "active"}, tool_context)
        assert not result.is_error
        assert result.data["total"] >= 1

        result = run_tool("list_users", {"status": "inactive"}, tool_context)
        assert not result.is_error

    def test_list_search(self, tool_context):
        result = run_tool(
            "list_users",
            {"search": tool_context.user.email[:5]},
            tool_context,
        )
        assert not result.is_error
        assert result.data["total"] >= 1

    def test_list_pagination(self, tool_context):
        result = run_tool("list_users", {"limit": 1, "offset": 0}, tool_context)
        assert not result.is_error
        assert len(result.data["users"]) <= 1


class TestListWorkspaceMembersTool:
    def test_list_current_workspace(self, tool_context):
        result = run_tool("list_workspace_members", {}, tool_context)

        assert not result.is_error
        assert result.data["workspace_id"] == str(tool_context.workspace.id)
        assert result.data["total"] >= 0

    def test_list_specific_workspace(self, tool_context):
        result = run_tool(
            "list_workspace_members",
            {"workspace_id": str(tool_context.workspace.id)},
            tool_context,
        )
        assert not result.is_error

    def test_list_nonexistent_workspace(self, tool_context):
        result = run_tool(
            "list_workspace_members",
            {"workspace_id": str(uuid.uuid4())},
            tool_context,
        )
        assert result.is_error
        assert "Not Found" in result.content


class TestListOrgMembersTool:
    def test_list_org_members(self, tool_context):
        result = run_tool("list_org_members", {}, tool_context)

        assert not result.is_error


class TestListOrganizationsTool:
    def test_list_organizations(self, tool_context):
        result = run_tool("list_organizations", {}, tool_context)

        assert not result.is_error


class TestListApiKeysTool:
    def test_list_api_keys(self, tool_context):
        result = run_tool("list_api_keys", {}, tool_context)

        assert not result.is_error

    def test_list_api_keys_leaves_out_sandbox_ingest_keys(self, tool_context):
        from accounts.models.user import OrgApiKey

        user_key = OrgApiKey.no_workspace_objects.create(
            name="listed-user-key",
            organization=tool_context.organization,
            workspace=tool_context.workspace,
            type="user",
            user=tool_context.user,
        )
        # Created last, so it would head the newest-first list.
        ingest_key = OrgApiKey.no_workspace_objects.create(
            name=f"simulation_export_key {uuid.uuid4()}",
            organization=tool_context.organization,
            workspace=tool_context.workspace,
            type="ingest",
            user=None,
            simulation_agent_id=uuid.uuid4(),
        )

        result = run_tool("list_api_keys", {}, tool_context)

        assert not result.is_error
        assert [key["id"] for key in result.data["api_keys"]] == [str(user_key.id)]
        assert result.data["total"] == 1
        assert str(ingest_key.id) not in result.content
        assert ingest_key.name not in result.content

        # A limit of one still returns the user key, not the newer ingest key.
        limited = run_tool("list_api_keys", {"limit": 1}, tool_context)
        assert [key["id"] for key in limited.data["api_keys"]] == [str(user_key.id)]

        # Asking for the type by name finds nothing either.
        by_type = run_tool("list_api_keys", {"key_type": "ingest"}, tool_context)
        assert not by_type.is_error
        assert by_type.data["api_keys"] == []
        assert by_type.data["total"] == 0


# ===================================================================
# INVITE: the inviter's level bounds the grant
# ===================================================================


def _inviter(tool_context, level, organization_role):
    """A second org user who invites, at ``level`` in the organization."""
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership
    from ai_tools.base import ToolContext

    organization = tool_context.organization
    inviter = User.objects.create_user(
        email=f"inviter-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Inviter",
        organization=organization,
        organization_role=organization_role,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=inviter,
        organization=organization,
        role={15: "Owner", 8: "Admin", 3: "Member", 1: "Viewer"}[level],
        level=level,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=inviter,
        workspace=tool_context.workspace,
        role="workspace_member",
        level=3,
        is_active=True,
        organization_membership=membership,
    )
    return ToolContext(
        user=inviter, organization=organization, workspace=tool_context.workspace
    )


class TestInviteUsersGrantsAtMostTheInvitersLevel:
    """The invite reads the inviter's live membership level, never grants a
    role above it, and writes the invitee's membership at the granted level."""

    def _invite(self, context, role):
        email = f"invitee-{uuid.uuid4().hex[:8]}@futureagi.com"
        return email, run_tool(
            "invite_users", {"emails": [email], "role": role}, context
        )

    @pytest.mark.parametrize("role", ["Owner", "Admin", "workspace_admin"])
    def test_member_cannot_grant_a_higher_role(self, tool_context, role):
        from accounts.models.user import User

        member = _inviter(tool_context, 3, "Member")
        email, result = self._invite(member, role)

        assert result.is_error
        assert result.error_code == "PERMISSION_DENIED"
        assert not User.objects.filter(email=email).exists()

    def test_owner_string_without_owner_membership_cannot_grant_owner(
        self, tool_context
    ):
        from accounts.models.user import User

        member = _inviter(tool_context, 3, "Owner")
        email, result = self._invite(member, "Owner")

        assert result.is_error
        assert result.error_code == "PERMISSION_DENIED"
        assert not User.objects.filter(email=email).exists()

    def test_viewer_cannot_invite(self, tool_context):
        from accounts.models.user import User

        viewer = _inviter(tool_context, 1, "Member")
        email, result = self._invite(viewer, "workspace_viewer")

        assert result.is_error
        assert result.error_code == "PERMISSION_DENIED"
        assert not User.objects.filter(email=email).exists()

    def test_member_invites_a_member_below_admin(self, tool_context):
        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.user import User
        from accounts.utils import resolve_org_role
        from tfc.constants.levels import Level

        member = _inviter(tool_context, 3, "Member")
        email, result = self._invite(member, "Member")

        assert not result.is_error
        invitee = User.objects.get(email=email)
        organization = tool_context.organization
        row = OrganizationMembership.no_workspace_objects.get(
            user=invitee, organization=organization
        )
        assert row.is_active and row.level == Level.MEMBER
        assert invitee.organization_role == "Member"
        assert invitee.get_membership_level(organization) == Level.MEMBER
        assert invitee.has_global_workspace_access(organization) is False
        assert resolve_org_role(invitee, organization) == "Member"

    def test_owner_grants_admin_at_the_admin_level(self, tool_context):
        from accounts.models.user import User
        from tfc.constants.levels import Level

        email, result = self._invite(tool_context, "Admin")

        assert not result.is_error
        invitee = User.objects.get(email=email)
        organization = tool_context.organization
        assert invitee.get_membership_level(organization) == Level.ADMIN
        assert invitee.organization_role == "Admin"
        assert invitee.has_global_workspace_access(organization) is True
