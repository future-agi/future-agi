"""Current canonical occurrences, counted in event-time windows."""

from datetime import UTC, datetime

from django.db.models import CharField, Count, F, Min, OuterRef, Q, Subquery, Value
from django.db.models.functions import Cast, Coalesce, Concat

from tracer.models.trace_error_analysis import ErrorClusterTraces
from tracer.models.trace_scan import TraceScanIssue
from tracer.services.feed_escalation_policy import (
    BASELINE_WINDOW,
    CADENCE,
    PROCESSING_ALLOWANCE,
    WINDOW,
    Counts,
)


class CountUnavailable(ValueError):
    """Required occurrence evidence cannot be calculated reliably."""


def current_memberships():
    """Shared source-aware selector, independent of worker request context."""
    return ErrorClusterTraces.no_workspace_objects.filter(
        cluster__deleted=False,
    ).filter(
        Q(cluster__issue_state__isnull=True)
        | Q(
            cluster__issue_state__dirty=False,
            cluster__issue_state__retired=False,
            finding__isnull=False,
            finding__deleted=False,
            finding__cluster_id=F("cluster_id"),
            trace_id=F("finding__report__trace_id"),
            finding__report__source="omega",
            finding__report__project_id=F("cluster__project_id"),
            finding__report__organization_id=F("cluster__project__organization_id"),
            finding__report__workspace_id=F("cluster__project__workspace_id"),
            finding__report__is_current=True,
            finding__report__deleted=False,
            finding__report__execution_status="completed",
            finding__report__grouping_status="completed",
        )
    )


def _aggregate(qs, *, identity, trace, boundary, now, unit="findings"):
    if qs.filter(occurred_at__isnull=True).exists():
        raise CountUnavailable("Required occurrence timestamp is unavailable")
    if qs.filter(occurred_at__gt=now).exists():
        raise CountUnavailable("Occurrence timestamp is in the future")
    current = Q(occurred_at__gte=boundary - WINDOW, occurred_at__lt=boundary)
    historical = Q(
        occurred_at__gte=boundary - WINDOW - BASELINE_WINDOW,
        occurred_at__lt=boundary - WINDOW,
    )
    result = qs.filter(
        occurred_at__gte=boundary - WINDOW - BASELINE_WINDOW, occurred_at__lte=now
    ).aggregate(
        current=Count(identity, distinct=True, filter=current),
        historical=Count(identity, distinct=True, filter=historical),
        traces=Count(trace, distinct=True, filter=current),
        next_occurrence=Min("occurred_at", filter=Q(occurred_at__gte=boundary)),
    )
    return Counts(
        result["current"],
        result["historical"],
        result["traces"],
        unit=unit,
        next_occurrence_at=result["next_occurrence"],
    )


def count_occurrences(
    cluster, boundary: datetime, *, now: datetime | None = None
) -> Counts:
    now = now or boundary + PROCESSING_ALLOWANCE + CADENCE
    if cluster.target_type != "error_feed" or cluster.project.workspace_id is None:
        raise CountUnavailable("Live issue has no explicit workspace")
    if cluster.source == "eval":
        from tracer.models.observation_span import EvalLogger

        target = {
            "span": "observation_span_id",
            "trace": "trace_id",
            "session": "trace_session_id",
        }.get(cluster.eval_target_type)
        if target is None:
            raise CountUnavailable("Evaluation target identity is unavailable")
        latest = (
            EvalLogger.no_workspace_objects.annotate(
                canonical_version=Coalesce("config_hash", Value("")),
            )
            .filter(
                deleted=False,
                custom_eval_config_id=OuterRef("eval_logger__custom_eval_config_id"),
                target_type=cluster.eval_target_type,
                status="completed",
                error=False,
                skipped_reason__isnull=True,
                canonical_version=OuterRef("canonical_version"),
                **{target: OuterRef(f"eval_logger__{target}")},
            )
            .order_by("-created_at", "-id")
            .values("id")[:1]
        )
        members = (
            current_memberships()
            .filter(
                cluster=cluster,
                eval_logger__isnull=False,
                eval_logger__deleted=False,
                eval_logger__status="completed",
                eval_logger__error=False,
                eval_logger__skipped_reason__isnull=True,
                eval_logger__custom_eval_config__project_id=cluster.project_id,
                eval_logger__target_type=cluster.eval_target_type,
            )
            .annotate(
                canonical_version=Coalesce("eval_logger__config_hash", Value("")),
                latest_logger_id=Subquery(latest),
                occurrence_identity=Concat(
                    Cast("eval_logger__custom_eval_config_id", CharField()),
                    Value(":"),
                    Coalesce("eval_logger__config_hash", Value("")),
                    Value(":"),
                    F("eval_logger__target_type"),
                    Value(":"),
                    Coalesce(
                        Cast("eval_logger__observation_span_id", CharField()), Value("")
                    ),
                    Value(":"),
                    Coalesce(Cast("eval_logger__trace_id", CharField()), Value("")),
                    Value(":"),
                    Coalesce(
                        Cast("eval_logger__trace_session_id", CharField()), Value("")
                    ),
                    output_field=CharField(),
                ),
            )
            .filter(eval_logger_id=F("latest_logger_id"))
            .exclude(eval_logger__output_bool=True)
        )
        if cluster.eval_target_type == "trace":
            # A trace evaluation's root span is supporting provenance, not its identity.
            members = members.annotate(
                occurrence_identity=Concat(
                    Cast("eval_logger__custom_eval_config_id", CharField()),
                    Value(":"),
                    Coalesce("eval_logger__config_hash", Value("")),
                    Value(":trace:"),
                    Cast("eval_logger__trace_id", CharField()),
                    output_field=CharField(),
                )
            )
        result = _aggregate(
            members,
            identity="occurrence_identity",
            trace="eval_logger__trace_id",
            boundary=boundary,
            now=now,
            unit="failed_evaluations",
        )
        if cluster.eval_target_type == "session":
            session_ids = list(
                members.filter(
                    occurred_at__gte=boundary - WINDOW, occurred_at__lt=boundary
                )
                .values_list("eval_logger__trace_session_id", flat=True)
                .distinct()
            )
            if session_ids:
                from tracer.services.clickhouse.v2 import get_reader

                try:
                    breadth = get_reader().aggregate_by_session_ids(
                        [str(sid) for sid in session_ids],
                        project_id=str(cluster.project_id),
                    )
                    if any(str(sid) not in breadth for sid in session_ids):
                        raise CountUnavailable("Session trace evidence is unavailable")
                    traces = sum(
                        breadth[str(sid)]["traces_count"] for sid in session_ids
                    )
                except CountUnavailable:
                    raise
                except Exception as exc:
                    raise CountUnavailable(
                        "Session trace breadth query failed"
                    ) from exc
                result = Counts(
                    result.current,
                    result.historical,
                    traces,
                    unit=result.unit,
                    next_occurrence_at=result.next_occurrence_at,
                )
        return result
    if cluster.source != "scanner":
        raise CountUnavailable("Unsupported live occurrence source")
    if hasattr(cluster, "issue_state"):
        return _aggregate(
            current_memberships().filter(cluster=cluster),
            identity="finding_id",
            trace="trace_id",
            boundary=boundary,
            now=now,
        )
    findings = TraceScanIssue.no_workspace_objects.filter(
        cluster=cluster,
        scan_result__deleted=False,
        scan_result__status="completed",
        scan_result__project_id=cluster.project_id,
    )
    return _aggregate(
        findings,
        identity="id",
        trace="scan_result__trace_id",
        boundary=boundary,
        now=now,
    )


def completion_time_utc(value):
    """CH's UTC DateTime64 contract can decode without a Python tzinfo."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise CountUnavailable("Source completion is not a timestamp")
    return (
        value.replace(tzinfo=UTC)
        if value.utcoffset() is None
        else value.astimezone(UTC)
    )


def source_occurrence_time(*, project_id, trace_id=None, span_id=None, session_id=None):
    """Capture authoritative completion time at publication; never processing time."""
    from tracer.services.clickhouse.v2 import get_reader

    reader = get_reader()
    if span_id:
        span = reader.get(str(span_id), project_id=str(project_id))
        return (
            (completion_time_utc(span.end_time), "span_end")
            if span and span.end_time
            else (None, "")
        )
    trace_ids = (
        [str(trace_id)]
        if trace_id
        else reader.session_trace_ids(str(project_id), str(session_id))
    )
    roots = reader.list_root_spans_by_trace_ids(
        trace_ids,
        project_id=str(project_id),
        include_heavy=False,
        columns=["trace_id", "end_time"],
    )
    times = [
        completion_time_utc(row["end_time"])
        for row in roots.values()
        if row["end_time"]
    ]
    if len(times) != len(trace_ids) or not times:
        return None, ""
    return max(times), "session_end" if session_id else "trace_end"
