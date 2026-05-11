from typing import Optional
from uuid import UUID

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


class ListUsersInput(PydanticBaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    workspace_id: Optional[UUID] = Field(
        default=None,
        description="Filter by workspace membership. If not set, lists all org users.",
    )
    role: Optional[str] = Field(
        default=None,
        description="Filter by organization role (e.g. Owner, Admin, Member, Viewer)",
    )
    status: Optional[str] = Field(
        default=None,
        description="Filter by status: 'active' or 'inactive'",
    )
    search: Optional[str] = Field(
        default=None,
        description="Search by email or name (case-insensitive contains)",
    )


@register_tool
class ListUsersTool(BaseTool):
    name = "list_users"
    description = (
        "Lists users in the current organization with optional filters for workspace, "
        "role, status, and search. Shows email, name, role, status, and last login."
    )
    category = "users"
    input_model = ListUsersInput

    def execute(self, params: ListUsersInput, context: ToolContext) -> ToolResult:
        from django.db.models import Q

        from accounts.models.user import User
        from accounts.models.workspace import WorkspaceMembership
        from tfc.permissions.utils import get_org_membership

        org = context.organization

        # Verify actor is a member of this org
        actor_membership = get_org_membership(context.user)
        if actor_membership is None:
            return ToolResult.permission_denied(
                "You are not a member of this organization."
            )

        # Base queryset: users in this organization
        qs = User.objects.filter(organization=org).order_by("-created_at")

        # Filter by workspace membership
        if params.workspace_id:
            workspace_user_ids = WorkspaceMembership.no_workspace_objects.filter(
                workspace_id=params.workspace_id,
                is_active=True,
            ).values_list("user_id", flat=True)
            qs = qs.filter(id__in=workspace_user_ids)

        # Filter by organization role
        if params.role:
            qs = qs.filter(organization_role__iexact=params.role)

        # Filter by active status
        if params.status:
            if params.status.lower() == "active":
                qs = qs.filter(is_active=True)
            elif params.status.lower() == "inactive":
                qs = qs.filter(is_active=False)

        # Search by email or name
        if params.search:
            qs = qs.filter(
                Q(email__icontains=params.search) | Q(name__icontains=params.search)
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
                    "role": user.organization_role,
                    "is_active": user.is_active,
                    "last_login": str(user.last_login) if user.last_login else None,
                }
            )

        table = markdown_table(
            ["ID", "Email", "Name", "Role", "Status", "Last Login"], rows
        )

        filters_desc = []
        if params.workspace_id:
            filters_desc.append(f"workspace={params.workspace_id}")
        if params.role:
            filters_desc.append(f"role={params.role}")
        if params.status:
            filters_desc.append(f"status={params.status}")
        if params.search:
            filters_desc.append(f"search='{params.search}'")

        showing = f"Showing {len(rows)} of {total}"
        if filters_desc:
            showing += f" (filters: {', '.join(filters_desc)})"

        content = section(f"Users ({total})", f"{showing}\n\n{table}")

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(content=content, data={"users": data_list, "total": total})
