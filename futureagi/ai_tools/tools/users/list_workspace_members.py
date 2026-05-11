from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_uuid,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class ListWorkspaceMembersInput(PydanticBaseModel):
    workspace_id: Optional[UUID] = Field(
        default=None,
        description="Workspace UUID. Defaults to the current workspace.",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")


@register_tool
class ListWorkspaceMembersTool(BaseTool):
    name = "list_workspace_members"
    description = (
        "Lists all members in a specific workspace, showing their email, name, "
        "workspace role, and when they joined. Defaults to the current workspace."
    )
    category = "users"
    input_model = ListWorkspaceMembersInput

    def execute(
        self, params: ListWorkspaceMembersInput, context: ToolContext
    ) -> ToolResult:

        from accounts.models.workspace import Workspace, WorkspaceMembership
        from tfc.permissions.utils import get_effective_workspace_level

        org = context.organization

        # Determine target workspace
        if params.workspace_id:
            try:
                workspace = Workspace.objects.get(
                    id=params.workspace_id,
                    organization=org,
                    is_active=True,
                )
            except Workspace.DoesNotExist:
                return ToolResult.not_found("Workspace", str(params.workspace_id))
        else:
            workspace = context.workspace

        # Verify actor can access this workspace
        actor_level = get_effective_workspace_level(context.user, workspace.id)
        if actor_level is None:
            return ToolResult.permission_denied(
                "You do not have access to this workspace."
            )

        # Query memberships
        qs = (
            WorkspaceMembership.no_workspace_objects.filter(
                workspace=workspace,
                is_active=True,
            )
            .select_related("user", "invited_by")
            .order_by("-created_at")
        )

        total = qs.count()
        memberships = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for m in memberships:
            invited_by = m.invited_by.email if m.invited_by else "—"
            rows.append(
                [
                    format_uuid(m.user.id),
                    m.user.email,
                    m.user.name or "—",
                    m.role or "—",
                    invited_by,
                    format_datetime(m.created_at),
                ]
            )
            data_list.append(
                {
                    "user_id": str(m.user.id),
                    "email": m.user.email,
                    "name": m.user.name,
                    "role": m.role,
                    "invited_by": invited_by,
                }
            )

        table = markdown_table(
            ["ID", "Email", "Name", "Role", "Invited By", "Joined"], rows
        )

        content = section(
            f"Workspace Members ({total})",
            f"Workspace: **{workspace.name}** (`{workspace.id}`)\n\n"
            f"Showing {len(rows)} of {total}\n\n{table}",
        )

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(
            content=content,
            data={
                "workspace_id": str(workspace.id),
                "workspace_name": workspace.name,
                "members": data_list,
                "total": total,
            },
        )
