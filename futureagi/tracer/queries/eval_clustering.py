"""
DB helpers for eval result clustering.

Mirrors scan_clustering.py — same online incremental approach but for
EvalLogger rows instead of TraceScanIssue rows.

Partition key: eval name (CustomEvalConfig.name) — clusters only form
within the same eval, never across different evals.
"""

import hashlib
import re
from typing import List, Optional, Tuple

import structlog
from django.db.models import Q
from django.utils import timezone

from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.embedding_manager import model_manager
from tracer.models.observation_span import EvalLogger
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    TraceErrorGroup,
)
from tracer.types.eval_cluster_types import ClusterableEvalResult

logger = structlog.get_logger(__name__)

CENTROIDS_TABLE = "cluster_centroids"  # shared with scanner — different family values
COSINE_THRESHOLD = 0.45


# ---------------------------------------------------------------------------
# Fetch unclustered failing eval results
# ---------------------------------------------------------------------------


def get_unclustered_eval_results(project_id: str) -> List[ClusterableEvalResult]:
    """
    Fetch EvalLogger rows that failed, have an explanation, and haven't
    been assigned to a cluster yet.

    "Failed" = output_bool is False OR output_float < 1.0.
    Skips rows with null eval_explanation (deterministic evals without reasoning).
    """
    # Already-clustered eval_logger IDs
    clustered_ids = set(
        ErrorClusterTraces.objects.filter(
            eval_logger__isnull=False,
            cluster__project_id=project_id,
        ).values_list("eval_logger_id", flat=True)
    )

    # PR3: target_type='span' keeps span-level and trace-level results from
    # being mixed in the same cluster. Trace evals are different semantic
    # units (one per trace, not per span) and clustering them with span-level
    # error themes would muddy the cluster centroids. Session evals have no
    # trace FK and would 404 the trace__project_id filter anyway.
    evals = (
        EvalLogger.objects.filter(
            trace__project_id=project_id,
            target_type="span",
            custom_eval_config__isnull=False,
        )
        .filter(
            Q(output_bool=False) | Q(output_float__lt=1.0),
        )
        .exclude(eval_explanation__isnull=True)
        .exclude(eval_explanation="")
        .select_related("custom_eval_config", "trace")
        .order_by("created_at")
    )

    results = []
    for ev in evals:
        if ev.id in clustered_ids:
            continue
        results.append(
            ClusterableEvalResult(
                eval_logger_id=str(ev.id),
                trace_id=str(ev.trace_id),
                project_id=project_id,
                eval_name=ev.custom_eval_config.name,
                eval_config_id=str(ev.custom_eval_config_id),
                explanation=ev.eval_explanation,
                score=ev.output_float,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Embedding (reuses scanner's embed_texts pattern)
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
# Centroid operations (shared ClickHouse table, eval-specific family)
# ---------------------------------------------------------------------------


def _ensure_centroid_table(db: ClickHouseVectorDB) -> None:
    """Ensure the cluster_centroids table exists (shared with scanner)."""
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


def _eval_family(eval_name: str) -> str:
    """Family key for eval centroids — prefixed to avoid collision with scanner families."""
    return f"eval:{eval_name}"


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
    eval_name: str,
) -> Optional[Tuple[str, float]]:
    """
    Find nearest cluster centroid for the given eval within threshold.

    Returns (cluster_id, distance) or None if no match.
    """
    db = ClickHouseVectorDB()
    try:
        _ensure_centroid_table(db)
        vector_str = "[" + ",".join(map(str, embedding)) + "]"
        family = _eval_family(eval_name)
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
            {"project_id": project_id, "family": family},
        )

        if rows and rows[0][1] < COSINE_THRESHOLD:
            return rows[0][0], rows[0][1]
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Impact from average score
# ---------------------------------------------------------------------------


def _score_to_impact(avg_score: Optional[float]) -> str:
    """Map average eval score to impact level for the cluster."""
    if avg_score is None:
        return "MEDIUM"
    if avg_score < 0.3:
        return "HIGH"
    if avg_score < 0.6:
        return "MEDIUM"
    return "LOW"


def _compute_cluster_impact(cluster: "TraceErrorGroup") -> str:
    """Compute impact from average output_float across all eval_loggers in cluster."""
    from django.db.models import Avg

    avg = ErrorClusterTraces.objects.filter(
        cluster=cluster,
        eval_logger__isnull=False,
        eval_logger__output_float__isnull=False,
    ).aggregate(avg_score=Avg("eval_logger__output_float"))["avg_score"]
    return _score_to_impact(avg)


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------


def _extract_title(explanation: str) -> str:
    """Extract first meaningful sentence from eval explanation for cluster title."""
    text = explanation.strip()
    # Split on sentence-ending punctuation followed by whitespace or end-of-string
    match = re.match(r"^(.+?[.!?])(?:\s|$)", text, re.DOTALL)
    if match:
        sentence = match.group(1).strip()
        if len(sentence) >= 15:
            return sentence[:200]
    # No clean sentence break — take up to first newline or 200 chars
    first_line = text.split("\n", 1)[0].strip()
    return first_line[:200] if first_line else text[:200]


# ---------------------------------------------------------------------------
# Cluster creation
# ---------------------------------------------------------------------------


def create_cluster(
    project_id: str,
    result: ClusterableEvalResult,
    embedding: List[float],
) -> str:
    """
    Create a new TraceErrorGroup cluster for an eval result + ClickHouse centroid.

    Returns the new cluster_id.
    """
    base = f"{project_id}|eval|{result.eval_name}|{result.explanation[:100]}"
    h = hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()[:8]
    cluster_id = f"E-{h.upper()}"

    # Handle collision
    if TraceErrorGroup.objects.filter(
        project_id=project_id, cluster_id=cluster_id
    ).exists():
        h2 = hashlib.md5(
            f"{base}|{result.eval_logger_id}".encode(), usedforsecurity=False
        ).hexdigest()[:8]
        cluster_id = f"E-{h2.upper()}"

    title = _extract_title(result.explanation)

    cluster = TraceErrorGroup.objects.create(
        project_id=project_id,
        cluster_id=cluster_id,
        source=ClusterSource.EVAL,
        issue_group=result.eval_name,
        issue_category=None,
        fix_layer=None,
        title=title,
        combined_description=result.explanation,
        combined_impact=_score_to_impact(result.score),
        status=FeedIssueStatus.ESCALATING,
        error_type=result.eval_name,
        eval_config_id=result.eval_config_id,
        total_events=1,
        unique_traces=1,
        error_count=1,
        first_seen=timezone.now(),
        last_seen=timezone.now(),
    )

    # Create junction entry
    ErrorClusterTraces.objects.create(
        cluster=cluster,
        trace_id=result.trace_id,
        eval_logger_id=result.eval_logger_id,
    )

    # Store centroid in ClickHouse
    family = _eval_family(result.eval_name)
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
                "family": family,
            },
        )
    finally:
        db.close()

    logger.info(
        "eval_cluster_created",
        cluster_id=cluster_id,
        eval_name=result.eval_name,
        title=title[:80],
    )
    return cluster_id


# ---------------------------------------------------------------------------
# Cluster assignment
# ---------------------------------------------------------------------------


def assign_to_cluster(
    cluster_id: str,
    project_id: str,
    result: ClusterableEvalResult,
    embedding: List[float],
) -> None:
    """Assign an eval result to an existing cluster and update centroid."""
    cluster = TraceErrorGroup.objects.get(cluster_id=cluster_id, project_id=project_id)

    cluster.error_count = (cluster.error_count or 0) + 1
    cluster.total_events = (cluster.total_events or 0) + 1
    cluster.last_seen = timezone.now()
    cluster.save(
        update_fields=["error_count", "total_events", "last_seen", "updated_at"]
    )

    # Create junction entry (ignore if trace already linked for this cluster)
    ErrorClusterTraces.objects.get_or_create(
        cluster=cluster,
        trace_id=result.trace_id,
        defaults={"eval_logger_id": result.eval_logger_id},
    )

    # Refresh unique traces count + recompute impact from avg score
    unique = cluster.clusters.values("trace").distinct().count()
    cluster.unique_traces = unique
    cluster.combined_impact = _compute_cluster_impact(cluster)
    cluster.save(update_fields=["unique_traces", "combined_impact", "updated_at"])

    # Incrementally update centroid in ClickHouse
    family = _eval_family(result.eval_name)
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
                "family": family,
            },
        )
    finally:
        db.close()

    logger.info(
        "eval_result_assigned_to_cluster",
        cluster_id=cluster_id,
        eval_logger_id=result.eval_logger_id,
    )
