"""Which eval tasks a caller may act on.

One tenant scope for every surface that resolves an eval task by id — the
eval-task endpoints and the AI tools — so a task in another workspace of the
same organization, or in a deleted project, is refused the same way whichever
surface asks. ``EvalTask`` carries no workspace of its own, so the manager's
automatic workspace filter never applies to it; the scope has to go through
the task's project.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet


def project_workspace_scope_q(organization_id, workspace) -> Q:
    """Rows whose ``project`` lies in ``workspace``.

    The default workspace also owns the organization's projects that sit in no
    workspace, and those of any other default workspace in the organization. No
    workspace narrows nothing — the organization filter is the caller's.
    """
    if not workspace:
        return Q()
    if getattr(workspace, "is_default", False):
        return (
            Q(project__workspace=workspace)
            | Q(
                project__workspace__is_default=True,
                project__workspace__organization_id=organization_id,
            )
            | Q(
                project__workspace__isnull=True,
                project__organization_id=organization_id,
            )
        )
    return Q(project__workspace=workspace)


def eval_tasks_in_scope(queryset: QuerySet, *, organization, workspace) -> QuerySet:
    """Narrow ``queryset`` to the tasks ``organization`` may act on in
    ``workspace``: its own, in a live project, in that workspace.

    No organization means nothing. For a default workspace the filter reaches
    the project's workspace through an outer join, so a caller that locks the
    result must lock with ``select_for_update(of=("self",))`` — PostgreSQL
    refuses a lock on the nullable side of an outer join.
    """
    if organization is None:
        return queryset.none()
    return queryset.filter(
        project__organization_id=organization.id,
        project__deleted=False,
    ).filter(project_workspace_scope_q(organization.id, workspace))


__all__ = ["eval_tasks_in_scope", "project_workspace_scope_q"]
