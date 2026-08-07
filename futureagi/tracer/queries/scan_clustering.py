"""
DB helpers for scanner issue clustering + success trace matching.

Handles: embedding generation, centroid matching, cluster creation/update,
         trace input embedding storage, and KNN success trace search.
Uses ClickHouse for centroid storage + cosine similarity search.
Online incremental approach: each issue embedded → nearest centroid → assign or create.
"""

import hashlib
from typing import Callable, List, Optional, Tuple

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone

from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.embedding_manager import model_manager
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    TraceErrorGroup,
)
from tracer.models.trace_scan import TraceScanIssue, TraceScanResult
from tracer.services.clickhouse.clustering_tables import (
    CENTROIDS_TABLE,
    TRACE_INPUTS_TABLE,
    ensure_centroid_table,
    ensure_trace_inputs_table,
)
from tracer.services.clickhouse.v2 import get_reader
from tracer.types.scan_types import ClusterableIssue, TraceInputData

logger = structlog.get_logger(__name__)

# Cosine distance: 0 = identical, 2 = opposite.
#
# 0.40 was tried and reverted. The reasoning was that dropping
# PARTITION_BY_CATEGORY (below) loosened the effective constraint — 0.45 had
# been an *intra-category* bound, and without the partition the same number
# admits a strictly larger candidate set — so tightening should shed the ~15%
# of merges a per-issue audit found wrong while keeping the rest.
#
# Measured on the 203-issue corpus, it does not:
#
#   0.45, partition ON   91 clusters, 60 singletons   (pre-fix-4)
#   0.45, partition OFF  74 clusters, 43 singletons   (what fix 4 buys)
#   0.40, partition OFF  91 clusters, 62 singletons   (identical to pre-fix-4)
#
# The good merges do NOT sit well inside 0.40 — almost all 17 live between
# 0.40 and 0.45, so tightening keeps only the two tightest (the period-mismatch
# and empty-generation merges) and gives back everything else. 0.40 buys
# precision by surrendering essentially all the recall fix 4 exists for.
#
# The ~15% bad-merge rate is therefore the standing price of fix 4 at this
# threshold, not something a threshold tweak removes. Reducing it needs a
# different mechanism (a category-agreement penalty folded into the distance
# rather than a hard filter, per the original audit proposal).
COSINE_THRESHOLD = 0.45

# Whether centroid matching is confined to the issue's own category.
#
# It used to be, unconditionally, which made a whole class of merge impossible
# rather than merely unlikely: the scanner picks the category, and one bug
# described two ways lands in two categories. On the post-fix-5 corpus this is
# visible directly — the SAME defect sits in two clusters purely because the
# categories differ:
#
#   S-232AA3EB  Context Handling Failures     15  "Queried past month instead of
#                                                  requested quarterly performance"
#   S-15263D7F  Poor Information Retrieval     5  "Fetched 1-month performance data
#                                                  instead of requested quarterly numbers"
#
# and likewise the hallucinated-holding bug across Poor Information Retrieval and
# Tool Output Misinterpretation. No embedding could ever merge those.
#
# An earlier attempt at this was measured on the pre-fix-5 corpus and showed no
# benefit — but that corpus was ~78% fabricated briefs, so the ground truth it
# was scored against was noise. Kept as a switch so it can be reverted without a
# code change if cross-category merging proves too loose.
PARTITION_BY_CATEGORY = False


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
        ensure_centroid_table(db)
        vector_str = "[" + ",".join(map(str, embedding)) + "]"
        family_clause = "AND family = %(family)s" if PARTITION_BY_CATEGORY else ""
        # ``cluster_centroids`` is a ReplacingMergeTree and assign_to_cluster
        # INSERTs a new row per member rather than updating, so every historical
        # version of a centroid coexists until a background merge collapses them.
        # Reading the table directly therefore compares the query vector against
        # stale centroids as well as current ones, and which one wins is decided
        # by whichever happens to be nearest — not by recency.
        #
        # ``LIMIT 1 BY cluster_id`` over an explicit recency ordering collapses
        # each cluster to its current centroid before the distance sort. This is
        # preferred over FINAL: it needs no schema change, and member_count
        # breaks ties that last_updated cannot, because that column is
        # second-granularity and two updates to one cluster inside the same
        # second are otherwise unordered.
        rows = db.client.execute(
            f"""
            SELECT
                cluster_id,
                cosineDistance(centroid, {vector_str}) AS distance
            FROM (
                SELECT cluster_id, centroid
                FROM {CENTROIDS_TABLE}
                WHERE project_id = %(project_id)s
                {family_clause}
                ORDER BY last_updated DESC, member_count DESC
                LIMIT 1 BY cluster_id
            )
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
    on_join: Optional[Callable[[], None]] = None,
) -> str:
    """
    Create a new TraceErrorGroup cluster + ClickHouse centroid.

    Returns the cluster_id — which may be an EXISTING cluster's, when the issue
    turns out to be a repeat that the centroid lookup failed to match. Callers
    that report create-vs-assign counts should pass ``on_join``; it fires when
    this call joined an existing cluster rather than creating one, so a missed
    centroid lookup is not miscounted as a new cluster. The return type stays a
    plain str so existing callers and their test doubles keep working.
    """
    # Stable cluster ID from project + category + brief prefix
    base = f"{project_id}|scanner|{issue.category}|{issue.brief[:100]}"
    h = hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()[:8]
    cluster_id = f"S-{h.upper()}"

    # A row already under this ID is almost always the SAME failure arriving
    # again, not an md5 collision: the ID hashes (project, category, brief), so
    # a repeat brief repeats the hash by construction. We land here when the
    # centroid lookup missed a cluster it should have matched — cluster_centroids
    # is a ReplacingMergeTree read without FINAL, so a just-written centroid is
    # not reliably visible. Minting a fresh ID here is what produced pairs of
    # clusters with byte-identical titles seconds apart; join the existing one
    # instead. Only a row whose (category, title) actually DIFFERS is a real
    # hash collision and still needs a distinct ID — note the ID hashes
    # brief[:100] while title holds the full brief, so two briefs sharing a
    # 100-char prefix legitimately reach this branch.
    existing = TraceErrorGroup.objects.filter(
        project_id=project_id, cluster_id=cluster_id
    ).first()
    if existing is not None:
        if (existing.issue_category, existing.title) == (issue.category, issue.brief):
            logger.info(
                "cluster_join_on_existing_id",
                cluster_id=cluster_id,
                issue_id=issue.issue_id,
                reason="centroid_lookup_missed",
            )
            assign_to_cluster(cluster_id, project_id, issue, embedding)
            if on_join is not None:
                on_join()
            return cluster_id

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

    try:
        # Savepoint so a unique-constraint violation here doesn't poison the
        # surrounding transaction/connection (mirrors the eval path).
        with transaction.atomic():
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
            # Fix 7: a brand-new cluster has one trace and therefore no trend.
            # It becomes ESCALATING only once _ESCALATING_MIN_TRACES is reached.
            if cluster.status != FeedIssueStatus.FOR_REVIEW:
                cluster.status = FeedIssueStatus.FOR_REVIEW
                cluster.save(update_fields=["status", "updated_at"])
    except IntegrityError:
        # A concurrent run created this cluster between the lookup above and
        # this insert (unique_project_cluster_if_not_deleted). Without this the
        # exception escapes to cluster_issues' blanket handler and the issue is
        # silently dropped — never clustered, never retried. Treat it as an
        # assignment so the trace is still linked and the centroid still moves.
        logger.info(
            "scan_cluster_create_race_assigning",
            cluster_id=cluster_id,
            issue_id=issue.issue_id,
        )
        assign_to_cluster(cluster_id, project_id, issue, embedding)
        return cluster_id

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


def delete_centroid(cluster_id: str, project_id: str) -> None:
    """Drop a cluster's centroid rows.

    Centroids outlive their TraceErrorGroup: nothing removes them when a cluster
    is deleted, so the store accumulates rows pointing at clusters that no longer
    exist. find_nearest_centroid will happily match one of those, and the
    subsequent assign_to_cluster raises DoesNotExist — which cluster_issues
    swallows, dropping the issue silently. Callers use this to self-heal when
    they discover an orphan.
    """
    db = ClickHouseVectorDB()
    try:
        ensure_centroid_table(db)
        db.client.execute(
            f"ALTER TABLE {CENTROIDS_TABLE} DELETE "
            f"WHERE cluster_id = %(cluster_id)s AND project_id = %(project_id)s",
            {"cluster_id": cluster_id, "project_id": project_id},
        )
        logger.info("centroid_deleted", cluster_id=cluster_id)
    finally:
        db.close()


# Cluster sizes at which the title is recomputed from members. A cluster's title
# is otherwise whichever brief happened to arrive FIRST, which has no claim to
# being representative — a 15-trace cluster ends up named after one accidental
# member, which is the "the heading doesn't describe what's inside" complaint.
# Recomputing on every assignment would re-embed the whole cluster each time, so
# it happens at a handful of growth points instead.
_RETITLE_AT = (2, 5, 10, 25, 50, 100, 250)


def _cosine_distance(a: List[float], b: List[float]) -> float:
    """1 - cosine similarity. Mirrors ClickHouse's cosineDistance."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 1.0
    return 1.0 - dot / (na * nb)


def _retitle_from_members(cluster, centroid: List[float]) -> None:
    """Rename a cluster after its MEDOID — the member nearest the centroid.

    First-arrival titling has no relationship to what a cluster contains; the
    medoid is the most typical member by construction, so the title describes the
    group rather than an accident of ordering. Best-effort: any failure leaves
    the existing title alone, since a stale title beats a broken assignment.
    """
    briefs = list(
        TraceScanIssue.objects.filter(cluster=cluster)
        .exclude(brief="")
        .values_list("brief", flat=True)
    )
    if len(briefs) < 2:
        return
    try:
        vectors = embed_texts(briefs)
    except Exception:
        logger.warning("retitle_embed_failed", cluster_id=cluster.cluster_id)
        return

    best_brief, best_distance = None, None
    for brief, vector in zip(briefs, vectors):
        distance = _cosine_distance(vector, centroid)
        if best_distance is None or distance < best_distance:
            best_brief, best_distance = brief, distance

    # The medoid is the most TYPICAL member, which is not the same as a good
    # group title: when every member names its own ticker, the most central one
    # names a ticker too. Measured on 30 real clusters, medoid titling changed 18
    # of them but several swapped one entity for another rather than generalising
    # ("Coca-Cola" -> "MSFT"). A generated title is asked to describe what the
    # members SHARE with no entity at all; it falls back to the medoid whenever
    # it is unavailable, declines, or still leaks an entity.
    from tracer.ee_boundary import generate_scan_cluster_title

    generated = generate_scan_cluster_title(briefs)
    if generated:
        best_brief = generated

    if best_brief and best_brief != cluster.title:
        previous = cluster.title
        cluster.title = best_brief
        cluster.save(update_fields=["title", "updated_at"])
        logger.info(
            "cluster_retitled_to_medoid",
            cluster_id=cluster.cluster_id,
            members=len(briefs),
            distance=round(best_distance, 4),
            previous_title=(previous or "")[:80],
            new_title=best_brief[:80],
        )


def _volume_floor(traces: int) -> str:
    """The severity a cluster earns from breadth alone."""
    if traces >= 25:
        return "high"
    if traces >= 5:
        return "medium"
    return "low"


def _refresh_severity(cluster) -> None:
    """Fix 6 — re-derive severity from the cluster, not from its seed issue.

    Severity used to be one cheap-LLM call on whichever issue happened to create
    the cluster, frozen forever: a 126-trace cluster kept the grade its single
    seed trace drew, and 72% of production clusters ended up high/critical.

    Two changes. The classifier now reads the cluster's CURRENT title, which by
    this point describes the group rather than one arbitrary member. And volume
    is applied as a floor afterwards: a defect reproducing across many traces is
    at least a medium regardless of how the text reads, because breadth is
    evidence the text alone cannot carry.
    """
    from tracer.queries.feed import severity_to_priority

    severity = _seed_severity(cluster.issue_category or "", cluster.title or "")
    if severity is None:
        return

    order = ["low", "medium", "high", "critical"]
    traces = cluster.unique_traces or 0
    floor = _volume_floor(traces)
    if order.index(floor) > order.index(severity):
        severity = floor

    impact, priority = _severity_to_impact(severity), severity_to_priority(severity)
    if (cluster.combined_impact, cluster.priority) == (impact, priority):
        return
    previous = cluster.priority
    cluster.combined_impact, cluster.priority = impact, priority
    cluster.save(update_fields=["combined_impact", "priority", "updated_at"])
    logger.info(
        "cluster_severity_refreshed",
        cluster_id=cluster.cluster_id,
        traces=traces,
        previous=previous,
        new=priority,
    )


# Below this many distinct traces a cluster has no trend to speak of, so calling
# it "escalating" is a decoration rather than a claim. 234 of 235 production
# clusters carried that status, including 138 singletons seen exactly once.
_ESCALATING_MIN_TRACES = 5


def _refresh_status(cluster) -> None:
    """Fix 7 — only claim "escalating" when there is evidence for it.

    Status was hardcoded to ESCALATING at creation and never recomputed; nothing
    in tracer/ transitioned it automatically. This does not invent a trend model
    — it applies the floor that makes the existing label honest, and leaves any
    status a human has since set (acknowledged/resolved) alone.
    """
    if cluster.status in (FeedIssueStatus.ACKNOWLEDGED, FeedIssueStatus.RESOLVED):
        return  # a person has triaged this; never overwrite that

    traces = cluster.unique_traces or 0
    target = (
        FeedIssueStatus.ESCALATING
        if traces >= _ESCALATING_MIN_TRACES
        else FeedIssueStatus.FOR_REVIEW
    )
    if cluster.status == target:
        return
    previous = cluster.status
    cluster.status = target
    cluster.save(update_fields=["status", "updated_at"])
    logger.info(
        "cluster_status_refreshed",
        cluster_id=cluster.cluster_id,
        traces=traces,
        previous=previous,
        new=target,
    )


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

    # Re-title from the members at growth points, so a cluster stops being named
    # after whichever brief arrived first. Never allowed to break the assignment.
    if unique in _RETITLE_AT:
        try:
            previous_title = cluster.title
            _retitle_from_members(cluster, new_centroid)
            # Severity reads the refreshed title, so it must run after it —
            # but ONLY when one of its two inputs actually moved.
            #
            # The grader is not reproducible: re-running the same corpus flipped
            # 6 of 91 grades, and 4 of those had byte-identical titles. That is
            # the serving layer, not sampling — temperature is already 0.0 and
            # neither Bedrock nor Vertex honours a seed. Re-asking an unchanged
            # question therefore does not refine the answer, it re-rolls it, and
            # the feed churns for free. Asking only when the question changed
            # removes the churn (and the wasted call) without pretending the
            # model is deterministic.
            if cluster.title != previous_title or _volume_floor(
                unique
            ) != _volume_floor(unique - 1):
                _refresh_severity(cluster)
        except Exception:
            logger.warning(
                "cluster_retitle_failed", cluster_id=cluster_id, exc_info=True
            )

    # Status depends only on trace count, so it is checked on every assignment —
    # a cluster crossing the threshold should stop saying "for review" promptly.
    try:
        _refresh_status(cluster)
    except Exception:
        logger.warning("cluster_status_failed", cluster_id=cluster_id, exc_info=True)

    logger.info(
        "issue_assigned_to_cluster",
        cluster_id=cluster_id,
        issue_id=issue.issue_id,
    )


# ---------------------------------------------------------------------------
# Trace input embeddings (for success trace matching)
# ---------------------------------------------------------------------------


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

    # The CH root span's ``input.value`` is the only source post-cutover; a trace
    # with no root input simply contributes no text (handled below).

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
        ensure_trace_inputs_table(db)
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
        ensure_trace_inputs_table(db)
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
        ensure_trace_inputs_table(db)
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
