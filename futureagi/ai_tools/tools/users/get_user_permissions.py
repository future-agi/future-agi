from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class GetUserPermissionsInput(PydanticBaseModel):
    user_id: Optional[UUID] = Field(
        default=None,
        description="UUID of the user to check. Defaults to the current user.",
    )
    workspace_id: Optional[UUID] = Field(
        default=None,
        description="Workspace UUID to check permissions for. Defaults to the current workspace.",
    )


@register_tool
class GetUserPermissionsTool(BaseTool):
    name = "get_user_permissions"
    description = (
        "Checks a user's permissions for a specific workspace. Shows whether they "
        "can read, write, and their workspace role. Defaults to the current user "
        "and current workspace if not specified."
    )
    category = "users"
    input_model = GetUserPermissionsInput

    def execute(
        self, params: GetUserPermissionsInput, context: ToolContext
    ) -> ToolResult:

        from accounts.models.user import User
        from accounts.models.workspace import Workspace
        from tfc.constants.levels import Level
        from tfc.permissions.utils import (
            get_effective_workspace_level,
            get_org_membership,
        )

        org = context.organization

        # Determine target user
        if params.user_id:
            try:
                target_user = User.objects.get(id=params.user_id, organization=org)
            except User.DoesNotExist:
                return ToolResult.not_found("User", str(params.user_id))
        else:
            target_user = context.user

        # If querying another user, require org admin
        if target_user.id != context.user.id:
            actor_membership = get_org_membership(context.user)
            if (
                actor_membership is None
                or actor_membership.level_or_legacy < Level.ADMIN
            ):
                return ToolResult.permission_denied(
                    "Only organization admins can view other users' permissions."
                )

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

        # Resolve permissions using level-based RBAC
        target_membership = get_org_membership(target_user)
        org_role = (
            Level.to_org_string(target_membership.level_or_legacy)
            if target_membership
            else "—"
        )
        has_global_access = (
            target_membership is not None
            and target_membership.level_or_legacy >= Level.ADMIN
        )

        ws_level = get_effective_workspace_level(target_user, workspace.id)
        workspace_role = Level.to_ws_string(ws_level) if ws_level else "—"

        can_access = target_user.can_access_workspace(workspace)
        can_read = target_user.can_read_from_workspace(workspace)
        can_write = target_user.can_write_to_workspace(workspace)

        info = key_value_block(
            [
                ("User", f"{target_user.name} ({target_user.email})"),
                ("Organization Role", org_role),
                ("Workspace", f"{workspace.name} (`{workspace.id}`)"),
                ("Workspace Role", workspace_role),
                ("Global Workspace Access", "Yes" if has_global_access else "No"),
                ("Can Access Workspace", "Yes" if can_access else "No"),
                ("Can Read", "Yes" if can_read else "No"),
                ("Can Write", "Yes" if can_write else "No"),
            ]
        )

        content = section("User Permissions", info)

        return ToolResult(
            content=content,
            data={
                "user_id": str(target_user.id),
                "user_email": target_user.email,
                "workspace_id": str(workspace.id),
                "workspace_name": workspace.name,
                "organization_role": org_role,
                "workspace_role": workspace_role,
                "has_global_access": has_global_access,
                "can_access": can_access,
                "can_read": can_read,
                "can_write": can_write,
            },
        )
