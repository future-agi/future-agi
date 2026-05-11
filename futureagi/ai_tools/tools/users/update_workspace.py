from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class UpdateWorkspaceInput(PydanticBaseModel):
    workspace_id: UUID = Field(description="UUID of the workspace to update")
    name: Optional[str] = Field(default=None, description="New name for the workspace")
    description: Optional[str] = Field(
        default=None, description="New description for the workspace"
    )


@register_tool
class UpdateWorkspaceTool(BaseTool):
    name = "update_workspace"
    description = (
        "Updates a workspace's name and/or description. "
        "Requires Owner or Admin permissions."
    )
    category = "users"
    input_model = UpdateWorkspaceInput

    def execute(self, params: UpdateWorkspaceInput, context: ToolContext) -> ToolResult:
        from django.db import IntegrityError

        from accounts.models.workspace import Workspace
        from tfc.constants.levels import Level
        from tfc.permissions.utils import get_effective_workspace_level

        org = context.organization
        actor = context.user

        # Level-based permission check: org admin or workspace admin for the target
        actor_level = get_effective_workspace_level(actor, params.workspace_id)
        if actor_level is None or actor_level < Level.WORKSPACE_ADMIN:
            return ToolResult.permission_denied(
                "You must be a workspace admin or organization admin "
                "to update this workspace."
            )

        try:
            workspace = Workspace.objects.get(
                id=params.workspace_id, organization=org, is_active=True
            )
        except Workspace.DoesNotExist:
            return ToolResult.not_found("Workspace", str(params.workspace_id))

        if params.name is None and params.description is None:
            return ToolResult.error(
                "At least one of 'name' or 'description' must be provided.",
                error_code="VALIDATION_ERROR",
            )

        changes = []
        if params.name is not None:
            old_name = workspace.name
            workspace.name = params.name
            workspace.display_name = params.name
            changes.append(("Name", f"{old_name} -> {params.name}"))

        if params.description is not None:
            old_desc = workspace.description or "—"
            workspace.description = params.description
            changes.append(
                ("Description", f"{old_desc} -> {params.description or '—'}")
            )

        try:
            workspace.save()
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
            ]
            + changes
        )
        content = section("Workspace Updated", info)

        return ToolResult(
            content=content,
            data={
                "workspace_id": str(workspace.id),
                "name": workspace.name,
                "description": workspace.description,
                "changes": [{"field": c[0], "detail": c[1]} for c in changes],
            },
        )
