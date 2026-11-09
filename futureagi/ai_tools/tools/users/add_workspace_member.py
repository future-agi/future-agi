from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from accounts.services.hard_erase_issuance_entry import tool_issuance_entry
from accounts.services.identity_issuance import issue_membership
from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class AddWorkspaceMemberInput(PydanticBaseModel):
    workspace_id: UUID = Field(description="UUID of the workspace to add the member to")
    user_id: UUID = Field(description="UUID of the user to add")
    role: str = Field(
        default="workspace_member",
        description="Role to assign: workspace_admin, workspace_member, or workspace_viewer",
    )


@register_tool
class AddWorkspaceMemberTool(BaseTool):
    name = "add_workspace_member"
    description = (
        "Adds an existing organization user to a workspace with a specified role. "
        "If the user is already a member, their role is updated. Requires admin permissions."
    )
    category = "users"
    input_model = AddWorkspaceMemberInput

    @tool_issuance_entry
    def execute(
        self, params: AddWorkspaceMemberInput, context: ToolContext
    ) -> ToolResult:

        from accounts.models.user import User
        from accounts.models.workspace import (
            OrganizationRoles,
            Workspace,
            WorkspaceMembership,
        )

        org = context.organization
        actor = context.user

        # Permission check
        if actor.organization_role not in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
            OrganizationRoles.MEMBER,
        ]:
            return ToolResult.error(
                "You do not have permission to add workspace members. "
                "Only Owner, Admin, or Member roles can add members to workspaces.",
                error_code="PERMISSION_DENIED",
            )

        # Validate workspace
        try:
            workspace = Workspace.objects.get(
                id=params.workspace_id, organization=org, is_active=True
            )
        except Workspace.DoesNotExist:
            return ToolResult.not_found("Workspace", str(params.workspace_id))

        # Validate user
        try:
            target_user = User.objects.get(id=params.user_id, organization=org)
        except User.DoesNotExist:
            return ToolResult.not_found("User", str(params.user_id))

        # Check for soft-deleted membership
        existing_deleted = WorkspaceMembership.all_objects.filter(
            workspace=workspace,
            user=target_user,
            deleted=True,
        ).first()

        if existing_deleted:
            existing_deleted.deleted = False
            existing_deleted.is_active = True
            existing_deleted.role = params.role
            existing_deleted.invited_by = actor
            existing_deleted.save()
            action = "re-added"
        else:
            membership, created = issue_membership(
                WorkspaceMembership.no_workspace_objects,
                "get_or_create",
                workspace=workspace,
                user=target_user,
                defaults={
                    "role": params.role,
                    "invited_by": actor,
                    "is_active": True,
                },
            )
            if created:
                action = "added"
            else:
                old_role = membership.role
                membership.role = params.role
                membership.is_active = True
                membership.save()
                action = f"updated (role changed from {old_role})"

        info = key_value_block(
            [
                ("User", f"{target_user.name} ({target_user.email})"),
                ("Workspace", f"{workspace.name} (`{workspace.id}`)"),
                ("Role", params.role),
                ("Action", action.capitalize()),
                ("Added By", f"{actor.name} ({actor.email})"),
            ]
        )
        content = section("Workspace Member Added", info)

        return ToolResult(
            content=content,
            data={
                "user_id": str(target_user.id),
                "workspace_id": str(workspace.id),
                "role": params.role,
                "action": action,
            },
        )
