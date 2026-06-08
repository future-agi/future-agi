from dataclasses import dataclass

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.onboarding.goals import resolve_goal_for_context
from tfc.constants.levels import Level
from tfc.constants.roles import RoleMapping, RolePermissions


@dataclass(frozen=True)
class OnboardingContext:
    user: object
    organization: object | None
    workspace: object | None
    organization_role: str | None
    workspace_role: str | None
    organization_level: int
    workspace_level: int
    selected_goal: str | None
    primary_path: str | None
    persona: str | None
    source: str
    email_context: dict | None
    permissions: dict
    warnings: list[str]


def _active_org_membership(user, organization):
    if not user or not organization:
        return None
    return (
        OrganizationMembership.no_workspace_objects.filter(
            user=user,
            organization=organization,
            is_active=True,
        )
        .order_by("-created_at")
        .first()
    )


def _workspace_membership(user, workspace):
    if not user or not workspace:
        return None
    return (
        WorkspaceMembership.no_workspace_objects.filter(
            user=user,
            workspace=workspace,
            is_active=True,
        )
        .order_by("-created_at")
        .first()
    )


def _organization_level(role, membership):
    if membership and membership.level is not None:
        return membership.level
    return Level.STRING_TO_LEVEL.get(role, 0)


def _workspace_level(role, membership):
    if membership and membership.level is not None:
        return membership.level
    return Level.STRING_TO_LEVEL.get(role, 0)


def _is_global_role(role):
    return role in RolePermissions.GLOBAL_ACCESS_ROLES


def _can_access_workspace(*, user, organization_role, workspace):
    if not user or not workspace:
        return False
    if _is_global_role(organization_role):
        return True
    return _workspace_membership(user, workspace) is not None


def _resolve_organization(request, user, warnings):
    request_org = getattr(request, "organization", None)
    query_org_id = request.query_params.get("organization_id")

    if query_org_id:
        membership = (
            OrganizationMembership.no_workspace_objects.select_related("organization")
            .filter(user=user, organization_id=query_org_id, is_active=True)
            .first()
        )
        if membership:
            return membership.organization
        warnings.append("organization_query_ignored")

    if request_org and _active_org_membership(user, request_org):
        return request_org

    user_org = getattr(user, "organization", None)
    if user_org and _active_org_membership(user, user_org):
        return user_org

    membership = (
        OrganizationMembership.no_workspace_objects.select_related("organization")
        .filter(user=user, is_active=True)
        .order_by("-created_at")
        .first()
    )
    return membership.organization if membership else None


def _workspace_from_user_config(user, organization):
    config = getattr(user, "config", None) or {}
    workspace_id = config.get("currentWorkspaceId") or config.get("defaultWorkspaceId")
    if not workspace_id or not organization:
        return None
    return (
        Workspace.no_workspace_objects.filter(
            id=workspace_id,
            organization=organization,
            is_active=True,
        )
        .order_by("-created_at")
        .first()
    )


def _default_workspace(organization):
    if not organization:
        return None
    return (
        Workspace.no_workspace_objects.filter(
            organization=organization,
            is_active=True,
        )
        .order_by("-is_default", "-created_at")
        .first()
    )


def _resolve_workspace(request, user, organization, organization_role, warnings):
    query_workspace_id = request.query_params.get("workspace_id")
    if query_workspace_id and organization:
        workspace = (
            Workspace.no_workspace_objects.filter(
                id=query_workspace_id,
                organization=organization,
                is_active=True,
            )
            .order_by("-created_at")
            .first()
        )
        if workspace and _can_access_workspace(
            user=user,
            organization_role=organization_role,
            workspace=workspace,
        ):
            return workspace
        warnings.append("workspace_query_ignored")

    request_workspace = getattr(request, "workspace", None)
    if (
        request_workspace
        and organization
        and request_workspace.organization_id == organization.id
        and _can_access_workspace(
            user=user,
            organization_role=organization_role,
            workspace=request_workspace,
        )
    ):
        return request_workspace

    config_workspace = _workspace_from_user_config(user, organization)
    if config_workspace and _can_access_workspace(
        user=user,
        organization_role=organization_role,
        workspace=config_workspace,
    ):
        return config_workspace

    workspace = _default_workspace(organization)
    if workspace and _can_access_workspace(
        user=user,
        organization_role=organization_role,
        workspace=workspace,
    ):
        return workspace
    return None


def _persona(user):
    config = getattr(user, "config", None) or {}
    onboarding_config = config.get("onboarding", {}) or {}
    return getattr(user, "role", None) or onboarding_config.get("role")


def _build_permissions(
    *,
    organization_role,
    workspace_role,
    organization_level,
    workspace_level,
):
    effective_level = max(organization_level, workspace_level)
    effective_role = workspace_role or organization_role
    if _is_global_role(organization_role):
        effective_role = organization_role
        effective_level = max(effective_level, Level.ADMIN)

    can_read = (
        effective_role in RolePermissions.READ_ACCESS_ROLES
        or effective_level >= Level.WORKSPACE_VIEWER
    )
    can_write = (
        effective_role in RolePermissions.WRITE_ACCESS_ROLES
        or effective_level >= Level.WORKSPACE_MEMBER
    )
    can_manage_workspace = (
        effective_role in RolePermissions.ADMIN_ROLES
        or effective_level >= Level.WORKSPACE_ADMIN
    )

    missing_permissions = []
    if not can_write:
        missing_permissions.append("workspace:write")
    if not can_manage_workspace:
        missing_permissions.append("workspace:manage")

    return {
        "role": str(effective_role) if effective_role else None,
        "can_read": can_read,
        "can_write": can_write,
        "can_manage_workspace": can_manage_workspace,
        "missing_permissions": missing_permissions,
        "request_access_href": "/dashboard/settings/user-management",
        "permission_limited": can_read and not can_write,
    }


def _email_context(query_params):
    keys = (
        "campaign_key",
        "email_key",
        "send_log_id",
        "email_status",
        "target_stage",
        "target_event",
        "target_route",
        "link_issued_at",
        "stale_reason",
        "context_status",
    )
    context = {
        key: query_params.get(key)
        for key in keys
        if query_params.get(key) not in {None, ""}
    }
    if "email_status" not in context and query_params.get("status") not in {None, ""}:
        context["email_status"] = query_params.get("status")
    return context or None


def resolve_onboarding_context(request):
    user = request.user
    warnings = []
    organization = _resolve_organization(request, user, warnings)
    org_membership = _active_org_membership(user, organization)
    organization_role = (
        org_membership.role
        if org_membership
        else getattr(user, "organization_role", None)
    )
    organization_level = _organization_level(organization_role, org_membership)
    workspace = _resolve_workspace(
        request,
        user,
        organization,
        organization_role,
        warnings,
    )
    ws_membership = _workspace_membership(user, workspace)
    workspace_role = None
    if ws_membership:
        workspace_role = ws_membership.role
    elif organization_role:
        workspace_role = str(RoleMapping.get_workspace_role(organization_role))
    workspace_level = _workspace_level(workspace_role, ws_membership)
    source = request.query_params.get("source") or "direct"
    goal_context = resolve_goal_for_context(
        user=user,
        organization=organization,
        workspace=workspace,
        requested_goal=request.query_params.get("quick_start_goal"),
        requested_primary_path=request.query_params.get("quick_start_primary_path"),
        source=source,
    )

    return OnboardingContext(
        user=user,
        organization=organization,
        workspace=workspace,
        organization_role=str(organization_role) if organization_role else None,
        workspace_role=str(workspace_role) if workspace_role else None,
        organization_level=organization_level,
        workspace_level=workspace_level,
        selected_goal=goal_context["goal"],
        primary_path=goal_context["primary_path"],
        persona=_persona(user),
        source=source,
        email_context=_email_context(request.query_params),
        permissions=_build_permissions(
            organization_role=organization_role,
            workspace_role=workspace_role,
            organization_level=organization_level,
            workspace_level=workspace_level,
        ),
        warnings=warnings,
    )
