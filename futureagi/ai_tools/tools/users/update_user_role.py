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
    workspace_id: UUID | None = Field(
        default=None,
        description="Workspace UUID (required when level='workspace')",
    )


_ORG_ROLES = ("Owner", "Admin", "Member", "Viewer")
_WORKSPACE_ROLES = ("workspace_admin", "workspace_member", "workspace_viewer")


def _apply(org, actor, target_user, **levels) -> ToolResult | None:
    """Change the role through the service the Members page uses, so role and level
    move together; a refusal comes back as the tool's error."""
    from accounts.services import member_role_service
    from tfc.utils.error_codes import get_error_message

    try:
        member_role_service.update_member_role(
            organization=org, actor=actor, target_user_id=target_user.id, **levels
        )
    except member_role_service.MemberRoleUpdateError as error:
        return ToolResult.error(
            get_error_message(error.code),
            error_code=(
                "PERMISSION_DENIED" if error.status_code == 403 else "VALIDATION_ERROR"
            ),
        )
    return None


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
        from accounts.models.workspace import Workspace, WorkspaceMembership
        from accounts.services.organization_access import (
            is_organization_admin,
            membership_level,
        )
        from tfc.constants.levels import Level

        org = context.organization
        actor = context.user

        if not is_organization_admin(actor, org):
            return ToolResult.error(
                "You do not have permission to update user roles. "
                "Only Owner or Admin roles can change user roles.",
                error_code="PERMISSION_DENIED",
            )

        # Find the target user
        try:
            target_user = User.objects.get(id=params.user_id, organization=org)
        except User.DoesNotExist:
            return ToolResult.not_found("User", str(params.user_id))

        # Prevent changing own role to a lower level
        if target_user.id == actor.id:
            return ToolResult.error(
                "You cannot change your own role.",
                error_code="VALIDATION_ERROR",
            )

        if (
            membership_level(target_user, org) >= Level.OWNER
            and membership_level(actor, org) < Level.OWNER
        ):
            return ToolResult.error(
                "Only Owners can modify another Owner's role.",
                error_code="PERMISSION_DENIED",
            )

        if params.level == "org":
            if params.new_role not in _ORG_ROLES:
                return ToolResult.error(
                    f"Invalid role '{params.new_role}'. Must be one of: "
                    f"{', '.join(_ORG_ROLES)}.",
                    error_code="VALIDATION_ERROR",
                )
            old_role = Level.to_org_string(membership_level(target_user, org))
            refused = _apply(
                org, actor, target_user, org_level=Level.from_string(params.new_role)
            )
            if refused is not None:
                return refused

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

            ws_role = Level.normalize_ws_role(params.new_role)
            if ws_role not in _WORKSPACE_ROLES:
                return ToolResult.error(
                    f"Invalid role '{params.new_role}'. Must be one of: "
                    f"{', '.join(_WORKSPACE_ROLES)}.",
                    error_code="VALIDATION_ERROR",
                )
            membership = WorkspaceMembership.no_workspace_objects.filter(
                workspace=workspace, user=target_user, is_active=True
            ).first()
            if membership is None:
                return ToolResult.error(
                    f"User {target_user.email} is not a member of workspace '{workspace.name}'.",
                    error_code="NOT_FOUND",
                )
            old_role = membership.role
            refused = _apply(
                org,
                actor,
                target_user,
                ws_level=Level.from_string(ws_role),
                workspace_id=workspace.id,
            )
            if refused is not None:
                return refused

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
