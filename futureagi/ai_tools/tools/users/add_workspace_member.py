from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class AddWorkspaceMemberInput(PydanticBaseModel):
    workspace_id: UUID = Field(description="UUID of the workspace to add the member to")
    user_id: UUID = Field(description="UUID of the user to add")
    role: str = Field(
        default="workspace_member",
        description="Role to assign: workspace_admin, workspace_member, or workspace_viewer",
    )


@register_tool
class AddWorkspaceMemberTool(BaseTool):
    name = "add_workspace_member"
    description = (
        "Adds an existing organization user to a workspace with a specified role. "
        "If the user is already a member, their role is updated. Requires admin permissions."
    )
    category = "users"
    input_model = AddWorkspaceMemberInput

    def execute(
        self, params: AddWorkspaceMemberInput, context: ToolContext
    ) -> ToolResult:
        from rest_framework.exceptions import APIException, NotFound

        from accounts.services.legacy_role_assignments import (
            RoleWorkspaceNotFound,
            assign_role,
        )

        actor = context.user
        try:
            target_user, workspace, changes = assign_role(
                actor=actor,
                organization=context.organization,
                target_user_id=params.user_id,
                role=params.role,
                workspace_id=params.workspace_id,
                workspace_only=True,
            )
        except RoleWorkspaceNotFound:
            return ToolResult.not_found("Workspace", str(params.workspace_id))
        except NotFound:
            return ToolResult.not_found("User", str(params.user_id))
        except APIException as exc:
            return ToolResult.error(
                str(exc.detail),
                error_code=(
                    "PERMISSION_DENIED"
                    if exc.status_code == 403
                    else "VALIDATION_ERROR"
                ),
            )
        action = changes["action"]

        info = key_value_block(
            [
                ("User", f"{target_user.name} ({target_user.email})"),
                ("Workspace", f"{workspace.name} (`{workspace.id}`)"),
                ("Role", params.role),
                ("Action", action.capitalize()),
                ("Added By", f"{actor.name} ({actor.email})"),
            ]
        )
        content = section("Workspace Member Added", info)

        return ToolResult(
            content=content,
            data={
                "user_id": str(target_user.id),
                "workspace_id": str(workspace.id),
                "role": params.role,
                "action": action,
            },
        )
