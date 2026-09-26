"""Schedule embeddings in the investigation transaction, without network I/O.

The row is the durable work item. Kafka wake-ups and worker claims will consume
this queue; neither is required for publication to remember the pending work.
"""

from django.db import transaction
from django.utils import timezone

from tracer.constants.grouping_versions import FEATURE_POLICY_VERSION
from tracer.models.trace_grouping import (
    GroupingFeatureState,
    TraceGroupingFeatureJob,
    TraceGroupingOutbox,
    TraceGroupingScope,
)
from tracer.models.trace_investigation import (
    TraceInvestigationReport,
    TraceInvestigationSource,
)
from tracer.queries.grouping import groupable_findings
from tracer.services.grouping.control import _eligible_project


def enqueue_grouping_features(
    *, report: TraceInvestigationReport
) -> TraceGroupingFeatureJob | None:
    """Persist immediately-due work for an explicitly enabled project.

    One publication contains all findings for a report. A report-level work item
    prepares each finding's views without N identical report snapshot reads.
    Retries retain the existing row/deadline; failed or historical reports never
    provide ordinary clustering input. No membership or public Feed data changes.
    """
    if not _eligible_project(
        report.project_id,
        simulation=report.workload_type == "simulation_test_execution",
    ):
        return None
    with transaction.atomic():
        # Refresh the authoritative row: callers must not enqueue an old Python
        # object after a concurrent replacement has made its report non-current.
        current = (
            TraceInvestigationReport.no_workspace_objects.select_for_update()
            .filter(pk=report.pk, is_current=True)
            .first()
        )
        if current is None or current.source != TraceInvestigationSource.OMEGA:
            return None
        now = timezone.now()
        # A replacement with no findings still invalidates the old feature work.
        replaced = (
            # A simulation run has one report per call; siblings are not replacements.
            {"report__job_id": current.job_id}
            if current.workload_type == "simulation_test_execution"
            else {"report__trace_id": current.trace_id}
        )
        TraceGroupingFeatureJob.no_workspace_objects.filter(
            report__project_id=current.project_id,
            report__is_current=False,
            **replaced,
        ).exclude(state=GroupingFeatureState.SUPERSEDED).update(
            state=GroupingFeatureState.SUPERSEDED, updated_at=now
        )
        if current.execution_status != "completed":
            return None
        if not groupable_findings(current).exists():
            return None
        job, _ = TraceGroupingFeatureJob.no_workspace_objects.get_or_create(
            report=current,
            policy_version=FEATURE_POLICY_VERSION,
            defaults={
                "publication_result_digest": current.result_digest,
                "not_before": now,
            },
        )
        scope, _ = TraceGroupingScope.no_workspace_objects.get_or_create(
            project_id=current.project_id,
            defaults={
                "organization_id": current.organization_id,
                "workspace_id": current.workspace_id,
            },
        )
        if (
            scope.organization_id != current.organization_id
            or scope.workspace_id != current.workspace_id
        ):
            raise ValueError("grouping scope disagrees with source report tenant")
        TraceGroupingOutbox.no_workspace_objects.get_or_create(
            scope=scope,
            event_kind="error-feed.grouping-feature-ready.v1",
            source_id=job.id,
            revision=0,
        )
        return job
