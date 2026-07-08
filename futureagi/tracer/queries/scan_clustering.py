"""
DB helpers for scanner issue clustering + success trace matching.

Handles: embedding generation, centroid matching, cluster creation/update,
         trace input embedding storage, and KNN success trace search.
Uses ClickHouse for centroid storage + cosine similarity search.
Online incremental approach: each issue embedded → nearest centroid → assign or create.
"""

import hashlib
from typing import List, Optional, Tuple

import structlog
from django.utils import timezone

from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.embedding_manager import model_manager
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    TraceErrorGroup,
)
from tracer.models.trace_scan import TraceScanIssue, TraceScanResult
from tracer.services.clickhouse.v2 import get_reader
from tracer.types.scan_types import ClusterableIssue, TraceInputData

logger = structlog.get_logger(__name__)

CENTROIDS_TABLE = "cluster_centroids"
COSINE_THRESHOLD = 0.45  # cosine distance: 0 = identical, 2 = opposite


def _severity_to_impact(severity: str | None) -> str:
    """Map the scanner's 4-level severity to the cluster's 3-level impact."""
    return {
        "critical": "HIGH", "high": "HIGH", "medium": "MEDIUM", "low": "LOW"
    }.get(severity or "medium", "MEDIUM")


def _seed_severity(category: str, brief: str) -> str | None:
    """Cheap-LLM user-impact severity for a new cluster's seed (centroid) issue,
    mirroring the eval-cluster path. Single datapoint, one call per new cluster.
    EE-absent (OSS) or any LLM failure → None so the caller defaults to medium."""
    from tracer.ee_boundary import generate_scan_cluster_severity

    return generate_scan_cluster_severity(category, brief)


# ---------------------------------------------------------------------------
# Fetch unclustered issues
# ---------------------------------------------------------------------------


def get_unclustered_issues(project_id: str) -> List[ClusterableIssue]:
    """Fetch TraceScanIssues that haven't been assigned to a cluster yet."""
    issues = (
        TraceScanIssue.objects.filter(
            scan_result__project_id=project_id,
            cluster__isnull=True,
        )
        .select_related("scan_result")
        .order_by("created_at")
    )

    result = []
    for issue in issues:
        key_moments = issue.scan_result.key_moments or []
        km_texts = [
            km.get("kevinified", "") for km in key_moments if km.get("kevinified")
        ]

        result.append(
            ClusterableIssue(
                issue_id=str(issue.id),
                trace_id=str(issue.scan_result.trace_id),
                project_id=project_id,
                category=issue.category,
                group=issue.group,
                fix_layer=issue.fix_layer,
                brief=issue.brief,
                confidence=issue.confidence,
                key_moments_text=km_texts,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a batch of texts using the model serving client."""
    text_embed = model_manager.text_model
    embeddings = []
    for text in texts:
        embedding = text_embed(text)
        embeddings.append(embedding)
    return embeddings


# ---------------------------------------------------------------------------
# Centroid operations (ClickHouse)
# ---------------------------------------------------------------------------


def _ensure_centroid_table(db: ClickHouseVectorDB) -> None:
    """Ensure the cluster_centroids table exists."""
    # Array(...) can't sit inside Nullable; override server profiles that set
    # data_type_default_nullable=1 so unmodified types aren't auto-wrapped.
    db.client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CENTROIDS_TABLE} (
            cluster_id String,
            project_id UUID,
            centroid Array(Float32),
            member_count UInt32,
            family String,
            last_updated DateTime DEFAULT now(),
            PRIMARY KEY (cluster_id)
        ) ENGINE = ReplacingMergeTree(last_updated)
        ORDER BY (cluster_id)
        """,
        settings={"data_type_default_nullable": 0},
    )


def _update_centroid(
    current: List[float], new_vector: List[float], count: int
) -> List[float]:
    """Incremental centroid update: (centroid * count + new) / (count + 1)."""
    if not current:
        return new_vector
    return [(c * count + n) / (count + 1) for c, n in zip(current, new_vector)]


def find_nearest_centroid(
    embedding: List[float],
    project_id: str,
    category: str,
) -> Optional[Tuple[str, float]]:
    """
    Find nearest cluster centroid for the given category within threshold.

    Returns (cluster_id, distance) or None if no match.
    Partitions by category (subcategory) to keep clusters precise.
    """
    db = ClickHouseVectorDB()
    try:
        _ensure_centroid_table(db)
        vector_str = "[" + ",".join(map(str, embedding)) + "]"
        rows = db.client.execute(
            f"""
            SELECT
                cluster_id,
                cosineDistance(centroid, {vector_str}) AS distance
            FROM {CENTROIDS_TABLE}
            WHERE project_id = %(project_id)s
            AND family = %(family)s
            ORDER BY distance ASC
            LIMIT 1
            """,
            {"project_id": project_id, "family": category},
        )

        if rows and rows[0][1] < COSINE_THRESHOLD:
            return rows[0][0], rows[0][1]
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cluster creation
# ---------------------------------------------------------------------------


def create_cluster(
    project_id: str,
    issue: ClusterableIssue,
    embedding: List[float],
) -> str:
    """
    Create a new TraceErrorGroup cluster + ClickHouse centroid.

    Returns the new cluster_id.
    """
    # Stable cluster ID from project + category + brief prefix
    base = f"{project_id}|scanner|{issue.category}|{issue.brief[:100]}"
    h = hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()[:8]
    cluster_id = f"S-{h.upper()}"

    # Handle collision — if cluster_id already exists, append suffix
    if TraceErrorGroup.objects.filter(
        project_id=project_id, cluster_id=cluster_id
    ).exists():
        h2 = hashlib.md5(
            f"{base}|{issue.issue_id}".encode(), usedforsecurity=False
        ).hexdigest()[:8]
        cluster_id = f"S-{h2.upper()}"

    # Centroid severity: classify the seed (representative) issue once via the
    # cheap LLM and keep it as the cluster's severity — no scanner change, no
    # per-issue column. Lazy import avoids a query-module cycle;
    # severity_to_priority falls back to "medium" when severity is None.
    from tracer.queries.feed import severity_to_priority

    seed_sev = _seed_severity(issue.category, issue.brief)

    cluster = TraceErrorGroup.objects.create(
        project_id=project_id,
        cluster_id=cluster_id,
        source=ClusterSource.SCANNER,
        issue_group=issue.group,
        issue_category=issue.category,
        fix_layer=issue.fix_layer,
        title=issue.brief,
        status=FeedIssueStatus.ESCALATING,
        error_type=issue.group,
        combined_impact=_severity_to_impact(seed_sev),
        priority=severity_to_priority(seed_sev),
        total_events=1,
        unique_traces=1,
        error_count=1,
        first_seen=timezone.now(),
        last_seen=timezone.now(),
    )

    # Link issue → cluster
    TraceScanIssue.objects.filter(id=issue.issue_id).update(cluster=cluster)

    # Create ErrorClusterTraces junction
    ErrorClusterTraces.objects.create(
        cluster=cluster,
        trace_id=issue.trace_id,
        scan_issue_id=issue.issue_id,
    )

    # Store centroid in ClickHouse
    db = ClickHouseVectorDB()
    try:
        _ensure_centroid_table(db)
        db.client.execute(
            f"""
            INSERT INTO {CENTROIDS_TABLE}
            (cluster_id, project_id, centroid, member_count, family, last_updated)
            VALUES
            (%(cluster_id)s, %(project_id)s, %(centroid)s, %(member_count)s, %(family)s, now())
            """,
            {
                "cluster_id": cluster_id,
                "project_id": project_id,
                "centroid": embedding,
                "member_count": 1,
                "family": issue.category,
            },
        )
    finally:
        db.close()

    logger.info(
        "cluster_created",
        cluster_id=cluster_id,
        category=issue.category,
        title=issue.brief[:80],
    )
    return cluster_id


# ---------------------------------------------------------------------------
# Cluster assignment
# ---------------------------------------------------------------------------


def assign_to_cluster(
    cluster_id: str,
    project_id: str,
    issue: ClusterableIssue,
    embedding: List[float],
) -> None:
    """Assign an issue to an existing cluster and update centroid incrementally."""
    cluster = TraceErrorGroup.objects.get(cluster_id=cluster_id, project_id=project_id)

    # Update cluster counts + bump last_seen so the Feed sorts/timelines
    # stay fresh as new traces roll in.
    cluster.error_count = (cluster.error_count or 0) + 1
    cluster.total_events = (cluster.total_events or 0) + 1
    cluster.last_seen = timezone.now()
    cluster.save(
        update_fields=["error_count", "total_events", "last_seen", "updated_at"]
    )

    # Link issue → cluster
    TraceScanIssue.objects.filter(id=issue.issue_id).update(cluster=cluster)

    # Create junction entry (ignore if trace already linked)
    ErrorClusterTraces.objects.get_or_create(
        cluster=cluster,
        trace_id=issue.trace_id,
        defaults={"scan_issue_id": issue.issue_id},
    )

    # Refresh unique traces count
    unique = cluster.clusters.values("trace").distinct().count()
    cluster.unique_traces = unique
    cluster.save(update_fields=["unique_traces", "updated_at"])

    # Incrementally update centroid in ClickHouse
    db = ClickHouseVectorDB()
    try:
        rows = db.client.execute(
            f"""
            SELECT centroid, member_count
            FROM {CENTROIDS_TABLE}
            WHERE cluster_id = %(cluster_id)s
            LIMIT 1
            """,
            {"cluster_id": cluster_id},
        )

        if rows:
            old_centroid, old_count = rows[0]
            new_centroid = _update_centroid(old_centroid, embedding, old_count)
            new_count = old_count + 1
        else:
            new_centroid = embedding
            new_count = 1

        db.client.execute(
            f"""
            INSERT INTO {CENTROIDS_TABLE}
            (cluster_id, project_id, centroid, member_count, family, last_updated)
            VALUES
            (%(cluster_id)s, %(project_id)s, %(centroid)s, %(member_count)s, %(family)s, now())
            """,
            {
                "cluster_id": cluster_id,
                "project_id": project_id,
                "centroid": new_centroid,
                "member_count": new_count,
                "family": issue.category,
            },
        )
    finally:
        db.close()

    logger.info(
        "issue_assigned_to_cluster",
        cluster_id=cluster_id,
        issue_id=issue.issue_id,
    )


# ---------------------------------------------------------------------------
# Trace input embeddings (for success trace matching)
# ---------------------------------------------------------------------------

TRACE_INPUTS_TABLE = "trace_input_embeddings"


def _ensure_trace_inputs_table(db: ClickHouseVectorDB) -> None:
    """Ensure the trace_input_embeddings table exists."""
    db.client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TRACE_INPUTS_TABLE} (
            trace_id UUID,
            project_id UUID,
            embedding Array(Float32),
            has_issues Bool,
            created_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(created_at)
        ORDER BY (project_id, trace_id)
        """,
        settings={"data_type_default_nullable": 0},
    )


def get_trace_input_data(trace_ids: List[str], project_id: str) -> List[TraceInputData]:
    """
    Fetch root span input text + has_issues flag for scanned traces.

    ONLY returns traces that have a TraceScanResult row — unscanned traces
    are in an unknown state (not necessarily clean) and must not be embedded
    as "success traces" for KNN comparison.

    Root span = span with no parent_span_id. Falls back to Trace.input if the
    root span has no input.value attribute.
    """
    # has_issues from TraceScanResult. Only scanned traces pass the filter below.
    scan_results = TraceScanResult.objects.filter(
        trace_id__in=trace_ids,
        project_id=project_id,
    ).values_list("trace_id", "has_issues")
    has_issues_map = {str(tid): hi for tid, hi in scan_results}

    scanned_trace_ids = [tid for tid in trace_ids if tid in has_issues_map]
    if not scanned_trace_ids:
        return []

    # First root span's input.value per scanned trace. roots_by_trace_ids returns
    # one parentless row per trace in (trace_id, start_time, id) order (first root
    # wins, matching PG); project-scoped + lean since only input.value is needed
    # (it lives in attrs_string, kept real). Reading every span here would repeat
    # the wide read that OOMs the scan step.
    with get_reader() as reader:
        ch_spans = reader.roots_by_trace_ids(
            scanned_trace_ids, project_id=str(project_id)
        )

    input_texts: dict[str, str] = {}
    for span in ch_spans:
        # Root span = no parent_span_id (CHSpan stores it as "" when absent).
        if span.parent_span_id:
            continue
        trace_id_str = str(span.trace_id)
        if trace_id_str in input_texts:
            continue  # already captured first root for this trace
        input_text = (span.attrs_string or {}).get("input.value", "")
        if input_text:
            input_texts[trace_id_str] = str(input_text)

    # Fallback: check Trace.input for any missing
    missing = [tid for tid in scanned_trace_ids if tid not in input_texts]
    if missing:
        traces = Trace.objects.filter(id__in=missing).values_list("id", "input")
        for trace_id, trace_input in traces:
            if trace_input:
                input_texts[str(trace_id)] = str(trace_input)

    # Build typed results — only scanned traces with known has_issues state
    result = []
    for trace_id in scanned_trace_ids:
        text = input_texts.get(trace_id)
        if not text:
            continue
        result.append(
            TraceInputData(
                trace_id=trace_id,
                project_id=project_id,
                input_text=text,
                has_issues=has_issues_map[trace_id],
            )
        )

    return result


def store_trace_input_embeddings(
    inputs: List[TraceInputData],
    embeddings: List[List[float]],
) -> int:
    """
    Store kevinified root input embeddings in ClickHouse.

    Args:
        inputs: TraceInputData objects (aligned with embeddings)
        embeddings: Embedding vectors (aligned with inputs)

    Returns number of embeddings stored.
    """
    db = ClickHouseVectorDB()
    try:
        _ensure_trace_inputs_table(db)
        rows = [
            {
                "trace_id": inp.trace_id,
                "project_id": inp.project_id,
                "embedding": emb,
                "has_issues": inp.has_issues,
            }
            for inp, emb in zip(inputs, embeddings)
        ]

        if rows:
            db.client.execute(
                f"""
                INSERT INTO {TRACE_INPUTS_TABLE}
                (trace_id, project_id, embedding, has_issues)
                VALUES
                """,
                rows,
            )

        return len(rows)
    finally:
        db.close()


def find_success_trace_baseline(
    query_embedding: List[float],
    project_id: str,
    k: int = 120,
    exclude_trace_ids: Optional[List[str]] = None,
) -> List[Tuple[str, float]]:
    """
    KNN: up to ``k`` nearest success traces (has_issues=False) to the query
    embedding, sorted nearest-first.

    Powers the Pattern Summary "vs working runs" baseline AND the per-trace
    Compare-with-passing match (build once, use twice). Returns a list of
    (trace_id, cosine_distance) — empty if no success traces exist.
    """
    db = ClickHouseVectorDB()
    try:
        _ensure_trace_inputs_table(db)
        vector_str = "[" + ",".join(map(str, query_embedding)) + "]"

        exclude_clause = ""
        if exclude_trace_ids:
            ids_str = ",".join(f"'{tid}'" for tid in exclude_trace_ids)
            exclude_clause = f"AND trace_id NOT IN ({ids_str})"

        rows = db.client.execute(
            f"""
            SELECT
                trace_id,
                cosineDistance(embedding, {vector_str}) AS distance
            FROM {TRACE_INPUTS_TABLE}
            WHERE project_id = %(project_id)s
            AND has_issues = false
            {exclude_clause}
            ORDER BY distance ASC
            LIMIT {int(k)}
            """,
            {"project_id": project_id},
        )
        return [(str(tid), dist) for tid, dist in rows]
    finally:
        db.close()


def find_nearest_success_trace(
    query_embedding: List[float],
    project_id: str,
    exclude_trace_ids: Optional[List[str]] = None,
) -> Optional[Tuple[str, float]]:
    """
    KNN: find the nearest success trace (has_issues=False) to the query embedding.

    Returns (trace_id, distance) or None if no success traces exist.
    """
    rows = find_success_trace_baseline(
        query_embedding, project_id, k=1, exclude_trace_ids=exclude_trace_ids
    )
    return rows[0] if rows else None


def get_cluster_trace_embeddings(
    cluster_id: str,
    project_id: str,
) -> Optional[Tuple[str, List[float]]]:
    """
    Get the root input embedding for a representative trace in the cluster.

    Picks the first (oldest) trace linked to the cluster that has an embedding.
    Returns (trace_id, embedding) or None.
    """
    # Get trace IDs in this cluster. Session members have trace_id NULL —
    # interpolating None into the CH IN-clause is a UUID parse error.
    trace_ids = [
        str(tid)
        for tid in ErrorClusterTraces.objects.filter(
            cluster__cluster_id=cluster_id,
            cluster__project_id=project_id,
        ).values_list("trace_id", flat=True)
        if tid
    ]

    if not trace_ids:
        return None

    db = ClickHouseVectorDB()
    try:
        _ensure_trace_inputs_table(db)
        ids_str = ",".join(f"'{tid}'" for tid in trace_ids)
        rows = db.client.execute(
            f"""
            SELECT trace_id, embedding
            FROM {TRACE_INPUTS_TABLE}
            WHERE trace_id IN ({ids_str})
            AND project_id = %(project_id)s
            LIMIT 1
            """,
            {"project_id": project_id},
        )

        if rows:
            return str(rows[0][0]), list(rows[0][1])
        return None
    finally:
        db.close()
