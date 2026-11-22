"""Read-only checks for a server-owned Falcon assignment. No dispatch or metering.

Only the canonical SAML credential carries a revalidatable token reference on
this branch. None (ordinary bearer), API-key and MCP scopes cannot be promoted
from a user ID. There is deliberately no queue decoder or system principal.
"""

from dataclasses import replace
from uuid import UUID

from django.utils import timezone

from accounts.models.auth_token import AuthToken, AuthTokenOrigin
from accounts.models.user import User
from accounts.models.workspace import Workspace
from accounts.saml_credential import (
    SamlCredential,
    SamlCredentialInvalid,
    validate_saml_credential,
)
from ai_tools.base import BackgroundExecutionScope, ToolContext
from ai_tools.tools.tracing.access import get_trace_project
from tfc.ee_gating import EEFeature, check_ee_feature
from tracer.models.trace_error_analysis import TraceErrorAnalysis


class BackgroundScopeDenied(PermissionError):
    error_code = "FALCON_BACKGROUND_SCOPE_DENIED"


def capture_background_execution_scope(
    foreground: ToolContext, *, attempt: TraceErrorAnalysis
) -> BackgroundExecutionScope:
    """Build from the authenticated context and assigned server row, never tool args."""
    if foreground.user is None or not foreground.user.is_authenticated:
        raise BackgroundScopeDenied("Background analysis requires an initiating user.")
    if foreground.organization is None or foreground.workspace is None:
        raise BackgroundScopeDenied("Background analysis requires an active workspace.")
    if not isinstance(foreground.auth_scope, SamlCredential):
        raise BackgroundScopeDenied("A revalidatable initiating credential is required.")
    if (
        foreground.background_execution_scope is not None
        or foreground.workspace.organization_id != foreground.organization_id
        or (
            foreground.project is not None
            and foreground.project.pk != attempt.project_id
        )
        or foreground.assigned_trace_id not in (None, str(attempt.trace_id))
    ):
        raise BackgroundScopeDenied("Assignment is outside the initiating context.")

    scope = BackgroundExecutionScope(
        org_id=foreground.organization_id,
        workspace_id=foreground.workspace_id,
        project_id=attempt.project_id,
        assigned_trace_id=str(attempt.trace_id),
        initiating_user_id=foreground.user_id,
        analysis_attempt_id=attempt.pk,
        auth_scope=foreground.auth_scope,
    )
    revalidate_background_execution_scope(
        scope,
        project_id=str(attempt.project_id),
        trace_id=str(attempt.trace_id),
        analysis_attempt_id=attempt.pk,
    )
    return scope


def _require_live_credential(scope: BackgroundExecutionScope) -> None:
    credential = scope.auth_scope
    if (
        not isinstance(credential, SamlCredential)
        or credential.org_id != scope.org_id
        or credential.user_id != scope.initiating_user_id
    ):
        raise BackgroundScopeDenied("Initiating credential does not match the assignment.")
    row = (
        AuthToken.objects.filter(pk=credential.token_id, auth_origin=AuthTokenOrigin.SAML)
        .values()
        .first()
    )
    try:
        live = validate_saml_credential(
            row,
            str(scope.initiating_user_id),
            now=timezone.now(),
            transport="falcon_background",
        )
    except SamlCredentialInvalid as exc:
        raise BackgroundScopeDenied("Initiating credential is no longer valid.") from exc
    # A new validation instant is expected; no other credential binding may change.
    if replace(live, validated_at=credential.validated_at) != credential:
        raise BackgroundScopeDenied("Initiating credential binding changed.")


def revalidate_background_execution_scope(
    scope: BackgroundExecutionScope | None,
    *,
    project_id: str,
    trace_id: str,
    analysis_attempt_id: UUID | None,
) -> ToolContext:
    """Rebuild from live records and the independently assigned job identity.

    Accept only an in-process trusted capture. Even a valid result does not
    satisfy readiness, prove an active claim, or authorize a provider call.
    """
    if not isinstance(scope, BackgroundExecutionScope) or scope.initiating_user_id is None:
        raise BackgroundScopeDenied("Background analysis requires an initiating user and scope.")
    if (
        analysis_attempt_id is None
        or analysis_attempt_id != scope.analysis_attempt_id
        or project_id != str(scope.project_id)
        or trace_id != scope.assigned_trace_id
    ):
        raise BackgroundScopeDenied("Background analysis assignment mismatch.")
    _require_live_credential(scope)

    user = User.objects.filter(pk=scope.initiating_user_id, is_active=True).first()
    workspace = (
        Workspace.objects.select_related("organization")
        .filter(pk=scope.workspace_id, organization_id=scope.org_id, is_active=True)
        .first()
    )
    # The same live write-role check used for a foreground workspace mutation
    # includes membership/access. Falcon entitlement is separate from that role.
    if user is None or workspace is None or not user.can_write_to_workspace(workspace):
        raise BackgroundScopeDenied("Initiator no longer has workspace write access.")
    check_ee_feature(EEFeature.FALCON_AI, org_id=str(scope.org_id))

    attempt = (
        TraceErrorAnalysis.objects.select_related("project")
        .filter(
            pk=analysis_attempt_id,
            project_id=scope.project_id,
            trace_id=trace_id,
            project__organization_id=scope.org_id,
        )
        .first()
    )
    if attempt is None:
        raise BackgroundScopeDenied("Background analysis assignment is not current.")
    context = ToolContext(
        user=user,
        organization=workspace.organization,
        workspace=workspace,
        project=attempt.project,
        assigned_trace_id=trace_id,
        auth_scope=scope.auth_scope,
        background_execution_scope=scope,
    )
    # Existing foreground project rules (including the default workspace) and
    # an exact-project root lookup. Never resolve a repeated trace ID globally.
    if get_trace_project(trace_id, context) is None:
        raise BackgroundScopeDenied("Assigned trace is not accessible in this project.")
    return context
