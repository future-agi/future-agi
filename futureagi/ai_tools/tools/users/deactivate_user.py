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
        from accounts.services.organization_access import (
            is_organization_admin,
            membership_level,
        )
        from tfc.constants.levels import Level

        org = context.organization
        actor = context.user

        if not is_organization_admin(actor, org):
            return ToolResult.error(
                "You do not have permission to deactivate users. "
                "Only Owner or Admin roles can deactivate users.",
                error_code="PERMISSION_DENIED",
            )

        try:
            target_user = User.objects.get(id=params.user_id, organization=org)
        except User.DoesNotExist:
            return ToolResult.not_found("User", str(params.user_id))

        # Prevent self-deactivation
        if target_user.id == actor.id:
            return ToolResult.error(
                "You cannot deactivate your own account.",
                error_code="VALIDATION_ERROR",
            )

        if (
            membership_level(target_user, org) >= Level.OWNER
            and membership_level(actor, org) < Level.OWNER
        ):
            return ToolResult.error(
                "Only Owners can deactivate another Owner.",
                error_code="PERMISSION_DENIED",
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
