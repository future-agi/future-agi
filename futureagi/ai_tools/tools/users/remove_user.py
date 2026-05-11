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
        from tfc.constants.levels import Level
        from tfc.permissions.utils import get_org_membership

        org = context.organization
        actor = context.user

        # Level-based permission check
        actor_membership = get_org_membership(actor)
        if actor_membership is None or actor_membership.level_or_legacy < Level.ADMIN:
            return ToolResult.permission_denied(
                "You do not have permission to remove users. "
                "Requires organization admin or owner role."
            )
        actor_level = actor_membership.level_or_legacy

        try:
            target_user = User.objects.get(id=params.user_id, organization=org)
        except User.DoesNotExist:
            return ToolResult.not_found("User", str(params.user_id))

        # Prevent self-removal
        if target_user.id == actor.id:
            return ToolResult.validation_error(
                "You cannot remove yourself from the organization."
            )

        # Prevent removing owners unless you're an owner
        target_membership = get_org_membership(target_user)
        target_level = target_membership.level_or_legacy if target_membership else 0
        if target_level >= Level.OWNER and actor_level < Level.OWNER:
            return ToolResult.permission_denied("Only Owners can remove another Owner.")

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
