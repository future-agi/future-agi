from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class GetUserInput(PydanticBaseModel):
    user_id: UUID = Field(description="UUID of the user to retrieve")


@register_tool
class GetUserTool(BaseTool):
    name = "get_user"
    description = (
        "Gets detailed profile for a specific user, including email, name, role, "
        "organization memberships, workspace memberships, active status, and creation date."
    )
    category = "users"
    input_model = GetUserInput

    def execute(self, params: GetUserInput, context: ToolContext) -> ToolResult:

        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.user import User
        from accounts.models.workspace import WorkspaceMembership
        from tfc.permissions.utils import get_org_membership

        # Verify actor is a member of this org
        actor_membership = get_org_membership(context.user)
        if actor_membership is None:
            return ToolResult.permission_denied(
                "You are not a member of this organization."
            )

        try:
            user = User.objects.get(
                id=params.user_id, organization=context.organization
            )
        except User.DoesNotExist:
            return ToolResult.not_found("User", str(params.user_id))

        # Build profile info
        status = "active" if user.is_active else "inactive"
        info = key_value_block(
            [
                ("User ID", f"`{user.id}`"),
                ("Email", user.email),
                ("Name", user.name or "—"),
                ("Organization Role", user.organization_role or "—"),
                ("Status", format_status(status)),
                ("Staff", "Yes" if user.is_staff else "No"),
                ("Last Login", format_datetime(user.last_login)),
                ("Created", format_datetime(user.created_at)),
                (
                    "Invited By",
                    user.invited_by.email if user.invited_by else "—",
                ),
            ]
        )

        content = section("User Profile", info)

        # Workspace memberships
        ws_memberships = WorkspaceMembership.no_workspace_objects.filter(
            user=user, is_active=True
        ).select_related("workspace")

        ws_rows = []
        ws_data = []
        for wm in ws_memberships:
            if wm.workspace.organization == context.organization:
                ws_rows.append(
                    [
                        f"`{str(wm.workspace.id)}`",
                        wm.workspace.name,
                        wm.role or "—",
                        format_datetime(wm.created_at),
                    ]
                )
                ws_data.append(
                    {
                        "workspace_id": str(wm.workspace.id),
                        "workspace_name": wm.workspace.name,
                        "role": wm.role,
                    }
                )

        if ws_rows:
            ws_table = markdown_table(["ID", "Workspace", "Role", "Joined"], ws_rows)
            content += "\n\n" + section(
                f"Workspace Memberships ({len(ws_rows)})", ws_table
            )

        # Organization memberships (invited orgs)
        org_memberships = OrganizationMembership.objects.filter(
            user=user, is_active=True
        )
        org_rows = []
        org_data = []
        for om in org_memberships:
            org_rows.append(
                [
                    f"`{str(om.organization.id)}`",
                    om.organization.display_name or om.organization.name,
                    om.role or "—",
                    format_datetime(om.joined_at),
                ]
            )
            org_data.append(
                {
                    "organization_id": str(om.organization.id),
                    "organization_name": om.organization.name,
                    "role": om.role,
                }
            )

        if org_rows:
            org_table = markdown_table(
                ["ID", "Organization", "Role", "Joined"], org_rows
            )
            content += "\n\n" + section(
                f"Organization Memberships ({len(org_rows)})", org_table
            )

        return ToolResult(
            content=content,
            data={
                "user": {
                    "id": str(user.id),
                    "email": user.email,
                    "name": user.name,
                    "organization_role": user.organization_role,
                    "is_active": user.is_active,
                    "is_staff": user.is_staff,
                    "last_login": str(user.last_login) if user.last_login else None,
                    "created_at": str(user.created_at),
                    "workspace_memberships": ws_data,
                    "organization_memberships": org_data,
                },
            },
        )
