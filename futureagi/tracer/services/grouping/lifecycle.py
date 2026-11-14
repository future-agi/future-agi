"""Synchronous source invalidation for replaced Omega reports."""

import uuid

from django.db.models import F
from django.utils import timezone

from tracer.models.trace_error_analysis import ErrorClusterTraces
from tracer.models.trace_grouping import (
    GroupingAttemptState,
    GroupingWorkState,
    TraceGroupingAttempt,
    TraceGroupingFindingState,
    TraceGroupingIssueState,
    TraceGroupingOutbox,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import (
    TraceInvestigationFinding,
    TraceInvestigationReport,
)
from tracer.services.grouping.control import GroupingConflict
from tracer.services.grouping.publish import _protected, _recount


def deproject_superseded_report(
    *, old_report_id: uuid.UUID, successor_report_id: uuid.UUID
) -> dict:
    """Run inside the investigation publication transaction after successor activation."""
    old = TraceInvestigationReport.no_workspace_objects.select_related("project").get(
        pk=old_report_id
    )
    successor = TraceInvestigationReport.no_workspace_objects.get(
        pk=successor_report_id
    )
    if (
        old.project_id != successor.project_id
        or old.trace_id != successor.trace_id
        or old.is_current
    ):
        raise GroupingConflict("source replacement identity is invalid")
    scope = (
        TraceGroupingScope.no_workspace_objects.select_for_update()
        .filter(project_id=old.project_id)
        .first()
    )
    if scope is None:
        return {"removed": 0, "registry_revision": None}
    if (
        scope.organization_id != old.organization_id
        or scope.workspace_id != old.workspace_id
    ):
        raise GroupingConflict("replaced report tenant differs from grouping scope")
    now = timezone.now()
    old_works = list(
        TraceGroupingWork.no_workspace_objects.filter(scope=scope, report=old)
    )
    if old_works:
        # One project lease: invalidate the entire in-flight cohort, including
        # peer work, then leave unaffected reports due for a fresh claim.
        TraceGroupingAttempt.no_workspace_objects.filter(
            work__scope=scope,
            state=GroupingAttemptState.CLAIMED,
        ).update(state=GroupingAttemptState.EXPIRED, updated_at=now)
        TraceGroupingWork.no_workspace_objects.filter(
            scope=scope,
            state=GroupingWorkState.RUNNING,
        ).exclude(report=old).update(
            state=GroupingWorkState.PENDING, not_before=now, updated_at=now
        )
        TraceGroupingWork.no_workspace_objects.filter(scope=scope, report=old).update(
            state=GroupingWorkState.SUPERSEDED,
            updated_at=now,
        )
        scope.lease_fence = F("lease_fence") + 1
        scope.pending_revision = F("pending_revision") + 1
        scope.save(update_fields=["lease_fence", "pending_revision", "updated_at"])
    finding_ids = list(
        TraceInvestigationFinding.no_workspace_objects.filter(
            report=old, cluster__issue_state__scope=scope
        )
        .order_by("id")
        .values_list("id", flat=True)[:101]
    )
    if len(finding_ids) > 100:
        raise GroupingConflict("replaced report exceeds bounded Omega finding limit")
    touched_ids = set(
        TraceInvestigationFinding.no_workspace_objects.filter(
            id__in=finding_ids
        ).values_list("cluster_id", flat=True)
    )
    states = list(
        TraceGroupingIssueState.no_workspace_objects.select_for_update()
        .select_related("cluster")
        .filter(scope=scope, cluster_id__in=touched_ids)
        .order_by("cluster_id")
    )
    if len(states) != len(touched_ids):
        raise GroupingConflict("replaced F6 membership has no owned issue state")
    findings = list(
        TraceInvestigationFinding.no_workspace_objects.select_for_update(of=("self",))
        .filter(id__in=finding_ids, report=old, cluster_id__in=touched_ids)
        .order_by("id")
    )
    if [item.id for item in findings] != finding_ids:
        raise GroupingConflict("replaced report membership changed during invalidation")
    for state in states:
        state.dirty = True
        state.save(update_fields=["dirty", "updated_at"])
    for finding in findings:
        ErrorClusterTraces.no_workspace_objects.filter(
            finding=finding,
            cluster_id=finding.cluster_id,
        ).update(deleted=True, deleted_at=now, updated_at=now)
        finding.cluster = None
        finding.save(update_fields=["cluster", "updated_at"])
        TraceGroupingFindingState.no_workspace_objects.filter(finding=finding).update(
            disposition="superseded",
            reason="source report replaced",
            updated_at=now,
        )
    for state in states:
        remaining = list(
            TraceInvestigationFinding.no_workspace_objects.filter(
                cluster=state.cluster,
            )
            .order_by("id")
            .values_list("id", flat=True)[:5]
        )
        state.prototype_occurrence_ids = [
            item
            for item in state.prototype_occurrence_ids
            if TraceInvestigationFinding.no_workspace_objects.filter(
                id=uuid.UUID(item), cluster=state.cluster
            ).exists()
        ]
        if remaining and not state.prototype_occurrence_ids:
            state.prototype_occurrence_ids = [str(remaining[0])]
        if not remaining and not _protected(state):
            state.retired = True
        state.revision += 1
        state.membership_revision += 1
        state.dirty = False
        state.save(
            update_fields=[
                "prototype_occurrence_ids",
                "retired",
                "revision",
                "membership_revision",
                "dirty",
                "updated_at",
            ]
        )
        _recount(state)
    if states:
        scope.registry_revision = F("registry_revision") + 1
        scope.save(update_fields=["registry_revision", "updated_at"])
        scope.refresh_from_db(fields=["registry_revision"])
        TraceGroupingOutbox.no_workspace_objects.get_or_create(
            scope=scope,
            event_kind="error-feed.grouping-invalidation.v1",
            source_id=old.id,
            revision=scope.registry_revision,
        )
    return {"removed": len(findings), "registry_revision": scope.registry_revision}
