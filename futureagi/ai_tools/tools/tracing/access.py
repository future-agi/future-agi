"""Resolve CH-only traces within the tool caller's trusted tenant scope."""

from django.db.models import Q

from ai_tools.base import ToolContext
from tracer.models.project import Project


def get_analysis_projects(context: ToolContext):
    """Authorized project set, including legacy projects in the default workspace."""
    if context.organization is None or context.workspace is None:
        return Project.objects.none()
    if context.workspace.organization_id != context.organization.id:
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


def get_trace_project(trace_id, context: ToolContext) -> Project | None:
    from tracer.services.clickhouse.v2 import get_reader

    allowed = {str(project.pk): project for project in get_analysis_projects(context)}
    # CH treats an empty project_ids list as unscoped: never send one.
    if not allowed:
        return None
    with get_reader() as reader:
        roots = reader.root_ids_by_trace_ids([str(trace_id)], project_ids=list(allowed))
    root = roots.get(str(trace_id))
    return allowed.get(str(root[1])) if root else None
