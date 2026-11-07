"""Explicit-tenant current-principal checks, also safe in background workers."""

from __future__ import annotations

from typing import Protocol

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.skills_hub import HubSkill
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from tfc.constants.levels import Level


class AuthenticatedRequest(Protocol):
    organization: object
    workspace: Workspace
    user: User


def _deny(
    code: str = "access_revoked",
    message: str = "Current access to this workspace is required.",
) -> None:
    raise SkillsHubError(code, message, status=403)


def require_valid_scope(*, scope: SkillScope) -> None:
    if (
        not scope.organization_id
        or not scope.workspace_id
        or not Workspace.no_workspace_objects.filter(
            pk=scope.workspace_id,
            organization_id=scope.organization_id,
            is_active=True,
        ).exists()
    ):
        _deny("workspace_required", "Select an active workspace in this organization.")


def _current_key(*, principal: SkillPrincipal) -> OrgApiKey:
    key = (
        OrgApiKey.no_workspace_objects.filter(
            pk=principal.api_key_id,
            organization_id=principal.scope.organization_id,
            enabled=True,
        )
        .only("id", "organization_id", "workspace_id", "user_id", "type")
        .first()
    )
    if key is None or (
        key.workspace_id is not None
        and key.workspace_id != principal.scope.workspace_id
    ):
        _deny()
    expected_kind = {"system": "system_key", "user": "user_key", "mcp": "mcp_key"}.get(
        key.type
    )
    if expected_kind != principal.kind or (
        key.type != "system" and (not key.user_id or key.user_id != principal.user_id)
    ):
        _deny()
    return key


def principal_from_request(request: AuthenticatedRequest) -> SkillPrincipal:
    organization = getattr(request, "organization", None)
    workspace = getattr(request, "workspace", None)
    if (
        organization is None
        or workspace is None
        or workspace.organization_id != organization.pk
    ):
        _deny("workspace_required", "Select a workspace to use Skills.")
    scope = SkillScope(organization_id=organization.pk, workspace_id=workspace.pk)
    key = getattr(request, "org_api_key", None)
    if key is not None:
        kind = {"system": "system_key", "user": "user_key", "mcp": "mcp_key"}.get(
            key.type
        )
        if kind is None:
            _deny("principal_not_supported", "This key cannot use Skills.")
        principal = SkillPrincipal(
            scope=scope,
            user_id=None if kind == "system_key" else key.user_id,
            api_key_id=key.pk,
            kind=kind,
        )
        _current_key(principal=principal)
        if kind != "system_key" and getattr(request.user, "pk", None) != key.user_id:
            _deny()
    else:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            _deny()
        principal = SkillPrincipal(
            scope=scope, user_id=user.pk, api_key_id=None, kind="human"
        )
    require_scope_access(principal=principal, write=False)
    return principal


def require_private_principal(*, principal: SkillPrincipal) -> None:
    if (
        principal.kind == "system_key"
        or principal.kind not in {"human", "user_key", "mcp_key"}
        or principal.user_id is None
    ):
        _deny(
            "principal_not_supported",
            "A real user or a user-bound key is required for private Skills resources.",
        )


def effective_scope_level(*, principal: SkillPrincipal) -> int:
    """Match central RBAC without ambient organization or synthetic-user fallback."""
    require_valid_scope(scope=principal.scope)
    require_private_principal(principal=principal)
    if principal.kind == "human":
        if principal.api_key_id is not None:
            _deny()
    else:
        _current_key(principal=principal)
    if not User.objects.filter(pk=principal.user_id, is_active=True).exists():
        _deny()
    membership = OrganizationMembership.no_workspace_objects.filter(
        organization_id=principal.scope.organization_id,
        user_id=principal.user_id,
        is_active=True,
    ).first()
    if membership is None:
        _deny()
    org_level = membership.level_or_legacy
    if org_level >= Level.ADMIN:
        return org_level
    workspace_membership = WorkspaceMembership.no_workspace_objects.filter(
        workspace_id=principal.scope.workspace_id,
        workspace__organization_id=principal.scope.organization_id,
        user_id=principal.user_id,
        is_active=True,
    ).first()
    if workspace_membership is None or (
        workspace_membership.organization_membership_id is not None
        and workspace_membership.organization_membership_id != membership.pk
    ):
        _deny()
    return max(org_level, workspace_membership.level_or_legacy)


def require_scope_access(*, principal: SkillPrincipal, write: bool) -> None:
    require_valid_scope(scope=principal.scope)
    if principal.kind == "system_key":
        if principal.user_id is not None:
            _deny("principal_not_supported")
        _current_key(principal=principal)
        return  # Published consumption/unprotected push only; private entrypoints guard first.
    level = effective_scope_level(principal=principal)
    if level < (Level.WORKSPACE_MEMBER if write else Level.WORKSPACE_VIEWER):
        _deny(
            "permission_denied",
            "Write access is required." if write else "Workspace access is required.",
        )


def require_skill_admin(*, principal: SkillPrincipal, skill: HubSkill) -> None:
    require_private_principal(principal=principal)
    if (
        skill.organization_id != principal.scope.organization_id
        or skill.workspace_id != principal.scope.workspace_id
        or skill.deleted
    ):
        raise SkillsHubError("resource_not_found", "Skill not found.", status=404)
    if effective_scope_level(principal=principal) < Level.WORKSPACE_ADMIN:
        _deny(
            "protected_label_forbidden",
            "A current workspace or organization administrator is required.",
        )
