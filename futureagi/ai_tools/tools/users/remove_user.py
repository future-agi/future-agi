from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class RemoveUserInput(PydanticBaseModel):
    user_id: UUID = Field(
        description="UUID of the user to remove from the organization"
    )


@register_tool
class RemoveUserTool(BaseTool):
    name = "remove_user"
    description = (
        "Removes a user from the organization entirely. This deactivates the user "
        "and removes all their workspace memberships. Requires Owner or Admin permissions."
    )
    category = "users"
    input_model = RemoveUserInput

    def execute(self, params: RemoveUserInput, context: ToolContext) -> ToolResult:
        from accounts.models.user import User
        from accounts.models.workspace import WorkspaceMembership
        from accounts.services.organization_access import (
            is_organization_admin,
            membership_level,
        )
        from tfc.constants.levels import Level

        org = context.organization
        actor = context.user

        if not is_organization_admin(actor, org):
            return ToolResult.error(
                "You do not have permission to remove users. "
                "Only Owner or Admin roles can remove users.",
                error_code="PERMISSION_DENIED",
            )

        try:
            target_user = User.objects.get(id=params.user_id, organization=org)
        except User.DoesNotExist:
            return ToolResult.not_found("User", str(params.user_id))

        # Prevent self-removal
        if target_user.id == actor.id:
            return ToolResult.error(
                "You cannot remove yourself from the organization.",
                error_code="VALIDATION_ERROR",
            )

        if (
            membership_level(target_user, org) >= Level.OWNER
            and membership_level(actor, org) < Level.OWNER
        ):
            return ToolResult.error(
                "Only Owners can remove another Owner.",
                error_code="PERMISSION_DENIED",
            )

        # Remove all workspace memberships in this org
        ws_memberships = WorkspaceMembership.no_workspace_objects.filter(
            user=target_user,
            workspace__organization=org,
            is_active=True,
        )
        ws_count = ws_memberships.count()
        ws_memberships.update(is_active=False, deleted=True)

        # Deactivate the user
        target_user.is_active = False
        target_user.save()

        info = key_value_block(
            [
                ("User", f"{target_user.name} ({target_user.email})"),
                ("User ID", f"`{target_user.id}`"),
                ("Status", "Removed"),
                ("Workspace Memberships Removed", str(ws_count)),
                (
                    "Action",
                    "User has been deactivated and removed from all workspaces.",
                ),
            ]
        )
        content = section("User Removed", info)

        return ToolResult(
            content=content,
            data={
                "user_id": str(target_user.id),
                "email": target_user.email,
                "workspaces_removed": ws_count,
            },
        )
