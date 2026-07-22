"""
DB helpers for eval result clustering.

Mirrors scan_clustering.py — same online incremental approach but for
EvalLogger rows instead of TraceScanIssue rows.

Partition key: eval name (CustomEvalConfig.name) — clusters only form
within the same eval, never across different evals.
"""

import hashlib
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import structlog
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.embedding_manager import model_manager
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    TraceErrorGroup,
)
from tracer.services.clickhouse.clustering_tables import (
    CENTROIDS_TABLE,
    ensure_centroid_table,
)
from tracer.types.eval_cluster_types import ClusterableEvalResult, EvalClusterMeta

logger = structlog.get_logger(__name__)

COSINE_THRESHOLD = 0.45

# Only cluster recent eval failures — old results aren't actionable and
# bound the per-run work unit.
_CLUSTER_WINDOW_DAYS = 60


# ---------------------------------------------------------------------------
# Fetch unclustered failing eval results
# ---------------------------------------------------------------------------


def _project_session_eval_ids(
    project_id: str, since: datetime, base_filters: Q
) -> set[str]:
    """``trace_session_id``s of session-target eval rows that belong to
    ``project_id`` — resolved through ClickHouse, not a PG FK join.

    Session evals anchor to a ``trace_session`` whose FK is unenforced
    (``db_constraint=False``) and whose PG ``TraceSession`` row is absent for
    CH-only sessions, so ``Q(trace_session__project_id=...)`` INNER-JOINs to
    nothing and silently drops every CH-only session eval. Session membership
    is a span fact, so we read it from CH instead: the candidate eval rows'
    session ids intersected with the project's live session ids, scoped through
    ``distinct_session_ids_with_filters`` (which resolves new→old internally so
    a candidate id matches regardless of cutover side).

    For CH-only (net-new) sessions — the case this fix targets — no id-remap row
    exists, so the candidate id and the id CH returns are byte-identical and a
    plain set intersection is exact. Returns the subset of candidate ids that
    belong to the project, in the id space the eval rows store.

    A CH read failure propagates rather than fail-open: an empty set would
    wrongly admit no session evals, and the clustering activity is idempotently
    retried, so starving one run is safer than silently mis-scoping.
    """
    from tracer.services.clickhouse.v2 import get_reader

    candidate_ids = list(
        EvalLogger.objects.filter(
            base_filters,
            target_type=EvalTargetType.SESSION,
            trace_session_id__isnull=False,
            created_at__gte=since,
        )
        .values_list("trace_session_id", flat=True)
        .distinct()
    )
    if not candidate_ids:
        return set()

    candidate_strs = {str(c) for c in candidate_ids}
    with get_reader() as reader:
        in_project = set(
            reader.distinct_session_ids_with_filters(
                project_id=str(project_id), session_id=list(candidate_strs)
            )
        )
    return candidate_strs & in_project


def get_unclustered_eval_results(
    project_id: str, limit: Optional[int] = None
) -> List[ClusterableEvalResult]:
    """
    Fetch EvalLogger rows that failed, have an explanation, and haven't
    been assigned to a cluster yet.

    "Failed" = output_bool is False OR output_float < 1.0.
    Skips rows with null eval_explanation (deterministic evals without reasoning).
    Only the last _CLUSTER_WINDOW_DAYS of results are considered.

    ``limit`` bounds the returned batch (oldest-first). The caller drains a
    large backlog over successive bounded runs so a single clustering
    activity can never grow unbounded and time out.
    """
    # Already-clustered eval_logger IDs
    clustered_ids = set(
        ErrorClusterTraces.objects.filter(
            eval_logger__isnull=False,
            cluster__project_id=project_id,
        ).values_list("eval_logger_id", flat=True)
    )

    since = timezone.now() - timedelta(days=_CLUSTER_WINDOW_DAYS)
    # Failure + explanation filters shared by both the span/trace branch and the
    # session-membership pre-pass, so a candidate session id can't enter the CH
    # set unless it would also survive the main filter.
    base_filters = (
        Q(custom_eval_config__isnull=False)
        & (Q(output_bool=False) | Q(output_float__lt=1.0))
        & ~Q(eval_explanation__isnull=True)
        & ~Q(eval_explanation="")
    )

    # All three eval targets cluster, but never into the SAME cluster — the
    # centroid family is keyed by (target_type, eval_name) downstream, so
    # span / trace / session results form separate, homogeneous clusters. The
    # targets reach their project two different ways: span/trace results anchor
    # to a trace (scoped by the PG ``trace__project_id`` FK), session results
    # anchor to a ``trace_session`` whose project we resolve through CH (the FK
    # is unenforced and absent for CH-only sessions — see
    # ``_project_session_eval_ids``).
    session_eval_ids = _project_session_eval_ids(project_id, since, base_filters)

    evals = (
        EvalLogger.objects.filter(
            base_filters,
            Q(trace__project_id=project_id)
            | Q(trace_session_id__in=session_eval_ids),
            created_at__gte=since,
        )
        .select_related("custom_eval_config", "trace", "trace_session")
        .order_by("created_at")
    )

    results: List[ClusterableEvalResult] = []
    # .iterator() so a huge backlog isn't all loaded into memory just to
    # stop early once `limit` unclustered rows have been collected.
    for ev in evals.iterator(chunk_size=2000):
        if ev.id in clustered_ids:
            continue
        results.append(
            ClusterableEvalResult(
                eval_logger_id=str(ev.id),
                project_id=project_id,
                eval_name=ev.custom_eval_config.name,
                eval_config_id=str(ev.custom_eval_config_id),
                explanation=ev.eval_explanation,
                target_type=ev.target_type,
                trace_id=str(ev.trace_id) if ev.trace_id else None,
                session_id=(
                    str(ev.trace_session_id) if ev.trace_session_id else None
                ),
                score=ev.output_float,
            )
        )
        if limit is not None and len(results) >= limit:
            break

    return results


# ---------------------------------------------------------------------------
# Embedding (reuses scanner's embed_texts pattern)
# ---------------------------------------------------------------------------


_EMBED_BATCH_SIZE = 64


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed texts via the serving client in bounded batches.

    Chunked rather than per-row or all-at-once: one request carries up to
    _EMBED_BATCH_SIZE texts (the single-worker serving process does one
    batched forward pass instead of N round-trips), and a failed chunk only
    costs re-embedding that chunk on the next idempotent clustering sweep —
    bounded blast radius, which is the fault-isolation the old per-row
    enqueue was reaching for, without the fan-out that overran serving.
    """
    if not texts:
        return []

    try:
        client = model_manager.serving_client
    except Exception:
        client = None

    if client is None:
        # Serving unavailable — preserve the previous per-item behaviour.
        text_embed = model_manager.text_model
        return [text_embed(t) for t in texts]

    embeddings: List[List[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        chunk = texts[start : start + _EMBED_BATCH_SIZE]
        try:
            embeddings.extend(client.embed_text_batch(chunk))
        except Exception:
            # One bad chunk must not fail the whole project sweep — fall
            # back to per-item for this chunk only.
            logger.warning(
                "embed_batch_fallback_per_item",
                chunk_start=start,
                chunk_size=len(chunk),
                exc_info=True,
            )
            text_embed = model_manager.text_model
            embeddings.extend(text_embed(t) for t in chunk)
    return embeddings


# ---------------------------------------------------------------------------
# Centroid operations (shared ClickHouse table, eval-specific family)
# ---------------------------------------------------------------------------


def _eval_family(eval_name: str, target_type: str = "span") -> str:
    """Family key for eval centroids — keeps the three targets (and scanner)
    in separate centroid spaces.

    span keeps the legacy unprefixed ``eval:{name}`` key on purpose: every
    centroid created before trace/session targets existed was span-level, so
    leaving span unprefixed means those existing centroids keep matching with
    no ClickHouse backfill. trace/session get explicit prefixes.
    """
    if target_type == "span":
        return f"eval:{eval_name}"
    return f"eval:{target_type}:{eval_name}"


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
    target_type: str = "span",
) -> Optional[Tuple[str, float]]:
    """
    Find nearest cluster centroid for the given eval within threshold.

    Scoped to the (target_type, eval_name) family so a span result can never
    match a trace/session cluster. Returns (cluster_id, distance) or None.
    """
    db = ClickHouseVectorDB()
    try:
        ensure_centroid_table(db)
        vector_str = "[" + ",".join(map(str, embedding)) + "]"
        family = _eval_family(eval_name, target_type)
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


def _eval_cluster_meta(eval_name: str, reasoning: str) -> EvalClusterMeta:
    """Title + fix_layer + severity for an eval cluster via the cheap-LLM
    EE helper, with deterministic fallback.

    EE absent (OSS) or any LLM failure → first-sentence title, null
    fix_layer, null severity (caller defaults priority). Each field
    degrades independently; metadata is best-effort and must never break
    cluster creation.
    """
    from tracer.ee_boundary import generate_eval_cluster_meta

    fallback = EvalClusterMeta(title=_extract_title(reasoning))
    meta = generate_eval_cluster_meta(eval_name, reasoning)
    if not meta:
        return fallback
    return EvalClusterMeta(
        title=meta.title or _extract_title(reasoning),
        fix_layer=meta.fix_layer,
        severity=meta.severity,
    )


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
    base = (
        f"{project_id}|eval|{result.target_type}|{result.eval_name}"
        f"|{result.explanation[:100]}"
    )
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

    meta = _eval_cluster_meta(result.eval_name, result.explanation)
    # Lazy import avoids a query-module import cycle; severity_to_priority
    # returns "medium" when severity is None (the fallback default).
    from tracer.queries.feed import severity_to_priority

    try:
        # Savepoint so a unique-constraint violation here doesn't poison the
        # surrounding transaction/connection.
        with transaction.atomic():
            cluster = TraceErrorGroup.objects.create(
                project_id=project_id,
                cluster_id=cluster_id,
                source=ClusterSource.EVAL,
                eval_target_type=result.target_type,
                issue_group=result.eval_name,
                issue_category=None,
                fix_layer=meta.fix_layer,
                title=meta.title,
                combined_description=result.explanation,
                combined_impact=_score_to_impact(result.score),
                status=FeedIssueStatus.ESCALATING,
                priority=severity_to_priority(meta.severity),
                error_type=result.eval_name,
                eval_config_id=result.eval_config_id,
                total_events=1,
                unique_traces=1,
                error_count=1,
                first_seen=timezone.now(),
                last_seen=timezone.now(),
            )
    except IntegrityError:
        # Another concurrent run created this cluster between our .exists()
        # check and the insert (unique_project_cluster_if_not_deleted). Treat
        # this as an assignment so the trace is still linked and the centroid
        # still updated.
        logger.info(
            "eval_cluster_create_race_assigning",
            cluster_id=cluster_id,
            eval_logger_id=result.eval_logger_id,
        )
        assign_to_cluster(cluster_id, project_id, result, embedding)
        return cluster_id

    # Create junction entry. Session evals anchor to the session (trace NULL);
    # span/trace evals anchor to the trace.
    ErrorClusterTraces.objects.create(
        cluster=cluster,
        trace_id=result.trace_id,
        trace_session_id=result.session_id,
        eval_logger_id=result.eval_logger_id,
    )

    # Store centroid in ClickHouse
    family = _eval_family(result.eval_name, result.target_type)
    db = ClickHouseVectorDB()
    try:
        ensure_centroid_table(db)
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
        title=(meta.title or "")[:80],
        fix_layer=meta.fix_layer,
        severity=meta.severity,
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

    # Create junction entry, keyed on the membership unit so a re-run can't
    # double-link. Session evals dedup on (cluster, trace_session) — a real
    # unique constraint, so get_or_create is safe. Trace/span evals can't use
    # get_or_create: the unique key is (cluster, trace, span) with span left
    # NULL here, so duplicate (cluster, trace) rows accumulate and
    # get_or_create's internal .get() raises MultipleObjectsReturned — guard
    # with exists()+create instead.
    if result.target_type == "session":
        ErrorClusterTraces.objects.get_or_create(
            cluster=cluster,
            trace_session_id=result.session_id,
            defaults={"eval_logger_id": result.eval_logger_id},
        )
    elif not ErrorClusterTraces.objects.filter(
        cluster=cluster, trace_id=result.trace_id
    ).exists():
        ErrorClusterTraces.objects.create(
            cluster=cluster,
            trace_id=result.trace_id,
            eval_logger_id=result.eval_logger_id,
        )

    # Refresh the unique-membership count + recompute impact from avg score.
    # The unit is sessions for session clusters, traces otherwise.
    if result.target_type == "session":
        unique = cluster.clusters.values("trace_session").distinct().count()
    else:
        unique = cluster.clusters.values("trace").distinct().count()
    cluster.unique_traces = unique
    cluster.combined_impact = _compute_cluster_impact(cluster)
    cluster.save(update_fields=["unique_traces", "combined_impact", "updated_at"])

    # Incrementally update centroid in ClickHouse
    family = _eval_family(result.eval_name, result.target_type)
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
