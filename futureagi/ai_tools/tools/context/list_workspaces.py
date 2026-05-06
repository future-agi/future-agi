from ai_tools.base import BaseTool, EmptyInput, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, markdown_table, section
from ai_tools.registry import register_tool


@register_tool
class ListWorkspacesTool(BaseTool):
    name = "list_workspaces"
    description = (
        "Lists all workspaces accessible to the current user in their organization. "
        "Shows workspace name, status, and whether it's the default workspace."
    )
    category = "context"
    input_model = EmptyInput

    def execute(self, params: EmptyInput, context: ToolContext) -> ToolResult:
        from accounts.models.workspace import Workspace, WorkspaceMembership

        user = context.user
        org = context.organization

        if user.has_global_workspace_access(org):
            # Org Admin/Owner: see all workspaces
            workspaces = (
                Workspace.objects.filter(
                    organization=org,
                    is_active=True,
                    deleted=False,
                )
                .order_by("-is_default", "name")
                .values_list("id", "name", "is_default", "created_at")
            )
        else:
            # Members/Viewers: only workspaces they have active membership in
            accessible_ws_ids = WorkspaceMembership.no_workspace_objects.filter(
                user=user, is_active=True
            ).values_list("workspace_id", flat=True)

            workspaces = (
                Workspace.objects.filter(
                    id__in=accessible_ws_ids,
                    organization=org,
                    is_active=True,
                    deleted=False,
                )
                .order_by("-is_default", "name")
                .values_list("id", "name", "is_default", "created_at")
            )

        rows = []
        data_list = []
        for ws_id, name, is_default, created_at in workspaces:
            default_marker = (
                " (current)" if str(ws_id) == str(context.workspace_id) else ""
            )
            default_label = "Yes" if is_default else "No"
            rows.append(
                [
                    f"`{str(ws_id)}`",
                    f"{name}{default_marker}",
                    default_label,
                    format_datetime(created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(ws_id),
                    "name": name,
                    "is_default": is_default,
                    "is_current": str(ws_id) == str(context.workspace_id),
                }
            )

        table = markdown_table(["ID", "Name", "Default", "Created"], rows)
        content = section(
            f"Workspaces ({len(rows)})",
            f"Organization: **{context.organization.name}**\n\n{table}",
        )

        return ToolResult(content=content, data={"workspaces": data_list})
