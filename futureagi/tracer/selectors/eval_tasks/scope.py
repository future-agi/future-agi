"""Which eval tasks a caller may act on, and which eval configs a task may run.

One tenant scope for every surface that resolves an eval task, an eval config
or their project by id — the eval-task and eval-config endpoints and the AI
tools — so a row in another workspace of the same organization, or in a
deleted project, is refused the same way whichever surface asks. ``EvalTask``
carries no workspace of its own, so the manager's automatic workspace filter
never applies to it; the scope has to go through the task's project.
``CustomEvalConfig`` carries neither a workspace nor an organization, so its
scope goes through its project the same way.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from django.db.models import Q, QuerySet

from tracer.models.custom_eval_config import CustomEvalConfig


def project_workspace_scope_q(
    organization_id, workspace, *, project_prefix: str = "project__"
) -> Q:
    """Rows whose project lies in ``workspace``; ``project_prefix`` is the
    lookup path to that project, ``""`` for projects themselves.

    The default workspace also owns the organization's projects that sit in no
    workspace, and those of any other default workspace in the organization. No
    workspace narrows nothing — the organization filter is the caller's.
    """
    if not workspace:
        return Q()
    if getattr(workspace, "is_default", False):
        return (
            Q(**{f"{project_prefix}workspace": workspace})
            | Q(
                **{
                    f"{project_prefix}workspace__is_default": True,
                    f"{project_prefix}workspace__organization_id": organization_id,
                }
            )
            | Q(
                **{
                    f"{project_prefix}workspace__isnull": True,
                    f"{project_prefix}organization_id": organization_id,
                }
            )
        )
    return Q(**{f"{project_prefix}workspace": workspace})


def projects_in_scope(queryset: QuerySet, *, organization, workspace) -> QuerySet:
    """Narrow ``queryset`` to the live projects ``organization`` may use in
    ``workspace``: the projects an eval task or an eval config may name.

    No organization means nothing.
    """
    if organization is None:
        return queryset.none()
    return queryset.filter(organization_id=organization.id, deleted=False).filter(
        project_workspace_scope_q(organization.id, workspace, project_prefix="")
    )


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


def eval_configs_in_scope(
    queryset: QuerySet,
    *,
    organization,
    workspace,
    project_id: UUID | str | None = None,
) -> QuerySet:
    """Narrow ``queryset`` to the live eval configs ``organization`` may use in
    ``workspace``, of a live project; ``project_id`` narrows them to that
    project's own.

    No organization means nothing.
    """
    if organization is None:
        return queryset.none()
    queryset = queryset.filter(
        deleted=False,
        project__organization_id=organization.id,
        project__deleted=False,
    ).filter(project_workspace_scope_q(organization.id, workspace))
    if project_id:
        queryset = queryset.filter(project_id=project_id)
    return queryset


def eval_config_ids_outside_project(
    eval_ids: Iterable[UUID | str] | None,
    *,
    project_id: UUID | str | None,
    organization,
    workspace,
) -> list[str]:
    """The requested eval config ids that are not live configs of
    ``project_id`` within the caller's scope, sorted.

    An eval task runs only its own project's configs: the reads of its results
    find each config through ``config.project``, so a config of another
    project would run with nothing to show for it. Fails closed — with no
    project, no requested config belongs.
    """
    requested_ids = {str(eval_id) for eval_id in eval_ids or ()}
    if not requested_ids or not project_id:
        return sorted(requested_ids)
    owned_ids = {
        str(config_id)
        for config_id in eval_configs_in_scope(
            CustomEvalConfig.objects.all(),
            organization=organization,
            workspace=workspace,
            project_id=project_id,
        )
        .filter(id__in=requested_ids)
        .values_list("id", flat=True)
    }
    return sorted(requested_ids - owned_ids)


__all__ = [
    "eval_config_ids_outside_project",
    "eval_configs_in_scope",
    "eval_tasks_in_scope",
    "project_workspace_scope_q",
    "projects_in_scope",
]
