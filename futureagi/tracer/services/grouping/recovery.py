"""Reviewed, bounded recovery without rewriting attempts or resetting spend."""

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.trace_grouping import (
    GroupingAttemptState,
    GroupingWorkState,
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingFeature,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import (
    TraceInvestigationFinding,
    TraceInvestigationGroupingStatus,
)
from tracer.queries.grouping import (
    GroupingSnapshotError,
    canonical_grouping_source_digest,
    export_grouping_snapshot,
)
from tracer.services.grouping.control import GroupingConflict, _live_report


def requeue_failed_work(
    *,
    project_id,
    apply=False,
    expected_registry_revision=None,
    work_ids=None,
    retry_attempts=1,
    limit=20,
):
    if not 1 <= limit <= 100 or not 1 <= retry_attempts <= 5:
        raise ValueError("limit 1-100 and retry attempts 1-5 required")
    if apply and (expected_registry_revision is None or not work_ids):
        raise ValueError(
            "apply requires reviewed registry revision and explicit work IDs"
        )
    if work_ids and (
        len(work_ids) > limit or len(set(map(str, work_ids))) != len(work_ids)
    ):
        raise ValueError("explicit work IDs must be unique and within the limit")
    with transaction.atomic():
        scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(
            project_id=project_id
        )
        if apply and scope.registry_revision != expected_registry_revision:
            raise GroupingConflict("Registry revision changed; preview again")
        active = TraceGroupingAttempt.no_workspace_objects.filter(
            work__scope=scope,
            state=GroupingAttemptState.CLAIMED,
            lease_expires_at__gte=timezone.now(),
        ).exists()
        if apply and active:
            raise GroupingConflict("Drain active grouping attempts before recovery")
        query = (
            TraceGroupingWork.no_workspace_objects.select_for_update(of=("self",))
            .select_related("report__job", "feature_job")
            .filter(scope=scope, state=GroupingWorkState.FAILED)
        )
        if work_ids:
            query = query.filter(id__in=work_ids)
        works = list(query.order_by("id")[:limit])
        if apply and {str(w.id) for w in works} != set(map(str, work_ids)):
            raise GroupingConflict("Reviewed work is no longer failed in this project")
        results = []
        for work in works:
            reason = "active_attempt" if active else None
            if (
                not _live_report(work.report)
                or work.feature_job.state != "ready"
                or work.report.grouping_status not in {"pending", "failed"}
            ):
                reason = "report_no_longer_eligible"
            try:
                snapshot = export_grouping_snapshot(report=work.report)
            except GroupingSnapshotError:
                snapshot = None
                reason = "snapshot_invalid"
            if snapshot is not None:
                ids = [o["occurrence_id"] for o in snapshot["occurrences"]]
                pending_ids = set(
                    map(
                        str,
                        TraceInvestigationFinding.no_workspace_objects.filter(
                            id__in=ids, cluster__isnull=True
                        ).values_list("id", flat=True),
                    )
                )
                if not pending_ids:
                    reason = "no_pending_findings"
                receipts = list(
                    TraceGroupingFeature.no_workspace_objects.filter(
                        job=work.feature_job,
                        finding_id__in=pending_ids,
                        view="semantics",
                    )
                )
                if {str(r.finding_id) for r in receipts} != pending_ids or any(
                    r.source_digest != canonical_grouping_source_digest(snapshot)
                    or r.evidence_revision != snapshot["report"]["evidence_digest"]
                    for r in receipts
                ):
                    reason = "features_stale_or_missing"
            budget_id = work.budget_work_id or work.id
            if TraceGroupingCall.no_workspace_objects.filter(
                Q(work_id=budget_id) | Q(work__budget_work_id=budget_id),
                status__in=["reserved", "unknown"],
            ).exists():
                reason = "unresolved_usage"
            if not getattr(settings, "ERROR_FEED_GROUPING_BUDGET_ENFORCED", True):
                reason = "budget_enforcement_disabled"
            if apply and reason is None:
                # Expire historical leases for this reviewed work. All attempt
                # numbers, paid receipts, checkpoints and budget ownership stay.
                TraceGroupingAttempt.no_workspace_objects.filter(
                    work=work,
                    state=GroupingAttemptState.CLAIMED,
                    lease_expires_at__lt=timezone.now(),
                ).update(
                    state=GroupingAttemptState.EXPIRED, failure_code="lease_expired"
                )
                work.retry_start_attempt = work.attempt_number
                work.retry_limit = retry_attempts
                work.state = GroupingWorkState.PENDING
                work.not_before = timezone.now()
                work.failure_code = ""
                work.save(
                    update_fields=[
                        "retry_start_attempt",
                        "retry_limit",
                        "state",
                        "not_before",
                        "failure_code",
                        "updated_at",
                    ]
                )
                work.report.grouping_status = TraceInvestigationGroupingStatus.PENDING
                work.report.save(update_fields=["grouping_status", "updated_at"])
            results.append(
                {
                    "work_id": str(work.id),
                    "failure_code": work.failure_code,
                    "eligible": reason is None,
                    "blocked_reason": reason,
                    "requeued": apply and reason is None,
                }
            )
        return {
            "apply": apply,
            "registry_revision": scope.registry_revision,
            "retry_attempts": retry_attempts,
            "works": results,
        }
