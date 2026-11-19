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

        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.user import User
        from accounts.models.workspace import (
            OrganizationRoles,
            Workspace,
            WorkspaceMembership,
        )
        from accounts.services.workspace_membership import ensure_org_membership
        from accounts.utils import generate_password
        from tfc.constants.levels import Level
        from tfc.constants.roles import RoleMapping, RolePermissions

        org = context.organization
        inviter = context.user

        # Permission check: only Owner/Admin/Member can invite. The level is
        # the inviter's active membership in this organization, not the
        # organization_role string on the account.
        inviter_membership = inviter.get_membership(org)
        inviter_level = (
            inviter_membership.level_or_legacy or 0 if inviter_membership else 0
        )
        if inviter_level < Level.MEMBER:
            return ToolResult.error(
                "You do not have permission to invite users. "
                "Only Owner, Admin, or Member roles can invite.",
                error_code="PERMISSION_DENIED",
            )

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
            org_role = role
        else:
            workspace_role = role
            org_role = OrganizationRoles.MEMBER.value

        # An invite grants at most the inviter's own level, in the
        # organization and in the workspace.
        granted_level = Level.STRING_TO_LEVEL.get(org_role)
        workspace_level = Level.STRING_TO_LEVEL.get(workspace_role)
        if granted_level is None or workspace_level is None:
            return ToolResult.error(
                f"Unknown role: {role}.",
                error_code="VALIDATION_ERROR",
            )
        if max(granted_level, workspace_level) > inviter_level:
            return ToolResult.error(
                "You can't invite someone with a role above your own.",
                error_code="PERMISSION_DENIED",
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
                            organization_role=org_role,
                            invited_by=inviter,
                        )
                        status = "created"

                    # Every org user needs an org membership. A missing row is
                    # written at the granted level and organization_role mirrors
                    # it; an existing active row keeps its level, and a removed
                    # member (inactive row) stays removed and gets None.
                    had_membership = OrganizationMembership._base_manager.filter(
                        user=target_user, organization=org
                    ).exists()
                    org_membership = ensure_org_membership(
                        target_user, org, invited_by=inviter, level=granted_level
                    )
                    if not had_membership and org_membership is not None:
                        mirrored = Level.to_org_string(granted_level)
                        if target_user.organization_role != mirrored:
                            target_user.organization_role = mirrored
                            target_user.save(update_fields=["organization_role"])

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
                            if existing_deleted.organization_membership_id is None:
                                existing_deleted.organization_membership = (
                                    org_membership
                                )
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
                                        "organization_membership": org_membership,
                                    },
                                )
                            )
                            if not created:
                                membership.role = workspace_role
                                if membership.organization_membership_id is None:
                                    membership.organization_membership = org_membership
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
