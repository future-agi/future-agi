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
        "Lightweight org-scoped listing of organization members, sorted by "
        "active status then role then email. Returns id, email, name, "
        "organization_role, active flag, and last_login time. **No filters, "
        "no search, no workspace scoping** — for richer queries with "
        "workspace_id/role/status filters and search, use `list_users` instead. "
        "Best fit: 'who is in this org overall?', 'when did people last log in?', "
        "or as a fast fallback when list_users isn't needed."
    )
    category = "users"
    input_model = ListOrgMembersInput

    def execute(self, params: ListOrgMembersInput, context: ToolContext) -> ToolResult:

        from accounts.models.user import User

        org = context.organization

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
