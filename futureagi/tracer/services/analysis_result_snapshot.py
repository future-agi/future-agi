"""Materialized result snapshots, never re-filter children by a moving binding.

Background results are immutable once published. Pinning the lifecycle's result
UUID and reading its children within one transaction therefore survives a retry
at any point, including under READ COMMITTED. No lifecycle lock is held by a
reader. Legacy queries retain their existing selection/order and soft deletes.
"""

from django.db import transaction


def materialize_analysis(analysis):
    from tracer.models.trace_error_analysis import AnalysisErrorGroup, TraceErrorDetail

    if analysis is not None:
        details = TraceErrorDetail._base_manager.filter(
            analysis_id=analysis.pk, deleted=False
        ).order_by("error_id")
        groups = AnalysisErrorGroup._base_manager.filter(
            analysis_id=analysis.pk, deleted=False
        )
        analysis._analysis_snapshot_details = list(details)
        analysis._analysis_snapshot_groups = list(groups)
        # Preserve the pre-existing prefetched reverse-manager interface too.
        # Both querysets are evaluated and remain pinned to this parent UUID.
        analysis._prefetched_objects_cache = {
            **getattr(analysis, "_prefetched_objects_cache", {}),
            "error_details": details,
            "analysis_error_groups": groups,
        }
    return analysis


def read_analysis(query):
    """The authorized visible parent selection pins the result UUID in SQL."""
    with transaction.atomic():
        return materialize_analysis(query.first())


def read_analyses(query):
    """Pin the authorized parent set before hydrating report/reverse readers."""
    with transaction.atomic():
        parents = list(query)
        return [materialize_analysis(parent) for parent in parents]


def trace_snapshot(trace_id, project_ids=None):
    from tracer.models.trace_error_analysis import TraceErrorAnalysis
    from tracer.queries import background_analysis_state

    with transaction.atomic():
        # One row read obtains both status and result ID. Never re-read status
        # after selecting children; the old completed snapshot remains valid.
        lifecycle = background_analysis_state.get_state(
            trace_id, project_ids=project_ids
        )
        if lifecycle is not None:
            if lifecycle.status != "completed" or not lifecycle.result_analysis_id:
                return lifecycle, None
            query = TraceErrorAnalysis._base_manager.filter(
                pk=lifecycle.result_analysis_id,
                project_id=lifecycle.project_id,
                trace_id=trace_id,
                deleted=False,
            )
        else:
            query = TraceErrorAnalysis.objects.filter(trace_id=trace_id)
            if project_ids is not None:
                query = query.filter(project_id__in=project_ids)
        return lifecycle, materialize_analysis(query.order_by("-analysis_date").first())


def cluster_snapshot(trace_ids):
    from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail

    with transaction.atomic():
        # Keep all historical details, as before; scores use the latest analysis
        # for each trace. Each background trace contributes only its bound UUID.
        parents = list(
            TraceErrorAnalysis.objects.filter(trace_id__in=trace_ids)
            .select_related("trace", "project")
            .order_by("trace_id", "-analysis_date")
        )
        details = list(
            TraceErrorDetail._base_manager.filter(
                analysis_id__in=[parent.pk for parent in parents], deleted=False
            )
            .select_related("analysis", "analysis__trace", "analysis__project")
            .order_by("-analysis__analysis_date", "error_id")
        )
        latest = {}
        for parent in parents:
            latest.setdefault(str(parent.trace_id), parent)
        return details, list(latest.values())
