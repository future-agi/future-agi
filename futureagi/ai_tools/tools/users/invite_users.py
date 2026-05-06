import re
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, markdown_table, section
from ai_tools.registry import register_tool


class InviteUsersInput(PydanticBaseModel):
    emails: List[str] = Field(description="List of email addresses to invite")
    role: str = Field(
        default="workspace_member",
        description=(
            "Role to assign. Organization-level: Owner, Admin, Member, Viewer. "
            "Workspace-level: workspace_admin, workspace_member, workspace_viewer."
        ),
    )
    workspace_ids: Optional[List[UUID]] = Field(
        default=None,
        description=(
            "List of workspace UUIDs to add users to. "
            "If not provided, users are added to the current workspace."
        ),
    )


@register_tool
class InviteUsersTool(BaseTool):
    name = "invite_users"
    description = (
        "Invites users to the organization and adds them to specified workspaces. "
        "Creates new user accounts for emails that don't exist yet, or adds existing "
        "users to the specified workspaces. Requires admin permissions."
    )
    category = "users"
    input_model = InviteUsersInput

    def execute(self, params: InviteUsersInput, context: ToolContext) -> ToolResult:
        from django.db import transaction

        from accounts.models.user import User
        from accounts.models.workspace import (
            OrganizationRoles,
            Workspace,
            WorkspaceMembership,
        )
        from accounts.utils import generate_password
        from tfc.constants.levels import Level
        from tfc.constants.roles import RoleMapping, RolePermissions
        from tfc.permissions.utils import (
            can_invite_at_level,
            get_effective_workspace_level,
            get_org_membership,
        )

        org = context.organization
        inviter = context.user

        # Permission check: resolve actor's org-level RBAC
        actor_membership = get_org_membership(inviter)
        if actor_membership is None:
            return ToolResult.permission_denied(
                "You are not a member of this organization."
            )
        actor_level = actor_membership.level_or_legacy

        # Validate emails
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        invalid_emails = [e for e in params.emails if not re.match(email_pattern, e)]
        if invalid_emails:
            return ToolResult.error(
                f"Invalid email format: {', '.join(invalid_emails)}",
                error_code="VALIDATION_ERROR",
            )

        # Determine target workspaces
        if params.workspace_ids:
            workspaces = Workspace.objects.filter(
                id__in=params.workspace_ids,
                organization=org,
                is_active=True,
                deleted=False,
            )
            if workspaces.count() != len(params.workspace_ids):
                return ToolResult.error(
                    "Some workspace IDs were not found or don't belong to this organization.",
                    error_code="NOT_FOUND",
                )
        else:
            workspaces = Workspace.objects.filter(
                id=context.workspace.id, organization=org, is_active=True, deleted=False
            )

        if not workspaces.exists():
            return ToolResult.error(
                "No valid workspaces found for invitation.",
                error_code="VALIDATION_ERROR",
            )

        # Check actor has admin access to all target workspaces
        if actor_level < Level.ADMIN:
            for ws in workspaces:
                ws_level = get_effective_workspace_level(inviter, ws.id)
                if ws_level is None or ws_level < Level.WORKSPACE_ADMIN:
                    return ToolResult.permission_denied(
                        f"You must be a workspace admin to invite users "
                        f"to workspace '{ws.name}'."
                    )

        # Determine workspace role
        organization_level_roles = [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
            OrganizationRoles.MEMBER,
            OrganizationRoles.MEMBER_VIEW_ONLY,
        ]

        role = params.role
        if role in [r.value for r in organization_level_roles]:
            workspace_role = RoleMapping.get_workspace_role(role)
        else:
            workspace_role = role

        # Validate workspace role and enforce hierarchy
        try:
            target_level = Level.from_string(workspace_role)
        except ValueError:
            return ToolResult.validation_error(
                f"Invalid workspace role: {workspace_role}"
            )

        if not can_invite_at_level(actor_level, target_level):
            return ToolResult.permission_denied(
                f"You cannot assign the '{workspace_role}' role — "
                "it requires a higher privilege level."
            )

        results = []
        errors = []

        for email in params.emails:
            email = email.lower().strip()
            try:
                with transaction.atomic():
                    # Check if user exists in organization
                    try:
                        target_user = User.objects.get(email=email, organization=org)
                        status = "existing"
                    except User.DoesNotExist:
                        # Check if user exists in another org
                        existing_user = (
                            User.objects.filter(email=email, is_active=True)
                            .exclude(organization=org)
                            .first()
                        )
                        if existing_user:
                            errors.append(
                                {
                                    "email": email,
                                    "error": "User already exists in another organization",
                                }
                            )
                            continue

                        # Create new user
                        password = generate_password()
                        target_user = User.objects.create_user(
                            email=email,
                            password=password,
                            name=email.split("@")[0],
                            organization=org,
                            organization_role=(
                                role
                                if role in [r.value for r in organization_level_roles]
                                else OrganizationRoles.MEMBER
                            ),
                            invited_by=inviter,
                        )
                        status = "created"

                    # Add to workspaces
                    ws_added = []
                    for workspace in workspaces:
                        # Check for soft-deleted membership
                        existing_deleted = WorkspaceMembership.all_objects.filter(
                            workspace=workspace,
                            user=target_user,
                            deleted=True,
                        ).first()

                        if existing_deleted:
                            existing_deleted.deleted = False
                            existing_deleted.is_active = True
                            existing_deleted.role = workspace_role
                            existing_deleted.invited_by = inviter
                            existing_deleted.save()
                        else:
                            membership, created = (
                                WorkspaceMembership.no_workspace_objects.get_or_create(
                                    workspace=workspace,
                                    user=target_user,
                                    defaults={
                                        "role": workspace_role,
                                        "invited_by": inviter,
                                        "is_active": True,
                                    },
                                )
                            )
                            if not created:
                                membership.role = workspace_role
                                membership.save()

                        ws_added.append(workspace.name)

                    results.append(
                        {
                            "email": email,
                            "status": status,
                            "workspaces": ws_added,
                            "role": workspace_role,
                        }
                    )

            except Exception as e:
                errors.append({"email": email, "error": str(e)})

        # Build response
        rows = []
        for r in results:
            rows.append(
                [
                    r["email"],
                    r["status"],
                    r["role"],
                    ", ".join(r["workspaces"]),
                ]
            )

        table = markdown_table(["Email", "Status", "Role", "Workspaces"], rows)
        content = section(
            f"Invite Results ({len(results)} succeeded, {len(errors)} failed)", table
        )

        if errors:
            error_rows = [[e["email"], e["error"]] for e in errors]
            error_table = markdown_table(["Email", "Error"], error_rows)
            content += "\n\n" + section("Errors", error_table)

        return ToolResult(
            content=content,
            data={"results": results, "errors": errors},
        )
