"""Reconsider completed simulation deferrals when another batch creates a target."""

from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from tracer.constants.grouping_versions import SIMULATION_GROUPING_POLICY_VERSION
from tracer.models.trace_grouping import (
    GroupingWorkState,
    TraceGroupingFindingState,
    TraceGroupingOutbox,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationGroupingStatus,
    TraceInvestigationReport,
)
from tracer.queries.grouping import GROUPABLE_RECOVERY


def requeue_deferred_reports(*, scope, source_attempt, reviewed_work_ids):
    """Called under the publication's scope lock, only for new/merged issues.

    Pending work coalesces multiple changes. Completed work from this attempt
    already had its within-batch revisit; only earlier deferrals are woken.
    Existing spend and budget ownership survive this fresh comparison window.
    A deferred-only publication does not call this function, so it cannot
    perpetually requeue itself.
    """
    if scope.policy_version != SIMULATION_GROUPING_POLICY_VERSION:
        return 0
    deferred_reports = TraceGroupingFindingState.no_workspace_objects.filter(
        scope=scope,
        disposition="deferred",
        finding__cluster__isnull=True,
        finding__recovery__in=GROUPABLE_RECOVERY,
    ).values("finding__report_id")
    works = list(
        TraceGroupingWork.no_workspace_objects.select_for_update(of=("self",))
        .filter(
            scope=scope,
            state=GroupingWorkState.COMPLETED,
            feature_job__state="ready",
            report_id__in=deferred_reports,
            report__workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
            report__deleted=False,
            report__is_current=True,
            report__source="omega",
            report__execution_status="completed",
            report__job__current_report_id=F("report_id"),
        )
        .exclude(id__in=reviewed_work_ids)
        .order_by("id")
    )
    if not works:
        return 0
    now = timezone.now()
    scope.pending_revision += 1
    scope.save(update_fields=["pending_revision", "updated_at"])
    deadline = now + timedelta(
        seconds=int(getattr(settings, "ERROR_FEED_GROUPING_DEBOUNCE_SECONDS", 5))
    )
    TraceGroupingWork.no_workspace_objects.filter(
        id__in=[work.id for work in works]
    ).update(
        state=GroupingWorkState.PENDING,
        input_revision=scope.pending_revision,
        not_before=deadline,
        retry_start_attempt=F("attempt_number"),
        failure_code="",
        updated_at=now,
    )
    TraceInvestigationReport.no_workspace_objects.filter(
        id__in=[work.report_id for work in works]
    ).update(grouping_status=TraceInvestigationGroupingStatus.PENDING, updated_at=now)
    TraceGroupingOutbox.no_workspace_objects.get_or_create(
        scope=scope,
        event_kind="error-feed.grouping-ready.v1",
        source_id=source_attempt.id,
        revision=scope.pending_revision,
    )
    return len(works)
