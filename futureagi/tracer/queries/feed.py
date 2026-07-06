"""
DB helpers for the Error Feed API.

Returns typed dataclasses from tracer.types.feed_types — no raw dicts.
Pure data-access layer: no HTTP, no business logic. Service layer composes.
"""

import re
import statistics
from collections import Counter
from datetime import UTC, datetime, timedelta

import numpy as np
import structlog
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import (
    Avg,
    Case,
    Count,
    F,
    FloatField,
    IntegerField,
    Q,
    QuerySet,
    Value,
    When,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from scipy.stats import ks_2samp
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer

from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace import Trace, TraceErrorAnalysisStatus
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    TraceErrorAnalysis,
    TraceErrorDetail,
    TraceErrorGroup,
)
from tracer.models.trace_scan import TraceScanIssue, TraceScanResult
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.v2.span_reader import CHSpan
from tracer.types.feed_types import (
    CoOccurringIssue,
    DeepAnalysisDispatchResponse,
    DeepAnalysisResponse,
    ErrorName,
    EvaluationResult,
    EventsOverTimePoint,
    FeedDetailCore,
    FeedListResponse,
    FeedListRow,
    FeedSidebar,
    FeedStats,
    FeedUpdatePayload,
    HeatmapCell,
    KeyMoment,
    OverviewResponse,
    PatternInsight,
    PatternSummary,
    RcaSummary,
    Recommendation,
    RepresentativeTrace,
    RootCause,
    ScoreTrend,
    SidebarAIMetadata,
    SidebarTimeline,
    TraceEvidence,
    TracePreview,
    TracesAggregates,
    TracesListRow,
    TracesTabResponse,
    TraceSummary,
    TrendMetric,
    TrendPoint,
    TrendsTabResponse,
)

logger = structlog.get_logger(__name__)
User = get_user_model()


# Coerce EvalLogger rows to a 0..1 score in SQL: prefer output_float when set,
# otherwise treat output_bool as 1.0/0.0. Mirrors the pattern in
# tracer/views/trace.py so eval aggregation has one canonical shape.
EVAL_SCORE_EXPR = Case(
    When(output_float__isnull=False, then=F("output_float")),
    When(output_bool=True, then=Value(1.0)),
    When(output_bool=False, then=Value(0.0)),
    default=None,
    output_field=FloatField(),
)


# Priority (backend) ↔ severity (frontend) mapping
_PRIORITY_TO_SEVERITY = {
    "urgent": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}
_SEVERITY_TO_PRIORITY = {v: k for k, v in _PRIORITY_TO_SEVERITY.items()}


def priority_to_severity(priority: str | None) -> str:
    return _PRIORITY_TO_SEVERITY.get(priority or "", priority or "medium")


def severity_to_priority(severity: str | None) -> str:
    return _SEVERITY_TO_PRIORITY.get(severity or "", severity or "medium")


def voice_project_ids(project_ids: list[str]) -> set[str]:
    """Subset of project_ids whose traces are voice calls.

    Modality is a project-level fact: a project is voice iff a voice-typed
    simulator AgentDefinition feeds it (via its observability provider).
    Clusters inherit it at read time — nothing is stamped per cluster.
    """
    from simulate.models.agent_definition import AgentDefinition, AgentTypeChoices

    if not project_ids:
        return set()
    rows = AgentDefinition.objects.filter(
        observability_provider__project_id__in=project_ids,
        agent_type=AgentTypeChoices.VOICE,
    ).values_list("observability_provider__project_id", flat=True)
    return {str(pid) for pid in rows}


def is_voice_project(project_id) -> bool:
    """Single-project convenience over voice_project_ids."""
    return bool(voice_project_ids([str(project_id)]))


# ---------------------------------------------------------------------------
# Filters (applied to the base queryset)
# ---------------------------------------------------------------------------


def _base_qs(project_ids: list[str]) -> QuerySet:
    """Base queryset for scanner + eval clusters across one or more projects."""
    # Exclude legacy pre-revamp rows (old agent-compass) that predate
    # feed fields — they have no issue_group and render as fallback K-IDs.
    return (
        TraceErrorGroup.objects.filter(project_id__in=project_ids, deleted=False)
        .exclude(issue_group__isnull=True)
        .select_related("project", "assignee", "success_trace")
    )


def _apply_filters(
    qs: QuerySet,
    *,
    search: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    fix_layer: str | None = None,
    source: str | None = None,
    issue_group: str | None = None,
    time_range_days: int | None = None,
) -> QuerySet:
    if search:
        qs = qs.filter(
            Q(title__icontains=search)
            | Q(issue_group__icontains=search)
            | Q(issue_category__icontains=search)
        )
    if status:
        qs = qs.filter(status=status)
    if severity:
        qs = qs.filter(priority=severity_to_priority(severity))
    if fix_layer:
        qs = qs.filter(fix_layer=fix_layer)
    if source:
        qs = qs.filter(source=source)
    if issue_group:
        qs = qs.filter(issue_group=issue_group)
    if time_range_days:
        since = timezone.now() - timedelta(days=time_range_days)
        qs = qs.filter(last_seen__gte=since)
    return qs


# ---------------------------------------------------------------------------
# Batch helpers (one query for many cluster IDs)
# ---------------------------------------------------------------------------


def _fetch_trends_batch(cluster_ids: list[str], days: int = 14) -> dict:
    """
    Return {cluster_id: [TrendPoint, ...]} with daily buckets over `days`.

    Buckets come from ErrorClusterTraces.created_at grouped by day.
    """
    if not cluster_ids:
        return {}

    since = timezone.now() - timedelta(days=days)
    rows = (
        ErrorClusterTraces.objects.filter(
            cluster__cluster_id__in=cluster_ids,
            created_at__gte=since,
        )
        .annotate(bucket=TruncDate("created_at"))
        .values("cluster__cluster_id", "bucket")
        .annotate(value=Count("id"))
        .order_by("cluster__cluster_id", "bucket")
    )

    result: dict = {cid: [] for cid in cluster_ids}
    for row in rows:
        cid = row["cluster__cluster_id"]
        if cid in result:
            bucket = row["bucket"]
            # TruncDate returns date, serializer needs datetime
            if not isinstance(bucket, datetime):
                bucket = datetime.combine(bucket, datetime.min.time(), tzinfo=UTC)
            result[cid].append(
                TrendPoint(timestamp=bucket, value=row["value"], users=0)
            )
    return result


def _fetch_users_affected_batch(cluster_ids: list[str]) -> dict:
    """
    Return {cluster_id: distinct_end_user_count}.

    Goes ErrorClusterTraces → trace → ObservationSpan.end_user.

    Cross-store algorithm (post-CH25 cutover):
      1. ECT (PG) → trace_id → set[cluster_id] map (one query). A trace
         can belong to multiple clusters; the legacy SQL JOIN counted
         the span once per (trace, cluster) pair so we must replicate
         that fan-out in Python.
      2. CHSpanReader.list_by_trace_ids — single CH read for every
         affected trace.
      3. Distinct(end_user_id) per cluster_id in Python — for each span
         we add the user to every cluster the trace belongs to.
    """
    if not cluster_ids:
        return {}

    ect_rows = ErrorClusterTraces.objects.filter(
        cluster__cluster_id__in=cluster_ids,
    ).values_list(
        "trace_id", "trace_session_id", "cluster__cluster_id", "cluster__project_id"
    )

    # trace_id → set of cluster_ids it belongs to (one trace can sit in
    # many clusters via separate ECT rows). Session members (trace NULL)
    # fan out to ALL of the session's traces — the user count must cover
    # the whole conversation, not just the representative turn.
    trace_to_clusters: dict[str, set] = {}
    session_to_clusters: dict[str, set] = {}
    # This batch may span projects (get_stats is multi-project); session→trace
    # resolution is multi-tenant-pinned, so group sessions by their project
    # (read off the junction's cluster, never a PG TraceSession row).
    sessions_by_project: dict[str, set[str]] = {}
    for tid, sid, cid, pid in ect_rows:
        if not cid:
            continue
        if tid:
            trace_to_clusters.setdefault(str(tid), set()).add(cid)
        elif sid:
            session_to_clusters.setdefault(str(sid), set()).add(cid)
            if pid:
                sessions_by_project.setdefault(str(pid), set()).add(str(sid))

    for pid, sids in sessions_by_project.items():
        for sid, tids in _session_traces_map(list(sids), pid).items():
            for tid in tids:
                trace_to_clusters.setdefault(tid, set()).update(
                    session_to_clusters[sid]
                )

    if not trace_to_clusters:
        return {}

    # DISTINCT pushed into CH — only user-ids come back, never span payloads.
    with get_reader() as reader:
        users_by_trace = reader.distinct_end_users_by_trace_ids(
            list(trace_to_clusters.keys())
        )

    # Distinct end_user_id per cluster_id — a trace contributes its users to
    # every cluster it is in (fan-out matches the legacy SQL join).
    users_by_cluster: dict[str, set] = {}
    for tid, users in users_by_trace.items():
        for cid in trace_to_clusters.get(tid, ()):
            users_by_cluster.setdefault(cid, set()).update(users)

    return {cid: len(users) for cid, users in users_by_cluster.items() if users}


def _fetch_sessions_batch(cluster_ids: list[str]) -> dict:
    """Return {cluster_id: distinct_session_count}."""
    if not cluster_ids:
        return {}

    # Session members store the session directly on the junction row;
    # trace members reach theirs through trace.session. Coalesce keeps it
    # one query for mixed batches.
    rows = (
        ErrorClusterTraces.objects.filter(cluster__cluster_id__in=cluster_ids)
        .filter(Q(trace__session__isnull=False) | Q(trace_session__isnull=False))
        .values("cluster__cluster_id")
        .annotate(
            sessions=Count(
                Coalesce("trace_session_id", "trace__session_id"), distinct=True
            )
        )
    )
    return {r["cluster__cluster_id"]: r["sessions"] for r in rows}


def _fetch_latest_trace_id_batch(cluster_ids: list[str]) -> dict:
    """Return {cluster_id: latest_trace_id_str}.

    Single Postgres DISTINCT ON query — relies on the
    (cluster, -created_at) index to pick the newest membership row per
    cluster without a per-cluster round-trip.
    """
    if not cluster_ids:
        return {}

    rows = (
        ErrorClusterTraces.objects.filter(
            cluster__cluster_id__in=cluster_ids,
            trace_id__isnull=False,
        )
        .order_by("cluster__cluster_id", "-created_at")
        .distinct("cluster__cluster_id")
        .values("cluster__cluster_id", "trace_id")
    )

    out = {
        str(r["cluster__cluster_id"]): str(r["trace_id"]) for r in rows if r["trace_id"]
    }

    # Session-membered clusters have trace_id NULL on every junction row —
    # fall back to the newest member session's latest trace.
    missing = [cid for cid in cluster_ids if str(cid) not in out]
    if missing:
        sess_rows = list(
            ErrorClusterTraces.objects.filter(
                cluster__cluster_id__in=missing,
                trace_session_id__isnull=False,
            )
            .order_by("cluster__cluster_id", "-created_at")
            .distinct("cluster__cluster_id")
            .values("cluster__cluster_id", "trace_session_id", "cluster__project_id")
        )
        # This batch may span projects; session→trace resolution is
        # multi-tenant-pinned, so resolve per project (project read off the
        # junction's cluster, never a PG TraceSession row).
        rep_map: dict[str, str] = {}
        sessions_by_project: dict[str, set[str]] = {}
        for r in sess_rows:
            pid = r["cluster__project_id"]
            if pid:
                sessions_by_project.setdefault(str(pid), set()).add(
                    str(r["trace_session_id"])
                )
        for pid, sids in sessions_by_project.items():
            rep_map.update(_session_rep_trace_map(list(sids), pid))
        for r in sess_rows:
            tid = rep_map.get(str(r["trace_session_id"]))
            if tid:
                out[str(r["cluster__cluster_id"])] = tid

    return out


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------


def _row_from_cluster(
    cluster: TraceErrorGroup,
    *,
    trends: list[TrendPoint],
    users_affected: int,
    sessions: int,
    latest_trace_id: str | None,
    modality: str = "text",
) -> FeedListRow:
    """Build a FeedListRow from a TraceErrorGroup + pre-fetched batch data."""
    assignees: list[str] = []
    if cluster.assignee:
        assignees.append(cluster.assignee.email or str(cluster.assignee.id))

    return FeedListRow(
        cluster_id=cluster.cluster_id,
        source=cluster.source or "scanner",
        modality=modality,
        error=ErrorName(
            name=cluster.title or cluster.issue_category or cluster.cluster_id,
            type=cluster.issue_category or cluster.issue_group or "",
        ),
        status=cluster.status,
        severity=priority_to_severity(cluster.priority),
        occurrences=cluster.error_count or 0,
        trace_count=cluster.unique_traces or 0,
        fix_layer=cluster.fix_layer.lower() if cluster.fix_layer else None,
        users_affected=users_affected,
        sessions=sessions,
        first_seen=cluster.first_seen,
        last_seen=cluster.last_seen,
        trends=trends,
        assignees=assignees,
        project=cluster.project.name if cluster.project_id else None,
        project_id=str(cluster.project_id) if cluster.project_id else None,
        trace_id=latest_trace_id,
        external_issue_url=cluster.external_issue_url,
        external_issue_id=cluster.external_issue_id,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_clusters(
    project_ids: list[str],
    *,
    search: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    fix_layer: str | None = None,
    source: str | None = None,
    issue_group: str | None = None,
    time_range_days: int | None = None,
    sort_by: str = "last_seen",
    sort_dir: str = "desc",
    limit: int = 25,
    offset: int = 0,
) -> FeedListResponse:
    """Paginated cluster list for the Feed table across the given projects."""
    qs = _base_qs(project_ids)
    qs = _apply_filters(
        qs,
        search=search,
        status=status,
        severity=severity,
        fix_layer=fix_layer,
        source=source,
        issue_group=issue_group,
        time_range_days=time_range_days,
    )

    # Sort
    valid_sorts = {
        "last_seen",
        "first_seen",
        "error_count",
        "unique_traces",
        "severity",
    }
    if sort_by not in valid_sorts:
        sort_by = "last_seen"
    if sort_by == "severity":
        qs = qs.annotate(
            severity_order=Case(
                When(priority="low", then=Value(0)),
                When(priority="medium", then=Value(1)),
                When(priority="high", then=Value(2)),
                When(priority="urgent", then=Value(3)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        order = "-severity_order" if sort_dir == "desc" else "severity_order"
        qs = qs.order_by(order, "-last_seen")
    else:
        order = f"-{sort_by}" if sort_dir == "desc" else sort_by
        qs = qs.order_by(order)

    total = qs.count()
    clusters = list(qs[offset : offset + limit])

    if not clusters:
        return FeedListResponse(data=[], total=total, limit=limit, offset=offset)

    cluster_ids = [c.cluster_id for c in clusters]
    trends_map = _fetch_trends_batch(cluster_ids)
    users_map = _fetch_users_affected_batch(cluster_ids)
    sessions_map = _fetch_sessions_batch(cluster_ids)
    latest_trace_map = _fetch_latest_trace_id_batch(cluster_ids)
    voice_pids = voice_project_ids([str(c.project_id) for c in clusters])

    rows = [
        _row_from_cluster(
            c,
            trends=trends_map.get(c.cluster_id, []),
            users_affected=users_map.get(c.cluster_id, 0),
            sessions=sessions_map.get(c.cluster_id, 0),
            latest_trace_id=latest_trace_map.get(c.cluster_id),
            modality="voice" if str(c.project_id) in voice_pids else "text",
        )
        for c in clusters
    ]

    return FeedListResponse(data=rows, total=total, limit=limit, offset=offset)


def get_stats(
    project_ids: list[str], *, time_range_days: int | None = None
) -> FeedStats:
    """Top stats bar: counts by status + total affected users."""
    qs = _base_qs(project_ids)
    if time_range_days:
        since = timezone.now() - timedelta(days=time_range_days)
        qs = qs.filter(last_seen__gte=since)

    counts = qs.values("status").annotate(n=Count("id"))
    status_counts = {row["status"]: row["n"] for row in counts}

    total_errors = qs.aggregate(total=Count("id"))["total"] or 0

    cluster_ids = list(qs.values_list("cluster_id", flat=True))
    users_map = _fetch_users_affected_batch(cluster_ids)
    affected_users = sum(users_map.values())

    return FeedStats(
        total_errors=total_errors,
        escalating=status_counts.get(FeedIssueStatus.ESCALATING, 0),
        for_review=status_counts.get(FeedIssueStatus.FOR_REVIEW, 0),
        acknowledged=status_counts.get(FeedIssueStatus.ACKNOWLEDGED, 0),
        resolved=status_counts.get(FeedIssueStatus.RESOLVED, 0),
        affected_users=affected_users,
    )


def get_cluster_detail(
    cluster_id: str, project_ids: list[str] | None = None
) -> FeedDetailCore | None:
    """
    Full detail core for a single cluster.

    If project_ids is None, finds by cluster_id alone (unique in practice since
    cluster_id is hashed from project+content).
    """
    qs = TraceErrorGroup.objects.filter(deleted=False).select_related(
        "project", "assignee", "success_trace"
    )
    if project_ids is not None:
        qs = qs.filter(project_id__in=project_ids)
    cluster = qs.filter(cluster_id=cluster_id).first()
    if not cluster:
        return None

    trends_map = _fetch_trends_batch([cluster.cluster_id])
    users_map = _fetch_users_affected_batch([cluster.cluster_id])
    sessions_map = _fetch_sessions_batch([cluster.cluster_id])
    latest_trace_map = _fetch_latest_trace_id_batch([cluster.cluster_id])

    row = _row_from_cluster(
        cluster,
        trends=trends_map.get(cluster.cluster_id, []),
        users_affected=users_map.get(cluster.cluster_id, 0),
        sessions=sessions_map.get(cluster.cluster_id, 0),
        latest_trace_id=latest_trace_map.get(cluster.cluster_id),
        modality="voice" if is_voice_project(cluster.project_id) else "text",
    )

    success_trace: TracePreview | None = None
    if cluster.success_trace_id:
        success_trace = TracePreview(
            trace_id=str(cluster.success_trace_id),
            input=_trace_input_str(cluster.success_trace),
            output=_trace_output_str(cluster.success_trace),
        )
    elif cluster.source == ClusterSource.EVAL and cluster.eval_config_id:
        # Eval clusters never get the scanner's KNN success match. A genuine
        # PASSING result for the same eval is the honest "working" reference
        # (powers the failing-vs-working comparison, e.g. voice call compare).
        if cluster.eval_target_type == EvalTargetType.SESSION:
            # Session evals anchor to the session (trace NULL): find a
            # passing session and represent it by its latest trace.
            _, member_session_ids = _cluster_member_ids(cluster.cluster_id)
            passing_session = (
                EvalLogger.objects.filter(
                    trace_session__project_id=cluster.project_id,
                    custom_eval_config_id=cluster.eval_config_id,
                    deleted=False,
                )
                .filter(Q(output_bool=True) | Q(output_float__gte=1.0))
                .exclude(trace_session_id__in=member_session_ids)
                .order_by("-created_at")
                .values_list("trace_session_id", flat=True)
                .first()
            )
            if passing_session:
                rep_tid = _session_rep_trace_map(
                    [str(passing_session)], str(cluster.project_id)
                ).get(str(passing_session))
                rep_trace = (
                    Trace.objects.filter(id=rep_tid).first() if rep_tid else None
                )
                if rep_trace:
                    success_trace = TracePreview(
                        trace_id=str(rep_trace.id),
                        input=_trace_input_str(rep_trace),
                        output=_trace_output_str(rep_trace),
                    )
        else:
            member_ids = _trace_ids_for_cluster(
                cluster.cluster_id, str(cluster.project_id)
            )
            passing = (
                EvalLogger.objects.filter(
                    trace__project_id=cluster.project_id,
                    custom_eval_config_id=cluster.eval_config_id,
                    deleted=False,
                )
                .filter(Q(output_bool=True) | Q(output_float__gte=1.0))
                .exclude(trace_id__in=member_ids)
                .select_related("trace")
                .order_by("-created_at")
                .first()
            )
            if passing and passing.trace:
                success_trace = TracePreview(
                    trace_id=str(passing.trace_id),
                    input=_trace_input_str(passing.trace),
                    output=_trace_output_str(passing.trace),
                )

    representative_trace: TracePreview | None = None
    if row.trace_id:
        # Direct Trace fetch — session clusters' latest_trace_id is an
        # effective trace (the session's rep), which has no junction row.
        rep_trace_obj = Trace.objects.filter(id=row.trace_id).first()
        if rep_trace_obj:
            representative_trace = TracePreview(
                trace_id=str(rep_trace_obj.id),
                input=_trace_input_str(rep_trace_obj),
                output=_trace_output_str(rep_trace_obj),
            )

    rca = RcaSummary(
        synthesis=cluster.rca_synthesis,
        fix=cluster.rca_fix,
        confidence=cluster.rca_confidence,
        evidence_trace_ids=cluster.rca_evidence_trace_ids or [],
        analyzed_at=cluster.rca_at,
        failures_at_run=cluster.rca_failures_at_run,
        trace=cluster.rca_trace or [],
    )

    return FeedDetailCore(
        row=row,
        description=cluster.combined_description,
        success_trace=success_trace,
        representative_trace=representative_trace,
        rca=rca,
    )


def update_cluster(
    cluster_id: str,
    project_ids: list[str] | None,
    payload: FeedUpdatePayload,
) -> FeedDetailCore | None:
    """Update status/severity/assignee on a cluster, return fresh detail."""
    qs = TraceErrorGroup.objects.filter(cluster_id=cluster_id, deleted=False)
    if project_ids is not None:
        qs = qs.filter(project_id__in=project_ids)
    cluster = qs.first()
    if not cluster:
        return None

    update_fields: list[str] = []

    if payload.status is not None:
        cluster.status = payload.status
        update_fields.append("status")

    if payload.severity is not None:
        cluster.priority = severity_to_priority(payload.severity)
        update_fields.append("priority")

    if payload.assignee_provided:
        user = None
        if payload.assignee:
            user = (
                User.objects.filter(email__iexact=payload.assignee, is_active=True)
                .filter(
                    Q(organization_id=cluster.project.organization_id)
                    | Q(
                        organization_memberships__organization_id=cluster.project.organization_id,
                        organization_memberships__is_active=True,
                        organization_memberships__deleted=False,
                    )
                )
                .distinct()
                .first()
            )
            if user is None:
                raise ValueError(
                    "Assignee is not an active member of this organization"
                )
        cluster.assignee = user
        update_fields.append("assignee")

    if update_fields:
        update_fields.append("updated_at")
        cluster.save(update_fields=update_fields)

    return get_cluster_detail(cluster_id, project_ids)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _safe_str(val) -> str | None:
    """Ensure a value is a plain string (not a dict/list that would serialize as [object Object])."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, (dict, list)):
        import json

        return json.dumps(val, default=str)
    return str(val)


def _trace_input_str(trace) -> str | None:
    if not trace or trace.input is None:
        return None
    return _safe_str(trace.input)


def _trace_output_str(trace) -> str | None:
    if not trace or trace.output is None:
        return None
    return _safe_str(trace.output)


class _CHTraceShim:
    """Trace-like view backed by the CH root span, for post-cutover traces
    that have no PG ``Trace`` row. Exposes the attributes the row / rep
    builders read (``id``, ``created_at``, ``input``, ``output``)."""

    __slots__ = ("id", "created_at", "input", "output")

    def __init__(self, trace_id: str, root):
        self.id = trace_id
        self.created_at = getattr(root, "start_time", None) or timezone.now()
        self.input = getattr(root, "input", None)
        self.output = getattr(root, "output", None)


def _resolve_member_traces(trace_ids: list[str]) -> dict:
    """``{trace_id: trace-like}`` for cluster members — PG ``Trace`` where it
    exists, else a CH-backed shim built from the root span.

    Post-CH-cutover traces are CH-only (no PG ``Trace`` row), so resolving
    members through PG alone silently drops every collector trace from the
    Traces tab / Overview. Hydrate the missing ones from the spans table.
    """
    ids = [str(t) for t in trace_ids if t]
    if not ids:
        return {}
    out: dict = {str(t.id): t for t in Trace.objects.filter(id__in=ids)}
    missing = [tid for tid in ids if tid not in out]
    if missing:
        roots = _get_root_spans_batch(missing)
        for tid in missing:
            root = roots.get(tid)
            if root is not None:
                out[tid] = _CHTraceShim(tid, root)
    return out


def _cluster_member_ids(cluster_id: str) -> tuple[list[str], list[str]]:
    """(trace_ids, session_ids) of the cluster's junction members.

    Session-target eval results anchor their junction row to a TraceSession
    (trace FK NULL), so the two member kinds must be read separately.
    """
    rows = ErrorClusterTraces.objects.filter(
        cluster__cluster_id=cluster_id
    ).values_list("trace_id", "trace_session_id")
    trace_ids = [str(t) for t, _ in rows if t]
    session_ids = [str(s) for t, s in rows if s and not t]
    return trace_ids, session_ids


def _session_traces_map(
    session_ids: list[str], project_id: str
) -> dict[str, list[str]]:
    """{session_id: [trace_ids, newest first]} for sessions in one project.

    Session membership is read from the spans table's ``trace_session_id``
    (via ``CHSpanReader.session_trace_ids``), not the PG ``Trace.session`` FK:
    post session-cutover that FK is ``None`` for every trace, so the old
    ``Trace.objects.filter(session_id__in=…)`` walk returns EMPTY for ALL
    sessions. ``session_trace_ids`` is multi-tenant-pinned, so it needs the
    project the spans live under — every feed query is single-project, so the
    caller already knows it and threads it in (no PG ``TraceSession`` read; that
    dimension is ``None`` for net-new CH-only sessions). The CH method returns
    DISTINCT trace_ids in no order, so we re-order each session's set
    newest-first via one best-effort PG ``Trace`` read on ``created_at`` to
    preserve the documented contract (the rep-trace map depends on ``[0]``
    being the latest turn).
    """
    if not session_ids:
        return {}

    sid_set = {str(s) for s in session_ids if s}
    if not sid_set:
        return {}

    # CH read: one call per session (no batch variant exists). Members resolve
    # on a CH box from the spans' trace_session_id, remap-aware on both sides.
    # All sessions in one call belong to the project being queried.
    sid_to_trace_ids: dict[str, list[str]] = {}
    all_trace_ids: set[str] = set()
    with get_reader() as reader:
        for sid in sid_set:
            tids = reader.session_trace_ids(project_id, sid)
            if tids:
                sid_to_trace_ids[sid] = [str(t) for t in tids]
                all_trace_ids.update(sid_to_trace_ids[sid])

    if not all_trace_ids:
        return {}

    # Re-order newest-first: session_trace_ids returns an unordered DISTINCT set,
    # but callers (rep-trace map) require the latest turn first.
    order_rank = {
        str(tid): i
        for i, tid in enumerate(
            Trace.objects.filter(id__in=all_trace_ids)
            .order_by("-created_at")
            .values_list("id", flat=True)
        )
    }
    out: dict[str, list[str]] = {}
    for sid, tids in sid_to_trace_ids.items():
        out[sid] = sorted(tids, key=lambda t: order_rank.get(t, len(order_rank)))
    return out


def _session_rep_trace_map(session_ids: list[str], project_id: str) -> dict[str, str]:
    """{session_id: the session's latest trace_id} for sessions in one project.

    The feed UI navigates traces; a session member is represented by its
    most recent trace — the turn where a conversation-level failure
    manifests.
    """
    return {
        sid: tids[0]
        for sid, tids in _session_traces_map(session_ids, project_id).items()
    }


def _trace_ids_for_cluster(cluster_id: str, project_id: str) -> list[str]:
    """Navigable trace_ids for a cluster's members.

    Trace/span members contribute their own trace; session members resolve
    to their session's latest trace via ``_session_rep_trace_map``.
    """
    trace_ids, session_ids = _cluster_member_ids(cluster_id)
    if session_ids:
        trace_ids.extend(_session_rep_trace_map(session_ids, project_id).values())
    return trace_ids


# ---------------------------------------------------------------------------
# Overview tab endpoint
# ---------------------------------------------------------------------------


def _fetch_events_over_time(
    cluster_id: str, days: int = 14
) -> list[EventsOverTimePoint]:
    """Bucket ErrorClusterTraces.created_at into daily error counts."""
    since = timezone.now() - timedelta(days=days)
    rows = (
        ErrorClusterTraces.objects.filter(
            cluster__cluster_id=cluster_id,
            created_at__gte=since,
        )
        .annotate(bucket=TruncDate("created_at"))
        .values("bucket")
        .annotate(errors=Count("id", distinct=False))
        .order_by("bucket")
    )
    return [
        EventsOverTimePoint(
            date=row["bucket"].isoformat() if row["bucket"] else "",
            errors=row["errors"],
            passing=0,
            users=0,
        )
        for row in rows
    ]


# Words that sklearn's default English stopword list doesn't catch but that
# are scanner-template noise (every brief says "result"/"output"/"task",
# every task says "asks"/"requires"/"returns"). Merged with the vectorizer's
# built-in 'english' list and filtered out AFTER TF-IDF scoring so they
# never surface as insights.
_SCANNER_FILLER_STOPWORDS = frozenset(
    {
        # Structural scanner-template nouns
        "result",
        "results",
        "output",
        "outputs",
        "input",
        "inputs",
        "task",
        "tasks",
        "trace",
        "traces",
        "span",
        "spans",
        "agent",
        "agents",
        "issue",
        "issues",
        "error",
        "errors",
        "step",
        "steps",
        "pipeline",
        "brief",
        "briefs",
        # Task-framing / descriptor verbs that appear in every scanner brief
        "asks",
        "ask",
        "requests",
        "requested",
        "requires",
        "require",
        "returns",
        "returned",
        "contains",
        "contain",
        "includes",
        "include",
        "expected",
        "expect",
        "expects",
        "provide",
        "provides",
        "provided",
        "given",
        "gives",
        "give",
        # Generic verb filler
        "failed",
        "fails",
        "fail",
        "failing",
        "unclear",
    }
)


def _tfidf_distinctive_terms(
    target_doc: str,
    corpus: list[str],
    top_k: int,
    ngram_range: tuple[int, int] = (1, 1),
) -> list[tuple[str, float]]:
    """Rank terms in ``target_doc`` by TF-IDF weight against ``corpus``.

    ``corpus`` must include ``target_doc`` as one of its entries. Returns
    up to ``top_k`` ``(term, score)`` pairs sorted by descending score.
    Empty list on degenerate inputs (corpus <2 docs, empty vocab, etc).
    """
    if not target_doc or len(corpus) < 2:
        return []
    try:
        target_idx = corpus.index(target_doc)
    except ValueError:
        return []

    try:
        vec = TfidfVectorizer(
            stop_words="english",
            ngram_range=ngram_range,
            lowercase=True,
            min_df=1,
            # Only real alphabetic words of length >=3 — skips numbers and
            # 1-2 char noise; TF-IDF's IDF handles the rest.
            token_pattern=r"(?u)\b[A-Za-z]{3,}\b",
            sublinear_tf=True,
        )
        matrix = vec.fit_transform(corpus)
    except ValueError:
        return []

    row = matrix[target_idx].toarray()[0]
    terms = vec.get_feature_names_out()
    scored = [
        (str(t), float(s))
        for t, s in zip(terms, row, strict=True)
        if s > 0 and str(t) not in _SCANNER_FILLER_STOPWORDS
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]


def _project_cluster_briefs_corpus(
    project_id: str,
) -> tuple[list[str], list[str]]:
    """One ``(cluster_ids, docs)`` pair per project — each doc is a cluster's
    concatenated scanner issue briefs. Clusters without briefs are skipped.
    Single query, grouped in Python.
    """
    rows = TraceScanIssue.objects.filter(
        scan_result__project_id=project_id,
        cluster__source=ClusterSource.SCANNER,
    ).values_list("cluster__cluster_id", "brief")

    by_cluster: dict[str, list[str]] = {}
    for cid, brief in rows:
        if not cid or not brief:
            continue
        by_cluster.setdefault(cid, []).append(brief)

    cluster_ids = list(by_cluster.keys())
    corpus = [" ".join(by_cluster[cid]) for cid in cluster_ids]
    return cluster_ids, corpus


def _project_cluster_inputs_corpus(
    project_id: str,
) -> tuple[list[str], list[str], dict]:
    """One doc per cluster = all its traces' root inputs concatenated.

    Returns ``(cluster_ids, docs, cluster_to_trace_inputs)`` where the last
    dict maps ``cluster_id → {trace_id: input_text}`` so callers can count
    how many traces contain a particular term without another round-trip.
    """
    ect_rows = ErrorClusterTraces.objects.filter(
        cluster__project_id=project_id,
        cluster__source=ClusterSource.SCANNER,
    ).values_list("cluster__cluster_id", "trace_id")
    cluster_to_traces: dict[str, list[str]] = {}
    all_trace_ids: set = set()
    for cid, tid in ect_rows:
        if not cid or not tid:
            continue
        tid_str = str(tid)
        cluster_to_traces.setdefault(cid, []).append(tid_str)
        all_trace_ids.add(tid_str)

    if not all_trace_ids:
        return [], [], {}

    # Was: ObservationSpan.filter(trace_id__in=, parent_span_id IS NULL
    # OR ="").values_list("trace_id", "span_attributes"). Loaded the
    # full row set since PG returns one row per matching span; CH
    # `list_by_trace_ids` does the same in one query then we pick the
    # first parentless span per trace in Python. attrs_string is the
    # typed-Map column where string-valued attrs like input.value live.
    with get_reader() as reader:
        all_spans = reader.list_by_trace_ids(list(all_trace_ids))
    trace_input: dict[str, str] = {}
    for span in all_spans:
        if span.parent_span_id:  # root = parent_span_id is "" (CHSpan default)
            continue
        tid_str = str(span.trace_id)
        if tid_str in trace_input:
            continue  # first root wins, matches PG one-row-per-trace semantics
        text = (span.attrs_string or {}).get("input.value", "") or ""
        if text:
            trace_input[tid_str] = str(text)

    cluster_ids: list[str] = []
    corpus: list[str] = []
    per_cluster_inputs: dict = {}
    for cid, tids in cluster_to_traces.items():
        inputs = {tid: trace_input[tid] for tid in tids if tid in trace_input}
        if not inputs:
            continue
        cluster_ids.append(cid)
        corpus.append(" ".join(inputs.values()))
        per_cluster_inputs[cid] = inputs
    return cluster_ids, corpus, per_cluster_inputs


def _cluster_highlight_terms(
    cluster_id: str, project_id: str, top_k: int = 10
) -> list[str]:
    """TF-IDF-distinctive terms for this cluster's briefs vs the rest of
    the project. Used to light up matching substrings in the failing trace
    evidence reel.
    """
    cluster_ids, corpus = _project_cluster_briefs_corpus(project_id)
    if cluster_id not in cluster_ids:
        return []
    target = corpus[cluster_ids.index(cluster_id)]
    return [term for term, _ in _tfidf_distinctive_terms(target, corpus, top_k)]


# ── Statistical helpers (effect-size vs the KNN-passing baseline) ──────────


def _log_odds_distinctive(
    fail_docs: list[str],
    base_docs: list[str],
    ngram_range: tuple[int, int] = (2, 3),
    min_z: float = 1.96,
    top_k: int = 8,
) -> list[tuple[str, float, int, int]]:
    """Monroe et al. "Fightin' Words" weighted log-odds z-score with an
    informative Dirichlet prior, for n-grams distinctive to ``fail_docs`` vs
    ``base_docs``. Returns ``(term, z, df_fail, df_base)`` (z >= ``min_z``,
    sorted by z desc then longer-phrase-first). df_* counts come from the SAME
    tokenization — do NOT re-match the stripped n-gram against raw text.

    Prior alpha_w = a0 * p_w (p_w = pooled frequency), with a0 = |V| — a light
    one-pseudo-count-per-term regularizer that tames rare-term inflation on
    small-N clusters without using the raw pooled count (which over-weights
    merely-frequent terms). For term w::

        delta = log((y_f+a)/(n_f+a0-y_f-a)) - log((y_b+a)/(n_b+a0-y_b-a))
        z     = delta / sqrt(1/(y_f+a) + 1/(y_b+a))
    """
    if not fail_docs or not base_docs:
        return []
    try:
        vec = CountVectorizer(
            stop_words="english",
            ngram_range=ngram_range,
            lowercase=True,
            token_pattern=r"(?u)\b[A-Za-z]{3,}\b",
            min_df=2,
        )
        matrix = vec.fit_transform(fail_docs + base_docs)
    except ValueError:
        return []

    n = len(fail_docs)
    y_f = np.asarray(matrix[:n].sum(axis=0)).ravel().astype(float)
    y_b = np.asarray(matrix[n:].sum(axis=0)).ravel().astype(float)
    # Document frequencies from the same matrix (binarized) — the honest
    # "how many docs contain this n-gram", immune to the stopword-gap regex
    # mismatch a raw-text re-match would suffer.
    binm = matrix > 0
    df_f = np.asarray(binm[:n].sum(axis=0)).ravel().astype(int)
    df_b = np.asarray(binm[n:].sum(axis=0)).ravel().astype(int)
    # Informative Dirichlet prior (Monroe): alpha_w = a0 * background-share,
    # the pooled (fail+base) corpus as the empirical background and prior mass
    # a0 = |V| (~one pseudo-count per term) as a light regularizer. Scaling to
    # |V| — rather than the raw pooled count (a0 = corpus size) — is what stops
    # the prior from swamping the data and over-weighting frequent terms.
    pooled = y_f + y_b
    pooled_total = float(pooled.sum())
    n_f = float(y_f.sum())
    n_b = float(y_b.sum())
    if pooled_total <= 0 or n_f <= 0 or n_b <= 0:
        return []
    a0 = float(len(pooled))  # |V| — total prior mass / shrinkage strength
    alpha = a0 * pooled / pooled_total  # sum(alpha) == a0

    eps = 1e-9
    f_num = y_f + alpha
    b_num = y_b + alpha
    # Denominators are >= 0 by construction (n>=y, alpha>=count); clamp the
    # degenerate all-same-term case so log/sqrt never blow up.
    f_den = np.maximum(n_f + a0 - f_num, eps)
    b_den = np.maximum(n_b + a0 - b_num, eps)
    delta = np.log(f_num / f_den) - np.log(b_num / b_den)
    var = 1.0 / np.maximum(f_num, eps) + 1.0 / np.maximum(b_num, eps)
    z = delta / np.sqrt(var)

    terms = vec.get_feature_names_out()
    scored = [
        (str(terms[i]), float(z[i]), int(df_f[i]), int(df_b[i]))
        for i in range(len(terms))
        if z[i] >= min_z and str(terms[i]) not in _SCANNER_FILLER_STOPWORDS
    ]
    # z desc, then longer phrase first — when correlated n-grams tie on z,
    # "billing api timed out" reads better than the "api timed" fragment.
    scored.sort(key=lambda r: (round(r[1], 3), r[0].count(" ")), reverse=True)
    return scored[:top_k]


def _readable_phrase(
    scored: list[tuple[str, float, int, int]],
) -> tuple[str, float, int, int] | None:
    """Pick the most readable phrase from log-odds output: among the terms
    whose z is within 15% of the top, prefer the longest (most words) — turns
    "api timed" into "billing api timed out" without dropping signal."""
    if not scored:
        return None
    top_z = scored[0][1]
    near = [r for r in scored if r[1] >= 0.85 * top_z]
    return max(near, key=lambda r: r[0].count(" "))


def _root_input_texts(trace_ids: list[str]) -> dict[str, str]:
    """{trace_id: root-span ``input.value``} for the given traces. Powers the
    failing-vs-passing input corpora for the distinctive-topic builder."""
    roots = _get_root_spans_batch(trace_ids)
    out: dict[str, str] = {}
    for tid, root in roots.items():
        text = (root.attrs_string or {}).get("input.value") or root.input or ""
        if text:
            out[str(tid)] = str(text)
    return out


# Short TTL for the project-wide KNN baseline (item below). The baseline is a
# statistical contrast set, not a correctness-critical read, so a few minutes of
# staleness is acceptable in exchange for keeping the linear vector scan off the
# synchronous overview/detail path on repeat hits.
_BASELINE_CACHE_TTL = 300  # 5 minutes


def _baseline_cache_key(cluster_id: str, project_id: str, k: int) -> str:
    return f"feed_passing_baseline:{project_id}:{cluster_id}:{k}"


def _passing_baseline_trace_ids(
    cluster_id: str, project_id: str, exclude: list[str], k: int = 120
) -> list[str]:
    """Up to ``k`` KNN-passing trace_ids for the cluster's representative input.

    A linear vector scan whose cost scales with project volume, not cluster
    size. Cached in the Django cache (short TTL, keyed by project + cluster + k)
    so the scan runs at most once per TTL instead of on every overview/detail
    request — the result is a statistical contrast set, so brief staleness is
    fine. ``exclude`` is the cluster's own members and is functionally determined
    by ``cluster_id`` within a TTL, so it stays out of the key. A proper async
    precompute (write the baseline once at cluster time) remains a follow-up; the
    builders below are unchanged when it moves.
    """
    cache_key = _baseline_cache_key(cluster_id, project_id, k)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Lazy import: scan_clustering pulls the ClickHouse + embedding stack,
    # which shouldn't load on every feed-query import.
    from tracer.queries.scan_clustering import (
        find_success_trace_baseline,
        get_cluster_trace_embeddings,
    )

    rep = get_cluster_trace_embeddings(cluster_id, project_id)
    if not rep:
        return []
    _rep_tid, embedding = rep
    baseline = find_success_trace_baseline(
        embedding, project_id, k=k, exclude_trace_ids=exclude
    )
    result = [tid for tid, _dist in baseline]
    cache.set(cache_key, result, _BASELINE_CACHE_TTL)
    return result


def _kfmt(n: float) -> str:
    """Compact token count: 8200 -> '8.2k', 420 -> '420'."""
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{int(round(n))}"


# ── Individual insight builders ───────────────────────────────────────────
#
# Each returns a PatternInsight | None. None = effect below the firing floor
# (the stat test is the GATE, not the message). The picker ranks firing
# builders by normalized ``effect`` (0..1) and renders the top 4. Card copy
# is plain-English and number-light; stat rigor lives in ``evidence`` (tooltip
# only), never in the headline/detail strings.


def _insight_distinctive_topic(
    cluster_id: str, project_id: str, trace_ids: list[str], baseline_ids: list[str]
) -> PatternInsight | None:
    """A topic these failing inputs share that working runs don't — log-odds
    of failing root-inputs vs the KNN-passing baseline."""
    if not baseline_ids:
        return None
    fail_texts = list(_root_input_texts(trace_ids).values())
    base_texts = list(_root_input_texts(baseline_ids).values())
    if len(fail_texts) < 2 or len(base_texts) < 2:
        return None
    picked = _readable_phrase(_log_odds_distinctive(fail_texts, base_texts, (2, 3)))
    if not picked:
        return None
    term, z, hits, base_hits = picked
    total = len(fail_texts)
    # z (>=1.96) is the statistical gate; just require the phrase to actually
    # recur (>=2 docs) so the rendered "X% of these" count isn't a single-doc
    # fluke.
    if hits < 2:
        return None
    fail_pct = round(100 * hits / total)
    base_pct = round(100 * base_hits / len(base_texts))
    return PatternInsight(
        title="Shared input topic",
        value=f"{fail_pct}%",
        caption=f'share topic **"{term}"** · vs {base_pct}% of working runs',
        effect=min(1.0, float(z) / 10.0),
        evidence={
            "test": "log-odds w/ Dirichlet prior (Monroe)",
            "z": round(float(z), 2),
            "fail_pct": fail_pct,
            "baseline_pct": base_pct,
            "baseline": f"{len(base_texts)} KNN-passing traces",
        },
    )


def _insight_brief_phrase(cluster_id: str, project_id: str) -> PatternInsight | None:
    """Phrase distinctive to this scanner cluster's briefs vs the rest of the
    project's clusters (log-odds). Passing traces have no briefs, so the
    contrast here is other clusters — not the passing baseline.

    Briefs are fed as individual docs (NOT concatenated) so the log-odds
    document-frequency prior is meaningful — concatenating to one-doc-per-side
    would make ``min_df`` require a term in both sides and kill every
    distinctive n-gram.
    """
    rows = (
        TraceScanIssue.objects.filter(
            scan_result__project_id=project_id, cluster__source=ClusterSource.SCANNER
        )
        .exclude(brief__isnull=True)
        .exclude(brief="")
        .values_list("cluster__cluster_id", "brief")
    )
    mine: list[str] = []
    others: list[str] = []
    for cid, brief in rows:
        (mine if cid == cluster_id else others).append(brief)
    if len(mine) < 2 or len(others) < 2:
        return None
    picked = _readable_phrase(_log_odds_distinctive(mine, others, (2, 3)))
    if not picked:
        return None
    term, z, hits, _base_hits = picked
    total = len(mine)
    if hits < 2:
        return None
    return PatternInsight(
        title="Common failure phrase",
        value=f"{hits} / {total}",
        caption=f'scans flag **"{term}"** · rare in other clusters',
        effect=min(1.0, float(z) / 10.0),
        evidence={
            "test": "log-odds w/ Dirichlet prior (Monroe)",
            "z": round(float(z), 2),
            "hits": hits,
            "total": total,
            "baseline": "other clusters' briefs in this project",
        },
    )


def _insight_distribution_shift(
    trace_ids: list[str], baseline_ids: list[str], metric: str
) -> PatternInsight | None:
    """KS two-sample on a per-trace numeric (``latency`` or ``tokens``) vs the
    passing baseline. Fires when the distributions differ (p < 0.05) AND the
    failing side is materially larger (median ratio >= 1.5)."""
    if not baseline_ids:
        return None
    fail_tot = _get_trace_totals_batch(trace_ids)
    base_tot = _get_trace_totals_batch(baseline_ids)

    def _pick(totals: dict) -> list[float]:
        vals = []
        for lat, prompt, completion in totals.values():
            v = lat if metric == "latency" else (prompt or 0) + (completion or 0)
            if v:
                vals.append(float(v))
        return vals

    fail_vals = _pick(fail_tot)
    base_vals = _pick(base_tot)
    if len(fail_vals) < 3 or len(base_vals) < 3:
        return None
    try:
        ks = ks_2samp(fail_vals, base_vals)
    except ValueError:
        return None
    if ks.pvalue > 0.05:
        return None
    fail_med = float(np.median(fail_vals))
    base_med = float(np.median(base_vals))
    if base_med <= 0 or fail_med / base_med < 1.5:
        return None
    ratio = fail_med / base_med
    evidence = {
        "test": "KS two-sample",
        "p_value": round(float(ks.pvalue), 4),
        "ks_stat": round(float(ks.statistic), 3),
        "fail_median": round(fail_med, 1),
        "baseline_median": round(base_med, 1),
        "baseline": f"{len(base_vals)} KNN-passing traces",
    }
    if metric == "latency":
        fs, bs = fail_med / 1000.0, base_med / 1000.0
        caption = f"vs **~{bs:.1f}s** on working runs"
        if fs >= 10:
            caption += " · likely timing out"
        return PatternInsight(
            title="Latency shift",
            value=f"~{fs:.1f}s",
            caption=caption,
            effect=min(1.0, (ratio - 1.0) / 4.0),
            evidence=evidence,
        )
    return PatternInsight(
        title="Token usage",
        value=f"~{_kfmt(fail_med)}",
        caption=f"vs **~{_kfmt(base_med)}** tokens on working runs",
        effect=min(1.0, (ratio - 1.0) / 4.0),
        evidence=evidence,
    )


def _tool_name_set(items) -> set[str]:
    """Normalize a ``tools_available``/``tools_called`` list (bare names or
    ``{"name", "status"}`` dicts) to a set of tool-name strings."""
    out: set[str] = set()
    for item in items or []:
        name = item.get("name") if isinstance(item, dict) else item
        if name:
            out.add(name)
    return out


def _insight_missing_tool(trace_ids: list[str]) -> PatternInsight | None:
    """A tool the agent had available but didn't use — the most actionable
    scanner signal ("the missing step")."""
    if not trace_ids:
        return None
    metas = TraceScanResult.objects.filter(trace_id__in=trace_ids).values_list(
        "meta", flat=True
    )
    missing_counter: Counter = Counter()
    traces_with_tools = 0
    for meta in metas:
        if not meta:
            continue
        # tools_available is a list of names; tools_called is a list of dicts
        # ({"name", "status"}). Normalize both to a set of names — a raw
        # set() over the dict form raises "unhashable type: dict".
        available = _tool_name_set(meta.get("tools_available"))
        if not available:
            continue
        traces_with_tools += 1
        called = _tool_name_set(meta.get("tools_called"))
        for tool in available - called:
            missing_counter[tool] += 1
    if not missing_counter or traces_with_tools == 0:
        return None
    top_tool, top_n = missing_counter.most_common(1)[0]
    if top_n < max(2, (traces_with_tools + 1) // 2):
        return None
    never = top_n == traces_with_tools
    if never:
        caption = f"never called **{top_tool}** — could be the missing step"
    else:
        caption = f"skipped **{top_tool}** in these runs"
    return PatternInsight(
        title="Missing step",
        value=f"{top_n} / {traces_with_tools}",
        caption=caption,
        effect=top_n / traces_with_tools,
        evidence={
            "tool": top_tool,
            "missing_in": top_n,
            "traces_with_tools": traces_with_tools,
        },
    )


def _insight_judge_phrase(cluster_id: str, project_id: str) -> PatternInsight | None:
    """Eval twin of ``_insight_brief_phrase``: phrase distinctive to this
    cluster's judge explanations vs the project's other eval clusters
    (log-odds). Eval clusters often have no scanner briefs and no KNN
    passing baseline (the embedding pass may never have run for the
    project), so the contrast is other eval clusters — same shape as the
    scanner brief contrast."""
    rows = (
        ErrorClusterTraces.objects.filter(
            cluster__project_id=project_id,
            cluster__source=ClusterSource.EVAL,
            eval_logger__isnull=False,
        )
        .exclude(eval_logger__eval_explanation__isnull=True)
        .exclude(eval_logger__eval_explanation="")
        .values_list("cluster__cluster_id", "eval_logger__eval_explanation")
    )
    mine: list[str] = []
    others: list[str] = []
    for cid, explanation in rows:
        (mine if cid == cluster_id else others).append(explanation)
    if len(mine) < 2 or len(others) < 2:
        return None
    picked = _readable_phrase(_log_odds_distinctive(mine, others, (2, 3)))
    if not picked:
        return None
    term, z, hits, _base_hits = picked
    if hits < 2:
        return None
    return PatternInsight(
        title="Common evaluator finding",
        value=f"{hits} / {len(mine)}",
        caption=f'evaluators flag **"{term}"** · rare in other clusters',
        effect=min(1.0, float(z) / 10.0),
        evidence={
            "test": "log-odds w/ Dirichlet prior (Monroe)",
            "z": round(float(z), 2),
            "hits": hits,
            "total": len(mine),
        },
    )


def _scanner_key_moments(trace_ids: list[str]) -> list[KeyMoment]:
    """Deduped kevinified key-moment pairs from the cluster's scan results
    (max 8). Separate from the insight grid — rendered as its own list."""
    rows = TraceScanResult.objects.filter(trace_id__in=trace_ids).values_list(
        "key_moments", flat=True
    )
    seen: set = set()
    out: list[KeyMoment] = []
    for km_list in rows:
        for km in km_list or []:
            kv = km.get("kevinified", "")
            if not kv or kv in seen:
                continue
            seen.add(kv)
            out.append(KeyMoment(kevinified=kv, verbatim=km.get("verbatim", "") or ""))
            if len(out) >= 8:
                return out
    return out


def _fetch_pattern_summary(cluster_id: str) -> PatternSummary:
    """Adaptive Pattern Summary: effect-size cards vs the KNN-passing baseline.

    Runs all applicable builders, keeps the ones that clear their firing floor
    (the stat test is the gate), ranks by normalized effect size, renders the
    top 4. Fewer than 4 strong cards is fine — better than 4 with 2 weak.

    Computed live on every overview request today — KNN baseline + several
    CountVectorizer/KS fits over corpora that scale with project size. No cache
    exists yet (no field reads or writes one); moving this to an async
    precompute that materializes the result is a tracked follow-up. The builders
    don't change when it moves.
    """
    cluster = TraceErrorGroup.objects.filter(cluster_id=cluster_id).first()
    if not cluster:
        return PatternSummary()

    project_id = str(cluster.project_id)
    trace_ids = _trace_ids_for_cluster(cluster_id, project_id)
    is_scanner = cluster.source == ClusterSource.SCANNER

    baseline_ids = _passing_baseline_trace_ids(
        cluster_id, project_id, exclude=trace_ids
    )

    # Shared builders (both sources) compare vs the KNN-passing baseline.
    candidates: list[PatternInsight | None] = [
        _insight_distinctive_topic(cluster_id, project_id, trace_ids, baseline_ids),
        _insight_distribution_shift(trace_ids, baseline_ids, "latency"),
        _insight_distribution_shift(trace_ids, baseline_ids, "tokens"),
    ]
    # Scanner-only builders (no passing counterpart for briefs / scan meta).
    if is_scanner:
        candidates.append(_insight_missing_tool(trace_ids))
        candidates.append(_insight_brief_phrase(cluster_id, project_id))
    else:
        candidates.append(_insight_judge_phrase(cluster_id, project_id))

    firing = [c for c in candidates if c is not None]
    firing.sort(key=lambda c: c.effect, reverse=True)
    insights = firing[:4]

    key_moments = _scanner_key_moments(trace_ids) if is_scanner else []
    return PatternSummary(insights=insights, key_moments=key_moments)


def _get_root_span(trace_id: str) -> CHSpan | None:
    """Root span = no parent (NULL or empty string).

    Single-trace convenience — delegates to roots_by_trace_ids which
    queries only root spans (parent_span_id = '') instead of listing
    every span. Returns the latest root per the batch helper's ordering.
    """
    roots = _get_root_spans_batch([str(trace_id)])
    return roots.get(str(trace_id))


def _get_root_spans_batch(trace_ids: list[str]) -> dict:
    """Return {trace_id_str: CHSpan} -- latest root span per trace."""
    if not trace_ids:
        return {}
    with get_reader() as reader:
        spans = reader.roots_by_trace_ids([str(t) for t in trace_ids])
    out: dict = {}
    # CH orders by (trace_id, start_time, id) ASC; we want the latest root
    # per trace_id, so a pass that always overwrites keeps the newest.
    for span in spans:
        out[str(span.trace_id)] = span
    return out


def _get_trace_totals(
    trace_id: str,
) -> tuple[int | None, int | None, int | None]:
    """Return (latency_ms, prompt_tokens, completion_tokens) aggregated from spans.

    Single-trace convenience -- uses _get_trace_totals_batch under the
    hood so the CH query shape is identical.
    """
    totals = _get_trace_totals_batch([str(trace_id)])
    return totals.get(str(trace_id), (None, None, None))


def _get_trace_totals_batch(trace_ids: list[str]) -> dict:
    """Return {trace_id_str: (latency, prompt, completion)} aggregated from spans.

    Summed CH-side — three ints per trace come back, not the span payloads.
    """
    if not trace_ids:
        return {}
    with get_reader() as reader:
        return reader.totals_by_trace_ids([str(t) for t in trace_ids])


def _get_trace_score(trace_id: str) -> float | None:
    """Average EvalLogger score across span-level evals on the trace.

    PR3: target_type='span' keeps this average comparable to its pre-row_type
    behaviour. Trace-level evals (PR4) are a different semantic unit (one
    per trace, not per span); their score should surface separately.

    Bool-typed evals contribute via EVAL_SCORE_EXPR (0/1) — sim/voice
    clusters need this or output_bool-only evals silently score 0.
    """
    return EvalLogger.objects.filter(trace_id=trace_id, target_type="span").aggregate(
        avg=Avg(EVAL_SCORE_EXPR)
    )["avg"]


def _get_trace_scores_batch(trace_ids: list[str]) -> dict:
    """Return {trace_id_str: avg eval score} — span-level evals; bool counted as 0/1."""
    if not trace_ids:
        return {}
    rows = (
        EvalLogger.objects.filter(trace_id__in=trace_ids, target_type="span")
        .values("trace_id")
        .annotate(avg=Avg(EVAL_SCORE_EXPR))
        .filter(avg__isnull=False)
    )
    return {str(r["trace_id"]): r["avg"] for r in rows}


def _get_scan_results_batch(trace_ids: list[str]) -> dict:
    """Return {trace_id_str: TraceScanResult} — first scan result per trace."""
    if not trace_ids:
        return {}
    rows = TraceScanResult.objects.filter(trace_id__in=trace_ids).only(
        "id", "trace_id", "meta", "key_moments"
    )
    out: dict = {}
    for sr in rows:
        tid = str(sr.trace_id)
        if tid not in out:
            out[tid] = sr
    return out


def _highlight_text(text: str, terms: list[str], hl: str) -> object:
    """Wrap matching substrings in ``text`` as rich-text segments.

    Returns the original string when nothing matches (frontend ``RichText``
    component accepts either a plain string or a ``[{t, hl}]`` array).
    """
    if not text or not terms:
        return text

    # Build one case-insensitive regex over all terms, longest first so
    # multi-word phrases (should we ever add them) take priority.
    sorted_terms = sorted({t for t in terms if t}, key=len, reverse=True)
    if not sorted_terms:
        return text
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(t) for t in sorted_terms) + r")\b",
        re.IGNORECASE,
    )

    segments: list[dict] = []
    last = 0
    for m in pattern.finditer(text):
        start, end = m.span()
        if start > last:
            segments.append({"t": text[last:start]})
        segments.append({"t": text[start:end], "hl": hl})
        last = end
    if not segments:
        return text
    if last < len(text):
        segments.append({"t": text[last:]})
    return segments


def _attribute_old_key_moments(key_moments: list, trace_id: str) -> list:
    """Reconstruct span attribution for old scans whose stored key_moments
    predate it. Deterministic (no LLM): re-match the stored quotes against the
    trace's live spans. Hybrid fast-path — new scans already carry ``role`` and
    never reach here. Returns the moments unchanged if EE/spans are unavailable.
    """
    from tracer.ee_boundary import attribute_key_moments

    return attribute_key_moments(key_moments, trace_id)


def _key_moments_to_reel(
    key_moments: list | None,
    highlight_terms: list[str] | None = None,
    hl: str = "error",
    trace_id: str | None = None,
) -> list[dict]:
    """
    Map TraceScanResult.key_moments to ReelStep dicts the frontend renders.

    Frontend ReelStep shape: { label, text, span, status, isFailure, raw, meta }.
    New scans carry deterministic span attribution (role/span/status/
    is_failure) → a rich, grounded breadcrumb row. Old scans (no ``role``) are
    enriched at runtime from the live span tree when ``trace_id`` is given;
    only if that's unavailable do we emit the plain flat row (label
    "EVIDENCE"). Either way the FE renders genuine data, never the stub.

    Displayed ``text`` is the kevinified excerpt (readable); ``raw`` is the
    exact verbatim for the expand-to-raw view.
    """
    moments = list(key_moments or [])
    # Hybrid: runtime-reconstruct attribution for old scans only (new scans
    # already have role → zero overhead, no span re-fetch).
    if trace_id and any(km and not km.get("role") for km in moments):
        moments = _attribute_old_key_moments(moments, trace_id)

    steps: list[dict] = []
    for km in moments:
        verbatim = (km.get("verbatim") or "").strip()
        kevinified = (km.get("kevinified") or "").strip()
        if not verbatim and not kevinified:
            continue
        display = kevinified or verbatim
        text = _highlight_text(display, highlight_terms or [], hl)
        role = (km.get("role") or "").strip()
        if role:
            steps.append(
                {
                    "label": role,
                    "text": text,
                    "span": km.get("span") or None,
                    "status": km.get("status") or "ok",
                    "isFailure": bool(km.get("is_failure")),
                    "raw": verbatim or display,
                    "meta": None,
                }
            )
        else:
            # Old scan (no attribution) — real flat evidence, not the stub.
            steps.append({"label": "EVIDENCE", "text": text, "meta": None})
    return steps


def _conversation_utterance(attrs: dict, *, last_agent: bool = False) -> str | None:
    """Fallback I/O text for voice conversation roots, which carry a
    flattened transcript (``conversation.transcript.{i}.message.role`` /
    ``.content``) instead of ``input.value``/``output.value``.

    Default: first caller utterance (the call's "input"). With
    ``last_agent=True``: last agent utterance (its "output").
    """
    if not attrs:
        return None
    target_roles = (
        ("assistant", "agent", "bot") if last_agent else ("user", "customer", "caller")
    )
    best_idx: int | None = None
    best_text: str | None = None
    for key, role in attrs.items():
        if not key.startswith("conversation.transcript.") or not key.endswith(
            ".message.role"
        ):
            continue
        if str(role).lower() not in target_roles:
            continue
        try:
            idx = int(key.split(".")[2])
        except (IndexError, ValueError):
            continue
        better = best_idx is None or (idx > best_idx if last_agent else idx < best_idx)
        if better:
            text = attrs.get(f"conversation.transcript.{idx}.message.content")
            if text:
                best_idx, best_text = idx, str(text)
    return best_text


def trace_judge(trace_id: str) -> tuple[str | None, float | None]:
    """The evaluator's reasoning + score for this trace — the lowest-scoring
    eval (the one explaining the failure), so the reason and the score badge
    refer to the SAME eval. (None, None) for scanner traces with no eval rows.
    Powers the eval-cluster I/O panel's judge-reason card."""
    row = (
        EvalLogger.objects.filter(trace_id=trace_id, deleted=False)
        .exclude(eval_explanation__isnull=True)
        .exclude(eval_explanation="")
        .annotate(_score=EVAL_SCORE_EXPR)
        # NULLS LAST: a scored eval outranks an explanation-only (null-score)
        # eval on every backend, not just Postgres.
        .order_by(F("_score").asc(nulls_last=True))
        .values("eval_explanation", "_score")
        .first()
    )
    if not row:
        return None, None
    return row["eval_explanation"], row["_score"]


def _trace_judges_batch(
    trace_ids: list[str],
) -> dict[str, tuple[str | None, float | None]]:
    """Batch form of trace_judge — one query for the whole rep-trace page
    instead of one per trace. Lowest-scoring eval wins per trace (ordering
    guarantees the first row seen per trace_id is the explainer)."""
    if not trace_ids:
        return {}
    rows = (
        EvalLogger.objects.filter(trace_id__in=trace_ids, deleted=False)
        .exclude(eval_explanation__isnull=True)
        .exclude(eval_explanation="")
        .annotate(_score=EVAL_SCORE_EXPR)
        # NULLS LAST per trace_judge: scored eval outranks null-score eval.
        .order_by("trace_id", F("_score").asc(nulls_last=True))
        .values_list("trace_id", "eval_explanation", "_score")
    )
    out: dict[str, tuple[str | None, float | None]] = {}
    for tid, explanation, score in rows:
        out.setdefault(str(tid), (explanation, score))
    return out


def _session_judges_batch(
    session_ids: list[str],
) -> dict[str, tuple[str | None, float | None]]:
    """Session twin of ``_trace_judges_batch`` — session-target EvalLogger
    rows anchor to trace_session (trace FK NULL), so the per-trace lookup
    never sees them. Keyed by session_id."""
    if not session_ids:
        return {}
    rows = (
        EvalLogger.objects.filter(trace_session_id__in=session_ids, deleted=False)
        .exclude(eval_explanation__isnull=True)
        .exclude(eval_explanation="")
        .annotate(_score=EVAL_SCORE_EXPR)
        # NULLS LAST per trace_judge: scored eval outranks null-score eval.
        .order_by("trace_session_id", F("_score").asc(nulls_last=True))
        .values_list("trace_session_id", "eval_explanation", "_score")
    )
    out: dict[str, tuple[str | None, float | None]] = {}
    for sid, explanation, score in rows:
        out.setdefault(str(sid), (explanation, score))
    return out


def _avg_session_eval_score(session_ids: list[str]) -> float | None:
    """Average session-target eval score over the given sessions
    (bool counted 0/1 via EVAL_SCORE_EXPR)."""
    if not session_ids:
        return None
    return EvalLogger.objects.filter(
        trace_session_id__in=session_ids, target_type=EvalTargetType.SESSION
    ).aggregate(avg=Avg(EVAL_SCORE_EXPR))["avg"]


def _build_representative_trace(
    trace: Trace,
    has_issues: bool,
    pass_reel: list[dict] | None = None,
    highlight_terms: list[str] | None = None,
    *,
    root: CHSpan | None = None,
    totals: tuple[int | None, int | None, int | None] | None = None,
    score: float | None = None,
    scan_result: TraceScanResult | None = None,
    judge: tuple[str | None, float | None] | None = None,
    _prefetched: bool = False,
) -> RepresentativeTrace:
    """Turn a Trace into a RepresentativeTrace dataclass.

    Prefetched values (``root``, ``totals``, ``score``, ``scan_result``)
    can be supplied by ``_fetch_representative_traces`` to avoid the per-
    trace round-trips. Pass ``_prefetched=True`` to skip the single-trace
    fallbacks even when a prefetched value is missing (i.e. genuine None
    rather than "not provided").

    ``highlight_terms`` should come from ``_cluster_highlight_terms`` — a
    TF-IDF ranking computed once per cluster — so every trace in the same
    cluster lights up the same distinctive words.
    """
    trace_id_str = str(trace.id)

    if not _prefetched and root is None:
        root = _get_root_span(trace_id_str)
    if not _prefetched and totals is None:
        totals = _get_trace_totals(trace_id_str)
    latency, prompt_tokens, completion_tokens = totals or (None, None, None)
    if not _prefetched and score is None:
        score = _get_trace_score(trace_id_str)

    model = root.model if root else None
    input_text = None
    output_text = None
    if root:
        # CHSpan stores typed-string attrs in attrs_string; input.value
        # and output.value are string-valued so they live there. Voice
        # conversation roots have neither — use the transcript instead.
        attrs = root.attrs_string or {}
        input_text = (
            _safe_str(attrs.get("input.value"))
            or _conversation_utterance(attrs)
            or _trace_input_str(trace)
        )
        output_text = (
            _safe_str(attrs.get("output.value"))
            or _conversation_utterance(attrs, last_agent=True)
            or _trace_output_str(trace)
        )
    else:
        input_text = _trace_input_str(trace)
        output_text = _trace_output_str(trace)

    turns = None
    fail_reel: list[dict] = []
    if not _prefetched and scan_result is None:
        scan_result = (
            TraceScanResult.objects.filter(trace_id=trace.id)
            .only("id", "meta", "key_moments")
            .first()
        )
    if scan_result:
        if scan_result.meta:
            turns = scan_result.meta.get("turn_count")
        fail_reel = _key_moments_to_reel(
            scan_result.key_moments,
            highlight_terms=highlight_terms or [],
            hl="error",
            trace_id=None if _prefetched else str(trace.id),
        )

    if judge is None and not _prefetched:
        judge = trace_judge(str(trace.id))
    judge_reason, judge_score = judge or (None, None)

    return RepresentativeTrace(
        id=str(trace.id),
        status="fail" if has_issues else "pass",
        timestamp=trace.created_at,
        summary=TraceSummary(
            eval_score=score,
            latency_ms=latency,
            turns=turns,
            model=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        ),
        evidence=TraceEvidence(
            input=input_text,
            output=output_text,
            fail_reel=fail_reel,
            pass_reel=pass_reel or [],
            judge_reason=judge_reason,
            score=judge_score if judge_score is not None else score,
        ),
    )


def _fetch_success_trace_pass_reel(cluster_id: str) -> list[dict]:
    """
    Build the "Working Trace" reel from the cluster's success trace.

    Success traces are matched via KNN on ClickHouse root-input embeddings —
    they are clean traces with similar inputs that may never have been scanned,
    so `TraceScanResult.key_moments` is usually empty. Fall back to the trace's
    own root input + output (+ key_moments if they exist) so the reel always
    has something useful to show.
    """
    cluster = (
        TraceErrorGroup.objects.filter(cluster_id=cluster_id)
        .select_related("success_trace")
        .first()
    )
    if not cluster or not cluster.success_trace:
        return []

    success = cluster.success_trace
    steps: list[dict] = []

    # 1. User input (from root span or Trace.input)
    root = _get_root_span(str(success.id))
    input_text = None
    output_text = None
    if root:
        # CHSpan typed-Map string-valued attrs live in attrs_string.
        attrs = root.attrs_string or {}
        input_text = attrs.get("input.value")
        output_text = attrs.get("output.value")
    input_text = input_text or _trace_input_str(success)
    output_text = output_text or _trace_output_str(success)

    if input_text:
        steps.append({"label": "USER INPUT", "text": input_text, "meta": None})

    # 2. Any key_moments the scanner captured (often empty for clean traces)
    scan_result = (
        TraceScanResult.objects.filter(trace_id=success.id).only("key_moments").first()
    )
    if scan_result:
        steps.extend(
            _key_moments_to_reel(scan_result.key_moments, trace_id=str(success.id))
        )

    # 3. Final successful output
    if output_text:
        steps.append({"label": "CORRECT OUTPUT", "text": output_text, "meta": None})

    return steps


def _fetch_representative_traces(
    cluster_id: str,
    project_id: str,
    limit: int | None = None,
    *,
    pass_reel: list[dict] | None = None,
    highlight_terms: list[str] | None = None,
) -> list[RepresentativeTrace]:
    """
    Failing traces in the cluster (Overview tab's "Traces affected" list).

    When ``limit`` is None (default), returns all traces. The frontend can
    pass a limit via query param if it wants pagination.

    Each failing trace is augmented with the cluster's success_trace key_moments
    as its ``pass_reel`` so the "Working Trace" toggle has data to display.

    TF-IDF-distinctive highlight terms are computed once for the cluster and
    reused across every rep trace — this keeps highlighting consistent
    (all traces light up the same "distinctive" words) and avoids re-fitting
    the vectorizer per trace.

    The success trace itself is NOT added to this list — it's surfaced via
    FeedDetailCore.success_trace for future comparison features.
    """
    if pass_reel is None:
        pass_reel = _fetch_success_trace_pass_reel(cluster_id)
    if highlight_terms is None:
        highlight_terms = _cluster_highlight_terms(cluster_id, project_id)

    qs = (
        ErrorClusterTraces.objects.filter(cluster__cluster_id=cluster_id)
        .select_related("trace")
        .order_by("-created_at")
    )
    ect_rows = list(
        qs[: limit * 3] if limit else qs
    )  # over-fetch for dedupe when limited

    # Session members carry no trace FK — resolve each to its session's
    # latest trace so the cards stay trace-shaped. Their judge reason and
    # score come from the session-level eval, keyed back via session_by_trace.
    member_session_ids = [
        str(e.trace_session_id)
        for e in ect_rows
        if e.trace_session_id and not e.trace_id
    ]
    rep_map = _session_rep_trace_map(member_session_ids, project_id)

    # First pass: resolve each member to a trace id (session members via their
    # CH representative trace), preserving -created_at order and deduping.
    ordered_ids: list[str] = []
    session_by_trace: dict[str, str] = {}
    seen_ids: set = set()
    for ect in ect_rows:
        if ect.trace_id:
            tid = str(ect.trace_id)
        elif ect.trace_session_id:
            tid = rep_map.get(str(ect.trace_session_id))
            if not tid:
                continue
            session_by_trace[tid] = str(ect.trace_session_id)
        else:
            continue
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        ordered_ids.append(tid)
        if limit and len(ordered_ids) >= limit:
            break

    # PG Trace where it exists, else CH-backed shim (post-cutover CH-only traces).
    traces_by_id = _resolve_member_traces(ordered_ids)
    deduped = [traces_by_id[t] for t in ordered_ids if t in traces_by_id]
    if not deduped:
        return []

    trace_ids = [str(t.id) for t in deduped]
    roots = _get_root_spans_batch(trace_ids)
    totals = _get_trace_totals_batch(trace_ids)
    scores = _get_trace_scores_batch(trace_ids)
    scans = _get_scan_results_batch(trace_ids)
    judges = _trace_judges_batch(trace_ids)
    session_judges = _session_judges_batch(list(session_by_trace.values()))

    def _judge_for(tid: str) -> tuple[str | None, float | None] | None:
        sid = session_by_trace.get(tid)
        if sid and sid in session_judges:
            return session_judges[sid]
        return judges.get(tid)

    def _score_for(tid: str) -> float | None:
        score = scores.get(tid)
        if score is None and tid in session_by_trace:
            judge = session_judges.get(session_by_trace[tid])
            score = judge[1] if judge else None
        return score

    return [
        _build_representative_trace(
            trace,
            has_issues=True,
            pass_reel=pass_reel,
            highlight_terms=highlight_terms,
            root=roots.get(str(trace.id)),
            totals=totals.get(str(trace.id)),
            score=_score_for(str(trace.id)),
            scan_result=scans.get(str(trace.id)),
            judge=_judge_for(str(trace.id)),
            _prefetched=True,
        )
        for trace in deduped
    ]


def _cluster_qs_for_access(
    cluster_id: str, project_ids: list[str] | None = None
) -> QuerySet:
    qs = TraceErrorGroup.objects.filter(cluster_id=cluster_id, deleted=False)
    if project_ids is not None:
        qs = qs.filter(project_id__in=project_ids)
    return qs


def get_overview(
    cluster_id: str,
    project_ids: list[str] | None = None,
    *,
    rep_limit: int = 20,
) -> OverviewResponse | None:
    """Full Overview tab payload for a cluster.

    ``rep_limit`` caps the representative-trace cards — full membership
    lives in the Traces tab, which paginates properly. Building a card is
    the expensive part (CH root span + totals + judges per trace), so
    shipping all members made big clusters crawl.
    """
    cluster = _cluster_qs_for_access(cluster_id, project_ids).first()
    if not cluster:
        return None
    project_id = str(cluster.project_id)

    member_total = ErrorClusterTraces.objects.filter(
        cluster__cluster_id=cluster_id
    ).aggregate(c=Count(Coalesce("trace_id", "trace_session_id"), distinct=True))["c"]

    pass_reel = _fetch_success_trace_pass_reel(cluster_id)
    highlight_terms = _cluster_highlight_terms(cluster_id, project_id)

    return OverviewResponse(
        events_over_time=_fetch_events_over_time(cluster_id),
        pattern_summary=_fetch_pattern_summary(cluster_id),
        representative_traces=_fetch_representative_traces(
            cluster_id,
            project_id,
            limit=rep_limit,
            pass_reel=pass_reel,
            highlight_terms=highlight_terms,
        ),
        representative_total=member_total or 0,
    )


# ---------------------------------------------------------------------------
# Traces tab endpoint
# ---------------------------------------------------------------------------


def _percentile(values: list[int], pct: float) -> int:
    """Simple percentile (no numpy dep). pct in [0, 100]."""
    if not values:
        return 0
    values = sorted(values)
    k = (len(values) - 1) * pct / 100
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    if lo == hi:
        return values[lo]
    return int(values[lo] + (values[hi] - values[lo]) * (k - lo))


def _fetch_traces_aggregates(cluster_id: str, project_id: str) -> TracesAggregates:
    """Compute per-cluster aggregates for the Traces tab stat bar."""
    member_trace_ids, member_session_ids = _cluster_member_ids(cluster_id)
    trace_ids = list(member_trace_ids)
    if member_session_ids:
        trace_ids.extend(
            _session_rep_trace_map(member_session_ids, project_id).values()
        )
    if not trace_ids:
        return TracesAggregates()

    total_traces = len(set(trace_ids))

    has_issues_map = dict(
        TraceScanResult.objects.filter(trace_id__in=trace_ids).values_list(
            "trace_id", "has_issues"
        )
    )
    failing = sum(1 for v in has_issues_map.values() if v)
    passing = sum(1 for v in has_issues_map.values() if not v)

    # PR3: span-only via _avg_eval_score — keeps the avg comparable to
    # pre-row_type semantics. Trace-level evals (PR4) surface elsewhere.
    # Helper uses EVAL_SCORE_EXPR for bool-aware avg (sim/voice clusters).
    # Session clusters have no span evals on their member traces — their
    # score lives on the session-target rows instead.
    avg_score = _avg_eval_score(trace_ids)
    if avg_score is None and member_session_ids:
        avg_score = _avg_session_eval_score(member_session_ids)
    avg_score = avg_score or 0.0

    # Latency percentiles: sum(latency_ms) per trace via CH batch helper.
    totals_by_trace = _get_trace_totals_batch(trace_ids)
    per_trace_latency: list[int] = [
        t[0] for t in totals_by_trace.values() if t[0] is not None
    ]

    p50 = _percentile(per_trace_latency, 50)
    p95 = _percentile(per_trace_latency, 95)

    # Average turn count from scan meta
    turn_counts: list[int] = []
    for meta in TraceScanResult.objects.filter(trace_id__in=trace_ids).values_list(
        "meta", flat=True
    ):
        if meta and meta.get("turn_count") is not None:
            try:
                turn_counts.append(int(meta["turn_count"]))
            except (TypeError, ValueError):
                continue
    avg_turns = statistics.fmean(turn_counts) if turn_counts else 0.0

    return TracesAggregates(
        total_traces=total_traces,
        failing_traces=failing,
        passing_traces=passing,
        avg_score=round(avg_score, 4),
        p50_latency=p50,
        p95_latency=p95,
        avg_turns=round(avg_turns, 2),
    )


# Very rough per-token cost (blended across providers). Callers can refine later.
_COST_PER_TOKEN = 0.0000037


def _fetch_trace_rows(
    cluster_id: str, project_id: str, limit: int, offset: int
) -> tuple[list[TracesListRow], int]:
    """Paginated list of traces in the cluster for the AG Grid."""
    base = (
        ErrorClusterTraces.objects.filter(cluster__cluster_id=cluster_id)
        .select_related("trace")
        .order_by("-created_at")
    )

    # Session members have trace_id NULL — count the member unit either way.
    total = base.aggregate(
        c=Count(Coalesce("trace_id", "trace_session_id"), distinct=True)
    )["c"]

    page_ects = list(base[offset : offset + limit * 3])  # over-fetch for dedupe
    page_session_ids = [
        str(e.trace_session_id)
        for e in page_ects
        if e.trace_session_id and not e.trace_id
    ]
    rep_map = _session_rep_trace_map(page_session_ids, project_id)

    # Resolve each member to a trace id (trace members carry trace_id; session
    # members resolve to their representative trace via CH), preserving the
    # -created_at order and deduping.
    page_trace_ids: list[str] = []
    session_by_trace: dict[str, str] = {}
    seen: set = set()
    for ect in page_ects:
        if ect.trace_id:
            tid = str(ect.trace_id)
        elif ect.trace_session_id:
            tid = rep_map.get(str(ect.trace_session_id))
            if not tid:
                continue
            session_by_trace[tid] = str(ect.trace_session_id)
        else:
            continue
        if tid in seen:
            continue
        seen.add(tid)
        page_trace_ids.append(tid)
        if len(page_trace_ids) >= limit:
            break

    # PG Trace where it exists, else CH-backed shim (post-cutover CH-only traces).
    traces_by_id = _resolve_member_traces(page_trace_ids)
    page_traces = [traces_by_id[t] for t in page_trace_ids if t in traces_by_id]
    if not page_traces:
        return [], total

    page_trace_ids = [str(t.id) for t in page_traces]
    session_judges = _session_judges_batch(list(session_by_trace.values()))

    # Pre-batch CH/PG lookups: one round-trip each.
    totals_by_trace = _get_trace_totals_batch(page_trace_ids)
    scores_by_trace = _get_trace_scores_batch(page_trace_ids)
    roots_by_trace = _get_root_spans_batch(page_trace_ids)
    scans_by_trace = {
        str(sr.trace_id): sr
        for sr in TraceScanResult.objects.filter(trace_id__in=page_trace_ids).only(
            "trace_id", "meta"
        )
    }

    rows: list[TracesListRow] = []
    for trace in page_traces:
        tid = str(trace.id)

        latency, prompt, completion = totals_by_trace.get(tid, (None, None, None))
        tokens = (prompt or 0) + (completion or 0)
        score = scores_by_trace.get(tid)
        if score is None and tid in session_by_trace:
            judge = session_judges.get(session_by_trace[tid])
            score = judge[1] if judge else None
        root = roots_by_trace.get(tid)

        input_text = None
        if root:
            # CHSpan typed-Map string attrs live in attrs_string. Voice
            # conversation roots: fall back to the first caller utterance.
            attrs = root.attrs_string or {}
            input_text = attrs.get("input.value") or _conversation_utterance(attrs)
        if not input_text:
            input_text = _trace_input_str(trace)

        turns = None
        scan_result = scans_by_trace.get(tid)
        if scan_result and scan_result.meta:
            turns = scan_result.meta.get("turn_count")

        rows.append(
            TracesListRow(
                id=tid,
                input=input_text,
                timestamp=trace.created_at,
                latency_ms=latency,
                tokens=tokens if tokens else None,
                cost=round(tokens * _COST_PER_TOKEN, 6) if tokens else None,
                score=score,
                turns=turns,
            )
        )

    return rows, total


def get_traces_tab(
    cluster_id: str,
    project_ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> TracesTabResponse | None:
    """Full Traces tab payload."""
    cluster = _cluster_qs_for_access(cluster_id, project_ids).first()
    if not cluster:
        return None

    project_id = str(cluster.project_id)
    aggregates = _fetch_traces_aggregates(cluster_id, project_id)
    rows, total = _fetch_trace_rows(cluster_id, project_id, limit=limit, offset=offset)
    return TracesTabResponse(aggregates=aggregates, traces=rows, total=total)


# ---------------------------------------------------------------------------
# Trends tab endpoint
# ---------------------------------------------------------------------------


def _member_ids_in_window(
    cluster_id: str, since: datetime, until: datetime | None = None
) -> tuple[list[str], list[str]]:
    """(trace_ids, session_ids) that joined the cluster within a window."""
    qs = ErrorClusterTraces.objects.filter(
        cluster__cluster_id=cluster_id, created_at__gte=since
    )
    if until is not None:
        qs = qs.filter(created_at__lt=until)
    rows = qs.values_list("trace_id", "trace_session_id")
    trace_ids = [str(t) for t, _ in rows if t]
    session_ids = [str(s) for t, s in rows if s and not t]
    return trace_ids, session_ids


def _users_affected_in_window(trace_ids: list[str]) -> int:
    """Distinct end_user_id across the given traces."""
    if not trace_ids:
        return 0
    with get_reader() as reader:
        users_by_trace = reader.distinct_end_users_by_trace_ids(
            [str(t) for t in trace_ids]
        )
    all_users: set = set()
    for users in users_by_trace.values():
        all_users.update(users)
    return len(all_users)


def _avg_eval_score(trace_ids: list[str]) -> float | None:
    """Average eval score over span-level evals on a list of traces.

    PR3: span-only filter. Trace-level evals (PR4) surface elsewhere.
    Uses EVAL_SCORE_EXPR so bool-only eval clusters (sim/voice) don't
    silently return 0 when output_bool is the only populated column.
    """
    if not trace_ids:
        return None
    return EvalLogger.objects.filter(
        trace_id__in=trace_ids, target_type="span"
    ).aggregate(avg=Avg(EVAL_SCORE_EXPR))["avg"]


def _project_scope_total(project_id: str, source: str, start, end=None) -> int:
    """Total project-wide events in a window, matched to the cluster's source.

    Scanner clusters: scanner ran on every trace, so denominator = scanner runs.
    Eval clusters: the scanner may not have run at all (e.g. sim/voice
    projects), so denominator = trace rows in the project window.
    """
    if source == ClusterSource.EVAL:
        qs = Trace.objects.filter(project_id=project_id, created_at__gte=start)
        if end is not None:
            qs = qs.filter(created_at__lt=end)
        return qs.count()
    qs = TraceScanResult.objects.filter(project_id=project_id, created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lt=end)
    return qs.count()


def _fetch_trend_metrics(
    cluster_id: str, project_id: str, days: int
) -> list[TrendMetric]:
    """Build the 3 KPI cards — current vs previous window."""
    cluster = TraceErrorGroup.objects.filter(cluster_id=cluster_id).first()
    cluster_source = cluster.source if cluster else ClusterSource.SCANNER

    now = timezone.now()
    window = timedelta(days=days)
    cur_start = now - window
    prev_start = cur_start - window

    cur_trace_members, cur_sessions = _member_ids_in_window(cluster_id, cur_start)
    prev_trace_members, prev_sessions = _member_ids_in_window(
        cluster_id, prev_start, cur_start
    )
    cur_traces = cur_trace_members + list(
        _session_rep_trace_map(cur_sessions, project_id).values()
    )
    prev_traces = prev_trace_members + list(
        _session_rep_trace_map(prev_sessions, project_id).values()
    )

    cur_total = _project_scope_total(project_id, cluster_source, cur_start)
    prev_total = _project_scope_total(project_id, cluster_source, prev_start, cur_start)

    cur_err_rate = (100.0 * len(cur_traces) / cur_total) if cur_total else 0.0
    prev_err_rate = (100.0 * len(prev_traces) / prev_total) if prev_total else 0.0

    # Session clusters score on session-target eval rows, not span evals.
    cur_score = (
        _avg_eval_score(cur_traces) or _avg_session_eval_score(cur_sessions) or 0.0
    )
    prev_score = (
        _avg_eval_score(prev_traces) or _avg_session_eval_score(prev_sessions) or 0.0
    )

    cur_users = _users_affected_in_window(cur_traces)
    prev_users = _users_affected_in_window(prev_traces)

    return [
        TrendMetric(
            label="Error rate",
            value=f"{round(cur_err_rate)}%",
            delta=round(cur_err_rate - prev_err_rate, 1),
            unit="%",
        ),
        TrendMetric(
            label="Avg eval score",
            value=f"{cur_score:.2f}",
            delta=round(cur_score - prev_score, 2),
        ),
        TrendMetric(
            label="Affected users",
            value=str(cur_users),
            delta=float(cur_users - prev_users),
        ),
    ]


def _fetch_events_over_time_with_passing(
    cluster_id: str, project_id: str, days: int
) -> list[EventsOverTimePoint]:
    """Daily bucket: cluster errors + project-wide passing + users."""
    since = timezone.now() - timedelta(days=days)

    err_rows = (
        ErrorClusterTraces.objects.filter(
            cluster__cluster_id=cluster_id, created_at__gte=since
        )
        .annotate(bucket=TruncDate("created_at"))
        .values("bucket")
        .annotate(errors=Count("id"))
    )
    errors_by_day: dict = {row["bucket"]: row["errors"] for row in err_rows}

    # Distinct end users affected per day (via the cluster's traces).
    # Was: ObservationSpan.filter(
    #          trace__error_cluster_traces__cluster__cluster_id=,
    #          trace__error_cluster_traces__created_at__gte=since,
    #          end_user__isnull=False
    #      ).annotate(bucket=TruncDate("trace__...__created_at"))
    #       .values("bucket").annotate(users=Count("end_user_id", distinct=True))
    # Cross-store: ECT (PG) gives us the (trace_id, bucket-day) pairs in
    # the window, then CH list_by_trace_ids gets the spans + end_user_ids,
    # then we group end_user_id by bucket in Python.
    #
    # Note: a single trace can appear in ECT multiple times across days
    # (rare — re-clustering); the PG `TruncDate(ect.created_at)` picks
    # each row's own bucket, so a span's end_user is counted on the day
    # its ECT row was created. We model that 1:1 by carrying the per-ECT
    # bucket dates rather than collapsing to one bucket per trace_id.
    users_by_day: dict[object, set] = {}
    ect_rows_in_window = list(
        ErrorClusterTraces.objects.filter(
            cluster__cluster_id=cluster_id,
            created_at__gte=since,
        ).values_list("trace_id", "trace_session_id", "created_at")
    )
    if ect_rows_in_window:
        # trace_id → list of bucket-dates (one ECT row may pair a trace
        # with multiple buckets; that's preserved 1:1 in the legacy join).
        # Use timezone.localtime to match Django's TruncDate semantics
        # (which converts to the active timezone before truncating —
        # otherwise UTC-side .date() can disagree with the err_rows /
        # pass_rows keys, breaking the day-bucket union below).
        # Session members resolve to their session's rep trace first.
        window_session_ids = [str(s) for t, s, _ in ect_rows_in_window if s and not t]
        rep_map = _session_rep_trace_map(window_session_ids, project_id)
        buckets_by_trace: dict[str, list] = {}
        for tid, sid, ect_created in ect_rows_in_window:
            if not ect_created:
                continue
            effective_tid = str(tid) if tid else rep_map.get(str(sid or ""))
            if not effective_tid:
                continue
            buckets_by_trace.setdefault(effective_tid, []).append(
                timezone.localtime(ect_created).date()
            )

        with get_reader() as reader:
            users_by_trace = reader.distinct_end_users_by_trace_ids(
                list(buckets_by_trace.keys())
            )

        for tid, user_ids in users_by_trace.items():
            buckets = buckets_by_trace.get(str(tid))
            if not buckets:
                continue
            for uid in user_ids:
                for bucket in buckets:
                    users_by_day.setdefault(bucket, set()).add(uid)

    users_by_day = {bucket: len(users) for bucket, users in users_by_day.items()}

    # Project-wide passing scans per day (has_issues=False) — context for
    # the dual-axis chart
    pass_rows = (
        TraceScanResult.objects.filter(
            project_id=project_id,
            has_issues=False,
            created_at__gte=since,
        )
        .annotate(bucket=TruncDate("created_at"))
        .values("bucket")
        .annotate(passing=Count("id"))
    )
    passing_by_day: dict = {row["bucket"]: row["passing"] for row in pass_rows}

    # Union of all days that have any data
    all_days = sorted(set(errors_by_day) | set(users_by_day) | set(passing_by_day))
    return [
        EventsOverTimePoint(
            date=d.isoformat() if d else "",
            errors=errors_by_day.get(d, 0),
            passing=passing_by_day.get(d, 0),
            users=users_by_day.get(d, 0),
        )
        for d in all_days
    ]


def _fetch_score_trends(
    cluster_id: str, days: int, max_labels: int = 4
) -> list[ScoreTrend]:
    """Per-CustomEvalConfig.name score sparkline over the last ``days``.

    Splits the window in half: first half = prev, second half = current.
    Daily sparkline is average ``output_float`` per day over the full window.
    """
    member_trace_ids, member_session_ids = _cluster_member_ids(cluster_id)
    if not member_trace_ids and not member_session_ids:
        return []

    now = timezone.now()
    since = now - timedelta(days=days)
    midpoint = now - timedelta(days=days / 2)

    # Session members' eval rows anchor to trace_session, not trace.
    member_q = Q(trace_id__in=member_trace_ids)
    if member_session_ids:
        member_q |= Q(trace_session_id__in=member_session_ids)

    rows = list(
        EvalLogger.objects.filter(
            member_q,
            created_at__gte=since,
            custom_eval_config__isnull=False,
        )
        .annotate(day=TruncDate("created_at"), score=EVAL_SCORE_EXPR)
        .filter(score__isnull=False)
        .values("day", "custom_eval_config__name", "score", "created_at")
    )
    if not rows:
        return []

    # Group: {label: {day: [scores...], "_prev": [...], "_cur": [...]}}.
    # Score coercion (float vs bool) is done in SQL via EVAL_SCORE_EXPR so the
    # sparkline tracks pass-rate for sim/voice projects too.
    groups: dict = {}
    for r in rows:
        score = r["score"]
        label = r["custom_eval_config__name"] or "Unnamed eval"
        g = groups.setdefault(label, {"days": {}, "prev": [], "cur": [], "count": 0})
        g["days"].setdefault(r["day"], []).append(score)
        if r["created_at"] >= midpoint:
            g["cur"].append(score)
        else:
            g["prev"].append(score)
        g["count"] += 1

    # Keep top N labels by sample count so we don't overwhelm the UI
    top_labels = sorted(groups.items(), key=lambda kv: kv[1]["count"], reverse=True)[
        :max_labels
    ]

    result: list[ScoreTrend] = []
    for label, g in top_labels:
        daily = [
            (day, statistics.fmean(scores)) for day, scores in sorted(g["days"].items())
        ]
        sparkline = [round(v, 4) for _, v in daily]
        cur_avg = (
            statistics.fmean(g["cur"])
            if g["cur"]
            else (sparkline[-1] if sparkline else 0.0)
        )
        prev_avg = (
            statistics.fmean(g["prev"])
            if g["prev"]
            else (sparkline[0] if sparkline else 0.0)
        )
        result.append(
            ScoreTrend(
                label=label,
                current=round(cur_avg, 4),
                prev=round(prev_avg, 4),
                sparkline=sparkline,
            )
        )
    return result


def _fetch_activity_heatmap(cluster_id: str, days: int = 30) -> list[list[HeatmapCell]]:
    """Build a 7×24 grid (day 0=Sun … 6=Sat) of cluster-error counts."""
    since = timezone.now() - timedelta(days=days)
    rows = ErrorClusterTraces.objects.filter(
        cluster__cluster_id=cluster_id, created_at__gte=since
    ).values_list("created_at", flat=True)

    counts: dict = {}
    for ts in rows:
        if ts is None:
            continue
        # Python: Monday=0..Sunday=6 → remap to Sun=0..Sat=6
        day = (ts.weekday() + 1) % 7
        hour = ts.hour
        counts[(day, hour)] = counts.get((day, hour), 0) + 1

    return [
        [HeatmapCell(day=d, hour=h, value=counts.get((d, h), 0)) for h in range(24)]
        for d in range(7)
    ]


def get_trends_tab(
    cluster_id: str, project_ids: list[str] | None = None, days: int = 14
) -> TrendsTabResponse | None:
    """Full Trends tab payload."""
    cluster = _cluster_qs_for_access(cluster_id, project_ids).first()
    if not cluster:
        return None
    project_id = str(cluster.project_id)

    return TrendsTabResponse(
        metrics=_fetch_trend_metrics(cluster_id, project_id, days),
        events_over_time=_fetch_events_over_time_with_passing(
            cluster_id, project_id, days
        ),
        score_trends=_fetch_score_trends(cluster_id, days),
        activity_heatmap=_fetch_activity_heatmap(cluster_id, days=max(days, 30)),
    )


# ---------------------------------------------------------------------------
# Sidebar endpoint
# ---------------------------------------------------------------------------


def _fetch_sidebar_ai_metadata(
    cluster: TraceErrorGroup,
    trace_ids: list[str],
    selected_trace_id: str | None = None,
    session_ids: list[str] | None = None,
    selected_session_id: str | None = None,
) -> SidebarAIMetadata:
    """Model / version / project / eval score / trace id for the sidebar.

    When ``selected_trace_id`` is provided, model/version/evalScore/traceId
    are computed from that specific trace — this keeps the sidebar in sync
    with the "Traces affected" list selection in the Overview tab. When
    absent, falls back to the cluster's latest trace and cluster-wide avg
    eval score.
    """
    project = cluster.project.name if cluster.project_id else None

    # Trace to inspect: caller's pick, or cluster's latest as fallback
    # (session-aware — resolves session members to their rep trace).
    focus_trace_id: str | None = selected_trace_id
    if focus_trace_id is None:
        focus_trace_id = _fetch_latest_trace_id_batch([cluster.cluster_id]).get(
            cluster.cluster_id
        )

    model: str | None = None
    model_version: str | None = None
    if focus_trace_id:
        with get_reader() as reader:
            llm_span = reader.first_span_by_type(focus_trace_id, "llm")
        if llm_span:
            model = llm_span.model or None
            # CHSpan typed-Map string attrs live in attrs_string.
            attrs = llm_span.attrs_string or {}
            model_version = (
                attrs.get("gen_ai.request.model_version")
                or attrs.get("llm.model_version")
                or None
            )

    # When a trace is explicitly selected, report THAT trace's score.
    # Otherwise show the cluster-wide average (current no-selection default).
    # Session clusters score on session-target eval rows, not span evals.
    if selected_trace_id:
        eval_score = _avg_eval_score([selected_trace_id])
        if eval_score is None and selected_session_id:
            eval_score = _avg_session_eval_score([selected_session_id])
    else:
        eval_score = _avg_eval_score(trace_ids)
        if eval_score is None and session_ids:
            eval_score = _avg_session_eval_score(session_ids)
    if eval_score is not None:
        eval_score = round(eval_score, 4)

    return SidebarAIMetadata(
        model=model,
        model_version=model_version,
        project=project,
        eval_score=eval_score,
        trace_id=focus_trace_id,
    )


def _fetch_sidebar_evaluations(
    trace_ids: list[str],
    selected_trace_id: str | None = None,
    session_ids: list[str] | None = None,
    selected_session_id: str | None = None,
) -> list[EvaluationResult]:
    """Roll up EvalLogger rows to one row per CustomEvalConfig.name.

    Type is inferred from the output shape — the spec's

    - ``output_float`` populated → ``llm_judge`` (renders as score bar)
    - ``output_bool``/``output_str`` only → ``deterministic`` (renders as
      verdict chip)

    When both are present, float wins so the score bar is always shown.

    When ``selected_trace_id`` is provided, only that trace's eval rows are
    considered — otherwise the rollup spans every trace in the cluster.
    Session-target eval rows anchor to trace_session, so ``session_ids``
    (and ``selected_session_id`` when the selected trace represents a
    session member) widen the filter for session clusters.
    """
    if selected_trace_id:
        effective_trace_ids = [selected_trace_id]
        effective_session_ids = [selected_session_id] if selected_session_id else []
    else:
        effective_trace_ids = trace_ids
        effective_session_ids = session_ids or []
    if not effective_trace_ids and not effective_session_ids:
        return []

    member_q = Q(trace_id__in=effective_trace_ids)
    if effective_session_ids:
        member_q |= Q(trace_session_id__in=effective_session_ids)

    rows = list(
        EvalLogger.objects.filter(member_q, custom_eval_config__isnull=False).values(
            "custom_eval_config__name",
            "output_bool",
            "output_float",
            "output_str",
        )
    )
    if not rows:
        return []

    groups: dict = {}
    for r in rows:
        label = r["custom_eval_config__name"] or "Unnamed eval"
        g = groups.setdefault(
            label,
            {"bools": [], "floats": [], "strs": []},
        )
        if r["output_bool"] is not None:
            g["bools"].append(r["output_bool"])
        if r["output_float"] is not None:
            g["floats"].append(r["output_float"])
        if r["output_str"]:
            g["strs"].append(r["output_str"])

    result: list[EvaluationResult] = []
    for label, g in groups.items():
        has_floats = bool(g["floats"])
        eval_type = "llm_judge" if has_floats else "deterministic"

        # Determine result
        if has_floats:
            avg = statistics.fmean(g["floats"])
            result_str = "passed" if avg >= 0.5 else "failed"
        elif g["bools"]:
            passed = sum(1 for b in g["bools"] if b) >= (len(g["bools"]) + 1) // 2
            result_str = "passed" if passed else "failed"
        else:
            result_str = "failed"

        score: float | None = (
            round(statistics.fmean(g["floats"]), 4) if has_floats else None
        )
        value: str | None = None
        if not has_floats and g["strs"]:
            value = Counter(g["strs"]).most_common(1)[0][0]
        elif not has_floats and g["bools"]:
            # For pure pass/fail evals, surface the verdict as the value so
            # the chip has something meaningful to render.
            value = "Passed" if result_str == "passed" else "Failed"

        result.append(
            EvaluationResult(
                label=label,
                type=eval_type,
                result=result_str,
                score=score,
                value=value,
            )
        )
    return result


def _fetch_co_occurring_issues(
    cluster_id: str, project_id: str, limit: int = 5
) -> list[CoOccurringIssue]:
    """Jaccard-rank other clusters in the same project that share traces.

    Pulls (cluster_id, trace_id) pairs for every scanner cluster in the project
    and computes Jaccard in Python. Cheap — projects have O(100) clusters max.
    """
    this_traces_set = set(_trace_ids_for_cluster(cluster_id, project_id))
    if not this_traces_set:
        return []

    ect_rows = ErrorClusterTraces.objects.filter(
        cluster__project_id=project_id
    ).values_list("cluster__cluster_id", "trace_id")

    other_traces: dict = {}
    for cid, tid in ect_rows:
        if not cid or not tid or cid == cluster_id:
            continue
        other_traces.setdefault(cid, set()).add(str(tid))

    scored: list[tuple[str, int, float]] = []
    for other_cid, traces in other_traces.items():
        shared = this_traces_set & traces
        if not shared:
            continue
        union = this_traces_set | traces
        jaccard = len(shared) / len(union) if union else 0.0
        scored.append((other_cid, len(shared), jaccard))

    scored.sort(key=lambda t: t[2], reverse=True)
    top = scored[:limit]
    if not top:
        return []

    # Hydrate with cluster metadata
    cluster_rows = TraceErrorGroup.objects.filter(
        cluster_id__in=[cid for cid, _, _ in top], deleted=False
    ).only("cluster_id", "title", "issue_category", "priority")
    cluster_map = {c.cluster_id: c for c in cluster_rows}

    result: list[CoOccurringIssue] = []
    for cid, count, jaccard in top:
        c = cluster_map.get(cid)
        if not c:
            continue
        result.append(
            CoOccurringIssue(
                id=cid,
                title=c.title or c.issue_category or cid,
                type=c.issue_category or c.issue_group or "",
                co_occurrence=round(jaccard, 3),
                count=count,
                severity=priority_to_severity(c.priority),
            )
        )
    return result


def get_sidebar(
    cluster_id: str,
    project_ids: list[str] | None = None,
    trace_id: str | None = None,
) -> FeedSidebar | None:
    """Full sidebar payload for a cluster.

    When ``trace_id`` is provided, the trace-level sections (AI Metadata +
    Evaluations) are computed for that specific trace so the sidebar stays
    in sync with the Overview tab's trace selection. Cluster-level sections
    (Timeline, Co-occurring Issues) ignore ``trace_id``.

    If ``trace_id`` is given but doesn't belong to this cluster, it's
    silently ignored and the sidebar falls back to the default "latest
    trace" view.
    """
    cluster = (
        _cluster_qs_for_access(cluster_id, project_ids)
        .select_related("project")
        .first()
    )
    if not cluster:
        return None

    project_id = str(cluster.project_id)
    member_trace_ids, member_session_ids = _cluster_member_ids(cluster_id)
    rep_map = _session_rep_trace_map(member_session_ids, project_id)
    session_by_trace = {tid: sid for sid, tid in rep_map.items()}
    trace_ids = member_trace_ids + list(rep_map.values())

    # Guardrail: only honor trace_id if it actually belongs to this cluster.
    selected_trace_id: str | None = None
    if trace_id and str(trace_id) in trace_ids:
        selected_trace_id = str(trace_id)
    # For session members, the selected trace stands in for its session —
    # eval rows anchor there.
    selected_session_id = (
        session_by_trace.get(selected_trace_id) if selected_trace_id else None
    )

    # Age since first_seen — frontend renders as integer days
    age_days: int | None = None
    if cluster.first_seen:
        delta = timezone.now() - cluster.first_seen
        age_days = max(delta.days, 0)

    timeline = SidebarTimeline(
        first_seen=cluster.first_seen,
        last_seen=cluster.last_seen,
        age_days=age_days,
    )
    ai_metadata = _fetch_sidebar_ai_metadata(
        cluster,
        trace_ids,
        selected_trace_id=selected_trace_id,
        session_ids=member_session_ids,
        selected_session_id=selected_session_id,
    )
    evaluations = _fetch_sidebar_evaluations(
        trace_ids,
        selected_trace_id=selected_trace_id,
        session_ids=member_session_ids,
        selected_session_id=selected_session_id,
    )
    co_occurring = _fetch_co_occurring_issues(cluster_id, project_id)

    return FeedSidebar(
        timeline=timeline,
        ai_metadata=ai_metadata,
        evaluations=evaluations,
        co_occurring_issues=co_occurring,
    )


# ---------------------------------------------------------------------------
# Deep analysis endpoints
# ---------------------------------------------------------------------------


_URGENCY_TO_PRIORITY = {
    "IMMEDIATE": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}


def _urgency_to_priority(urgency: str | None) -> str:
    """Map TraceErrorDetail.urgency_to_fix (uppercase enum) to the frontend's
    lowercase priority bucket. Falls back to ``medium`` for unknown values."""
    return _URGENCY_TO_PRIORITY.get((urgency or "").upper(), "medium")


def _recommendation_title_from_category(category: str | None) -> str:
    """The error category path looks like "A > B > C"; the leaf is the most
    specific label and reads best as a card title."""
    if not category:
        return "Recommendation"
    parts = [p.strip() for p in category.split(">") if p.strip()]
    return parts[-1] if parts else category.strip()


# Cap on probable root causes surfaced per cluster — keeps the card
# readable. See the NOTE in _build_root_causes for the real upstream fix.
_MAX_ROOT_CAUSES = 4


def _build_root_causes(details: list[TraceErrorDetail]) -> list[RootCause]:
    """Flatten ``TraceErrorDetail.root_causes`` across every detail for a
    trace, dedupe, and produce a ranked ``RootCause`` list.

    Each ``TraceErrorDetail.root_causes`` is a list of free-form strings
    produced by the analysis agent; individual items are full sentences.
    Title = first clause before the first period/comma; description = the
    full string (so the card renders a natural headline + body).
    """
    # NOTE: this is a display-layer mitigation, not the real fix. Two
    # upstream problems make this list explode and both should be fixed
    # at the source, not here:
    #   1. The analysis agent over-generates root causes per trace instead
    #      of committing to the few that matter — needs a hard cap + ranking
    #      instruction in the agent prompt.
    #   2. Dedup below is exact-text only, so the same cause phrased
    #      slightly differently across traces survives as N near-duplicates
    #      — needs semantic dedup (same gap as cluster fragmentation).
    # Until those land we frequency-rank (most recurrent cause first) and
    # cap the list so the card stays readable.
    counts: dict = {}
    for d in details:
        for raw in d.root_causes or []:
            if not raw:
                continue
            text = str(raw).strip()
            if not text:
                continue
            key = text.lower()
            entry = counts.get(key)
            if entry is None:
                counts[key] = {"text": text, "count": 1}
            else:
                entry["count"] += 1

    # Most-recurrent first; stable on first-seen order for ties.
    ranked = sorted(counts.values(), key=lambda e: -e["count"])

    result: list[RootCause] = []
    for rank, entry in enumerate(ranked[:_MAX_ROOT_CAUSES], start=1):
        text = entry["text"]
        # Headline: first clause before . or ,
        split_idx = min(
            (i for i in (text.find("."), text.find(",")) if i > 0),
            default=-1,
        )
        title = text[:split_idx].strip() if split_idx > 0 else text
        if len(title) > 120:
            title = title[:117].rstrip() + "..."
        result.append(RootCause(rank=rank, title=title, description=text))
    return result


def _build_recommendations(
    details: list[TraceErrorDetail], root_causes: list[RootCause]
) -> list[Recommendation]:
    """Produce one ``Recommendation`` card per ``TraceErrorDetail`` row.

    ``root_cause_link`` points into ``root_causes`` by rank — we match
    each detail's primary root cause against the deduped global list so
    the frontend can highlight the linkage.
    """
    # Index for quick lookup: normalized text → rank
    by_text: dict = {rc.description.lower(): rc.rank for rc in root_causes}

    result: list[Recommendation] = []
    for d in details:
        linked_rank: int | None = None
        for raw in d.root_causes or []:
            if not raw:
                continue
            linked_rank = by_text.get(str(raw).strip().lower())
            if linked_rank:
                break

        result.append(
            Recommendation(
                id=d.error_id,
                title=_recommendation_title_from_category(d.category),
                description=(d.recommendation or "").strip() or (d.description or ""),
                priority=_urgency_to_priority(d.urgency_to_fix),
                root_cause_link=linked_rank,
                immediate_fix=(d.immediate_fix or "").strip() or None,
                # ``llm_analysis`` is the agent's reasoning blob — useful as
                # "insights" context under the expandable card.
                insights=(d.llm_analysis or "").strip() or None,
                evidence=[str(s) for s in (d.evidence_snippets or []) if s],
            )
        )
    return result


_TRACE_STATUS_TO_FEED = {
    TraceErrorAnalysisStatus.PENDING: "idle",
    TraceErrorAnalysisStatus.SKIPPED: "idle",
    TraceErrorAnalysisStatus.PROCESSING: "running",
    TraceErrorAnalysisStatus.COMPLETED: "done",
    TraceErrorAnalysisStatus.FAILED: "failed",
}


def _deep_analysis_status(trace: Trace, has_analysis: bool) -> str:
    """Map ``Trace.error_analysis_status`` to the frontend state machine.

    One nuance: a trace can be in COMPLETED state but have zero
    ``TraceErrorDetail`` rows (the analysis ran, found nothing). We still
    return ``done`` — the frontend decides what to render when the lists
    are empty. Conversely, if status is COMPLETED but the
    ``TraceErrorAnalysis`` row got deleted (e.g. cascade from a trace
    delete), we treat that as ``idle`` so the button re-enables.
    """
    status = _TRACE_STATUS_TO_FEED.get(trace.error_analysis_status, "idle")
    if status == "done" and not has_analysis:
        return "idle"
    return status


def _cluster_has_trace(
    cluster_id: str, trace_id: str, project_ids: list[str] | None = None
) -> bool:
    """Guardrail: the POST / GET endpoints only act on traces that are
    actually linked to the given cluster. Prevents a user from analyzing
    an arbitrary trace by hitting the wrong URL."""
    qs = ErrorClusterTraces.objects.filter(
        cluster__cluster_id=cluster_id, trace_id=trace_id
    )
    if project_ids is not None:
        qs = qs.filter(cluster__project_id__in=project_ids)
    return qs.exists()


def get_deep_analysis(
    cluster_id: str, trace_id: str, project_ids: list[str] | None = None
) -> DeepAnalysisResponse | None:
    """Read the cached deep analysis for ``trace_id`` within ``cluster_id``.

    Returns ``None`` when the cluster doesn't exist or the trace isn't
    part of it. Otherwise always returns a response — the ``status``
    field tells the frontend whether data is available.
    """
    if not _cluster_qs_for_access(cluster_id, project_ids).exists():
        return None

    if not _cluster_has_trace(cluster_id, trace_id, project_ids):
        return None

    trace = Trace.objects.filter(id=trace_id).only("error_analysis_status").first()
    if not trace:
        return None

    analysis = (
        TraceErrorAnalysis.objects.filter(trace_id=trace_id)
        .order_by("-analysis_date")
        .first()
    )
    status = _deep_analysis_status(trace, has_analysis=bool(analysis))

    if not analysis or status != "done":
        return DeepAnalysisResponse(
            status=status,
            trace_id=str(trace_id),
        )

    details = list(
        TraceErrorDetail.objects.filter(analysis=analysis).order_by("error_id")
    )
    root_causes = _build_root_causes(details)
    recommendations = _build_recommendations(details, root_causes)

    # Show the first IMMEDIATE-urgency immediate_fix as the headline fix —
    # if none, fall back to the first non-empty immediate_fix we find.
    immediate_fix: str | None = None
    for d in details:
        if (d.urgency_to_fix or "").upper() == "IMMEDIATE" and d.immediate_fix:
            immediate_fix = d.immediate_fix.strip()
            break
    if immediate_fix is None:
        for d in details:
            if d.immediate_fix:
                immediate_fix = d.immediate_fix.strip()
                break

    return DeepAnalysisResponse(
        status="done",
        trace_id=str(trace_id),
        root_causes=root_causes,
        recommendations=recommendations,
        immediate_fix=immediate_fix,
    )


def dispatch_deep_analysis(
    cluster_id: str,
    trace_id: str,
    project_ids: list[str] | None = None,
    force: bool = False,
) -> DeepAnalysisDispatchResponse | None:
    """POST handler for running deep analysis on a single trace.

    Semantics:

    - If the cluster or trace doesn't exist (or isn't linked), return
      ``None`` so the view returns 404.
    - If cached results already exist and ``force=False``, return a
      ``done`` response without dispatching — the frontend will just
      scroll to the existing panel.
    - If the trace is already in PROCESSING state, return ``running``
      without re-dispatching (idempotent double-click protection).
    - Otherwise: set ``Trace.error_analysis_status=PROCESSING``
      synchronously and dispatch the Temporal activity. The view returns
      202 ``running``.
    """
    # Import here to avoid pulling the Temporal runtime into module-load
    # time for everything that imports `feed.py`. Task modules can have
    # slow transitive imports (agentic_eval, CH vector clients, etc).
    from tracer.tasks import run_deep_analysis_on_demand

    if not _cluster_qs_for_access(cluster_id, project_ids).exists():
        return None

    if not _cluster_has_trace(cluster_id, trace_id, project_ids):
        return None

    trace = Trace.objects.filter(id=trace_id).only("error_analysis_status").first()
    if not trace:
        return None

    has_analysis = TraceErrorAnalysis.objects.filter(trace_id=trace_id).exists()

    # Idempotent click: cached result exists and user didn't ask for a
    # re-run → no-op, frontend reads existing results from GET.
    if has_analysis and not force:
        return DeepAnalysisDispatchResponse(status="done", trace_id=str(trace_id))

    # Already running → don't double-dispatch.
    if trace.error_analysis_status == TraceErrorAnalysisStatus.PROCESSING:
        return DeepAnalysisDispatchResponse(status="running", trace_id=str(trace_id))

    # Flip status synchronously so the first frontend poll sees the
    # running state without racing the Temporal worker.
    Trace.objects.filter(id=trace_id).update(
        error_analysis_status=TraceErrorAnalysisStatus.PROCESSING
    )

    run_deep_analysis_on_demand.delay(str(trace_id), bool(force))

    return DeepAnalysisDispatchResponse(status="running", trace_id=str(trace_id))
