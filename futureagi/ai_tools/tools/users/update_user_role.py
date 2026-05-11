from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class UpdateUserRoleInput(PydanticBaseModel):
    user_id: UUID = Field(description="UUID of the user whose role to update")
    new_role: str = Field(
        description=(
            "New role to assign. Organization-level: Owner, Admin, Member, Viewer. "
            "Workspace-level: workspace_admin, workspace_member, workspace_viewer."
        )
    )
    level: str = Field(
        description="Level at which to change the role: 'org' or 'workspace'"
    )
    workspace_id: Optional[UUID] = Field(
        default=None,
        description="Workspace UUID (required when level='workspace')",
    )


@register_tool
class UpdateUserRoleTool(BaseTool):
    name = "update_user_role"
    description = (
        "Changes a user's role at the organization or workspace level. "
        "Requires admin permissions. For workspace-level changes, a workspace_id must be provided."
    )
    category = "users"
    input_model = UpdateUserRoleInput

    def execute(self, params: UpdateUserRoleInput, context: ToolContext) -> ToolResult:

        from accounts.models.user import User
        from accounts.models.workspace import (
            Workspace,
            WorkspaceMembership,
        )
        from tfc.constants.levels import Level
        from tfc.permissions.utils import (
            can_invite_at_level,
            get_effective_workspace_level,
            get_org_membership,
        )

        org = context.organization
        actor = context.user

        # Level-based permission check
        actor_membership = get_org_membership(actor)
        if actor_membership is None or actor_membership.level_or_legacy < Level.ADMIN:
            return ToolResult.permission_denied(
                "You do not have permission to update user roles. "
                "Requires organization admin or owner role."
            )
        actor_level = actor_membership.level_or_legacy

        # Find the target user
        try:
            target_user = User.objects.get(id=params.user_id, organization=org)
        except User.DoesNotExist:
            return ToolResult.not_found("User", str(params.user_id))

        # Prevent changing own role
        if target_user.id == actor.id:
            return ToolResult.validation_error("You cannot change your own role.")

        # Prevent non-owners from modifying owners
        target_membership = get_org_membership(target_user)
        target_level = target_membership.level_or_legacy if target_membership else 0
        if target_level >= Level.OWNER and actor_level < Level.OWNER:
            return ToolResult.permission_denied(
                "Only Owners can modify another Owner's role."
            )

        # Validate the new role and enforce hierarchy
        try:
            new_level = Level.from_string(params.new_role)
        except ValueError:
            return ToolResult.validation_error(f"Invalid role '{params.new_role}'.")

        if not can_invite_at_level(actor_level, new_level):
            return ToolResult.permission_denied(
                f"You cannot assign the '{params.new_role}' role — "
                "it requires a higher privilege level than yours."
            )

        if params.level == "org":
            old_role = target_user.organization_role
            target_user.organization_role = params.new_role
            target_user.save()

            info = key_value_block(
                [
                    ("User", f"{target_user.name} ({target_user.email})"),
                    ("Level", "Organization"),
                    ("Previous Role", old_role or "—"),
                    ("New Role", params.new_role),
                ]
            )
            content = section("Role Updated", info)

            return ToolResult(
                content=content,
                data={
                    "user_id": str(target_user.id),
                    "level": "org",
                    "old_role": old_role,
                    "new_role": params.new_role,
                },
            )

        elif params.level == "workspace":
            if not params.workspace_id:
                return ToolResult.error(
                    "workspace_id is required when level='workspace'",
                    error_code="VALIDATION_ERROR",
                )

            try:
                workspace = Workspace.objects.get(
                    id=params.workspace_id, organization=org, is_active=True
                )
            except Workspace.DoesNotExist:
                return ToolResult.not_found("Workspace", str(params.workspace_id))

            try:
                membership = WorkspaceMembership.no_workspace_objects.get(
                    workspace=workspace, user=target_user, is_active=True
                )
                old_role = membership.role
                membership.role = params.new_role
                membership.save()
            except WorkspaceMembership.DoesNotExist:
                return ToolResult.error(
                    f"User {target_user.email} is not a member of workspace '{workspace.name}'.",
                    error_code="NOT_FOUND",
                )

            info = key_value_block(
                [
                    ("User", f"{target_user.name} ({target_user.email})"),
                    ("Level", "Workspace"),
                    ("Workspace", f"{workspace.name} (`{workspace.id}`)"),
                    ("Previous Role", old_role or "—"),
                    ("New Role", params.new_role),
                ]
            )
            content = section("Role Updated", info)

            return ToolResult(
                content=content,
                data={
                    "user_id": str(target_user.id),
                    "level": "workspace",
                    "workspace_id": str(workspace.id),
                    "old_role": old_role,
                    "new_role": params.new_role,
                },
            )

        else:
            return ToolResult.error(
                f"Invalid level '{params.level}'. Must be 'org' or 'workspace'.",
                error_code="VALIDATION_ERROR",
            )
