from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class RemoveUserInput(PydanticBaseModel):
    user_id: str = Field(
        default="",
        description="UUID, email, or exact/fuzzy name of the user to remove. Omit to list candidates."
    )


@register_tool
class RemoveUserTool(BaseTool):
    name = "remove_user"
    description = (
        "Removes a user from the organization entirely. This deactivates the user "
        "and removes all their workspace memberships. Requires Owner or Admin permissions. "
        "Call without a user ID to list candidates."
    )
    category = "users"
    input_model = RemoveUserInput

    def execute(self, params: RemoveUserInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.users._utils import resolve_user
        from accounts.models.workspace import OrganizationRoles, WorkspaceMembership

        org = context.organization
        actor = context.user

        # Permission check
        if actor.organization_role not in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]:
            return ToolResult.error(
                "You do not have permission to remove users. "
                "Only Owner or Admin roles can remove users.",
                error_code="PERMISSION_DENIED",
            )

        target_user, user_result = resolve_user(
            params.user_id,
            context,
            title="User Required",
        )
        if user_result:
            return user_result

        # Prevent self-removal
        if target_user.id == actor.id:
            return ToolResult.error(
                "You cannot remove yourself from the organization.",
                error_code="VALIDATION_ERROR",
            )

        # Prevent removing owners unless you're an owner
        if (
            target_user.organization_role == OrganizationRoles.OWNER
            and actor.organization_role != OrganizationRoles.OWNER
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
