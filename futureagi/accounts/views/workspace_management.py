import json
import re
import traceback
from datetime import datetime

import structlog
from django.conf import settings as django_settings
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import (
    Case,
    CharField,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest
from django.shortcuts import get_object_or_404
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import OrganizationRoles, Workspace, WorkspaceMembership
from accounts.serializers.user import CreateMemberSerializer, UserSerializer
from accounts.serializers.workspace import (
    DeactivateUserSerializer,
    DeleteUserSerializer,
    ResendInviteSerializer,
    SwitchWorkspaceSerializer,
    UserListSerializer,
    UserRoleUpdateSerializer,
    WorkspaceInviteSerializer,
    WorkspaceListSerializer,
)
from accounts.utils import generate_password, resolve_org, resolve_org_role
from tfc.middleware.workspace_context import get_current_workspace

logger = structlog.get_logger(__name__)
from analytics.mixpanel_util import mixpanel_tracker
from analytics.utils import (
    MixpanelEvents,
    MixpanelModes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from tfc.constants.levels import Level
from tfc.constants.roles import RoleMapping, RolePermissions
from tfc.permissions.utils import can_invite_at_level
from tfc.settings import settings
from tfc.settings.settings import ssl
from tfc.utils.email import email_helper
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tfc.utils.parse_errors import parse_serialized_errors

try:
    from ee.usage.models.usage import (
        APICallStatusChoices,
        APICallTypeChoices,
        OrganizationSubscription,
        SubscriptionTierChoices,
    )
except ImportError:
    from futureagi._ee_stubs import APICallStatusChoices, APICallTypeChoices, OrganizationSubscription, SubscriptionTierChoices
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_resource_request
except ImportError:
    from futureagi._ee_stubs import log_and_deduct_cost_for_resource_request


def clear_user_redis_cache(user_id):
    """
    Clear all Redis cache entries for a user (access tokens and refresh tokens)
    This ensures immediate logout when user is deleted or deactivated
    """
    try:
        # Get all active tokens for the user
        user_tokens = AuthToken.objects.filter(user_id=user_id, is_active=True)

        # Clear access token cache entries
        for token in user_tokens.filter(auth_type=AuthTokenType.ACCESS.value):
            cache_key = f"access_token_{str(token.id)}"
            cache.delete(cache_key)
            logger.info(f"Cleared access token cache: {cache_key}")

        # Clear refresh token cache entries
        for token in user_tokens.filter(auth_type=AuthTokenType.REFRESH.value):
            cache_key = f"refresh_token_{str(token.id)}"
            cache.delete(cache_key)
            logger.info(f"Cleared refresh token cache: {cache_key}")

        # Clear any other potential user-related cache entries
        # This is a safety measure to catch any other cached user data
        try:
            # Clear any user-specific cache keys that might exist
            user_cache_patterns = [
                f"user_{user_id}",
                f"user_profile_{user_id}",
                f"user_org_{user_id}",
                f"user_workspace_{user_id}",
            ]
            for pattern in user_cache_patterns:
                cache.delete(pattern)
                logger.info(f"Cleared potential user cache: {pattern}")
        except Exception as cache_error:
            logger.warning(
                f"Error clearing additional user cache patterns: {str(cache_error)}"
            )

        # Deactivate all tokens in database
        user_tokens.update(is_active=False)

        logger.info(f"Cleared Redis cache for user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Error clearing Redis cache for user {user_id}: {str(e)}")
        return False


class WorkspaceListAPIView(APIView):
    """API for getting paginated list of workspaces"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    pagination_class = ExtendedPageNumberPagination

    def get(self, request):
        """Get paginated list of workspaces"""
        try:
            # Get query parameters instead of request data
            search_query = request.query_params.get("search", "")
            sort_params = request.query_params.getlist("sort", [])

            user = request.user
            organization = resolve_org(request)

            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            # Check if user has global workspace access (Owner, Admin roles)
            if user.has_global_workspace_access(organization):
                # User can see all workspaces in the organization
                workspaces = Workspace.objects.filter(
                    organization=organization, is_active=True
                )
            else:
                # User can only see workspaces where they have membership
                user_workspace_ids = WorkspaceMembership.no_workspace_objects.filter(
                    user=user, is_active=True
                ).values_list("workspace_id", flat=True)

                workspaces = Workspace.objects.filter(
                    id__in=user_workspace_ids, organization=organization, is_active=True
                )

            # Apply search filter
            if search_query:
                workspaces = workspaces.filter(
                    Q(name__icontains=search_query)
                    | Q(display_name__icontains=search_query)
                )

            # Apply sorting
            ALLOWED_WS_SORT_FIELDS = {"name", "display_name", "created_at"}
            if sort_params:
                for sort_field in sort_params:
                    if sort_field.startswith("-"):
                        field = sort_field[1:]
                        if field in ALLOWED_WS_SORT_FIELDS:
                            workspaces = workspaces.order_by(f"-{field}")
                    else:
                        if sort_field in ALLOWED_WS_SORT_FIELDS:
                            workspaces = workspaces.order_by(sort_field)
            else:
                # Default sorting
                workspaces = workspaces.order_by("-created_at")

            # Use default pagination
            paginator = self.pagination_class()
            paginated_workspaces = paginator.paginate_queryset(workspaces, request)

            # Serialize results
            serializer = WorkspaceListSerializer(paginated_workspaces, many=True)
            data = serializer.data

            # Enrich with user's workspace level for each workspace
            from tfc.constants.levels import Level

            org_membership = OrganizationMembership.objects.filter(
                user=user, organization=organization, is_active=True
            ).first()
            org_level = org_membership.level_or_legacy if org_membership else 0
            ws_memberships = {
                str(wm.workspace_id): wm
                for wm in WorkspaceMembership.no_workspace_objects.filter(
                    user=user,
                    workspace__in=[w["id"] for w in data],
                    is_active=True,
                )
            }

            if org_level and org_level >= Level.ADMIN:
                # Org Admin+ auto-have WS Admin in all workspaces
                for ws_data in data:
                    ws_data["user_ws_level"] = Level.WORKSPACE_ADMIN
                    ws_data["user_ws_role"] = "Workspace Admin"
            else:
                # Look up actual workspace memberships
                for ws_data in data:
                    wm = ws_memberships.get(str(ws_data["id"]))
                    if wm:
                        level = wm.level_or_legacy
                        ws_data["user_ws_level"] = level
                        ws_data["user_ws_role"] = Level.to_ws_string(level)
                    else:
                        ws_data["user_ws_level"] = None
                        ws_data["user_ws_role"] = None

            return paginator.get_paginated_response(data)

        except Exception as e:
            logger.exception(f"Error while fetching workspace list: {str(e)}")
            return self._gm.bad_request("Error while fetching workspace list")


class WorkspaceInviteAPIView(APIView):
    """API for inviting users to workspaces"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @transaction.atomic
    def post(self, request):
        """Invite users to workspaces"""
        try:
            # Validate request data
            serializer = WorkspaceInviteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data

            user = request.user
            organization = resolve_org(request)

            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            emails = data["emails"]
            role = data["role"]
            select_all = data.get("select_all", False)
            workspace_ids = data.get("workspace_ids", [])

            # Validate at least one email provided
            if not emails:
                return self._gm.bad_request("At least one email is required")

            # Validate email format
            email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            for email in emails:
                if not re.match(email_pattern, email):
                    return self._gm.bad_request(f"Invalid email format: {email}")

            # Determine which workspaces to include based on select_all logic
            if select_all:
                # Include all workspaces except those in workspace_ids (excluded workspaces)
                all_workspaces = Workspace.objects.filter(
                    organization=organization, is_active=True
                )
                if workspace_ids:
                    # Exclude the specified workspaces
                    workspaces = all_workspaces.exclude(id__in=workspace_ids)
                else:
                    # Include all workspaces (no exclusions)
                    workspaces = all_workspaces
            else:
                # Include only the specified workspaces
                if not workspace_ids:
                    return self._gm.bad_request(
                        "workspace_ids is required when select_all is False"
                    )

                workspaces = Workspace.objects.filter(
                    id__in=workspace_ids, organization=organization, is_active=True
                )

                if len(workspaces) != len(workspace_ids):
                    return self._gm.bad_request(
                        "Some workspaces not found or don't belong to your organization"
                    )

            if not workspaces.exists():
                return self._gm.bad_request("No valid workspaces found for invitation")

            # Check permissions: allow org-level OWNER/ADMIN or workspace-level ADMIN for all target workspaces
            org_role = resolve_org_role(user, organization)
            has_org_permission = org_role and org_role in [
                OrganizationRoles.OWNER,
                OrganizationRoles.ADMIN,
            ]

            has_workspace_permission = False
            if not has_org_permission:
                # Check if user is workspace admin for ALL target workspaces
                workspace_ids_list = list(workspaces.values_list("id", flat=True))
                user_workspace_admins = WorkspaceMembership.no_workspace_objects.filter(
                    workspace_id__in=workspace_ids_list,
                    user=user,
                    role=OrganizationRoles.WORKSPACE_ADMIN,
                    is_active=True,
                ).values_list("workspace_id", flat=True)

                # User must be admin for all workspaces they're trying to invite to
                if len(user_workspace_admins) == len(workspace_ids_list):
                    has_workspace_permission = True

            if not (has_org_permission or has_workspace_permission):
                return self._gm.forbidden_response(
                    get_error_message("UNAUTHORIZED_ACCESS")
                )

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

            # Restrict workspace admins from assigning organization-level roles
            # Only org owners/admins can assign organization-level roles
            if role in organization_level_roles and not has_org_permission:
                return self._gm.forbidden_response(
                    "Only organization owners or admins can assign organization-level roles (owner, admin, member, member_view_only)"
                )

            # Enforce invite level rule: actor can only invite at or below own level
            if role in organization_level_roles and has_org_permission:
                actor_level = Level.from_string(org_role)
                target_level = Level.from_string(role)
                if not can_invite_at_level(actor_level, target_level):
                    return self._gm.forbidden_response(
                        get_error_message("INVITE_LEVEL_FORBIDDEN")
                    )

            # Validate that the role is either organization-level or workspace-level
            if (
                role not in organization_level_roles
                and role not in workspace_level_roles
            ):
                return self._gm.bad_request(
                    f"Invalid role. Must be either an organization-level role ({', '.join([r.value for r in organization_level_roles])}) or a workspace-level role ({', '.join([r.value for r in workspace_level_roles])})"
                )

            # Determine workspace role: if role is org-level, map to workspace role; otherwise use role as-is
            if role in organization_level_roles:
                # Map organization role to workspace role
                from tfc.constants.roles import RoleMapping

                workspace_role = RoleMapping.get_workspace_role(role)
            else:
                # Role is already a workspace-level role
                workspace_role = role

            results = []
            errors = []
            added_users = []

            for email in emails:
                email = email.lower()

                # Check if user exists in organization
                try:
                    target_user = User.objects.get(
                        email=email, organization=organization
                    )

                    # Reactivate org membership if it was deactivated
                    # (e.g. user was deactivated then re-invited).
                    # Without this, login returns requires_org_setup=True.
                    org_mem = OrganizationMembership.all_objects.filter(
                        user=target_user, organization=organization
                    ).first()
                    if org_mem and not org_mem.is_active:
                        org_mem.is_active = True
                        org_mem.deleted = False
                        org_mem.deleted_at = None
                        org_mem.invited_by = user
                        if role in organization_level_roles:
                            org_mem.role = role
                            org_mem.level = Level.from_string(role)
                        org_mem.save()

                    # Add user to workspaces
                    for workspace in workspaces:
                        # Check if there's a soft-deleted membership that we should reactivate
                        existing_deleted_membership = (
                            WorkspaceMembership.all_objects.filter(
                                workspace=workspace,
                                user=target_user,
                                deleted=True,
                            ).first()
                        )

                        if existing_deleted_membership:
                            # Reactivate the soft-deleted membership
                            existing_deleted_membership.deleted = False
                            existing_deleted_membership.is_active = True
                            existing_deleted_membership.role = workspace_role
                            existing_deleted_membership.invited_by = user
                            existing_deleted_membership.save()
                            created = False
                            membership = existing_deleted_membership
                        else:
                            # No soft-deleted membership exists, create or get existing active one
                            membership, created = (
                                WorkspaceMembership.no_workspace_objects.get_or_create(
                                    workspace=workspace,
                                    user=target_user,
                                    defaults={
                                        "role": workspace_role,
                                        "invited_by": user,
                                        "is_active": True,
                                    },
                                )
                            )

                            if not created:
                                # Update existing membership
                                membership.role = workspace_role
                                membership.save()

                    results.append(
                        {
                            "email": email,
                            "status": "added" if created else "updated",
                            "workspaces": [str(w.id) for w in workspaces],
                            "select_all": select_all,
                            "total_workspaces": workspaces.count(),
                        }
                    )

                except User.DoesNotExist:
                    # User doesn't exist in organization
                    # Check if user exists in another organization
                    user_in_other_org = (
                        User.objects.filter(email=email, is_active=True)
                        .exclude(organization=organization)
                        .first()
                    )

                    if user_in_other_org:
                        return self._gm.bad_request(
                            "User already exists in another organization"
                        )
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

                            # Use first workspace as default workspace for user config
                            first_workspace = workspaces.first()
                            if not first_workspace:
                                errors.append(
                                    {
                                        "email": email,
                                        "error": "No valid workspace found for invitation",
                                    }
                                )
                                continue

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
                                config={"defaultWorkspaceId": str(first_workspace.id)},
                            )

                            # Set password
                            new_member.set_password(password)
                            new_member.save()

                            # Add user to ALL selected workspaces
                            for workspace in workspaces:
                                # Check if there's a soft-deleted membership first (unlikely for new user, but handle it)
                                existing_deleted_membership = (
                                    WorkspaceMembership.all_objects.filter(
                                        workspace=workspace,
                                        user=new_member,
                                        deleted=True,
                                    ).first()
                                )

                                if existing_deleted_membership:
                                    # Reactivate the soft-deleted membership
                                    existing_deleted_membership.deleted = False
                                    existing_deleted_membership.is_active = True
                                    existing_deleted_membership.role = workspace_role
                                    existing_deleted_membership.invited_by = user
                                    existing_deleted_membership.save()
                                else:
                                    WorkspaceMembership.no_workspace_objects.create(
                                        workspace=workspace,
                                        user=new_member,
                                        role=workspace_role,
                                        invited_by=user,
                                    )

                            # Send invitation email with credentials
                            # ssl_context = ssl.create_default_context()
                            uidb64 = urlsafe_base64_encode(force_bytes(new_member.pk))
                            token = default_token_generator.make_token(new_member)
                            email_helper(
                                f"You are invited to join {organization.display_name if organization.display_name else organization.name} - Future AGI",
                                "member_invite.html",
                                {
                                    "org_name": organization.display_name
                                    or organization.name,
                                    "workspace_name": first_workspace.name,
                                    "invited_by": user.name,
                                    "email": email,
                                    "password": password,
                                    "app_url": settings.APP_URL,
                                    "ssl": ssl,
                                    "uid": str(uidb64),
                                    "token": token,
                                },
                                [email],
                            )

                            results.append(
                                {
                                    "email": email,
                                    "status": "invited",
                                    "workspaces": [str(w.id) for w in workspaces],
                                    "select_all": select_all,
                                    "total_workspaces": workspaces.count(),
                                }
                            )

                        except Exception as e:
                            # Log error but continue with other users
                            logger.exception(
                                f"Failed to create new user {email}: {str(e)}"
                            )
                            continue

            response_data = {
                "results": results,
                "total_invited": len(results),
                "select_all": select_all,
                "total_workspaces": workspaces.count(),
            }

            if errors:
                response_data["errors"] = errors
                return self._gm.bad_request(response_data)

            return self._gm.success_response(response_data)

        except Exception as e:
            logger.exception(f"Error in inviting users to workspace: {str(e)}")
            return self._gm.bad_request("Error in inviting users to workspace")


class UserListAPIView(APIView):
    """API for getting paginated list of users with filtering at workspace level"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    pagination_class = ExtendedPageNumberPagination

    def get(self, request):
        """Get paginated list of users with filtering at workspace level"""
        try:
            # Get query parameters instead of request data
            search_query = request.query_params.get("search", "")
            filter_status = request.query_params.get(
                "filter_status", []
            ) or request.query_params.get("filterStatus", [])
            filter_role = request.query_params.get(
                "filter_role", []
            ) or request.query_params.get("filterRole", [])
            sort_params_raw = request.query_params.get("sort", "")

            if filter_status:
                filter_status = json.loads(filter_status)
            if filter_role:
                filter_role = json.loads(filter_role)
            sort_params = None
            if sort_params_raw:
                try:
                    sort_params = json.loads(sort_params_raw)
                except Exception:
                    sort_params = None

            user = request.user
            organization = resolve_org(request)

            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            # Resolve workspace context: prefer explicit query param, else request-global, else org default
            workspace_id = request.query_params.get(
                "workspace_id"
            ) or request.query_params.get("workspaceId")
            current_workspace = None
            if workspace_id:
                try:
                    current_workspace = Workspace.objects.get(
                        id=workspace_id, organization=organization, is_active=True
                    )
                except Workspace.DoesNotExist:
                    return self._gm.bad_request(
                        "Invalid workspace_id or workspace not in your organization"
                    )
            else:
                current_workspace = get_current_workspace()
                if not current_workspace:
                    try:
                        current_workspace = Workspace.objects.get(
                            organization=organization, is_default=True, is_active=True
                        )
                    except Workspace.DoesNotExist:
                        return self._gm.bad_request("No workspace context available")

            # Check permissions: allow org-level OWNER/ADMIN or workspace-level ADMIN
            org_role = resolve_org_role(user, organization)
            has_org_permission = org_role and org_role in [
                OrganizationRoles.OWNER,
                OrganizationRoles.ADMIN,
                OrganizationRoles.MEMBER,
            ]

            has_workspace_permission = False
            if current_workspace and not has_org_permission:
                try:
                    workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                        workspace=current_workspace, user=user, is_active=True
                    )
                    has_workspace_permission = (
                        workspace_membership.role == OrganizationRoles.WORKSPACE_ADMIN
                    )
                except WorkspaceMembership.DoesNotExist:
                    pass

            if not (has_org_permission or has_workspace_permission):
                return self._gm.forbidden_response(
                    get_error_message("UNAUTHORIZED_ACCESS")
                )

            # Build base queryset: include users who either
            # - are explicit members of the current workspace, or
            # - have an organization-level role implying workspace access (Owner/Admin/Member/Viewer)
            allowed_org_roles = [
                OrganizationRoles.OWNER,
                OrganizationRoles.ADMIN,
                OrganizationRoles.MEMBER,
                OrganizationRoles.MEMBER_VIEW_ONLY,
            ]

            org_user_ids = OrganizationMembership.no_workspace_objects.filter(
                organization=organization, is_active=True
            ).values_list("user_id", flat=True)
            users = User.objects.filter(id__in=org_user_ids).filter(
                Q(
                    id__in=WorkspaceMembership.no_workspace_objects.filter(
                        workspace=current_workspace, is_active=True
                    ).values_list("user_id", flat=True)
                )
                | Q(organization_role__in=allowed_org_roles)
            )

            # Apply search filter
            if search_query:
                users = users.filter(
                    Q(name__icontains=search_query) | Q(email__icontains=search_query)
                )

            # Apply status filter (supports: All status, Active, Inactive, Request pending, Request expired)
            if filter_status:
                normalized_statuses = {
                    str(s).strip().lower() for s in filter_status if str(s).strip()
                }
                if "all status" not in normalized_statuses:
                    status_q = Q()
                    # Active users
                    if "active" in normalized_statuses:
                        status_q |= Q(is_active=True)
                    # Inactive users (not invited or invitation context not applicable)
                    if "inactive" in normalized_statuses:
                        status_q |= Q(is_active=False, invited_by__isnull=True)
                    # Request Pending users (invited but not activated)
                    if (
                        "request pending" in normalized_statuses
                        or "request_pending" in normalized_statuses
                        or "request-pending" in normalized_statuses
                        or "pending" in normalized_statuses
                    ):
                        status_q |= Q(is_active=False, invited_by__isnull=False)
                    # Request Expired users (invitation exists but is no longer active)
                    if (
                        "request expired" in normalized_statuses
                        or "request_expired" in normalized_statuses
                        or "request-expired" in normalized_statuses
                        or "expired" in normalized_statuses
                    ):
                        try:
                            expired_invited_user_ids = (
                                OrganizationMembership.objects.filter(
                                    organization=organization, is_active=False
                                ).values_list("user_id", flat=True)
                            )
                            status_q |= Q(id__in=expired_invited_user_ids)
                        except Exception:
                            # If membership model isn't available or any error occurs, skip expired filtering
                            pass
                    if status_q.children:
                        users = users.filter(status_q)

            # Prepare annotations for computed fields used in sorting and filtering
            # 1) Workspace role subquery
            ws_role_sq = Subquery(
                WorkspaceMembership.no_workspace_objects.filter(
                    workspace=current_workspace, user_id=OuterRef("pk"), is_active=True
                ).values("role")[:1]
            )

            # Invitation existence checks for status
            active_invite_exists = Exists(
                OrganizationMembership.objects.filter(
                    user_id=OuterRef("pk"), organization=organization, is_active=True
                )
            )
            any_invite_exists = Exists(
                OrganizationMembership.objects.filter(
                    user_id=OuterRef("pk"), organization=organization
                )
            )

            # 2) Annotate workspace role field first so it can be reused in subsequent annotations
            users = users.annotate(ws_role=ws_role_sq)

            # 3) Rank mappings (higher number = higher precedence)
            # Normalize everything to workspace-level for display/filter/sort
            org_role_rank = Case(
                When(organization_role=OrganizationRoles.OWNER, then=Value(70)),
                When(organization_role=OrganizationRoles.ADMIN, then=Value(60)),
                When(organization_role=OrganizationRoles.MEMBER, then=Value(40)),
                When(
                    organization_role=OrganizationRoles.MEMBER_VIEW_ONLY, then=Value(20)
                ),
                default=Value(0),
                output_field=IntegerField(),
            )

            ws_role_rank = Case(
                When(ws_role=OrganizationRoles.WORKSPACE_ADMIN, then=Value(50)),
                When(ws_role=OrganizationRoles.WORKSPACE_MEMBER, then=Value(30)),
                When(ws_role=OrganizationRoles.WORKSPACE_VIEWER, then=Value(10)),
                default=Value(0),
                output_field=IntegerField(),
            )

            # Derived workspace-rank from org role
            org_as_ws_rank = Case(
                When(
                    organization_role__in=[
                        OrganizationRoles.OWNER,
                        OrganizationRoles.ADMIN,
                    ],
                    then=Value(50),
                ),
                When(organization_role=OrganizationRoles.MEMBER, then=Value(30)),
                When(
                    organization_role=OrganizationRoles.MEMBER_VIEW_ONLY, then=Value(10)
                ),
                default=Value(0),
                output_field=IntegerField(),
            )

            users = users.annotate(
                org_role_rank=org_role_rank,
                ws_role_rank=ws_role_rank,
                org_as_ws_rank=org_as_ws_rank,
            )

            # 4) Effective workspace role and rank, plus status
            effective_role = Case(
                When(
                    Q(org_as_ws_rank__gte=F("ws_role_rank"))
                    & Q(
                        organization_role__in=[
                            OrganizationRoles.OWNER,
                            OrganizationRoles.ADMIN,
                        ]
                    ),
                    then=Value(OrganizationRoles.WORKSPACE_ADMIN),
                ),
                When(
                    Q(org_as_ws_rank__gte=F("ws_role_rank"))
                    & Q(organization_role=OrganizationRoles.MEMBER),
                    then=Value(OrganizationRoles.WORKSPACE_MEMBER),
                ),
                When(
                    Q(org_as_ws_rank__gte=F("ws_role_rank"))
                    & Q(organization_role=OrganizationRoles.MEMBER_VIEW_ONLY),
                    then=Value(OrganizationRoles.WORKSPACE_VIEWER),
                ),
                default=Coalesce(
                    F("ws_role"), Value(OrganizationRoles.WORKSPACE_MEMBER)
                ),
                output_field=CharField(),
            )

            users = users.annotate(
                computed_workspace_role=effective_role,
                computed_role_rank=Greatest(F("org_as_ws_rank"), F("ws_role_rank")),
                computed_status=Case(
                    When(
                        Q(invited_by__isnull=False) & Q(is_active=False),
                        then=Value("Request Pending"),
                    ),
                    When(is_active=False, then=Value("Inactive")),
                    When(active_invite_exists, then=Value("Request Pending")),
                    When(any_invite_exists, then=Value("Request Expired")),
                    default=Value("Active"),
                    output_field=CharField(),
                ),
            )

            # Apply role filter on effective workspace role (after annotations)
            if filter_role:
                users = users.filter(computed_workspace_role__in=filter_role)

            # Parse bracket-style sort params if JSON not provided
            if not sort_params:
                sort_items = {}
                for key in request.query_params.keys():
                    # Matches sort[0][columnId] or sort[0][type]
                    m = re.match(r"^sort\[(\d+)\]\[(columnId|type)\]$", key)
                    if m:
                        idx = int(m.group(1))
                        subkey = m.group(2)
                        sort_items.setdefault(idx, {})[subkey] = (
                            request.query_params.get(key)
                        )
                if sort_items:
                    sort_params = [sort_items[i] for i in sorted(sort_items.keys())]

            # Apply sorting based on mapping
            ordering = []
            if sort_params:

                def map_column_to_field(column_id: str) -> str:
                    if not column_id:
                        return None
                    cid = str(column_id).strip()
                    lc = cid.lower()
                    mapping = {
                        "name": "name",
                        "email": "email",
                        "role": "computed_role_rank",
                        "status": "computed_status",
                        "startdate": "created_at",
                        "start_date": "created_at",
                    }
                    if lc in mapping:
                        return mapping[lc]
                    # handle camelCase
                    if cid == "startDate":
                        return "created_at"
                    if cid == "lastUpdatedDate":
                        return "created_at"
                    return None

                # Normalize JSON or bracket sort params into ordering list
                if isinstance(sort_params, list):
                    for item in sort_params:
                        if isinstance(item, dict):
                            column_id = (
                                item.get("columnId")
                                or item.get("id")
                                or item.get("column")
                            )
                            sort_type = (
                                item.get("type") or item.get("order") or item.get("dir")
                            )
                            field_name = map_column_to_field(column_id)
                            if field_name:
                                if str(sort_type).lower() in [
                                    "desc",
                                    "descending",
                                    "down",
                                    "false",
                                ]:
                                    ordering.append(f"-{field_name}")
                                else:
                                    ordering.append(field_name)
                        elif isinstance(item, str):
                            # e.g., "-name" or "name"
                            if item.startswith("-"):
                                base = item[1:]
                                field_name = map_column_to_field(base)
                                if field_name:
                                    ordering.append(f"-{field_name}")
                            else:
                                field_name = map_column_to_field(item)
                                if field_name:
                                    ordering.append(field_name)
                elif isinstance(sort_params, dict):
                    column_id = (
                        sort_params.get("columnId")
                        or sort_params.get("id")
                        or sort_params.get("column")
                    )
                    sort_type = (
                        sort_params.get("type")
                        or sort_params.get("order")
                        or sort_params.get("dir")
                    )
                    field_name = map_column_to_field(column_id)
                    if field_name:
                        if str(sort_type).lower() in [
                            "desc",
                            "descending",
                            "down",
                            "false",
                        ]:
                            ordering.append(f"-{field_name}")
                        else:
                            ordering.append(field_name)

            if ordering:
                users = users.order_by(*ordering)
            else:
                users = users.order_by("-created_at")

            # Use default pagination
            paginator = self.pagination_class()
            paginated_users = paginator.paginate_queryset(users, request)

            # Serialize results with context for role determination
            serializer = UserListSerializer(
                paginated_users, many=True, context={"request": request}
            )

            # Add workspace context to response
            response_data = serializer.data
            response_data = list(response_data)  # Convert to list if it's not already

            # Add workspace-specific information
            for user_data in response_data:
                user_obj = next(
                    (u for u in paginated_users if str(u.id) == user_data["id"]), None
                )
                if user_obj:
                    # Get workspace membership details or derive from org role
                    try:
                        workspace_membership = (
                            WorkspaceMembership.no_workspace_objects.get(
                                workspace=current_workspace,
                                user=user_obj,
                                is_active=True,
                            )
                        )
                        user_data["workspace_role"] = workspace_membership.role
                        user_data["workspace_member_since"] = (
                            workspace_membership.created_at.strftime("%Y-%m-%d")
                            if workspace_membership.created_at
                            else ""
                        )
                        user_data["invited_by"] = (
                            workspace_membership.invited_by.name
                            if workspace_membership.invited_by
                            else None
                        )
                    except WorkspaceMembership.DoesNotExist:
                        # Derive workspace role from organization role for non-members
                        # If organization_role is None, user is workspace-only, so no default role
                        if user_obj.organization_role:
                            user_data["workspace_role"] = (
                                RoleMapping.get_workspace_role(
                                    user_obj.organization_role
                                )
                            )
                        else:
                            user_data["workspace_role"] = None
                        user_data["workspace_member_since"] = ""
                        user_data["invited_by"] = None

            return paginator.get_paginated_response(response_data)

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in getting users list: {str(e)}")
            return self._gm.bad_request("Error in getting users list")


class UserRoleUpdateAPIView(APIView):
    """API for updating user roles at both organization and workspace levels"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def post(self, request):
        """Update user role at organization or workspace level"""
        try:
            # Validate request data
            serializer = UserRoleUpdateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data

            user = request.user
            organization = resolve_org(request)

            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            target_user_id = data["user_id"]
            new_role = data["new_role"]

            try:
                target_user = User.objects.get(
                    id=target_user_id, organization=organization
                )
            except User.DoesNotExist:
                return self._gm.not_found("User not found in organization")

            # Prevent changing own role
            if target_user == user:
                return self._gm.bad_request("Cannot change your own role")

            # Resolve workspace context: prefer explicit request data, then query param, then request-global
            workspace_id = (
                data.get("workspace_id")
                or request.query_params.get("workspace_id")
                or request.query_params.get("workspaceId")
            )
            current_workspace = None
            if workspace_id:
                try:
                    current_workspace = Workspace.objects.get(
                        id=workspace_id, organization=organization, is_active=True
                    )
                except Workspace.DoesNotExist:
                    return self._gm.bad_request(
                        "Invalid workspace_id or workspace not in your organization"
                    )
            else:
                current_workspace = get_current_workspace()
                if not current_workspace:
                    try:
                        current_workspace = Workspace.objects.get(
                            organization=organization, is_default=True, is_active=True
                        )
                    except Workspace.DoesNotExist:
                        # No workspace context - will update organization role
                        pass

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

            # Check permissions: allow org-level OWNER/ADMIN/MEMBER or workspace-level ADMIN
            org_role = resolve_org_role(user, organization)
            has_org_permission = org_role and org_role in [
                OrganizationRoles.OWNER,
                OrganizationRoles.ADMIN,
                OrganizationRoles.MEMBER,
            ]

            has_workspace_permission = False
            if current_workspace and not has_org_permission:
                try:
                    user_workspace_membership = (
                        WorkspaceMembership.no_workspace_objects.get(
                            workspace=current_workspace, user=user, is_active=True
                        )
                    )
                    has_workspace_permission = (
                        user_workspace_membership.role
                        == OrganizationRoles.WORKSPACE_ADMIN
                    )
                except WorkspaceMembership.DoesNotExist:
                    pass

            # Restrict workspace admins from assigning organization-level roles
            # Only org owners/admins can assign organization-level roles
            if new_role in organization_level_roles and not has_org_permission:
                return self._gm.forbidden_response(
                    "Only organization owners or admins can assign organization-level roles (owner, admin, member, member_view_only)"
                )

            # Validate that the role is either organization-level or workspace-level
            if (
                new_role not in organization_level_roles
                and new_role not in workspace_level_roles
            ):
                return self._gm.bad_request(
                    f"Invalid role. Must be either an organization-level role ({', '.join([r.value for r in organization_level_roles])}) or a workspace-level role ({', '.join([r.value for r in workspace_level_roles])})"
                )

            if not (has_org_permission or has_workspace_permission):
                return self._gm.forbidden_response(
                    get_error_message("UNAUTHORIZED_ACCESS")
                )

            # Determine workspace role: if role is org-level, map to workspace role; otherwise use role as-is
            if new_role in organization_level_roles:
                # Map organization role to workspace role
                workspace_role = RoleMapping.get_workspace_role(new_role)
            else:
                # Role is already a workspace-level role
                workspace_role = new_role

            if new_role in organization_level_roles:
                # Update organization-level role
                # Only org-level OWNER/ADMIN can update organization roles
                org_role = resolve_org_role(user, organization)
                if not org_role or org_role not in [
                    OrganizationRoles.OWNER,
                    OrganizationRoles.ADMIN,
                ]:
                    return self._gm.forbidden_response(
                        get_error_message("UNAUTHORIZED_ACCESS")
                    )

                # Prevent demoting the last owner
                if (
                    target_user.organization_role == OrganizationRoles.OWNER
                    and new_role != OrganizationRoles.OWNER
                ):
                    with transaction.atomic():
                        owner_count = (
                            User.objects.select_for_update()
                            .filter(
                                organization=organization,
                                organization_role=OrganizationRoles.OWNER,
                                is_active=True,
                            )
                            .count()
                        )
                        if owner_count <= 1:
                            return self._gm.bad_request("Cannot demote the last owner")

                        # Update organization role within transaction
                        target_user.organization_role = new_role
                        target_user.save()
                else:
                    # Update organization role
                    target_user.organization_role = new_role
                    target_user.save()

                # If workspace context exists, also update workspace membership
                if current_workspace:
                    try:
                        workspace_membership = (
                            WorkspaceMembership.no_workspace_objects.get(
                                workspace=current_workspace,
                                user=target_user,
                                is_active=True,
                            )
                        )
                        # Update existing membership with mapped workspace role
                        workspace_membership.role = workspace_role
                        workspace_membership.save()
                    except WorkspaceMembership.DoesNotExist:
                        # Check if there's a soft-deleted membership that we should reactivate
                        existing_deleted_membership = (
                            WorkspaceMembership.all_objects.filter(
                                workspace=current_workspace,
                                user=target_user,
                                deleted=True,
                            ).first()
                        )

                        if existing_deleted_membership:
                            # Reactivate the soft-deleted membership
                            existing_deleted_membership.deleted = False
                            existing_deleted_membership.is_active = True
                            existing_deleted_membership.role = workspace_role
                            existing_deleted_membership.invited_by = user
                            existing_deleted_membership.save()
                        else:
                            # Create workspace membership if it doesn't exist
                            WorkspaceMembership.no_workspace_objects.create(
                                workspace=current_workspace,
                                user=target_user,
                                role=workspace_role,
                                is_active=True,
                                invited_by=user,
                            )

                return self._gm.success_response(
                    {
                        "message": "User organization role updated successfully",
                        "user_id": str(target_user_id),
                        "new_role": new_role,
                        "workspace_role": (
                            workspace_role.value if current_workspace else None
                        ),
                        "workspace": (
                            current_workspace.name if current_workspace else None
                        ),
                        "level": "organization",
                    }
                )
            else:
                # Update workspace-level role only
                if not current_workspace:
                    return self._gm.bad_request(
                        "Workspace context is required for workspace-level roles"
                    )

                # Additional check: workspace admins cannot change other workspace admins (only org owners/admins can)
                try:
                    workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                        workspace=current_workspace, user=target_user, is_active=True
                    )
                    if (
                        workspace_membership.role == OrganizationRoles.WORKSPACE_ADMIN
                        and new_role != OrganizationRoles.WORKSPACE_ADMIN
                        and not has_org_permission
                    ):
                        return self._gm.forbidden_response(
                            "Only organization owners/admins can change workspace admin roles"
                        )
                    # Update existing membership
                    workspace_membership.role = new_role
                    workspace_membership.save()
                except WorkspaceMembership.DoesNotExist:
                    # Check if there's a soft-deleted membership that we should reactivate
                    existing_deleted_membership = (
                        WorkspaceMembership.all_objects.filter(
                            workspace=current_workspace,
                            user=target_user,
                            deleted=True,
                        ).first()
                    )

                    if existing_deleted_membership:
                        # Reactivate the soft-deleted membership
                        existing_deleted_membership.deleted = False
                        existing_deleted_membership.is_active = True
                        existing_deleted_membership.role = new_role
                        existing_deleted_membership.invited_by = user
                        existing_deleted_membership.save()
                    else:
                        # Create workspace membership if it doesn't exist
                        WorkspaceMembership.no_workspace_objects.create(
                            workspace=current_workspace,
                            user=target_user,
                            role=new_role,
                            is_active=True,
                            invited_by=user,
                        )

                return self._gm.success_response(
                    {
                        "message": "User workspace role updated successfully",
                        "user_id": str(target_user_id),
                        "new_role": new_role,
                        "workspace": current_workspace.name,
                        "level": "workspace",
                    }
                )

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in updating user role: {str(e)}")
            return self._gm.bad_request("Error in updating user role")


class ResendInviteAPIView(APIView):
    """API for resending invites with workspace context"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def post(self, request):
        """Resend invitation email with workspace context"""
        try:
            # Validate request data
            serializer = ResendInviteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data

            user = request.user
            organization = resolve_org(request)

            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            # Check permissions
            org_role = resolve_org_role(user, organization)
            if not org_role or org_role not in [
                OrganizationRoles.OWNER,
                OrganizationRoles.ADMIN,
            ]:
                return self._gm.forbidden_response(
                    get_error_message("UNAUTHORIZED_ACCESS")
                )

            target_user_id = data["user_id"]

            try:
                target_user = User.objects.get(
                    id=target_user_id, organization=organization
                )
            except User.DoesNotExist:
                return self._gm.not_found("User not found in organization")

            # Get current workspace context
            current_workspace = get_current_workspace()
            # Validate workspace belongs to organization
            if current_workspace and current_workspace.organization != organization:
                current_workspace = None

            # Generate new password and send email
            from accounts.utils import generate_password

            new_password = generate_password()
            target_user.set_password(new_password)
            target_user.save()

            # Send invitation email with workspace context
            try:
                # Generate token and uid for invitation link
                token = default_token_generator.make_token(target_user)
                uidb64 = urlsafe_base64_encode(force_bytes(target_user.pk))

                email_context = {
                    "password": new_password,
                    "email": target_user.email,
                    "org_name": organization.display_name or organization.name,
                    "app_url": settings.APP_URL,
                    "ssl": ssl,
                    "uid": str(uidb64),
                    "token": token,
                }

                # Add workspace information if available
                if current_workspace:
                    email_context["workspace_name"] = current_workspace.name
                    email_context["subject"] = (
                        f"You are invited to join {organization.display_name or organization.name} - {current_workspace.name} - Future AGI"
                    )
                else:
                    email_context["subject"] = (
                        f"You are invited to join {organization.display_name or organization.name} - Future AGI"
                    )

                email_helper(
                    email_context["subject"],
                    "member_invite.html",
                    email_context,
                    [target_user.email],
                )
            except Exception as e:
                return self._gm.bad_request(f"Failed to send email: {str(e)}")

            response_data = {
                "message": "Invitation email sent successfully",
                "user_id": str(target_user_id),
            }

            # Add workspace context to response
            if current_workspace:
                response_data["workspace"] = current_workspace.name

            return self._gm.success_response(response_data)

        except Exception as e:
            logger.info(f"Error in resending invite: {str(e)}")
            return self._gm.bad_request("Error in resending invite")


class DeleteUserAPIView(APIView):
    """API for deleting users or removing invites at both organization and workspace levels"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def post(self, request):
        """Delete user or remove invite at organization or workspace level"""
        try:
            # Validate request data
            serializer = DeleteUserSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data

            user = request.user
            organization = resolve_org(request)

            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            # Check permissions
            org_role = resolve_org_role(user, organization)
            if not org_role or org_role not in [
                OrganizationRoles.OWNER,
                OrganizationRoles.ADMIN,
            ]:
                return self._gm.forbidden_response(
                    get_error_message("UNAUTHORIZED_ACCESS")
                )

            target_user_id = data["user_id"]

            try:
                target_user = User.objects.get(
                    id=target_user_id, organization=organization
                )
            except User.DoesNotExist:
                return self._gm.not_found("User not found in organization")

            # Prevent deleting self
            if target_user == user:
                return self._gm.bad_request("Cannot delete your own account")

            # Get current workspace context
            current_workspace = get_current_workspace()
            # Validate workspace belongs to organization
            if current_workspace and current_workspace.organization != organization:
                current_workspace = None

            if current_workspace:
                # Workspace-level deletion - remove from current workspace only
                try:
                    workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                        workspace=current_workspace, user=target_user, is_active=True
                    )

                    # Check if user has permission to remove this user
                    if (
                        workspace_membership.role == OrganizationRoles.WORKSPACE_ADMIN
                        and org_role != OrganizationRoles.OWNER
                    ):
                        return self._gm.forbidden_response(
                            "Only organization owners can remove workspace admins"
                        )

                    workspace_membership.is_active = False
                    workspace_membership.deleted = True
                    workspace_membership.save()

                    return self._gm.success_response(
                        {
                            "message": f"User removed from workspace '{current_workspace.name}' successfully",
                            "user_id": str(target_user_id),
                            "workspace": current_workspace.name,
                            "level": "workspace",
                        }
                    )

                except WorkspaceMembership.DoesNotExist:
                    return self._gm.bad_request(
                        "User is not a member of the current workspace"
                    )
            else:
                # Organization-level deletion - remove from all workspaces and delete user
                # Prevent deleting the last owner
                if target_user.organization_role == OrganizationRoles.OWNER:
                    with transaction.atomic():
                        owner_count = (
                            User.objects.select_for_update()
                            .filter(
                                organization=organization,
                                organization_role=OrganizationRoles.OWNER,
                                is_active=True,
                            )
                            .count()
                        )
                        if owner_count <= 1:
                            return self._gm.bad_request("Cannot delete the last owner")

                        # Clear Redis cache for immediate logout
                        clear_user_redis_cache(target_user_id)

                        # Remove from all workspaces
                        WorkspaceMembership.no_workspace_objects.filter(
                            user=target_user, workspace__organization=organization
                        ).update(is_active=False, deleted=True)

                        # Delete user
                        target_user.delete()
                else:
                    # Clear Redis cache for immediate logout
                    clear_user_redis_cache(target_user_id)

                    # Remove from all workspaces
                    WorkspaceMembership.no_workspace_objects.filter(
                        user=target_user, workspace__organization=organization
                    ).update(is_active=False, deleted=True)

                    # Delete user
                    target_user.delete()

                return self._gm.success_response(
                    {
                        "message": "User deleted from organization successfully",
                        "user_id": str(target_user_id),
                        "level": "organization",
                    }
                )

        except Exception as e:
            logger.exception(f"Error in deleting user: {str(e)}")
            return self._gm.bad_request("Error in deleting user")


class DeactivateUserAPIView(APIView):
    """API for deactivating users (marking as inactive)"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def post(self, request):
        """Deactivate user by marking is_active as False"""
        try:
            # Validate request data
            serializer = DeactivateUserSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data

            user = request.user
            organization = resolve_org(request)

            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            # Check permissions
            org_role = resolve_org_role(user, organization)
            if not org_role or org_role not in [
                OrganizationRoles.OWNER,
                OrganizationRoles.ADMIN,
            ]:
                return self._gm.forbidden_response(
                    get_error_message("UNAUTHORIZED_ACCESS")
                )

            target_user_id = data["user_id"]

            try:
                target_user = User.objects.get(
                    id=target_user_id, organization=organization
                )
            except User.DoesNotExist:
                return self._gm.not_found("User not found in organization")

            # Prevent deactivating self
            if target_user == user:
                return self._gm.bad_request("Cannot deactivate your own account")

            # Prevent deactivating the last owner
            if target_user.organization_role == OrganizationRoles.OWNER:
                with transaction.atomic():
                    owner_count = (
                        User.objects.select_for_update()
                        .filter(
                            organization=organization,
                            organization_role=OrganizationRoles.OWNER,
                            is_active=True,
                        )
                        .count()
                    )
                    if owner_count <= 1:
                        return self._gm.bad_request("Cannot deactivate the last owner")

                    # Clear Redis cache for immediate logout
                    clear_user_redis_cache(target_user_id)

                    # Deactivate organization membership for this org
                    OrganizationMembership.no_workspace_objects.filter(
                        user=target_user, organization=organization
                    ).update(is_active=False)

                    # Deactivate all workspace memberships for this user in this org
                    WorkspaceMembership.no_workspace_objects.filter(
                        user=target_user, workspace__organization=organization
                    ).update(is_active=False)

                    # Only deactivate user globally if they have no other active org memberships
                    has_other_orgs = OrganizationMembership.no_workspace_objects.filter(
                        user=target_user, is_active=True
                    ).exists()
                    if not has_other_orgs:
                        target_user.is_active = False
                        target_user.save(update_fields=["is_active"])
            else:
                # Clear Redis cache for immediate logout
                clear_user_redis_cache(target_user_id)

                with transaction.atomic():
                    # Deactivate organization membership for this org
                    OrganizationMembership.no_workspace_objects.filter(
                        user=target_user, organization=organization
                    ).update(is_active=False)

                    # Deactivate all workspace memberships for this user in this org
                    WorkspaceMembership.no_workspace_objects.filter(
                        user=target_user, workspace__organization=organization
                    ).update(is_active=False)

                    # Only deactivate user globally if they have no other active org memberships
                    has_other_orgs = OrganizationMembership.no_workspace_objects.filter(
                        user=target_user, is_active=True
                    ).exists()
                    if not has_other_orgs:
                        target_user.is_active = False
                        target_user.save(update_fields=["is_active"])

            return self._gm.success_response(
                {
                    "message": "User deactivated successfully",
                    "user_id": str(target_user_id),
                    "user_email": target_user.email,
                    "user_name": target_user.name,
                }
            )

        except Exception as e:
            logger.exception(f"Error in deactivating user: {str(e)}")
            return self._gm.bad_request("Error in deactivating user")


class SwitchWorkspaceAPIView(APIView):
    """API for switching workspaces with proper validation"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def post(self, request):
        """Switch to a different workspace with proper validation"""
        try:
            # Validate request data
            serializer = SwitchWorkspaceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data

            user = request.user
            organization = resolve_org(request)

            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            new_workspace_id = data["new_workspace_id"]

            try:
                workspace = Workspace.objects.get(
                    id=new_workspace_id, organization=organization, is_active=True
                )
            except Workspace.DoesNotExist:
                return self._gm.not_found(
                    "Workspace not found or doesn't belong to your organization"
                )

            # Check if user has access to this workspace
            try:
                membership = WorkspaceMembership.no_workspace_objects.get(
                    workspace=workspace, user=user, is_active=True
                )
                workspace_role = membership.role
                access_type = "workspace_member"
            except WorkspaceMembership.DoesNotExist:
                # Check if user has global access (Owner/Admin only)
                org_role = resolve_org_role(user, organization)
                if org_role and org_role in RolePermissions.GLOBAL_ACCESS_ROLES:
                    workspace_role = RoleMapping.get_workspace_role(org_role)
                    access_type = "global_access"
                else:
                    return self._gm.forbidden_response(
                        "You don't have access to this workspace"
                    )

            # Update user's config with current workspace
            if not user.config:
                user.config = {}

            user.config["currentWorkspaceId"] = str(new_workspace_id)
            user.config["defaultWorkspaceId"] = str(
                new_workspace_id
            )  # Also update default for backward compatibility

            # Update orgWorkspaceMap so org switch remembers this workspace
            org_id = str(organization.id)
            org_workspace_map = user.config.get("orgWorkspaceMap", {})
            org_workspace_map[org_id] = str(new_workspace_id)
            user.config["orgWorkspaceMap"] = org_workspace_map

            user.save(update_fields=["config"])

            return self._gm.success_response(
                {
                    "message": "Workspace switched successfully",
                    "workspace": {
                        "id": str(workspace.id),
                        "name": workspace.name,
                        "display_name": workspace.display_name,
                        "description": workspace.description,
                        "is_default": workspace.is_default,
                    },
                    "user_role": workspace_role,
                    "access_type": access_type,
                    "organization": organization.display_name or organization.name,
                }
            )

        except Exception as e:
            logger.exception(f"Error in switching workspace: {str(e)}")
            return self._gm.bad_request("Error in switching workspace")


class ManageTeamView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get(self, request, *args, **kwargs):
        user = request.user
        organization = resolve_org(request)
        org_role = resolve_org_role(user, organization)
        if not org_role or org_role != OrganizationRoles.OWNER:
            return self._gm.forbidden_response(
                get_error_message("ONLY_OWNER_CAN_VIEW_TEAMS")
            )

        if not organization:
            return self._gm.bad_request(
                get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
            )

        # Get workspace context if provided
        workspace_id = request.query_params.get("workspace_id")
        workspace = None
        if workspace_id:
            try:
                workspace = Workspace.objects.get(
                    id=workspace_id, organization=organization, is_active=True
                )
            except Workspace.DoesNotExist:
                return self._gm.bad_request("Invalid workspace ID")

        search_query = request.query_params.get("search_query", "")
        is_active = request.query_params.get("is_active", "true")
        if is_active == "false":
            is_active = False
        elif is_active == "true":
            is_active = True
        else:
            return self._gm.bad_request(get_error_message("INVALID_VALUE_OF_is_active"))

        try:
            page_size = request.query_params.get("page_size", 10)
            page = request.query_params.get("page", 1)
            page = int(page)
            page_size = int(page_size)
        except (ValueError, TypeError):
            page_size = 10
            page = 1

        # Get all organization members (primary + invited), deduplicated
        team_members_qs = User.objects.filter(
            Q(organization=organization) | Q(invited_organizations=organization),
            name__icontains=search_query,
            is_active=is_active,
        ).distinct()

        total_count = team_members_qs.count()

        # Apply pagination
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        team_members = list(team_members_qs[start_idx:end_idx])

        serializer = UserSerializer(team_members, many=True)
        user_data = serializer.data

        for user in user_data:
            timestamp = user["created_at"]
            dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
            date_str = dt.strftime("%Y-%m-%d")
            user["created_at"] = date_str

            # Add status information
            user_obj = next((m for m in team_members if str(m.id) == user["id"]), None)
            if user_obj:
                # Determine user status

                if user_obj.invited_by and not user_obj.is_active:
                    user["status"] = "Request Pending"
                elif not user_obj.is_active:
                    user["status"] = "Inactive"
                elif user_obj.organization:
                    user["status"] = "Active"
                elif user_obj.invited_organizations.exists():
                    # Check if any invitation is still active
                    try:
                        active_invitations = OrganizationMembership.objects.filter(
                            user=user_obj, organization=organization, is_active=True
                        )
                        if active_invitations.exists():
                            user["status"] = "Request Pending"
                        else:
                            user["status"] = "Request Expired"
                    except Exception:
                        user["status"] = "Request Expired"
                else:
                    user["status"] = "Active"

                # Add membership type information
                if user_obj.organization == organization:
                    user["membership_type"] = "primary"
                    # Return workspace role if organization_role is None, otherwise return org role
                    if user_obj.organization_role:
                        user["role"] = user_obj.organization_role
                    elif workspace:
                        # Try to get workspace role
                        try:
                            ws_membership = (
                                WorkspaceMembership.no_workspace_objects.get(
                                    workspace=workspace, user=user_obj, is_active=True
                                )
                            )
                            user["role"] = ws_membership.role
                        except WorkspaceMembership.DoesNotExist:
                            user["role"] = None
                    else:
                        user["role"] = None
                else:
                    user["membership_type"] = "invited"
                    try:
                        membership = OrganizationMembership.objects.get(
                            user=user_obj, organization=organization, is_active=True
                        )
                        user["role"] = membership.role
                    except OrganizationMembership.DoesNotExist:
                        user["role"] = "Unknown"

                # Add workspace membership information
                if workspace:
                    try:
                        workspace_membership = (
                            WorkspaceMembership.no_workspace_objects.get(
                                workspace=workspace, user=user_obj, is_active=True
                            )
                        )
                        user["workspace_role"] = workspace_membership.role
                        user["workspace_member"] = True
                    except WorkspaceMembership.DoesNotExist:
                        user["workspace_role"] = None
                        user["workspace_member"] = False
                else:
                    # Show all workspaces the user is a member of
                    user["workspaces"] = []
                    for (
                        ws_membership
                    ) in WorkspaceMembership.no_workspace_objects.filter(
                        user=user_obj,
                        workspace__organization=organization,
                        is_active=True,
                    ):
                        user["workspaces"].append(
                            {
                                "id": str(ws_membership.workspace.id),
                                "name": ws_membership.workspace.name,
                                "role": ws_membership.role,
                            }
                        )

        response = {}
        response["org_name"] = organization.display_name
        if workspace:
            response["workspace_name"] = workspace.name
        response["results"] = user_data
        response["total"] = total_count

        return self._gm.success_response(response)

    def post(self, request, *args, **kwargs):
        try:
            user = request.user
            organization = resolve_org(request)
            org_role = resolve_org_role(user, organization)
            if not org_role or org_role != OrganizationRoles.OWNER:
                return self._gm.forbidden_response(
                    get_error_message("UNAUTHORIZED_ACCESS")
                )

            if not organization:
                return self._gm.bad_request(
                    get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
                )

            # Handle organization name update
            org_display_name = request.data.get("org_name", None)
            if org_display_name:
                organization.display_name = org_display_name
                # Subscription tracking requires ee — skip the lookup and report
                # the org as unmanaged when absent.
                if OrganizationSubscription is not None:
                    try:
                        subscription = OrganizationSubscription.objects.get(
                            organization=organization
                        ).get_subscription_name()
                    except OrganizationSubscription.DoesNotExist:
                        subscription = SubscriptionTierChoices.FREE.value
                else:
                    subscription = None
                mixpanel_tracker.update_org_details(
                    org_id=str(organization.id),
                    org_name=org_display_name,
                    subscription=subscription,
                )
            organization.is_new = False
            organization.save()

            # Handle workspace creation/management
            workspace_data = request.data.get("workspace", {})
            workspace = None
            if workspace_data:
                workspace_name = workspace_data.get("name")
                workspace_display_name = workspace_data.get(
                    "display_name", workspace_name
                )
                workspace_description = workspace_data.get("description", "")

                if workspace_name:
                    # Check if workspace already exists
                    try:
                        workspace = Workspace.objects.get(
                            name=workspace_name, organization=organization
                        )
                        # Update existing workspace
                        workspace.display_name = workspace_display_name
                        workspace.description = workspace_description
                        workspace.save()
                    except Workspace.DoesNotExist:
                        # Create new workspace
                        workspace = Workspace.objects.create(
                            name=workspace_name,
                            display_name=workspace_display_name,
                            description=workspace_description,
                            organization=organization,
                            created_by=user,
                        )

                        # Add organization owner to workspace with admin role
                        WorkspaceMembership.no_workspace_objects.create(
                            workspace=workspace,
                            user=user,
                            role=OrganizationRoles.WORKSPACE_ADMIN,
                            invited_by=user,
                        )

            # If no specific workspace, use default workspace
            if not workspace:
                try:
                    workspace = Workspace.objects.get(
                        organization=organization, is_default=True, is_active=True
                    )
                except Workspace.DoesNotExist:
                    # Create default workspace if it doesn't exist
                    workspace = Workspace.objects.create(
                        name="default",
                        display_name="Default Workspace",
                        description="Default workspace for the organization",
                        organization=organization,
                        is_default=True,
                        created_by=user,
                    )

                    # Add organization owner to default workspace
                    WorkspaceMembership.no_workspace_objects.create(
                        workspace=workspace,
                        user=user,
                        role=OrganizationRoles.WORKSPACE_ADMIN,
                        invited_by=user,
                    )

            members_data = request.data.get("members", [])
            if not isinstance(members_data, list):
                return self._gm.bad_request(get_error_message("INVALID_DATA_TYPE"))

            created_members = []
            errors = []

            org_user_ids = OrganizationMembership.no_workspace_objects.filter(
                organization=organization, is_active=True
            ).values_list("user_id", flat=True)
            user_count = User.objects.filter(id__in=org_user_ids).count()
            logger.info(f"member_count: {len(members_data)} user_count: {user_count}")
            # Resource limit / billing requires ee — skip the deduction and
            # treat as always-allowed when absent.
            if log_and_deduct_cost_for_resource_request is not None:
                call_log_row = log_and_deduct_cost_for_resource_request(
                    organization,
                    APICallTypeChoices.USER_ADD.value,
                    config={"user_count": user_count, "extra_users": len(members_data)},
                    workspace=workspace,
                )
            else:
                call_log_row = None
            if log_and_deduct_cost_for_resource_request is not None and (
                call_log_row is None
                or call_log_row.status == APICallStatusChoices.RESOURCE_LIMIT.value
            ):
                config = json.loads(call_log_row.config)
                return self._gm.too_many_requests(
                    {
                        "errors": [
                            {
                                "error": get_error_message(
                                    "MEMBER_LIMIT_REACHED"
                                ).format(
                                    SubscriptionTierChoices(
                                        config.get("subscription_name")
                                    ),
                                    config.get("limit"),
                                )
                            }
                        ]
                    }
                )

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

            for index, member_data in enumerate(members_data):
                serializer = CreateMemberSerializer(data=member_data)

                if serializer.is_valid():
                    try:
                        member_data["email"] = member_data["email"].lower()

                        # Get role from member_data - can be organization_role or a generic role field
                        # Support both old format (organization_role) and new format (role)
                        role = member_data.get("role") or member_data.get(
                            "organization_role"
                        )

                        if not role:
                            errors.append(
                                {
                                    "index": index,
                                    "email": member_data.get("email"),
                                    "error": "Role is required. Must be either an organization-level role or workspace-level role",
                                }
                            )
                            continue

                        # Validate that the role is either organization-level or workspace-level
                        if (
                            role not in organization_level_roles
                            and role not in workspace_level_roles
                        ):
                            errors.append(
                                {
                                    "index": index,
                                    "email": member_data.get("email"),
                                    "error": f"Invalid role. Must be either an organization-level role ({', '.join([r.value for r in organization_level_roles])}) or a workspace-level role ({', '.join([r.value for r in workspace_level_roles])})",
                                }
                            )
                            continue

                        # Determine organization role and workspace role
                        if role in organization_level_roles:
                            # Organization-level role: set org role and map to workspace role
                            org_role = role
                            workspace_role = RoleMapping.get_workspace_role(role)
                        else:
                            # Workspace-level role: don't set org role, use role as workspace role
                            org_role = None
                            workspace_role = role

                        # Check if user already exists in this organization
                        existing_user = User.objects.filter(
                            email=member_data["email"], organization=organization
                        ).first()

                        if existing_user:
                            return self._gm.bad_request(
                                "User is already a member of this organization"
                            )

                        # Check if user exists in other organizations
                        user_in_other_org = (
                            User.objects.filter(email=member_data["email"])
                            .exclude(organization=organization)
                            .first()
                        )

                        if user_in_other_org:
                            errors.append(
                                {
                                    "index": index,
                                    "email": member_data["email"],
                                    "error": get_error_message("USER_ALREADY_EXISTS"),
                                }
                            )
                            continue
                            # User exists in another organization - invite them to this organization
                            # try:
                            #     from accounts.models.organization_membership import OrganizationMembership

                            #     # Check if already invited
                            #     existing_invite = OrganizationMembership.objects.filter(
                            #         user=user_in_other_org,
                            #         organization=organization,
                            #         is_active=True
                            #     ).first()

                            #     if existing_invite:
                            #         errors.append(
                            #             {
                            #                 "email": member_data["email"],
                            #                 "error": "User is already invited to this organization",
                            #             }
                            #         )
                            #         continue

                            #     # Create invitation
                            #     OrganizationMembership.objects.create(
                            #         user=user_in_other_org,
                            #         organization=organization,
                            #         role=member_data["organization_role"],
                            #         invited_by=request.user,
                            #         is_active=True
                            #     )

                            #     # Add user to workspace
                            #     self._add_user_to_workspace(
                            #         user_in_other_org,
                            #         workspace,
                            #         member_data.get("workspace_role", OrganizationRoles.WORKSPACE_MEMBER),
                            #         request.user
                            #     )

                            #     # Send invitation email to existing user
                            #     email_helper(
                            #         f"You are invited to join {organization.display_name if organization.display_name else organization.name} - Future AGI",
                            #         "existing_user_invite.html",
                            #         {
                            #             "org_name": organization.display_name or organization.name,
                            #             "workspace_name": workspace.name,
                            #             "invited_by": request.user.name,
                            #             "app_url": settings.APP_URL,
                            #             "ssl": ssl,
                            #         },
                            #         [member_data["email"]],
                            #     )

                            #     created_members.append(UserSerializer(user_in_other_org).data)
                            #     continue

                            # except Exception as e:
                            #     errors.append(
                            #         {
                            #             "email": member_data["email"],
                            #             "error": f"Failed to invite existing user: {str(e)}",
                            #         }
                            #     )
                            #     continue

                        # Create new user for this organization
                        new_member = User.objects.create(
                            email=member_data["email"],
                            name=member_data["name"],
                            organization=organization,
                            organization_role=org_role,  # None for workspace-level roles
                            is_active=False,
                            invited_by=request.user,
                        )
                        password = generate_password()
                        new_member.set_password(password)
                        new_member.save()

                        # Add user to workspace with determined workspace role
                        self._add_user_to_workspace(
                            new_member,
                            workspace,
                            workspace_role,
                            request.user,
                        )

                        token = default_token_generator.make_token(new_member)
                        uidb64 = urlsafe_base64_encode(force_bytes(new_member.pk))
                        email_helper(
                            f"You are invited by {organization.display_name if organization.display_name else organization.name} - Future AGI",
                            "member_invite.html",
                            {
                                "password": password,
                                "email": member_data["email"],
                                "uid": str(uidb64),
                                "token": token,
                                "workspace_name": workspace.name,
                                "app_url": settings.APP_URL,
                                "ssl": ssl,
                            },
                            [member_data["email"]],
                        )
                        created_members.append(UserSerializer(new_member).data)

                    except IntegrityError:
                        errors.append(
                            {
                                "index": index,
                                "email": member_data.get("email"),
                                "error": get_error_message("EMAIL_ALREADY_EXIST"),
                            }
                        )
                else:
                    errors.append(
                        {
                            "index": index,
                            "email": member_data.get("email"),
                            "error": parse_serialized_errors(serializer),
                        }
                    )

            response_data = {
                "created_members": created_members,
                "workspace": {
                    "id": str(workspace.id),
                    "name": workspace.name,
                    "display_name": workspace.display_name,
                },
            }

            properties = get_mixpanel_properties(
                user=request.user, mode=MixpanelModes.EMAIL.value
            )
            track_mixpanel_event(MixpanelEvents.SET_UP_ORG.value, properties)

            if errors:
                response_data["errors"] = errors
                return self._gm.bad_request(response_data)

            return self._gm.create_response(response_data)
        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in managing users: {str(e)}")
            return self._gm.bad_request("Error in managing users")

    def _add_user_to_workspace(self, user, workspace, role, invited_by):
        """Helper method to add user to workspace"""
        try:
            # Check if user is already a member of this workspace
            existing_membership = WorkspaceMembership.no_workspace_objects.filter(
                workspace=workspace, user=user, is_active=True
            ).first()

            if existing_membership:
                # Update existing membership role if different
                if existing_membership.role != role:
                    existing_membership.role = role
                    existing_membership.save()
            else:
                # Check if there's a soft-deleted membership that we should reactivate
                existing_deleted_membership = WorkspaceMembership.all_objects.filter(
                    workspace=workspace,
                    user=user,
                    deleted=True,
                ).first()

                if existing_deleted_membership:
                    # Reactivate the soft-deleted membership
                    existing_deleted_membership.deleted = False
                    existing_deleted_membership.is_active = True
                    existing_deleted_membership.role = role
                    existing_deleted_membership.invited_by = invited_by
                    existing_deleted_membership.save()
                else:
                    # Create new workspace membership
                    WorkspaceMembership.no_workspace_objects.create(
                        workspace=workspace,
                        user=user,
                        role=role,
                        invited_by=invited_by,
                        is_active=True,
                    )
        except Exception as e:
            logger.error(
                f"Failed to add user {user.email} to workspace {workspace.name}: {str(e)}"
            )
            raise

    def delete(self, request, member_id, *args, **kwargs):
        user = request.user
        organization = resolve_org(request)
        org_role = resolve_org_role(user, organization)
        if not org_role or org_role != OrganizationRoles.OWNER:
            return self._gm.forbidden_response(get_error_message("UNAUTHORIZED_ACCESS"))

        if not organization:
            return self._gm.bad_request(
                get_error_message("USER_ORGANIZATION_CONNECTION_ERROR")
            )

        if not member_id:
            return self._gm.bad_request(get_error_message("MEMBER_ID_NOT_MENTIONED"))

        # Get optional workspace_id from query params
        workspace_id = request.query_params.get("workspace_id")
        workspace = None
        if workspace_id:
            try:
                workspace = Workspace.objects.get(
                    id=workspace_id, organization=organization, is_active=True
                )
            except Workspace.DoesNotExist:
                return self._gm.bad_request("Invalid workspace ID")

        # Check if member is a primary member or invited member
        member = get_object_or_404(User, id=member_id)

        if member == user:
            return self._gm.bad_request(get_error_message("USER_CANNOT_REMOVE_ORG"))

        # Check if member belongs to this organization
        if member.organization == organization:
            # Primary member - remove from organization (this will delete the user entirely)
            if workspace:
                # Remove from specific workspace only
                try:
                    workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                        workspace=workspace, user=member, is_active=True
                    )
                    workspace_membership.is_active = False
                    workspace_membership.save()
                    return self._gm.success_response(
                        {
                            "message": f"Member removed from workspace '{workspace.name}' successfully.",
                            "removed_from": "workspace_only",
                        }
                    )
                except WorkspaceMembership.DoesNotExist:
                    return self._gm.bad_request(
                        f"Member is not part of workspace '{workspace.name}'"
                    )
            else:
                # Remove from all workspaces and organization (full removal)
                WorkspaceMembership.no_workspace_objects.filter(
                    user=member, workspace__organization=organization
                ).update(is_active=False)

                # Clear Redis cache for immediate logout before deleting user
                clear_user_redis_cache(member.id)

                member.delete()
                return self._gm.success_response(
                    {
                        "message": "Member removed from all workspaces and organization successfully.",
                        "removed_from": "all_workspaces_and_organization",
                    }
                )

        elif member.invited_organizations.filter(id=organization.id).exists():
            # Invited member - remove from organization membership
            try:
                membership = OrganizationMembership.objects.get(
                    user=member, organization=organization, is_active=True
                )

                if workspace:
                    # Remove from specific workspace only
                    try:
                        workspace_membership = (
                            WorkspaceMembership.no_workspace_objects.get(
                                workspace=workspace, user=member, is_active=True
                            )
                        )
                        workspace_membership.is_active = False
                        workspace_membership.save()
                        return self._gm.success_response(
                            {
                                "message": f"Invited member removed from workspace '{workspace.name}' successfully.",
                                "removed_from": "workspace_only",
                            }
                        )
                    except WorkspaceMembership.DoesNotExist:
                        return self._gm.bad_request(
                            f"Invited member is not part of workspace '{workspace.name}'"
                        )
                else:
                    # Remove from all workspaces and organization invitation
                    WorkspaceMembership.no_workspace_objects.filter(
                        user=member, workspace__organization=organization
                    ).update(is_active=False)
                    membership.delete()
                    return self._gm.success_response(
                        {
                            "message": "Invited member removed from all workspaces and organization successfully.",
                            "removed_from": "all_workspaces_and_organization",
                        }
                    )

            except OrganizationMembership.DoesNotExist:
                return self._gm.bad_request("Member not found in organization")
        else:
            return self._gm.bad_request("Member not found in organization")
