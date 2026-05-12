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

        assert not result.is_error
        assert result.data["requires_user_id"] is True
        assert "User Not Found" in result.content

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

    def test_revoke_missing_api_key_returns_candidates(self, tool_context):
        from accounts.models.user import OrgApiKey

        key = OrgApiKey.objects.create(
            name="Candidate Key",
            organization=tool_context.organization,
            user=tool_context.user,
            workspace=tool_context.workspace,
            type="user",
            enabled=True,
        )

        result = run_tool(
            "revoke_api_key",
            {"key_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_key_id"] is True
        assert result.data["api_keys"][0]["id"] == str(key.id)
        assert "API Key Not Found" in result.content


class TestUserMutationRecovery:
    def test_deactivate_missing_user_returns_candidates(self, tool_context):
        result = run_tool(
            "deactivate_user",
            {"user_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_user_id"] is True
        assert "User Not Found" in result.content

    def test_remove_missing_user_returns_candidates(self, tool_context):
        result = run_tool(
            "remove_user",
            {"user_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_user_id"] is True
        assert "User Not Found" in result.content

    def test_update_role_missing_user_returns_candidates(self, tool_context):
        result = run_tool(
            "update_user_role",
            {
                "user_id": str(uuid.uuid4()),
                "new_role": "Member",
                "level": "org",
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_user_id"] is True
        assert "User Not Found" in result.content
