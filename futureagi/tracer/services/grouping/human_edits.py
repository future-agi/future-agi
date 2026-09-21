"""Fence user-owned Feed edits against automatic F6 grouping publication."""

from collections.abc import Callable
from uuid import UUID

from django.db import transaction
from django.db.models import F

from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.models.trace_grouping import TraceGroupingIssueState, TraceGroupingScope


class GroupingHumanEditUnavailable(Exception):
    """The F6 issue was retired, dirty, or changed before a user edit."""


def is_grouping_issue(cluster_pk: UUID) -> bool:
    if not isinstance(cluster_pk, UUID):
        return False
    return TraceGroupingIssueState.no_workspace_objects.filter(
        cluster_id=cluster_pk
    ).exists()


def edit_grouping_issue(
    cluster_pk: UUID,
    mutate: Callable[[TraceErrorGroup], list[str]],
    *,
    protect_on_intent: bool = False,
) -> TraceErrorGroup:
    """Apply a local human edit after scope → issue → cluster locks.

    ``mutate`` must only change local model fields and return their names. It
    must never make an external call while these database locks are held.
    """
    reference = (
        TraceGroupingIssueState.no_workspace_objects.filter(cluster_id=cluster_pk)
        .values("scope_id")
        .first()
    )
    if reference is None:
        raise GroupingHumanEditUnavailable("F6 issue state disappeared")

    with transaction.atomic():
        scope = (
            TraceGroupingScope.no_workspace_objects.select_for_update()
            .filter(pk=reference["scope_id"])
            .first()
        )
        if scope is None:
            raise GroupingHumanEditUnavailable("F6 scope disappeared")
        state = (
            TraceGroupingIssueState.no_workspace_objects.select_for_update()
            .filter(
                cluster_id=cluster_pk,
                scope=scope,
                dirty=False,
                retired=False,
            )
            .first()
        )
        if state is None:
            raise GroupingHumanEditUnavailable("F6 issue is not currently visible")
        cluster = (
            TraceErrorGroup.no_workspace_objects.select_for_update()
            .filter(pk=cluster_pk, project_id=scope.project_id)
            .first()
        )
        if cluster is None:
            raise GroupingHumanEditUnavailable("F6 issue is not currently visible")

        fields = mutate(cluster)
        if fields:
            cluster.save(update_fields=[*fields, "updated_at"])
        if fields or protect_on_intent:
            state.protected = True
            state.revision = F("revision") + 1
            state.save(update_fields=["protected", "revision", "updated_at"])
            scope.registry_revision = F("registry_revision") + 1
            scope.save(update_fields=["registry_revision", "updated_at"])
        return cluster
