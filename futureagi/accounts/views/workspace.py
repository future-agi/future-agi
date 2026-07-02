import re
import ssl

import structlog
from django.contrib.auth.tokens import (
    default_token_generator,
)
from django.db import transaction
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    WorkspaceCreateRequestSerializer,
    WorkspaceCreateResponseSerializer,
    WorkspaceDeleteResponseSerializer,
    WorkspaceManagementListResponseSerializer,
    WorkspaceMemberRemoveResponseSerializer,
    WorkspaceMembersAddResponseSerializer,
    WorkspaceMembersListResponseSerializer,
    WorkspaceMembersRequestSerializer,
    WorkspaceUpdateRequestSerializer,
    WorkspaceUpdateResponseSerializer,
)
from accounts.services.workspace_membership import create_workspace_membership
from accounts.utils import generate_password, resolve_org, resolve_org_role
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.settings import settings
from tfc.utils.api_contracts import validated_request
from tfc.utils.email import email_helper
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class WorkspaceManagementView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        responses={
            200: WorkspaceManagementListResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def get(self, request, workspace_id=None, *args, **kwargs):
        """Get workspaces for the current organization"""
        user = request.user
        organization = resolve_org(request)
        if not organization:
            return self._gm.bad_request(
                get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
            )

        # Check permissions: allow org-level OWNER/ADMIN or workspace-level ADMIN
        org_role = resolve_org_role(user, organization)
        has_org_permission = org_role and org_role in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]

        # For GET, workspace admins can see workspaces they're members of
        # This is handled by filtering workspaces they have access to
        if not has_org_permission:
            # User can only see workspaces where they have membership
            user_workspace_ids = WorkspaceMembership.no_workspace_objects.filter(
                user=user, is_active=True
            ).values_list("workspace_id", flat=True)
            workspaces = Workspace.objects.filter(
                id__in=user_workspace_ids, organization=organization, is_active=True
            )
        else:
            # User can see all workspaces in the organization
            workspaces = Workspace.objects.filter(
                organization=organization, is_active=True
            )

        if workspace_id:
            workspaces = workspaces.filter(id=workspace_id)
            if not workspaces.exists():
                return self._gm.not_found("Workspace not found")

        # Apply ordering
        workspaces = workspaces.order_by("-created_at")

        workspace_data = []
        for workspace in workspaces:
            # Get member count for each workspace
            member_count = WorkspaceMembership.no_workspace_objects.filter(
                workspace=workspace, is_active=True
            ).count()

            workspace_data.append(
                {
                    "id": str(workspace.id),
                    "name": workspace.name,
                    "display_name": workspace.display_name,
                    "description": workspace.description,
                    "is_default": workspace.is_default,
                    "member_count": member_count,
                    "created_at": workspace.created_at.strftime("%Y-%m-%d"),
                    "created_by": workspace.created_by.name,
                }
            )

        response = {
            "organization": organization.display_name or organization.name,
            "workspaces": workspace_data,
            "total": len(workspace_data),
        }

        return self._gm.success_response(response)

    @validated_request(
        request_serializer=WorkspaceCreateRequestSerializer,
        responses={201: WorkspaceCreateResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        """Create a new workspace"""
        if kwargs.get("workspace_id"):
            return self._gm.bad_request(
                "Workspace detail POST is not supported; use /accounts/workspaces/ to create a workspace"
            )

        user = request.user
        organization = resolve_org(request)
        if not organization:
            return self._gm.bad_request(
                get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
            )

        # Check permissions: only org-level OWNER/ADMIN can create workspaces
        # Workspace admins cannot create new workspaces
        org_role = resolve_org_role(user, organization)
        has_org_permission = org_role and org_role in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]

        if not has_org_permission:
            return self._gm.forbidden_response(get_error_message("UNAUTHORIZED_ACCESS"))

        workspace_name = request.validated_data.get("name")
        if not workspace_name:
            return self._gm.bad_request("Workspace name is required")

        # Check if workspace already exists
        if Workspace.objects.filter(
            name=workspace_name, organization=organization
        ).exists():
            return self._gm.bad_request("Workspace with this name already exists")

        # Validate emails and role
        emails = request.validated_data.get("emails", [])
        role = request.validated_data.get("role", "")

        if not isinstance(emails, list):
            return self._gm.bad_request("Emails must be a list")

        # Validate email format
        import re as email_re

        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        for email in emails:
            if not email_re.match(email_pattern, email):
                return self._gm.bad_request(f"Invalid email format: {email}")

        # Role is only required when inviting members
        if emails and not role:
            return self._gm.bad_request("Role is required when inviting members")

        # Define organization-level and workspace-level roles
        organization_level_roles = [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
            OrganizationRoles.MEMBER,
            OrganizationRoles.MEMBER_VIEW_ONLY,
        ]
        workspace_level_roles = [
            OrganizationRoles.WORKSPACE_ADMIN,
            OrganizationRoles.WORKSPACE_MEMBER,
            OrganizationRoles.WORKSPACE_VIEWER,
        ]

        # Validate that the role is either organization-level or workspace-level
        if (
            role
            and role not in organization_level_roles
            and role not in workspace_level_roles
        ):
            return self._gm.bad_request(
                f"Invalid role. Must be either an organization-level role ({', '.join([r.value for r in organization_level_roles])}) or a workspace-level role ({', '.join([r.value for r in workspace_level_roles])})"
            )

        try:
            # Create new workspace
            workspace = Workspace.objects.create(
                name=workspace_name,
                display_name=request.validated_data.get("display_name", workspace_name),
                description=request.validated_data.get("description", ""),
                organization=organization,
                created_by=user,
            )

            # Add creator as workspace admin
            org_membership = OrganizationMembership.no_workspace_objects.filter(
                user=user, organization=organization, is_active=True
            ).first()
            create_workspace_membership(
                workspace=workspace,
                user=user,
                role=OrganizationRoles.WORKSPACE_ADMIN,
                level=Level.WORKSPACE_ADMIN,
                organization_membership=org_membership,
                invited_by=user,
            )

            # Add specified users by email with the specified role
            added_users = []
            created_users = []
            failed_users = []
            other_org_users = []
            for email in emails:
                try:
                    # Find user by email in the organization
                    member = User.objects.get(email=email)
                    if not OrganizationMembership.no_workspace_objects.filter(
                        user=member, organization=organization, is_active=True
                    ).exists():
                        raise User.DoesNotExist

                    # Don't create duplicate membership for creator
                    if member != user:
                        member_org_mem = (
                            OrganizationMembership.no_workspace_objects.filter(
                                user=member, organization=organization, is_active=True
                            ).first()
                        )
                        ws_level = Level.STRING_TO_LEVEL.get(str(role))
                        create_workspace_membership(
                            workspace=workspace,
                            user=member,
                            role=role,
                            level=ws_level,
                            organization_membership=member_org_mem,
                            invited_by=user,
                        )
                        added_users.append(email)

                except User.DoesNotExist:
                    # Check if user exists in another organization
                    user_in_other_org = (
                        User.objects.filter(email=email)
                        .exclude(
                            id__in=OrganizationMembership.no_workspace_objects.filter(
                                organization=organization, is_active=True
                            ).values_list("user_id", flat=True)
                        )
                        .first()
                    )

                    if user_in_other_org:
                        other_org_users.append(
                            {
                                "email": email,
                                "error": "User already exists in another organization. Workspace has been created.",
                            }
                        )
                        # return self._gm.bad_request(
                        #     f"User with email {email} already exists in another organization. Workspace has been created."
                        # )
                        # User exists in another organization - invite them to this organization
                        # try:
                        #     from accounts.models.organization_membership import (
                        #         OrganizationMembership,
                        #     )

                        #     # Check if already invited
                        #     existing_invite = OrganizationMembership.objects.filter(
                        #         user=user_in_other_org,
                        #         organization=organization,
                        #         is_active=True,
                        #     ).first()

                        #     if existing_invite:
                        #         # User already invited, add to workspace
                        #         WorkspaceMembership.no_workspace_objects.create(
                        #             workspace=workspace,
                        #             user=user_in_other_org,
                        #             role=role,
                        #             invited_by=user,
                        #         )
                        #         added_users.append(email)
                        #     else:
                        #         # Determine organization role for invitation
                        #         if role in organization_level_roles:
                        #             org_role = role
                        #         else:
                        #             # Role is workspace-level, don't set organization role
                        #             # User will only have workspace-level access
                        #             org_role = None

                        #         # Create invitation to organization
                        #         OrganizationMembership.objects.create(
                        #             user=user_in_other_org,
                        #             organization=organization,
                        #             role=org_role,
                        #             invited_by=user,
                        #             is_active=True,
                        #         )

                        #         # Add user to workspace
                        #         WorkspaceMembership.no_workspace_objects.create(
                        #             workspace=workspace,
                        #             user=user_in_other_org,
                        #             role=role,
                        #             invited_by=user,
                        #         )
                        #         added_users.append(email)

                        #         # Send invitation email to existing user
                        #         ssl_context = ssl.create_default_context()
                        #         email_helper(
                        #             f"You are invited to join {organization.display_name if organization.display_name else organization.name} - Future AGI",
                        #             "existing_user_invite.html",
                        #             {
                        #                 "org_name": organization.display_name
                        #                 or organization.name,
                        #                 "workspace_name": workspace.name,
                        #                 "invited_by": user.name,
                        #                 "app_url": settings.APP_URL,
                        #                 "ssl": ssl_context,
                        #             },
                        #             [email],
                        #         )

                        # except Exception as e:
                        #     # Log error but continue with other users
                        #     print(f"Failed to invite existing user {email}: {str(e)}")
                        #     continue
                    else:
                        # Create new user for this organization
                        try:
                            # Extract name from email (username part)
                            username = email.split("@")[0]
                            # Remove numbers and special characters, capitalize first letter of each word
                            name = re.sub(r"[0-9._-]+", " ", username)
                            name = " ".join(word.capitalize() for word in name.split())

                            # Generate password for new user
                            password = generate_password()

                            # Determine organization role
                            if role in organization_level_roles:
                                # Role is organization-level, use it for org role
                                org_role = role
                            else:
                                # Role is workspace-level, don't set organization role
                                # User will only have workspace-level access
                                org_role = None

                            # Create new user
                            new_member = User.objects.create(
                                email=email,
                                name=name,
                                organization=organization,
                                organization_role=org_role,
                                is_active=False,  # User needs to activate account
                                invited_by=user,
                                config={"defaultWorkspaceId": str(workspace.id)},
                            )

                            # Set password
                            new_member.set_password(password)
                            new_member.save()

                            # Add user to workspace
                            create_workspace_membership(
                                workspace=workspace,
                                user=new_member,
                                role=role,
                                invited_by=user,
                            )

                            uidb64 = urlsafe_base64_encode(force_bytes(new_member.pk))
                            token = default_token_generator.make_token(new_member)

                            # Send invitation email with credentials
                            ssl_context = ssl.create_default_context()
                            email_helper(
                                f"You are invited to join {organization.display_name if organization.display_name else organization.name} - Future AGI",
                                "member_invite.html",
                                {
                                    "org_name": organization.display_name
                                    or organization.name,
                                    "workspace_name": workspace.name,
                                    "invited_by": user.name,
                                    "email": email,
                                    "password": password,
                                    "app_url": settings.APP_URL,
                                    "ssl": ssl_context,
                                    "uid": str(uidb64),
                                    "token": token,
                                },
                                [email],
                            )

                            created_users.append(email)
                            added_users.append(email)

                        except Exception as e:
                            # Log error but continue with other users
                            logger.exception(
                                f"Failed to create new user {email}: {str(e)}"
                            )
                            failed_users.append({"email": email, "error": str(e)})
                            continue

            response_data = {
                "workspace": {
                    "id": str(workspace.id),
                    "name": workspace.name,
                    "display_name": workspace.display_name,
                    "description": workspace.description,
                },
                "message": "Workspace created successfully",
                "added_users": added_users,
                "created_users": created_users,
                "total_users_added": len(added_users),
                "total_users_created": len(created_users),
                "failed_users": failed_users,
                "other_org_users": other_org_users,
            }

            return self._gm.create_response(response_data)

        except Exception as e:
            logger.exception(f"Failed to create workspace: {str(e)}")
            return self._gm.bad_request("Failed to create workspace")

    @validated_request(
        request_serializer=WorkspaceUpdateRequestSerializer,
        responses={200: WorkspaceUpdateResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def put(self, request, workspace_id=None, *args, **kwargs):
        """Update workspace details"""
        if not workspace_id:
            return self._gm.bad_request("workspace_id is required")

        user = request.user
        organization = resolve_org(request)
        if not organization:
            return self._gm.bad_request(
                get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
            )

        try:
            workspace = Workspace.objects.get(
                id=workspace_id, organization=organization, is_active=True
            )
        except Workspace.DoesNotExist:
            return self._gm.not_found("Workspace not found")

        # Check permissions: allow org-level OWNER/ADMIN or workspace-level ADMIN
        org_role = resolve_org_role(user, organization)
        has_org_permission = org_role and org_role in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]

        has_workspace_permission = False
        if not has_org_permission:
            try:
                workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                    workspace=workspace, user=user, is_active=True
                )
                has_workspace_permission = (
                    workspace_membership.role == OrganizationRoles.WORKSPACE_ADMIN
                )
            except WorkspaceMembership.DoesNotExist:
                pass

        if not (has_org_permission or has_workspace_permission):
            return self._gm.forbidden_response(get_error_message("UNAUTHORIZED_ACCESS"))

        # Update workspace fields
        if "display_name" in request.validated_data:
            workspace.display_name = request.validated_data["display_name"]
        if "description" in request.validated_data:
            workspace.description = request.validated_data["description"]

        workspace.save()

        response_data = {
            "workspace": {
                "id": str(workspace.id),
                "name": workspace.name,
                "display_name": workspace.display_name,
                "description": workspace.description,
            },
            "message": "Workspace updated successfully",
        }

        return self._gm.success_response(response_data)

    @validated_request(
        responses={200: WorkspaceDeleteResponseSerializer, **ACCOUNTS_ERROR_RESPONSES}
    )
    def delete(self, request, workspace_id=None, *args, **kwargs):
        """Delete a workspace"""
        if not workspace_id:
            return self._gm.bad_request("workspace_id is required")

        user = request.user
        organization = resolve_org(request)
        org_role = resolve_org_role(user, organization) if organization else None
        if not org_role or org_role != OrganizationRoles.OWNER:
            return self._gm.forbidden_response(
                get_error_message("ONLY_OWNER_CAN_DELETE_WORKSPACES")
            )

        if not organization:
            return self._gm.bad_request(
                get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
            )

        try:
            workspace = Workspace.objects.get(
                id=workspace_id, organization=organization, is_active=True
            )
        except Workspace.DoesNotExist:
            return self._gm.not_found("Workspace not found")

        # Prevent deletion of default workspace
        if workspace.is_default:
            return self._gm.bad_request("Cannot delete default workspace")

        # Soft delete workspace
        workspace.is_active = False
        workspace.save()

        # Remove all workspace memberships
        WorkspaceMembership.no_workspace_objects.filter(workspace=workspace).update(
            is_active=False
        )

        return self._gm.success_response({"message": "Workspace deleted successfully"})


class WorkspaceMembershipView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        responses={
            200: WorkspaceMembersListResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def get(self, request, workspace_id, member_id=None, *args, **kwargs):
        """Get members of a specific workspace"""
        user = request.user
        organization = resolve_org(request)
        if not organization:
            return self._gm.bad_request(
                get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
            )

        try:
            workspace = Workspace.objects.get(
                id=workspace_id, organization=organization, is_active=True
            )
        except Workspace.DoesNotExist:
            return self._gm.not_found("Workspace not found")

        # Check permissions: allow org-level OWNER/ADMIN or workspace-level ADMIN
        org_role = resolve_org_role(user, organization)
        has_org_permission = org_role and org_role in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]

        has_workspace_permission = False
        if not has_org_permission:
            try:
                workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                    workspace=workspace, user=user, is_active=True
                )
                has_workspace_permission = (
                    workspace_membership.role == OrganizationRoles.WORKSPACE_ADMIN
                )
            except WorkspaceMembership.DoesNotExist:
                pass

        if not (has_org_permission or has_workspace_permission):
            return self._gm.forbidden_response(get_error_message("UNAUTHORIZED_ACCESS"))

        # Get workspace members
        memberships = WorkspaceMembership.no_workspace_objects.filter(
            workspace=workspace, is_active=True
        ).select_related("user")
        if member_id:
            memberships = memberships.filter(user_id=member_id)
            if not memberships.exists():
                return self._gm.not_found("User not found in workspace")

        members_data = []
        for membership in memberships:
            members_data.append(
                {
                    "user_id": str(membership.user.id),
                    "email": membership.user.email,
                    "name": membership.user.name,
                    "role": membership.role,
                    "joined_at": membership.created_at.strftime("%Y-%m-%d"),
                    "invited_by": (
                        membership.invited_by.name if membership.invited_by else None
                    ),
                }
            )

        response = {
            "workspace": {
                "id": str(workspace.id),
                "name": workspace.name,
                "display_name": workspace.display_name,
            },
            "members": members_data,
            "total": len(members_data),
        }

        return self._gm.success_response(response)

    @validated_request(
        request_serializer=WorkspaceMembersRequestSerializer,
        responses={
            201: WorkspaceMembersAddResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @transaction.atomic
    def post(self, request, workspace_id, *args, **kwargs):
        """Add users to workspace"""
        if kwargs.get("member_id"):
            return self._gm.bad_request(
                "Workspace member detail POST is not supported; use /members/ to add users"
            )

        user = request.user
        organization = resolve_org(request)
        if not organization:
            return self._gm.bad_request(
                get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
            )

        try:
            workspace = Workspace.objects.get(
                id=workspace_id, organization=organization, is_active=True
            )
        except Workspace.DoesNotExist:
            return self._gm.not_found("Workspace not found")

        # Check permissions: allow org-level OWNER/ADMIN or workspace-level ADMIN
        org_role = resolve_org_role(user, organization)
        has_org_permission = org_role and org_role in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]

        has_workspace_permission = False
        if not has_org_permission:
            try:
                workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                    workspace=workspace, user=user, is_active=True
                )
                has_workspace_permission = (
                    workspace_membership.role == OrganizationRoles.WORKSPACE_ADMIN
                )
            except WorkspaceMembership.DoesNotExist:
                pass

        if not (has_org_permission or has_workspace_permission):
            return self._gm.forbidden_response(get_error_message("UNAUTHORIZED_ACCESS"))

        users_data = request.validated_data.get("users", [])
        if not isinstance(users_data, list):
            return self._gm.bad_request("Users data must be a list")

        # Define organization-level and workspace-level roles
        organization_level_roles = [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
            OrganizationRoles.MEMBER,
            OrganizationRoles.MEMBER_VIEW_ONLY,
        ]
        added_users = []
        errors = []

        for user_data in users_data:
            user_email = user_data.get("email", "").lower()
            user_role = user_data.get("role", OrganizationRoles.WORKSPACE_MEMBER)

            if not user_email:
                errors.append({"email": user_email, "error": "Email is required"})
                continue

            try:
                # Find user in organization
                target_user = User.objects.filter(
                    email=user_email, organization=organization
                ).first()

                if not target_user:
                    # Check if user is invited to organization
                    from accounts.models.organization_membership import (
                        OrganizationMembership,
                    )

                    org_membership = OrganizationMembership.objects.filter(
                        user__email=user_email,
                        organization=organization,
                        is_active=True,
                    ).first()

                    if org_membership:
                        target_user = org_membership.user
                    else:
                        # Check if user exists in another organization
                        user_in_other_org = (
                            User.objects.filter(email=user_email, is_active=True)
                            .exclude(organization=organization)
                            .first()
                        )

                        if user_in_other_org:
                            return self._gm.bad_request(
                                "User already exists in another organization"
                            )
                            # User exists in another organization - invite them to this organization
                            try:
                                # Check if already invited
                                existing_invite = OrganizationMembership.objects.filter(
                                    user=user_in_other_org,
                                    organization=organization,
                                    is_active=True,
                                ).first()

                                if not existing_invite:
                                    # Determine organization role for invitation
                                    if user_role in organization_level_roles:
                                        org_role = user_role
                                    else:
                                        # Role is workspace-level, don't set organization role
                                        # User will only have workspace-level access
                                        org_role = None

                                    # Create invitation to organization
                                    OrganizationMembership.objects.create(
                                        user=user_in_other_org,
                                        organization=organization,
                                        role=org_role,
                                        invited_by=user,
                                        is_active=True,
                                    )

                                target_user = user_in_other_org

                                # Send invitation email to existing user
                                ssl_context = ssl.create_default_context()
                                email_helper(
                                    f"You are invited to join {organization.display_name if organization.display_name else organization.name} - Future AGI",
                                    "existing_user_invite.html",
                                    {
                                        "org_name": organization.display_name
                                        or organization.name,
                                        "workspace_name": workspace.name,
                                        "invited_by": user.name,
                                        "app_url": settings.APP_URL,
                                        "ssl": ssl_context,
                                    },
                                    [user_email],
                                )
                            except Exception as e:
                                errors.append(
                                    {
                                        "email": user_email,
                                        "error": f"Failed to invite existing user: {str(e)}",
                                    }
                                )
                                continue
                        else:
                            # Create new user for this organization
                            try:
                                # Extract name from email (username part)
                                username = user_email.split("@")[0]
                                # Remove numbers and special characters, capitalize first letter of each word
                                name = re.sub(r"[0-9._-]+", " ", username)
                                name = " ".join(
                                    word.capitalize() for word in name.split()
                                )

                                # Generate password for new user
                                password = generate_password()

                                # Determine organization role
                                if user_role in organization_level_roles:
                                    # Role is organization-level, use it for org role
                                    org_role = user_role
                                else:
                                    # Role is workspace-level, don't set organization role
                                    # User will only have workspace-level access
                                    org_role = None

                                # Create new user
                                target_user = User.objects.create(
                                    email=user_email,
                                    name=name,
                                    organization=organization,
                                    invited_by=user,
                                    organization_role=org_role,
                                    is_active=False,  # User needs to activate account
                                    config={"defaultWorkspaceId": str(workspace.id)},
                                )

                                # Set password
                                target_user.set_password(password)
                                target_user.save()

                                uidb64 = urlsafe_base64_encode(
                                    force_bytes(target_user.pk)
                                )
                                token = default_token_generator.make_token(target_user)

                                # Send invitation email with credentials
                                ssl_context = ssl.create_default_context()
                                email_helper(
                                    f"You are invited to join {organization.display_name if organization.display_name else organization.name} - Future AGI",
                                    "member_invite.html",
                                    {
                                        "org_name": organization.display_name
                                        or organization.name,
                                        "workspace_name": workspace.name,
                                        "invited_by": user.name,
                                        "email": user_email,
                                        "password": password,
                                        "app_url": settings.APP_URL,
                                        "ssl": ssl_context,
                                        "uid": str(uidb64),
                                        "token": token,
                                    },
                                    [user_email],
                                )

                            except Exception as e:
                                errors.append(
                                    {
                                        "email": user_email,
                                        "error": f"Failed to create new user: {str(e)}",
                                    }
                                )
                                continue

                # Check if user is already in workspace
                existing_membership = WorkspaceMembership.no_workspace_objects.filter(
                    workspace=workspace, user=target_user, is_active=True
                ).first()

                if existing_membership:
                    # Update role if different
                    if existing_membership.role != user_role:
                        existing_membership.role = user_role
                        existing_membership.save()
                        added_users.append(
                            {
                                "email": target_user.email,
                                "name": target_user.name,
                                "role": user_role,
                                "action": "role_updated",
                            }
                        )
                    else:
                        added_users.append(
                            {
                                "email": target_user.email,
                                "name": target_user.name,
                                "role": user_role,
                                "action": "already_member",
                            }
                        )
                else:
                    # Add user to workspace
                    create_workspace_membership(
                        workspace=workspace,
                        user=target_user,
                        role=user_role,
                        invited_by=user,
                    )
                    added_users.append(
                        {
                            "email": target_user.email,
                            "name": target_user.name,
                            "role": user_role,
                            "action": "added",
                        }
                    )

            except Exception as e:
                errors.append(
                    {"email": user_email, "error": f"Failed to add user: {str(e)}"}
                )

        response_data = {
            "workspace": {"id": str(workspace.id), "name": workspace.name},
            "added_users": added_users,
        }

        if errors:
            response_data["errors"] = errors
            return self._gm.bad_request(response_data)

        return self._gm.create_response(response_data)

    @validated_request(
        responses={
            200: WorkspaceMemberRemoveResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def delete(self, request, workspace_id, member_id=None, *args, **kwargs):
        """Remove user from workspace"""
        if not member_id:
            return self._gm.bad_request("member_id is required")

        user = request.user
        organization = resolve_org(request)
        if not organization:
            return self._gm.bad_request(
                get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
            )

        try:
            workspace = Workspace.objects.get(
                id=workspace_id, organization=organization, is_active=True
            )
        except Workspace.DoesNotExist:
            return self._gm.not_found("Workspace not found")

        # Check permissions: allow org-level OWNER/ADMIN or workspace-level ADMIN
        org_role = resolve_org_role(user, organization)
        has_org_permission = org_role and org_role in [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
        ]

        has_workspace_permission = False
        if not has_org_permission:
            try:
                workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                    workspace=workspace, user=user, is_active=True
                )
                has_workspace_permission = (
                    workspace_membership.role == OrganizationRoles.WORKSPACE_ADMIN
                )
            except WorkspaceMembership.DoesNotExist:
                pass

        if not (has_org_permission or has_workspace_permission):
            return self._gm.forbidden_response(get_error_message("UNAUTHORIZED_ACCESS"))

        try:
            membership = WorkspaceMembership.no_workspace_objects.get(
                workspace=workspace, user_id=member_id, is_active=True
            )
        except WorkspaceMembership.DoesNotExist:
            return self._gm.not_found("User not found in workspace")

        # Prevent removing the last admin
        if membership.role == OrganizationRoles.WORKSPACE_ADMIN:
            admin_count = WorkspaceMembership.no_workspace_objects.filter(
                workspace=workspace,
                role=OrganizationRoles.WORKSPACE_ADMIN,
                is_active=True,
            ).count()
            if admin_count <= 1:
                return self._gm.bad_request(
                    "Cannot remove the last admin from workspace"
                )

        # Soft delete membership
        membership.is_active = False
        membership.save()

        return self._gm.success_response(
            {"message": "User removed from workspace successfully"}
        )
