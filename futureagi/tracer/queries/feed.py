"""
DB helpers for the Error Feed API.

Returns typed dataclasses from tracer.types.feed_types — no raw dicts.
Pure data-access layer: no HTTP, no business logic. Service layer composes.
"""

import re
import statistics
from collections import Counter
from datetime import datetime, timedelta
from datetime import timezone as dt_tz
from typing import List, Optional, Tuple

import structlog
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Avg, Count, Q, QuerySet, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone
from sklearn.feature_extraction.text import TfidfVectorizer

from tracer.models.observation_span import EvalLogger, ObservationSpan
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


# Priority (backend) ↔ severity (frontend) mapping
_PRIORITY_TO_SEVERITY = {
    "urgent": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}
_SEVERITY_TO_PRIORITY = {v: k for k, v in _PRIORITY_TO_SEVERITY.items()}


def priority_to_severity(priority: Optional[str]) -> str:
    return _PRIORITY_TO_SEVERITY.get(priority or "", priority or "medium")


def severity_to_priority(severity: Optional[str]) -> str:
    return _SEVERITY_TO_PRIORITY.get(severity or "", severity or "medium")


# ---------------------------------------------------------------------------
# Filters (applied to the base queryset)
# ---------------------------------------------------------------------------


def _base_qs(project_ids: List[str]) -> QuerySet:
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
    search: Optional[str] = None,
    status: Optional[str] = None,
    fix_layer: Optional[str] = None,
    source: Optional[str] = None,
    issue_group: Optional[str] = None,
    time_range_days: Optional[int] = None,
) -> QuerySet:
    if search:
        qs = qs.filter(
            Q(title__icontains=search)
            | Q(issue_group__icontains=search)
            | Q(issue_category__icontains=search)
        )
    if status:
        qs = qs.filter(status=status)
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


def _fetch_trends_batch(cluster_ids: List[str], days: int = 14) -> dict:
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
                bucket = datetime.combine(bucket, datetime.min.time(), tzinfo=dt_tz.utc)
            result[cid].append(
                TrendPoint(timestamp=bucket, value=row["value"], users=0)
            )
    return result


def _fetch_users_affected_batch(cluster_ids: List[str]) -> dict:
    """
    Return {cluster_id: distinct_end_user_count}.

    Goes ErrorClusterTraces → trace → ObservationSpan.end_user.
    """
    if not cluster_ids:
        return {}

    rows = (
        ObservationSpan.objects.filter(
            trace__error_cluster_traces__cluster__cluster_id__in=cluster_ids,
            end_user__isnull=False,
        )
        .values("trace__error_cluster_traces__cluster__cluster_id")
        .annotate(users=Count("end_user_id", distinct=True))
    )

    return {
        r["trace__error_cluster_traces__cluster__cluster_id"]: r["users"] for r in rows
    }


def _fetch_sessions_batch(cluster_ids: List[str]) -> dict:
    """Return {cluster_id: distinct_session_count}."""
    if not cluster_ids:
        return {}

    rows = (
        ErrorClusterTraces.objects.filter(
            cluster__cluster_id__in=cluster_ids,
            trace__session__isnull=False,
        )
        .values("cluster__cluster_id")
        .annotate(sessions=Count("trace__session_id", distinct=True))
    )
    return {r["cluster__cluster_id"]: r["sessions"] for r in rows}


def _fetch_latest_trace_id_batch(cluster_ids: List[str]) -> dict:
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

    return {
        str(r["cluster__cluster_id"]): str(r["trace_id"])
        for r in rows
        if r["trace_id"]
    }


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------


def _row_from_cluster(
    cluster: TraceErrorGroup,
    *,
    trends: List[TrendPoint],
    users_affected: int,
    sessions: int,
    latest_trace_id: Optional[str],
) -> FeedListRow:
    """Build a FeedListRow from a TraceErrorGroup + pre-fetched batch data."""
    assignees: List[str] = []
    if cluster.assignee:
        assignees.append(cluster.assignee.email or str(cluster.assignee.id))

    return FeedListRow(
        cluster_id=cluster.cluster_id,
        source=cluster.source or "scanner",
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
    project_ids: List[str],
    *,
    search: Optional[str] = None,
    status: Optional[str] = None,
    fix_layer: Optional[str] = None,
    source: Optional[str] = None,
    issue_group: Optional[str] = None,
    time_range_days: Optional[int] = None,
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
        fix_layer=fix_layer,
        source=source,
        issue_group=issue_group,
        time_range_days=time_range_days,
    )

    # Sort
    valid_sorts = {"last_seen", "first_seen", "error_count", "unique_traces"}
    if sort_by not in valid_sorts:
        sort_by = "last_seen"
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

    rows = [
        _row_from_cluster(
            c,
            trends=trends_map.get(c.cluster_id, []),
            users_affected=users_map.get(c.cluster_id, 0),
            sessions=sessions_map.get(c.cluster_id, 0),
            latest_trace_id=latest_trace_map.get(c.cluster_id),
        )
        for c in clusters
    ]

    return FeedListResponse(data=rows, total=total, limit=limit, offset=offset)


def get_stats(
    project_ids: List[str], *, time_range_days: Optional[int] = None
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
    cluster_id: str, project_ids: Optional[List[str]] = None
) -> Optional[FeedDetailCore]:
    """
    Full detail core for a single cluster.

    If project_ids is None, finds by cluster_id alone (unique in practice since
    cluster_id is hashed from project+content).
    """
    qs = TraceErrorGroup.objects.filter(deleted=False).select_related(
        "project", "assignee", "success_trace"
    )
    if project_ids:
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
    )

    success_trace: Optional[TracePreview] = None
    if cluster.success_trace_id:
        success_trace = TracePreview(
            trace_id=str(cluster.success_trace_id),
            input=_trace_input_str(cluster.success_trace),
            output=_trace_output_str(cluster.success_trace),
        )

    representative_trace: Optional[TracePreview] = None
    if row.trace_id:
        rep = (
            ErrorClusterTraces.objects.filter(
                cluster__cluster_id=cluster.cluster_id,
                trace_id=row.trace_id,
            )
            .select_related("trace")
            .first()
        )
        if rep and rep.trace:
            representative_trace = TracePreview(
                trace_id=str(rep.trace.id),
                input=_trace_input_str(rep.trace),
                output=_trace_output_str(rep.trace),
            )

    return FeedDetailCore(
        row=row,
        description=cluster.combined_description,
        success_trace=success_trace,
        representative_trace=representative_trace,
    )


def update_cluster(
    cluster_id: str,
    project_ids: Optional[List[str]],
    payload: FeedUpdatePayload,
) -> Optional[FeedDetailCore]:
    """Update status/severity/assignee on a cluster, return fresh detail."""
    qs = TraceErrorGroup.objects.filter(cluster_id=cluster_id, deleted=False)
    if project_ids:
        qs = qs.filter(project_id__in=project_ids)
    cluster = qs.first()
    if not cluster:
        return None

    update_fields: List[str] = []

    if payload.status is not None:
        cluster.status = payload.status
        update_fields.append("status")

    if payload.severity is not None:
        cluster.priority = severity_to_priority(payload.severity)
        update_fields.append("priority")

    if payload.assignee is not None:
        user = User.objects.filter(email=payload.assignee).first()
        cluster.assignee = user
        update_fields.append("assignee")

    if update_fields:
        update_fields.append("updated_at")
        cluster.save(update_fields=update_fields)

    return get_cluster_detail(cluster_id, project_ids)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _safe_str(val) -> Optional[str]:
    """Ensure a value is a plain string (not a dict/list that would serialize as [object Object])."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, (dict, list)):
        import json

        return json.dumps(val, default=str)
    return str(val)


def _trace_input_str(trace) -> Optional[str]:
    if not trace or trace.input is None:
        return None
    return _safe_str(trace.input)


def _trace_output_str(trace) -> Optional[str]:
    if not trace or trace.output is None:
        return None
    return _safe_str(trace.output)


def _trace_ids_for_cluster(cluster_id: str) -> List[str]:
    """Return all trace_ids linked to a cluster via ErrorClusterTraces."""
    return [
        str(tid)
        for tid in ErrorClusterTraces.objects.filter(
            cluster__cluster_id=cluster_id
        ).values_list("trace_id", flat=True)
    ]


# ---------------------------------------------------------------------------
# Overview tab endpoint
# ---------------------------------------------------------------------------


def _fetch_events_over_time(
    cluster_id: str, days: int = 14
) -> List[EventsOverTimePoint]:
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


_FLOW_ANOMALY_RE = re.compile(
    r"\b(skip|skipped|missing|out of order|before|after|fail at|stuck|loop|"
    r"never|did not|didn't)\b",
    re.IGNORECASE,
)

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
    corpus: List[str],
    top_k: int,
    ngram_range: Tuple[int, int] = (1, 1),
) -> List[Tuple[str, float]]:
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
        for t, s in zip(terms, row)
        if s > 0 and str(t) not in _SCANNER_FILLER_STOPWORDS
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]


def _project_cluster_briefs_corpus(
    project_id: str,
) -> Tuple[List[str], List[str]]:
    """One ``(cluster_ids, docs)`` pair per project — each doc is a cluster's
    concatenated scanner issue briefs. Clusters without briefs are skipped.
    Single query, grouped in Python.
    """
    rows = TraceScanIssue.objects.filter(
        scan_result__project_id=project_id,
        cluster__source=ClusterSource.SCANNER,
    ).values_list("cluster__cluster_id", "brief")

    by_cluster: dict[str, List[str]] = {}
    for cid, brief in rows:
        if not cid or not brief:
            continue
        by_cluster.setdefault(cid, []).append(brief)

    cluster_ids = list(by_cluster.keys())
    corpus = [" ".join(by_cluster[cid]) for cid in cluster_ids]
    return cluster_ids, corpus


def _project_cluster_inputs_corpus(
    project_id: str,
) -> Tuple[List[str], List[str], dict]:
    """One doc per cluster = all its traces' root inputs concatenated.

    Returns ``(cluster_ids, docs, cluster_to_trace_inputs)`` where the last
    dict maps ``cluster_id → {trace_id: input_text}`` so callers can count
    how many traces contain a particular term without another round-trip.
    """
    ect_rows = ErrorClusterTraces.objects.filter(
        cluster__project_id=project_id,
        cluster__source=ClusterSource.SCANNER,
    ).values_list("cluster__cluster_id", "trace_id")
    cluster_to_traces: dict[str, List[str]] = {}
    all_trace_ids: set = set()
    for cid, tid in ect_rows:
        if not cid or not tid:
            continue
        tid_str = str(tid)
        cluster_to_traces.setdefault(cid, []).append(tid_str)
        all_trace_ids.add(tid_str)

    if not all_trace_ids:
        return [], [], {}

    root_spans = (
        ObservationSpan.objects.filter(trace_id__in=list(all_trace_ids))
        .filter(models.Q(parent_span_id__isnull=True) | models.Q(parent_span_id=""))
        .values_list("trace_id", "span_attributes")
    )
    trace_input: dict[str, str] = {}
    for tid, attrs in root_spans:
        text = (attrs or {}).get("input.value", "") or ""
        if text:
            trace_input[str(tid)] = str(text)

    cluster_ids: List[str] = []
    corpus: List[str] = []
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
) -> List[str]:
    """TF-IDF-distinctive terms for this cluster's briefs vs the rest of
    the project. Used to light up matching substrings in the failing trace
    evidence reel.
    """
    cluster_ids, corpus = _project_cluster_briefs_corpus(project_id)
    if cluster_id not in cluster_ids:
        return []
    target = corpus[cluster_ids.index(cluster_id)]
    return [term for term, _ in _tfidf_distinctive_terms(target, corpus, top_k)]


# ── Individual insight builders ───────────────────────────────────────────
#
# Each returns a PatternInsight or None. The calling function picks the
# top-4 non-None ones, preserving priority order.


def _insight_affected_scope(
    cluster_id: str, project_id: str, trace_ids: List[str]
) -> Optional[PatternInsight]:
    n = len(trace_ids)
    total = TraceScanResult.objects.filter(project_id=project_id).count()
    if n == 0 or total == 0:
        return None
    pct = round(100 * n / total)
    return PatternInsight(
        value=f"{n} / {total}",
        caption=f"traces affected ({pct}%)",
    )


def _insight_category_concentration(cluster_id: str) -> Optional[PatternInsight]:
    cats = list(
        TraceScanIssue.objects.filter(cluster__cluster_id=cluster_id).values_list(
            "category", flat=True
        )
    )
    cats = [c for c in cats if c]
    if not cats:
        return None
    counter = Counter(cats)
    top, top_n = counter.most_common(1)[0]
    pct = round(100 * top_n / len(cats))
    if pct < 50:
        return None
    value = "All" if pct == 100 else f"{pct}%"
    return PatternInsight(value=value, caption=top)


def _insight_fix_layer_concentration(cluster_id: str) -> Optional[PatternInsight]:
    layers = list(
        TraceScanIssue.objects.filter(cluster__cluster_id=cluster_id).values_list(
            "fix_layer", flat=True
        )
    )
    layers = [layer for layer in layers if layer]
    if not layers:
        return None
    counter = Counter(layers)
    top, top_n = counter.most_common(1)[0]
    pct = round(100 * top_n / len(layers))
    if pct < 50:
        return None
    value = "All" if pct == 100 else f"{pct}%"
    return PatternInsight(value=value, caption=f"need {top} fix")


def _insight_failure_phrase(
    cluster_id: str, project_id: str
) -> Optional[PatternInsight]:
    """TF-IDF-distinctive word in this cluster's briefs vs the rest of the
    project. A word with high score is frequent HERE but rare in other
    clusters' briefs — that's signal, not boilerplate.
    """
    cluster_ids, corpus = _project_cluster_briefs_corpus(project_id)
    if cluster_id not in cluster_ids or len(corpus) < 2:
        return None
    target = corpus[cluster_ids.index(cluster_id)]
    top = _tfidf_distinctive_terms(target, corpus, top_k=1)
    if not top:
        return None
    term, _score = top[0]

    # How many of this cluster's briefs actually mention the term?
    # (TF-IDF picked it because it's distinctive, but we want to show a
    # concrete count — "in 3/3 briefs".)
    briefs = [
        b
        for b in TraceScanIssue.objects.filter(
            cluster__cluster_id=cluster_id
        ).values_list("brief", flat=True)
        if b
    ]
    pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
    hits = sum(1 for b in briefs if pattern.search(b))
    total = len(briefs)
    if total == 0 or hits < max(2, (total + 1) // 2):
        return None
    return PatternInsight(
        value=f'"{term}"',
        caption=f"in {hits}/{total} briefs",
    )


def _insight_input_topic(cluster_id: str, project_id: str) -> Optional[PatternInsight]:
    """TF-IDF-distinctive word in this cluster's user inputs vs the rest of
    the project. Signal: "these traces all share topic X".
    """
    cluster_ids, corpus, per_cluster_inputs = _project_cluster_inputs_corpus(project_id)
    if cluster_id not in cluster_ids or len(corpus) < 2:
        return None
    target = corpus[cluster_ids.index(cluster_id)]
    top = _tfidf_distinctive_terms(target, corpus, top_k=1)
    if not top:
        return None
    term, _score = top[0]

    inputs_map = per_cluster_inputs.get(cluster_id, {})
    total = len(inputs_map)
    if total == 0:
        return None
    pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
    hits = sum(1 for text in inputs_map.values() if pattern.search(text))
    if hits < max(2, (total + 1) // 2):
        return None
    value = "All" if hits == total else f"{hits}/{total}"
    return PatternInsight(
        value=value,
        caption=f'share topic: "{term}"',
    )


def _insight_avg_turns(trace_ids: List[str]) -> Optional[PatternInsight]:
    if not trace_ids:
        return None
    metas = TraceScanResult.objects.filter(trace_id__in=trace_ids).values_list(
        "meta", flat=True
    )
    turns = []
    for meta in metas:
        n = (meta or {}).get("turn_count") or 0
        if n > 0:
            turns.append(n)
    if not turns or statistics.fmean(turns) < 1.5:
        return None
    avg = statistics.fmean(turns)
    return PatternInsight(
        value=f"{avg:.1f}",
        caption="avg turns per trace",
    )


def _insight_missing_tools(trace_ids: List[str]) -> Optional[PatternInsight]:
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
        available = set(meta.get("tools_available") or [])
        if not available:
            continue
        traces_with_tools += 1
        called = set(meta.get("tools_called") or [])
        for tool in available - called:
            missing_counter[tool] += 1
    if not missing_counter or traces_with_tools == 0:
        return None
    top_tool, top_n = missing_counter.most_common(1)[0]
    return PatternInsight(
        value=f"{top_n}/{traces_with_tools}",
        caption=f"missing tool: {top_tool}",
    )


def _insight_flow_anomaly(cluster_id: str) -> Optional[PatternInsight]:
    """Fraction of briefs mentioning flow / ordering / missing-step phrases."""
    briefs = list(
        TraceScanIssue.objects.filter(cluster__cluster_id=cluster_id).values_list(
            "brief", flat=True
        )
    )
    briefs = [b for b in briefs if b]
    if not briefs:
        return None
    hits = sum(1 for b in briefs if _FLOW_ANOMALY_RE.search(b))
    total = len(briefs)
    if hits < max(2, (total + 2) // 3):
        return None
    pct = round(100 * hits / total)
    return PatternInsight(
        value=f"{hits}/{total}",
        caption=f"flow anomaly briefs ({pct}%)",
    )


def _eval_score_insights(trace_ids: List[str]) -> List[PatternInsight]:
    """Build per-eval average score insight cards for eval-sourced clusters.

    Returns one card per CustomEvalConfig that has scores on the cluster's traces,
    sorted by lowest average first (worst evals surface first).
    """
    from django.db.models import Avg

    rows = (
        EvalLogger.objects.filter(
            trace_id__in=trace_ids,
            custom_eval_config__isnull=False,
            output_float__isnull=False,
            deleted=False,
        )
        .values("custom_eval_config__name")
        .annotate(avg_score=Avg("output_float"))
        .order_by("avg_score")
    )

    insights = []
    for row in rows:
        name = row["custom_eval_config__name"]
        avg = row["avg_score"]
        pct = round(avg * 100)
        insights.append(PatternInsight(value=f"{pct}%", caption=f"avg {name}"))
    return insights


def _fetch_pattern_summary(cluster_id: str) -> PatternSummary:
    """Build the adaptive 4-card Pattern Summary for a cluster.

    For scanner clusters: runs scanner-specific insight builders.
    For eval clusters: shows affected scope + per-eval average scores.
    """
    cluster = TraceErrorGroup.objects.filter(cluster_id=cluster_id).first()
    if not cluster:
        return PatternSummary()

    project_id = str(cluster.project_id)
    trace_ids = _trace_ids_for_cluster(cluster_id)

    MAX_INSIGHTS = 4

    if cluster.source == ClusterSource.EVAL:
        # Eval clusters: affected scope + eval score averages
        scope = _insight_affected_scope(cluster_id, project_id, trace_ids)
        score_cards = _eval_score_insights(trace_ids)
        candidates = ([scope] if scope else []) + score_cards
        insights = candidates[:MAX_INSIGHTS]
        return PatternSummary(insights=insights, key_moments=[])

    # Scanner clusters: original logic
    scan_key_moments = TraceScanResult.objects.filter(
        trace_id__in=trace_ids
    ).values_list("key_moments", flat=True)

    seen: set = set()
    key_moments: List[KeyMoment] = []
    for km_list in scan_key_moments:
        for km in km_list or []:
            kv = km.get("kevinified", "")
            if not kv or kv in seen:
                continue
            seen.add(kv)
            key_moments.append(
                KeyMoment(kevinified=kv, verbatim=km.get("verbatim", "") or "")
            )
            if len(key_moments) >= 8:
                break
        if len(key_moments) >= 8:
            break

    candidates = [
        _insight_affected_scope(cluster_id, project_id, trace_ids),
        _insight_category_concentration(cluster_id),
        _insight_fix_layer_concentration(cluster_id),
        _insight_failure_phrase(cluster_id, project_id),
        _insight_avg_turns(trace_ids),
        _insight_missing_tools(trace_ids),
        _insight_flow_anomaly(cluster_id),
        _insight_input_topic(cluster_id, project_id),
    ]
    insights = [i for i in candidates if i is not None][:MAX_INSIGHTS]

    return PatternSummary(insights=insights, key_moments=key_moments)


def _get_root_span(trace_id: str) -> Optional[ObservationSpan]:
    """Root span = no parent (NULL or empty string)."""
    return (
        ObservationSpan.objects.filter(trace_id=trace_id)
        .filter(models.Q(parent_span_id__isnull=True) | models.Q(parent_span_id=""))
        .first()
    )


def _get_root_spans_batch(trace_ids: List[str]) -> dict:
    """Return {trace_id_str: ObservationSpan} — first root span per trace."""
    if not trace_ids:
        return {}
    rows = ObservationSpan.objects.filter(trace_id__in=trace_ids).filter(
        models.Q(parent_span_id__isnull=True) | models.Q(parent_span_id="")
    )
    out: dict = {}
    for span in rows:
        tid = str(span.trace_id)
        if tid not in out:
            out[tid] = span
    return out


def _get_trace_totals(
    trace_id: str,
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Return (latency_ms, prompt_tokens, completion_tokens) aggregated from spans."""
    agg = ObservationSpan.objects.filter(trace_id=trace_id).aggregate(
        latency=Sum("latency_ms"),
        prompt=Sum("prompt_tokens"),
        completion=Sum("completion_tokens"),
    )
    return agg["latency"], agg["prompt"], agg["completion"]


def _get_trace_totals_batch(trace_ids: List[str]) -> dict:
    """Return {trace_id_str: (latency, prompt, completion)} aggregated from spans."""
    if not trace_ids:
        return {}
    rows = (
        ObservationSpan.objects.filter(trace_id__in=trace_ids)
        .values("trace_id")
        .annotate(
            latency=Sum("latency_ms"),
            prompt=Sum("prompt_tokens"),
            completion=Sum("completion_tokens"),
        )
    )
    return {
        str(r["trace_id"]): (r["latency"], r["prompt"], r["completion"]) for r in rows
    }


def _get_trace_score(trace_id: str) -> Optional[float]:
    """Average EvalLogger.output_float across the trace."""
    agg = EvalLogger.objects.filter(trace_id=trace_id).aggregate(
        avg=Avg("output_float")
    )
    return agg["avg"]


def _get_trace_scores_batch(trace_ids: List[str]) -> dict:
    """Return {trace_id_str: avg_output_float}."""
    if not trace_ids:
        return {}
    rows = (
        EvalLogger.objects.filter(trace_id__in=trace_ids)
        .values("trace_id")
        .annotate(avg=Avg("output_float"))
    )
    return {str(r["trace_id"]): r["avg"] for r in rows}


def _get_scan_results_batch(trace_ids: List[str]) -> dict:
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


def _highlight_text(text: str, terms: List[str], hl: str) -> object:
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

    segments: List[dict] = []
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


def _key_moments_to_reel(
    key_moments: Optional[list],
    highlight_terms: Optional[List[str]] = None,
    hl: str = "error",
) -> List[dict]:
    """
    Map TraceScanResult.key_moments to ReelStep dicts the frontend renders.

    Frontend ReelStep shape: { label: str, text: str | List[{t, hl?}], meta }.
    When ``highlight_terms`` is provided, matching substrings inside each
    step's text are wrapped as rich-text segments so the UI can paint them.
    """
    steps: List[dict] = []
    for km in key_moments or []:
        verbatim = (km.get("verbatim") or "").strip()
        kevinified = (km.get("kevinified") or "").strip()
        if not verbatim and not kevinified:
            continue
        raw_text = verbatim or kevinified
        text = _highlight_text(raw_text, highlight_terms or [], hl)
        steps.append({"label": "EVIDENCE", "text": text, "meta": None})
    return steps


def _build_representative_trace(
    trace: Trace,
    has_issues: bool,
    pass_reel: Optional[List[dict]] = None,
    highlight_terms: Optional[List[str]] = None,
    *,
    root: Optional[ObservationSpan] = None,
    totals: Optional[Tuple[Optional[int], Optional[int], Optional[int]]] = None,
    score: Optional[float] = None,
    scan_result: Optional[TraceScanResult] = None,
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
        attrs = root.span_attributes or {}
        input_text = _safe_str(attrs.get("input.value")) or _trace_input_str(trace)
        output_text = _safe_str(attrs.get("output.value")) or _trace_output_str(trace)
    else:
        input_text = _trace_input_str(trace)
        output_text = _trace_output_str(trace)

    turns = None
    fail_reel: List[dict] = []
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
        )

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
        ),
    )


def _fetch_success_trace_pass_reel(cluster_id: str) -> List[dict]:
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
    steps: List[dict] = []

    # 1. User input (from root span or Trace.input)
    root = _get_root_span(str(success.id))
    input_text = None
    output_text = None
    if root:
        attrs = root.span_attributes or {}
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
        steps.extend(_key_moments_to_reel(scan_result.key_moments))

    # 3. Final successful output
    if output_text:
        steps.append({"label": "CORRECT OUTPUT", "text": output_text, "meta": None})

    return steps


def _fetch_representative_traces(
    cluster_id: str,
    project_id: str,
    limit: Optional[int] = None,
) -> List[RepresentativeTrace]:
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
    pass_reel = _fetch_success_trace_pass_reel(cluster_id)
    highlight_terms = _cluster_highlight_terms(cluster_id, project_id)

    qs = (
        ErrorClusterTraces.objects.filter(cluster__cluster_id=cluster_id)
        .select_related("trace")
        .order_by("-created_at")
    )
    ect_rows = list(
        qs[: limit * 3] if limit else qs
    )  # over-fetch for dedupe when limited

    # First pass: dedupe by trace id so the batch helpers below only fetch
    # what we'll actually emit.
    deduped: List[Trace] = []
    seen_ids: set = set()
    for ect in ect_rows:
        if not ect.trace:
            continue
        tid = str(ect.trace.id)
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        deduped.append(ect.trace)
        if limit and len(deduped) >= limit:
            break

    if not deduped:
        return []

    trace_ids = [str(t.id) for t in deduped]
    roots = _get_root_spans_batch(trace_ids)
    totals = _get_trace_totals_batch(trace_ids)
    scores = _get_trace_scores_batch(trace_ids)
    scans = _get_scan_results_batch(trace_ids)

    return [
        _build_representative_trace(
            trace,
            has_issues=True,
            pass_reel=pass_reel,
            highlight_terms=highlight_terms,
            root=roots.get(str(trace.id)),
            totals=totals.get(str(trace.id)),
            score=scores.get(str(trace.id)),
            scan_result=scans.get(str(trace.id)),
            _prefetched=True,
        )
        for trace in deduped
    ]


def get_overview(cluster_id: str) -> Optional[OverviewResponse]:
    """Full Overview tab payload for a cluster."""
    cluster = TraceErrorGroup.objects.filter(cluster_id=cluster_id).first()
    if not cluster:
        return None
    project_id = str(cluster.project_id)

    return OverviewResponse(
        events_over_time=_fetch_events_over_time(cluster_id),
        pattern_summary=_fetch_pattern_summary(cluster_id),
        representative_traces=_fetch_representative_traces(cluster_id, project_id),
    )


# ---------------------------------------------------------------------------
# Traces tab endpoint
# ---------------------------------------------------------------------------


def _percentile(values: List[int], pct: float) -> int:
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


def _fetch_traces_aggregates(cluster_id: str) -> TracesAggregates:
    """Compute per-cluster aggregates for the Traces tab stat bar."""
    trace_ids = _trace_ids_for_cluster(cluster_id)
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

    # Avg eval score across all trace-level EvalLogger rows
    avg_score = (
        EvalLogger.objects.filter(trace_id__in=trace_ids).aggregate(
            avg=Avg("output_float")
        )["avg"]
        or 0.0
    )

    # Latency percentiles: sum(latency_ms) per trace
    per_trace_latency: List[int] = []
    latency_rows = (
        ObservationSpan.objects.filter(trace_id__in=trace_ids)
        .values("trace_id")
        .annotate(total=Sum("latency_ms"))
    )
    for row in latency_rows:
        if row["total"] is not None:
            per_trace_latency.append(row["total"])

    p50 = _percentile(per_trace_latency, 50)
    p95 = _percentile(per_trace_latency, 95)

    # Average turn count from scan meta
    turn_counts: List[int] = []
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
    cluster_id: str, limit: int, offset: int
) -> tuple[List[TracesListRow], int]:
    """Paginated list of traces in the cluster for the AG Grid."""
    base = (
        ErrorClusterTraces.objects.filter(cluster__cluster_id=cluster_id)
        .select_related("trace")
        .order_by("-created_at")
    )

    total = base.values("trace_id").distinct().count()
    rows: List[TracesListRow] = []
    seen: set = set()
    for ect in base[offset : offset + limit * 3]:  # over-fetch for dedupe
        if not ect.trace:
            continue
        tid = str(ect.trace.id)
        if tid in seen:
            continue
        seen.add(tid)

        latency, prompt, completion = _get_trace_totals(tid)
        tokens = (prompt or 0) + (completion or 0)
        score = _get_trace_score(tid)
        root = _get_root_span(tid)
        input_text = None
        if root:
            attrs = root.span_attributes or {}
            input_text = attrs.get("input.value")
        if not input_text:
            input_text = _trace_input_str(ect.trace)

        turns = None
        scan_result = TraceScanResult.objects.filter(trace_id=tid).only("meta").first()
        if scan_result and scan_result.meta:
            turns = scan_result.meta.get("turn_count")

        rows.append(
            TracesListRow(
                id=tid,
                input=input_text,
                timestamp=ect.trace.created_at,
                latency_ms=latency,
                tokens=tokens if tokens else None,
                cost=round(tokens * _COST_PER_TOKEN, 6) if tokens else None,
                score=score,
                turns=turns,
            )
        )
        if len(rows) >= limit:
            break

    return rows, total


def get_traces_tab(
    cluster_id: str, limit: int = 50, offset: int = 0
) -> Optional[TracesTabResponse]:
    """Full Traces tab payload."""
    if not TraceErrorGroup.objects.filter(cluster_id=cluster_id).exists():
        return None

    aggregates = _fetch_traces_aggregates(cluster_id)
    rows, total = _fetch_trace_rows(cluster_id, limit=limit, offset=offset)
    return TracesTabResponse(aggregates=aggregates, traces=rows, total=total)


# ---------------------------------------------------------------------------
# Trends tab endpoint
# ---------------------------------------------------------------------------


def _trace_ids_in_cluster_window(
    cluster_id: str, since: datetime, until: Optional[datetime] = None
) -> List[str]:
    """Trace IDs that joined the cluster within a time window (via ECT)."""
    qs = ErrorClusterTraces.objects.filter(
        cluster__cluster_id=cluster_id, created_at__gte=since
    )
    if until is not None:
        qs = qs.filter(created_at__lt=until)
    return [str(tid) for tid in qs.values_list("trace_id", flat=True) if tid]


def _users_affected_in_window(trace_ids: List[str]) -> int:
    """Distinct end_user_id across the given traces."""
    if not trace_ids:
        return 0
    return (
        ObservationSpan.objects.filter(trace_id__in=trace_ids, end_user__isnull=False)
        .values("end_user_id")
        .distinct()
        .count()
    )


def _avg_eval_score(trace_ids: List[str]) -> Optional[float]:
    """Average EvalLogger.output_float over a list of traces."""
    if not trace_ids:
        return None
    return EvalLogger.objects.filter(trace_id__in=trace_ids).aggregate(
        avg=Avg("output_float")
    )["avg"]


def _fetch_trend_metrics(
    cluster_id: str, project_id: str, days: int
) -> List[TrendMetric]:
    """Build the 3 KPI cards — current vs previous window."""
    now = timezone.now()
    window = timedelta(days=days)
    cur_start = now - window
    prev_start = cur_start - window

    cur_traces = _trace_ids_in_cluster_window(cluster_id, cur_start)
    prev_traces = _trace_ids_in_cluster_window(cluster_id, prev_start, cur_start)

    cur_total = TraceScanResult.objects.filter(
        project_id=project_id, created_at__gte=cur_start
    ).count()
    prev_total = TraceScanResult.objects.filter(
        project_id=project_id,
        created_at__gte=prev_start,
        created_at__lt=cur_start,
    ).count()

    cur_err_rate = (100.0 * len(cur_traces) / cur_total) if cur_total else 0.0
    prev_err_rate = (100.0 * len(prev_traces) / prev_total) if prev_total else 0.0

    cur_score = _avg_eval_score(cur_traces) or 0.0
    prev_score = _avg_eval_score(prev_traces) or 0.0

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
) -> List[EventsOverTimePoint]:
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

    # Distinct end users affected per day (via the cluster's traces)
    user_rows = (
        ObservationSpan.objects.filter(
            trace__error_cluster_traces__cluster__cluster_id=cluster_id,
            trace__error_cluster_traces__created_at__gte=since,
            end_user__isnull=False,
        )
        .annotate(bucket=TruncDate("trace__error_cluster_traces__created_at"))
        .values("bucket")
        .annotate(users=Count("end_user_id", distinct=True))
    )
    users_by_day: dict = {row["bucket"]: row["users"] for row in user_rows}

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
) -> List[ScoreTrend]:
    """Per-CustomEvalConfig.name score sparkline over the last ``days``.

    Splits the window in half: first half = prev, second half = current.
    Daily sparkline is average ``output_float`` per day over the full window.
    """
    trace_ids = _trace_ids_for_cluster(cluster_id)
    if not trace_ids:
        return []

    now = timezone.now()
    since = now - timedelta(days=days)
    midpoint = now - timedelta(days=days / 2)

    rows = list(
        EvalLogger.objects.filter(
            trace_id__in=trace_ids,
            created_at__gte=since,
            output_float__isnull=False,
            custom_eval_config__isnull=False,
        )
        .annotate(day=TruncDate("created_at"))
        .values(
            "day",
            "custom_eval_config__name",
            "output_float",
            "created_at",
        )
    )
    if not rows:
        return []

    # Group: {label: {day: [scores...], "_prev": [...], "_cur": [...]}}
    groups: dict = {}
    for r in rows:
        label = r["custom_eval_config__name"] or "Unnamed eval"
        g = groups.setdefault(label, {"days": {}, "prev": [], "cur": [], "count": 0})
        g["days"].setdefault(r["day"], []).append(r["output_float"])
        if r["created_at"] >= midpoint:
            g["cur"].append(r["output_float"])
        else:
            g["prev"].append(r["output_float"])
        g["count"] += 1

    # Keep top N labels by sample count so we don't overwhelm the UI
    top_labels = sorted(groups.items(), key=lambda kv: kv[1]["count"], reverse=True)[
        :max_labels
    ]

    result: List[ScoreTrend] = []
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


def _fetch_activity_heatmap(cluster_id: str, days: int = 30) -> List[List[HeatmapCell]]:
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


def get_trends_tab(cluster_id: str, days: int = 14) -> Optional[TrendsTabResponse]:
    """Full Trends tab payload."""
    cluster = TraceErrorGroup.objects.filter(cluster_id=cluster_id).first()
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
    trace_ids: List[str],
    selected_trace_id: Optional[str] = None,
) -> SidebarAIMetadata:
    """Model / version / project / eval score / trace id for the sidebar.

    When ``selected_trace_id`` is provided, model/version/evalScore/traceId
    are computed from that specific trace — this keeps the sidebar in sync
    with the "Traces affected" list selection in the Overview tab. When
    absent, falls back to the cluster's latest trace and cluster-wide avg
    eval score.
    """
    project = cluster.project.name if cluster.project_id else None

    # Trace to inspect: caller's pick, or cluster's latest as fallback.
    focus_trace_id: Optional[str] = selected_trace_id
    if focus_trace_id is None:
        latest = (
            ErrorClusterTraces.objects.filter(cluster__cluster_id=cluster.cluster_id)
            .order_by("-created_at")
            .values_list("trace_id", flat=True)
            .first()
        )
        if latest:
            focus_trace_id = str(latest)

    model: Optional[str] = None
    model_version: Optional[str] = None
    if focus_trace_id:
        llm_span = (
            ObservationSpan.objects.filter(
                trace_id=focus_trace_id, observation_type__iexact="llm"
            )
            .order_by("start_time")
            .only("model", "span_attributes")
            .first()
        )
        if llm_span:
            model = llm_span.model or None
            attrs = llm_span.span_attributes or {}
            model_version = (
                attrs.get("gen_ai.request.model_version")
                or attrs.get("llm.model_version")
                or None
            )

    # When a trace is explicitly selected, report THAT trace's score.
    # Otherwise show the cluster-wide average (current no-selection default).
    if selected_trace_id:
        eval_score = _avg_eval_score([selected_trace_id])
    else:
        eval_score = _avg_eval_score(trace_ids)
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
    trace_ids: List[str],
    selected_trace_id: Optional[str] = None,
) -> List[EvaluationResult]:
    """Roll up EvalLogger rows to one row per CustomEvalConfig.name.

    Type is inferred from the output shape — the spec's

    - ``output_float`` populated → ``llm_judge`` (renders as score bar)
    - ``output_bool``/``output_str`` only → ``deterministic`` (renders as
      verdict chip)

    When both are present, float wins so the score bar is always shown.

    When ``selected_trace_id`` is provided, only that trace's eval rows are
    considered — otherwise the rollup spans every trace in the cluster.
    """
    if selected_trace_id:
        effective_trace_ids = [selected_trace_id]
    else:
        effective_trace_ids = trace_ids
    if not effective_trace_ids:
        return []

    rows = list(
        EvalLogger.objects.filter(
            trace_id__in=effective_trace_ids, custom_eval_config__isnull=False
        ).values(
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

    result: List[EvaluationResult] = []
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

        score: Optional[float] = (
            round(statistics.fmean(g["floats"]), 4) if has_floats else None
        )
        value: Optional[str] = None
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
) -> List[CoOccurringIssue]:
    """Jaccard-rank other clusters in the same project that share traces.

    Pulls (cluster_id, trace_id) pairs for every scanner cluster in the project
    and computes Jaccard in Python. Cheap — projects have O(100) clusters max.
    """
    this_traces_set = set(_trace_ids_for_cluster(cluster_id))
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

    scored: List[Tuple[str, int, float]] = []
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

    result: List[CoOccurringIssue] = []
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
    cluster_id: str, trace_id: Optional[str] = None
) -> Optional[FeedSidebar]:
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
        TraceErrorGroup.objects.filter(cluster_id=cluster_id, deleted=False)
        .select_related("project")
        .first()
    )
    if not cluster:
        return None

    project_id = str(cluster.project_id)
    trace_ids = _trace_ids_for_cluster(cluster_id)

    # Guardrail: only honor trace_id if it actually belongs to this cluster.
    selected_trace_id: Optional[str] = None
    if trace_id and str(trace_id) in trace_ids:
        selected_trace_id = str(trace_id)

    # Age since first_seen — frontend renders as integer days
    age_days: Optional[int] = None
    if cluster.first_seen:
        delta = timezone.now() - cluster.first_seen
        age_days = max(delta.days, 0)

    timeline = SidebarTimeline(
        first_seen=cluster.first_seen,
        last_seen=cluster.last_seen,
        age_days=age_days,
    )
    ai_metadata = _fetch_sidebar_ai_metadata(
        cluster, trace_ids, selected_trace_id=selected_trace_id
    )
    evaluations = _fetch_sidebar_evaluations(
        trace_ids, selected_trace_id=selected_trace_id
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


def _urgency_to_priority(urgency: Optional[str]) -> str:
    """Map TraceErrorDetail.urgency_to_fix (uppercase enum) to the frontend's
    lowercase priority bucket. Falls back to ``medium`` for unknown values."""
    return _URGENCY_TO_PRIORITY.get((urgency or "").upper(), "medium")


def _recommendation_title_from_category(category: Optional[str]) -> str:
    """The error category path looks like "A > B > C"; the leaf is the most
    specific label and reads best as a card title."""
    if not category:
        return "Recommendation"
    parts = [p.strip() for p in category.split(">") if p.strip()]
    return parts[-1] if parts else category.strip()


def _build_root_causes(details: List[TraceErrorDetail]) -> List[RootCause]:
    """Flatten ``TraceErrorDetail.root_causes`` across every detail for a
    trace, dedupe, and produce a ranked ``RootCause`` list.

    Each ``TraceErrorDetail.root_causes`` is a list of free-form strings
    produced by the analysis agent; individual items are full sentences.
    Title = first clause before the first period/comma; description = the
    full string (so the card renders a natural headline + body).
    """
    seen: set = set()
    result: List[RootCause] = []
    rank = 1
    for d in details:
        for raw in d.root_causes or []:
            if not raw:
                continue
            text = str(raw).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)

            # Headline: first clause before . or ,
            split_idx = min(
                (i for i in (text.find("."), text.find(",")) if i > 0),
                default=-1,
            )
            title = text[:split_idx].strip() if split_idx > 0 else text
            if len(title) > 120:
                title = title[:117].rstrip() + "..."

            result.append(RootCause(rank=rank, title=title, description=text))
            rank += 1
    return result


def _build_recommendations(
    details: List[TraceErrorDetail], root_causes: List[RootCause]
) -> List[Recommendation]:
    """Produce one ``Recommendation`` card per ``TraceErrorDetail`` row.

    ``root_cause_link`` points into ``root_causes`` by rank — we match
    each detail's primary root cause against the deduped global list so
    the frontend can highlight the linkage.
    """
    # Index for quick lookup: normalized text → rank
    by_text: dict = {rc.description.lower(): rc.rank for rc in root_causes}

    result: List[Recommendation] = []
    for d in details:
        linked_rank: Optional[int] = None
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


def _cluster_has_trace(cluster_id: str, trace_id: str) -> bool:
    """Guardrail: the POST / GET endpoints only act on traces that are
    actually linked to the given cluster. Prevents a user from analyzing
    an arbitrary trace by hitting the wrong URL."""
    return ErrorClusterTraces.objects.filter(
        cluster__cluster_id=cluster_id, trace_id=trace_id
    ).exists()


def get_deep_analysis(cluster_id: str, trace_id: str) -> Optional[DeepAnalysisResponse]:
    """Read the cached deep analysis for ``trace_id`` within ``cluster_id``.

    Returns ``None`` when the cluster doesn't exist or the trace isn't
    part of it. Otherwise always returns a response — the ``status``
    field tells the frontend whether data is available.
    """
    if not TraceErrorGroup.objects.filter(
        cluster_id=cluster_id, deleted=False
    ).exists():
        return None

    if not _cluster_has_trace(cluster_id, trace_id):
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
    immediate_fix: Optional[str] = None
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
    cluster_id: str, trace_id: str, force: bool = False
) -> Optional[DeepAnalysisDispatchResponse]:
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

    if not TraceErrorGroup.objects.filter(
        cluster_id=cluster_id, deleted=False
    ).exists():
        return None

    if not _cluster_has_trace(cluster_id, trace_id):
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
