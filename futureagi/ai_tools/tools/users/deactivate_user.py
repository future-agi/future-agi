from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class DeactivateUserInput(PydanticBaseModel):
    user_id: UUID = Field(description="UUID of the user to deactivate")


@register_tool
class DeactivateUserTool(BaseTool):
    name = "deactivate_user"
    description = (
        "Deactivates a user account (soft disable). The user will no longer "
        "be able to log in but their data is preserved. Requires admin permissions."
    )
    category = "users"
    input_model = DeactivateUserInput

    def execute(self, params: DeactivateUserInput, context: ToolContext) -> ToolResult:

        from accounts.models.user import User
        from tfc.constants.levels import Level
        from tfc.permissions.utils import get_org_membership

        org = context.organization
        actor = context.user

        # Level-based permission check
        actor_membership = get_org_membership(actor)
        if actor_membership is None or actor_membership.level_or_legacy < Level.ADMIN:
            return ToolResult.permission_denied(
                "You do not have permission to deactivate users. "
                "Requires organization admin or owner role."
            )
        actor_level = actor_membership.level_or_legacy

        try:
            target_user = User.objects.get(id=params.user_id, organization=org)
        except User.DoesNotExist:
            return ToolResult.not_found("User", str(params.user_id))

        # Prevent self-deactivation
        if target_user.id == actor.id:
            return ToolResult.validation_error(
                "You cannot deactivate your own account."
            )

        # Prevent deactivating owners unless you're an owner
        target_membership = get_org_membership(target_user)
        target_level = target_membership.level_or_legacy if target_membership else 0
        if target_level >= Level.OWNER and actor_level < Level.OWNER:
            return ToolResult.permission_denied(
                "Only Owners can deactivate another Owner."
            )

        if not target_user.is_active:
            return ToolResult.error(
                f"User {target_user.email} is already deactivated.",
                error_code="VALIDATION_ERROR",
            )

        target_user.is_active = False
        target_user.save()

        info = key_value_block(
            [
                ("User", f"{target_user.name} ({target_user.email})"),
                ("User ID", f"`{target_user.id}`"),
                ("Status", "Deactivated"),
                ("Action", "User can no longer log in. Data is preserved."),
            ]
        )
        content = section("User Deactivated", info)

        return ToolResult(
            content=content,
            data={
                "user_id": str(target_user.id),
                "email": target_user.email,
                "is_active": False,
            },
        )
