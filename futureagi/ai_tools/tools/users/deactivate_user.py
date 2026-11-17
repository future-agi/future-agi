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
        "Deactivates a membership in the acting organization. The user will no longer "
        "have access to this organization; other organizations are unchanged. Requires admin permissions."
    )
    category = "users"
    input_model = DeactivateUserInput

    def execute(self, params: DeactivateUserInput, context: ToolContext) -> ToolResult:

        from accounts.models.user import User
        from accounts.models.workspace import OrganizationRoles
        from accounts.services.offboarding import offboard_organization_member
        from accounts.utils import resolve_org_role
        from tfc.utils.tenant_scope import counts_as_member

        org = context.organization
        actor = context.user

        # Permission check
        actor_role = resolve_org_role(actor, org)
        if actor_role not in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]:
            return ToolResult.error(
                "You do not have permission to deactivate users. "
                "Only Owner or Admin roles can deactivate users.",
                error_code="PERMISSION_DENIED",
            )

        try:
            target_user = User.objects.get(id=params.user_id)
            if not counts_as_member(target_user, org):
                return ToolResult.not_found("User", str(params.user_id))
        except User.DoesNotExist:
            return ToolResult.not_found("User", str(params.user_id))

        # Prevent self-deactivation
        if target_user.id == actor.id:
            return ToolResult.error(
                "You cannot deactivate your own account.",
                error_code="VALIDATION_ERROR",
            )

        # Prevent deactivating owners unless you're an owner
        if (
            resolve_org_role(target_user, org) == OrganizationRoles.OWNER
            and actor_role != OrganizationRoles.OWNER
        ):
            return ToolResult.error(
                "Only Owners can deactivate another Owner.",
                error_code="PERMISSION_DENIED",
            )

        try:
            offboard_organization_member(target_user, org, actor=actor)
        except ValueError as exc:
            return ToolResult.error(str(exc), error_code="VALIDATION_ERROR")

        info = key_value_block(
            [
                ("User", f"{target_user.name} ({target_user.email})"),
                ("User ID", f"`{target_user.id}`"),
                ("Status", "Deactivated"),
                (
                    "Action",
                    "Access to this organization is disabled. Other organizations are unchanged.",
                ),
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
