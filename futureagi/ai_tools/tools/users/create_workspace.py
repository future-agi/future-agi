from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, key_value_block, section
from ai_tools.registry import register_tool


class CreateWorkspaceInput(PydanticBaseModel):
    name: str = Field(description="Name for the new workspace")
    description: Optional[str] = Field(
        default=None, description="Optional description for the workspace"
    )


@register_tool
class CreateWorkspaceTool(BaseTool):
    name = "create_workspace"
    description = (
        "Creates a new workspace in the current organization. "
        "Requires Owner or Admin permissions."
    )
    category = "users"
    input_model = CreateWorkspaceInput

    def execute(self, params: CreateWorkspaceInput, context: ToolContext) -> ToolResult:
        from django.db import IntegrityError

        from accounts.models.workspace import OrganizationRoles, Workspace
        from tfc.constants.levels import Level
        from tfc.permissions.utils import get_org_membership

        org = context.organization
        actor = context.user

        # Level-based permission check
        actor_membership = get_org_membership(actor)
        if actor_membership is None or actor_membership.level_or_legacy < Level.ADMIN:
            return ToolResult.permission_denied(
                "You do not have permission to create workspaces. "
                "Requires organization admin or owner role."
            )

        try:
            workspace = Workspace(
                name=params.name,
                display_name=params.name,
                description=params.description or "",
                organization=org,
                created_by=actor,
                is_active=True,
                is_default=False,
            )
            workspace.save()

            # Add creator as workspace admin (matches WorkspaceManagementView.post behavior)
            from accounts.models.workspace import WorkspaceMembership

            WorkspaceMembership.no_workspace_objects.create(
                workspace=workspace,
                user=actor,
                role=OrganizationRoles.WORKSPACE_ADMIN,
                invited_by=actor,
            )
        except IntegrityError:
            return ToolResult.error(
                f"A workspace with the name '{params.name}' already exists in this organization.",
                error_code="VALIDATION_ERROR",
            )

        info = key_value_block(
            [
                ("Workspace ID", f"`{workspace.id}`"),
                ("Name", workspace.name),
                ("Description", workspace.description or "—"),
                ("Organization", org.display_name or org.name),
                ("Created By", f"{actor.name} ({actor.email})"),
                ("Created", format_datetime(workspace.created_at)),
            ]
        )
        content = section("Workspace Created", info)

        return ToolResult(
            content=content,
            data={
                "workspace_id": str(workspace.id),
                "name": workspace.name,
                "description": workspace.description,
                "organization_id": str(org.id),
            },
        )
