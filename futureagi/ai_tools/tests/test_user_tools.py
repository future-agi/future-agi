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
# WRITE TOOLS: who may act
# ===================================================================


def _acting_context(tool_context, *, legacy_role, level):
    from accounts.tests.test_rbac_regressions import _make_workspace_user
    from ai_tools.base import ToolContext
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    workspace_role, workspace_level = (
        (OrganizationRoles.WORKSPACE_ADMIN, Level.WORKSPACE_ADMIN)
        if level >= Level.ADMIN
        else (OrganizationRoles.WORKSPACE_MEMBER, Level.WORKSPACE_MEMBER)
    )
    actor = _make_workspace_user(
        tool_context.organization,
        tool_context.workspace,
        org_role=legacy_role,
        org_level=level,
        ws_role=workspace_role,
        ws_level=workspace_level,
        prefix="tool-actor",
    )
    return ToolContext(
        user=actor,
        organization=tool_context.organization,
        workspace=tool_context.workspace,
    )


def _member_in_the_organization(tool_context, *, legacy_role=None, level=None):
    from accounts.tests.test_rbac_regressions import _make_workspace_user
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    return _make_workspace_user(
        tool_context.organization,
        tool_context.workspace,
        org_role=legacy_role or OrganizationRoles.MEMBER,
        org_level=level or Level.MEMBER,
        ws_role=OrganizationRoles.WORKSPACE_MEMBER,
        ws_level=Level.WORKSPACE_MEMBER,
        prefix="tool-target",
    )


def _someone_elses_key(tool_context):
    from accounts.models.user import OrgApiKey

    return OrgApiKey.no_workspace_objects.create(
        name=f"team key {uuid.uuid4().hex[:6]}",
        organization=tool_context.organization,
        workspace=tool_context.workspace,
        type="user",
        user=tool_context.user,
    )


USER_WRITE_TOOLS = [
    ("create_api_key", lambda owner, target: {"name": f"k-{uuid.uuid4().hex[:6]}"}),
    (
        "revoke_api_key",
        lambda owner, target: {"key_id": str(_someone_elses_key(owner).id)},
    ),
    (
        "update_user_role",
        lambda owner, target: {
            "user_id": str(target.id),
            "new_role": "Admin",
            "level": "org",
        },
    ),
    ("remove_user", lambda owner, target: {"user_id": str(target.id)}),
    ("deactivate_user", lambda owner, target: {"user_id": str(target.id)}),
    (
        "add_workspace_member",
        lambda owner, target: {
            "workspace_id": str(owner.workspace.id),
            "user_id": str(target.id),
            "role": "workspace_member",
        },
    ),
    ("create_workspace", lambda owner, target: {"name": f"ws-{uuid.uuid4().hex[:6]}"}),
    (
        "update_workspace",
        lambda owner, target: {
            "workspace_id": str(owner.workspace.id),
            "description": "updated by a tool",
        },
    ),
    (
        "invite_users",
        lambda owner, target: {"emails": [f"new-{uuid.uuid4().hex[:6]}@futureagi.com"]},
    ),
]

TARGET_OWNER_TOOLS = ["update_user_role", "remove_user", "deactivate_user"]


@pytest.mark.parametrize(("tool_name", "params_for"), USER_WRITE_TOOLS)
def test_a_member_of_the_organization_cannot_run_user_write_tools(
    tool_context, tool_name, params_for
):
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    actor = _acting_context(
        tool_context, legacy_role=OrganizationRoles.OWNER, level=Level.MEMBER
    )
    target = _member_in_the_organization(tool_context)

    result = run_tool(tool_name, params_for(tool_context, target), actor)

    assert result.is_error, result.content
    assert result.error_code == "PERMISSION_DENIED", result.content


@pytest.mark.parametrize(("tool_name", "params_for"), USER_WRITE_TOOLS)
def test_an_admin_of_the_organization_runs_user_write_tools(
    tool_context, tool_name, params_for
):
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    actor = _acting_context(
        tool_context, legacy_role=OrganizationRoles.MEMBER_VIEW_ONLY, level=Level.ADMIN
    )
    target = _member_in_the_organization(tool_context)

    result = run_tool(tool_name, params_for(tool_context, target), actor)

    assert not result.is_error, result.content


def test_the_creator_of_a_key_revokes_it(tool_context):
    from accounts.models.user import OrgApiKey
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    actor = _acting_context(
        tool_context, legacy_role=OrganizationRoles.MEMBER, level=Level.MEMBER
    )
    own_key = OrgApiKey.no_workspace_objects.create(
        name="my key",
        organization=tool_context.organization,
        workspace=tool_context.workspace,
        type="user",
        user=actor.user,
    )

    result = run_tool("revoke_api_key", {"key_id": str(own_key.id)}, actor)

    assert not result.is_error, result.content
    own_key.refresh_from_db()
    assert own_key.enabled is False


@pytest.mark.parametrize(
    ("tool_name", "params_for"),
    [call for call in USER_WRITE_TOOLS if call[0] in TARGET_OWNER_TOOLS],
)
def test_an_admin_cannot_change_an_owner_of_the_organization(
    tool_context, tool_name, params_for
):
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    actor = _acting_context(
        tool_context, legacy_role=OrganizationRoles.ADMIN, level=Level.ADMIN
    )
    owner = _member_in_the_organization(
        tool_context, legacy_role=OrganizationRoles.MEMBER, level=Level.OWNER
    )

    result = run_tool(tool_name, params_for(tool_context, owner), actor)

    assert result.is_error, result.content
    assert result.error_code == "PERMISSION_DENIED", result.content
    owner.refresh_from_db()
    assert owner.is_active is True
