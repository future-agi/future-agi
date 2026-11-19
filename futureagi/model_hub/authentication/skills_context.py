"""Opt-in Context authentication. Never delegates to a synthetic user."""

from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.credentials import (
    authenticate_credential,
    require_active_credential,
)
from model_hub.services.skills_hub.v12.types import ContextCredentialPrincipal


class ContextCredentialAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return "Context"

    def authenticate(self, request):
        authorization = request.headers.get("Authorization", "")
        if not authorization:
            return None
        parts = authorization.split()
        if not parts or parts[0].lower() != "context":
            return None
        if len(parts) != 2 or len(authorization) > 256:
            raise SkillsHubError(
                "context_credential_invalid", "Invalid Context credential.", status=401
            )
        if any(
            request.headers.get(name) for name in ("X-Api-Key", "X-Secret-Key")
        ) or any(
            name in request.query_params for name in ("api_key", "secret_key", "token")
        ):
            raise SkillsHubError(
                "context_credential_invalid",
                "Use only one authentication scheme.",
                status=401,
            )
        principal = authenticate_credential(token=parts[1])
        for field, expected in (
            ("X-Workspace-Id", principal.scope.workspace_id),
            ("X-Organization-Id", principal.scope.organization_id),
        ):
            supplied = request.headers.get(field)
            if supplied and supplied != str(expected):
                raise SkillsHubError(
                    "invalid_credential_scope",
                    "The scope header conflicts with this credential.",
                    status=403,
                )
        for field, expected in (
            ("workspace_id", principal.scope.workspace_id),
            ("organization_id", principal.scope.organization_id),
        ):
            supplied = request.query_params.get(field)
            if supplied and supplied != str(expected):
                raise SkillsHubError(
                    "invalid_credential_scope",
                    "The requested scope conflicts with this credential.",
                    status=403,
                )
        return principal, principal


class ContextPurposePermission(BasePermission):
    def has_permission(self, request, view):
        if not isinstance(request.auth, ContextCredentialPrincipal):
            raise SkillsHubError(
                "context_credential_invalid",
                "A Context credential is required.",
                status=401,
            )
        purpose = getattr(view, "context_purpose", None)
        if purpose not in {"consume", "report"}:
            raise SkillsHubError(
                "credential_operation_forbidden",
                "Context authentication is not enabled here.",
                status=403,
            )
        require_active_credential(principal=request.auth, purpose=purpose)
        return True
