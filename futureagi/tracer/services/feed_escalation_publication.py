"""Capture occurrence time and publish a durable evaluation request."""

import structlog
from django.db import transaction

from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_scan import TraceScanIssue
from tracer.services.feed_escalation import request_evaluation
from tracer.services.feed_escalation_inputs import source_occurrence_time

logger = structlog.get_logger(__name__)


def capture_occurrence(**source):
    try:
        at, provenance = source_occurrence_time(**source)
        if at is not None and at.utcoffset() is not None:
            return at, provenance
    except Exception:
        logger.exception(
            "feed_occurrence_time_unavailable", project_id=str(source["project_id"])
        )
    # Keep publication available; the evaluator fails closed and timestamp
    # preparation retries missing source evidence without fabricating time.
    return None, ""


def bind_scan_occurrence(*, cluster, finding_id, trace_id):
    finding = TraceScanIssue.no_workspace_objects.get(
        pk=finding_id,
        scan_result__project_id=cluster.project_id,
    )
    at, provenance = finding.occurred_at, finding.occurrence_time_source
    if at is None:
        known = (
            TraceScanIssue.no_workspace_objects.filter(
                scan_result_id=finding.scan_result_id,
                occurred_at__isnull=False,
            )
            .values_list("occurred_at", "occurrence_time_source")
            .first()
        )
        at, provenance = (
            known
            if known
            else capture_occurrence(project_id=cluster.project_id, trace_id=trace_id)
        )
    old_id = finding.cluster_id
    with transaction.atomic():
        list(
            TraceErrorGroup.no_workspace_objects.select_for_update()
            .filter(
                pk__in={cluster.pk, old_id} - {None},
            )
            .order_by("id")
        )
        finding = TraceScanIssue.no_workspace_objects.select_for_update().get(
            pk=finding_id
        )
        if finding.cluster_id != old_id:
            raise ValueError("Finding membership changed; retry publication")
        finding.cluster = cluster
        if finding.occurred_at is None:
            finding.occurred_at, finding.occurrence_time_source = at, provenance
        finding.save(
            update_fields=[
                "cluster",
                "occurred_at",
                "occurrence_time_source",
                "updated_at",
            ]
        )
        ErrorClusterTraces.no_workspace_objects.get_or_create(
            cluster=cluster,
            trace_id=trace_id,
            defaults={
                "scan_issue_id": finding_id,
                "occurred_at": finding.occurred_at,
                "occurrence_time_source": finding.occurrence_time_source,
            },
        )
        moved = old_id is not None and old_id != cluster.pk
        if moved:
            request_evaluation(old_id, structural=True)
        request_evaluation(cluster.pk, structural=moved)


def eval_occurrence_time(project_id, result):
    from tracer.models.observation_span import EvalLogger

    entry = EvalLogger.no_workspace_objects.get(pk=result.eval_logger_id)
    if (
        str(entry.custom_eval_config.project_id) != str(project_id)
        or entry.target_type != result.target_type
    ):
        raise ValueError("Evaluation publication scope changed")
    known = known_eval_occurrence_time(project_id, entry)
    if known:
        return known
    return capture_occurrence(
        project_id=project_id,
        span_id=entry.observation_span_id if entry.target_type == "span" else None,
        trace_id=entry.trace_id if entry.target_type == "trace" else None,
        session_id=entry.trace_session_id if entry.target_type == "session" else None,
    )


def known_eval_occurrence_time(project_id, entry):
    """The target's original completion time survives evaluation retries."""
    if str(entry.custom_eval_config.project_id) != str(project_id):
        raise ValueError("Evaluation publication scope changed")
    target = {
        "span": "observation_span_id",
        "trace": "trace_id",
        "session": "trace_session_id",
    }[entry.target_type]
    return (
        ErrorClusterTraces.no_workspace_objects.filter(
            cluster__project_id=project_id,
            occurred_at__isnull=False,
            eval_logger__custom_eval_config_id=entry.custom_eval_config_id,
            eval_logger__config_hash=entry.config_hash,
            eval_logger__target_type=entry.target_type,
            **{f"eval_logger__{target}": getattr(entry, target)},
        )
        .order_by("occurred_at")
        .values_list("occurred_at", "occurrence_time_source")
        .first()
    )
