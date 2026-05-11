from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    format_uuid,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class ListOrgMembersInput(PydanticBaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")


@register_tool
class ListOrgMembersTool(BaseTool):
    name = "list_org_members"
    description = (
        "Lists all members in the current organization with their roles, "
        "status, and last login time."
    )
    category = "users"
    input_model = ListOrgMembersInput

    def execute(self, params: ListOrgMembersInput, context: ToolContext) -> ToolResult:

        from accounts.models.user import User
        from tfc.permissions.utils import get_org_membership

        org = context.organization

        # Verify actor is a member of this org
        actor_membership = get_org_membership(context.user)
        if actor_membership is None:
            return ToolResult.permission_denied(
                "You are not a member of this organization."
            )

        qs = User.objects.filter(organization=org).order_by(
            "-is_active", "organization_role", "email"
        )

        total = qs.count()
        users = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for user in users:
            status = "active" if user.is_active else "inactive"
            rows.append(
                [
                    format_uuid(user.id),
                    user.email,
                    user.name or "—",
                    user.organization_role or "—",
                    format_status(status),
                    format_datetime(user.last_login),
                ]
            )
            data_list.append(
                {
                    "id": str(user.id),
                    "email": user.email,
                    "name": user.name,
                    "organization_role": user.organization_role,
                    "is_active": user.is_active,
                    "last_login": str(user.last_login) if user.last_login else None,
                }
            )

        table = markdown_table(
            ["ID", "Email", "Name", "Role", "Status", "Last Login"], rows
        )

        content = section(
            f"Organization Members ({total})",
            f"Organization: **{org.display_name or org.name}**\n\n"
            f"Showing {len(rows)} of {total}\n\n{table}",
        )

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(content=content, data={"members": data_list, "total": total})
