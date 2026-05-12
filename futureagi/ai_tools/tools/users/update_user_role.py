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

        from ai_tools.tools.users._utils import resolve_user
        from accounts.models.workspace import (
            OrganizationRoles,
            Workspace,
            WorkspaceMembership,
        )
        from tfc.constants.roles import RolePermissions

        org = context.organization
        actor = context.user

        # Permission check: only Owner/Admin can change roles
        if actor.organization_role not in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]:
            return ToolResult.error(
                "You do not have permission to update user roles. "
                "Only Owner or Admin roles can change user roles.",
                error_code="PERMISSION_DENIED",
            )

        # Find the target user
        target_user, user_result = resolve_user(
            params.user_id,
            context,
            title="User Required",
        )
        if user_result:
            return user_result

        # Prevent changing own role to a lower level
        if target_user.id == actor.id:
            return ToolResult.error(
                "You cannot change your own role.",
                error_code="VALIDATION_ERROR",
            )

        # Prevent non-owners from modifying owner roles
        if (
            target_user.organization_role == OrganizationRoles.OWNER
            and actor.organization_role != OrganizationRoles.OWNER
        ):
            return ToolResult.error(
                "Only Owners can modify another Owner's role.",
                error_code="PERMISSION_DENIED",
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
