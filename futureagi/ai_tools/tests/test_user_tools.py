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
