"""Resolve traces for projects in the tool context."""

from uuid import UUID

from django.db.models import Q

from accounts.saml_credential import SamlCredential
from ai_tools.base import BackgroundExecutionScope, ToolContext, ToolResult
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorAnalysis


def get_analysis_projects(context: ToolContext):
    """Projects in the caller's organization and workspace."""
    if context.organization is None or context.workspace is None:
        return Project.objects.none()
    if context.workspace.organization_id != context.organization.id:
        return Project.objects.none()

    # Replacing or reconstructing the mutable tool context may never widen a
    # captured assignment. Ordinary foreground contexts keep their existing rules.
    scope = context.background_execution_scope
    if scope is not None and (
        not isinstance(scope, BackgroundExecutionScope)
        or context.user is None
        or context.user_id != scope.initiating_user_id
        or context.organization_id != scope.org_id
        or context.workspace_id != scope.workspace_id
        or context.project is None
        or context.project.pk != scope.project_id
        or context.assigned_trace_id != scope.assigned_trace_id
        or not isinstance(context.auth_scope, SamlCredential)
        or context.auth_scope.user_id != context.user_id
        or context.auth_scope.org_id != context.organization_id
        or context.auth_scope != scope.auth_scope
    ):
        return Project.objects.none()

    workspace_scope = Q(workspace=context.workspace)
    if context.workspace.is_default:
        workspace_scope |= Q(workspace__isnull=True)
    projects = Project.objects.filter(
        workspace_scope, organization=context.organization
    )
    if context.project is not None:
        projects = projects.filter(pk=context.project.pk)
    return projects


def allows_trace_assignment(trace_id, context: ToolContext) -> bool:
    return context.assigned_trace_id is None or (
        context.project is not None and str(trace_id) == context.assigned_trace_id
    )


def get_trace_project(trace_id, context: ToolContext) -> Project | None:
    from tracer.services.clickhouse.v2 import get_reader

    if not allows_trace_assignment(trace_id, context):
        return None

    allowed = {str(project.pk): project for project in get_analysis_projects(context)}
    # The reader applies no project filter to an empty list, so return first.
    if not allowed:
        return None
    with get_reader() as reader:
        roots = reader.root_ids_by_trace_ids([str(trace_id)], project_ids=list(allowed))
    root = roots.get(str(trace_id))
    return allowed.get(str(root[1])) if root else None


def select_analysis_for_write(
    context: ToolContext,
    project: Project,
    trace_id: UUID,
    analysis_id: UUID | None,
    *,
    reuse_latest: bool,
) -> TraceErrorAnalysis | ToolResult | None:
    """Resolve within the checked trace/project; None permits the caller to create."""
    analyses = TraceErrorAnalysis.objects.filter(
        trace_id=trace_id, project=project, project__organization=context.organization
    )
    scope = context.background_execution_scope
    if scope is not None:
        if analysis_id is not None and analysis_id != scope.analysis_attempt_id:
            return ToolResult.not_found("Analysis", str(analysis_id))
        analysis = analyses.filter(pk=scope.analysis_attempt_id).first()
        if analysis is None:
            return ToolResult.not_found("Analysis", str(scope.analysis_attempt_id))
        return analysis

    if analysis_id is not None:
        analysis = analyses.filter(pk=analysis_id).first()
        if analysis is not None:
            return analysis
        if context.assigned_trace_id is not None:
            return ToolResult.not_found("Analysis", str(analysis_id))

    # Preserve the foreground policies: scores reuse the latest row, findings
    # start a new analysis. Neither fallback is available to a captured attempt.
    return analyses.order_by("-analysis_date").first() if reuse_latest else None
