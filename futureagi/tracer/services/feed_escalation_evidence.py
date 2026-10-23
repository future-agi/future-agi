"""Prepare missing occurrence timestamps outside calculation/write locks."""

from django.db import transaction
from django.utils import timezone

from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_scan import TraceScanIssue
from tracer.services.feed_escalation_publication import (
    capture_occurrence,
    known_eval_occurrence_time,
)


def prepare_issue_evidence(cluster_id, *, limit=100, dry_run=False, now=None):
    if not 1 <= limit <= 500:
        raise ValueError("Evidence batch limit must be between 1 and 500")
    issue = (
        TraceErrorGroup.no_workspace_objects.select_related("project")
        .filter(
            pk=cluster_id,
            target_type="error_feed",
        )
        .exclude(issue_state__dirty=True)
        .exclude(issue_state__retired=True)
        .first()
    )
    if issue is None:
        return {"prepared": 0, "unresolved": 0}
    rows = list(
        TraceScanIssue.no_workspace_objects.filter(
            cluster=issue,
            occurred_at__isnull=True,
            scan_result__project_id=issue.project_id,
            scan_result__deleted=False,
        )
        .select_related("scan_result")
        .order_by("id")[:limit]
    )
    remaining = limit - len(rows)
    if remaining:
        rows += list(
            ErrorClusterTraces.no_workspace_objects.filter(
                cluster=issue,
                occurred_at__isnull=True,
            )
            .select_related(
                "finding__report__job",
                "scan_issue__scan_result",
                "eval_logger__custom_eval_config",
            )
            .order_by("id")[:remaining]
        )
    cache, resolved = {}, []
    unresolved = 0
    for row in rows:
        at, provenance = None, ""
        if isinstance(row, TraceScanIssue):
            key = ("trace", str(row.scan_result.trace_id))
            source = {"trace_id": row.scan_result.trace_id}
        elif row.finding_id:
            report = row.finding.report
            if report.project_id == issue.project_id and report.job_id:
                at, provenance = report.job.root_end_time, "omega_root_end"
            key, source = None, None
        elif row.eval_logger_id:
            entry = row.eval_logger
            if entry.custom_eval_config.project_id != issue.project_id:
                key, source = None, None
            elif known := known_eval_occurrence_time(issue.project_id, entry):
                at, provenance = known
                key, source = None, None
            else:
                source = (
                    {"span_id": entry.observation_span_id}
                    if entry.target_type == "span"
                    else (
                        {"trace_id": entry.trace_id}
                        if entry.target_type == "trace"
                        else {"session_id": entry.trace_session_id}
                    )
                )
                key = (entry.target_type, str(next(iter(source.values()))))
        elif row.scan_issue_id:
            source = {"trace_id": row.scan_issue.scan_result.trace_id}
            key = ("trace", str(source["trace_id"]))
        else:
            key, source = None, None
        if key:
            if key not in cache:
                cache[key] = capture_occurrence(project_id=issue.project_id, **source)
            at, provenance = cache[key]
        if at is None or at.utcoffset() is None:
            unresolved += 1
        else:
            resolved.append((row, at, provenance))
    if resolved and not dry_run:
        changed = 0
        with transaction.atomic():
            current = TraceErrorGroup.no_workspace_objects.select_for_update().get(
                pk=issue.pk
            )
            for row, at, provenance in resolved:
                # A concurrent publisher may already have captured an immutable time.
                changed += (
                    type(row)
                    .no_workspace_objects.filter(
                        pk=row.pk,
                        cluster_id=current.pk,
                        occurred_at__isnull=True,
                    )
                    .update(occurred_at=at, occurrence_time_source=provenance)
                )
            if changed:
                from tracer.services.feed_escalation import request_evaluation

                request_evaluation(current.pk, now=now or timezone.now())
    return {"prepared": len(resolved), "unresolved": unresolved}
