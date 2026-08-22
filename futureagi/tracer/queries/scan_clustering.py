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
from tracer.utils.semantic_conventions import get_attribute

logger = structlog.get_logger(__name__)

# Cosine distance: 0 = identical, 2 = opposite.
#
# Left at 0.45. This was briefly tightened to 0.40 on the argument that the
# threshold and PARTITION_BY_CATEGORY below are coupled — that with the
# partition ON, 0.45 is an *intra-category* bound whose candidates have already
# cleared a categorical filter, so restoring the partition loosens nothing only
# if the threshold tightens with it. A full 2x2 over 3 arrival orders falsified
# the coupling: the two effects are roughly additive, and the threshold's own
# contribution is +6.5pp coherence, 95% CI [-5.2, +18.0] — not distinguishable
# from zero. Tightening also splits more, and the fragmentation it costs is
# likewise unmeasurable at this sample size.
#
# So there is no evidence for moving the incumbent, and a constant that changes
# every user's feed needs evidence. Revisit only with a purity AND fragmentation
# reading, since coherence alone is maximised by all-singletons.
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
# That argument is now mostly spent, because distilling the brief before
# embedding fixes the same problem at its source: the phrase is stable, so what
# is left wavering is only the scanner's category choice, and it wavers on 1.2%
# of distinct phrases / 4.5% of issues.
#
# Turned back ON after measuring it on 792 issues from 62 tenants, re-scanned
# with the current scanner (the earlier corpus this was argued from had briefs
# that largely did not describe their own trace, so its numbers said nothing).
# Replayed per project, as assign_to_cluster runs, over 3 arrival orders, with
# within-entry pairs judged "would ONE fix resolve both?":
#
#   partition ON - OFF   +11.4pp coherence, 95% CI [+0.2, +22.4]
#
# The mechanism is visible without a judge: without the partition a single head
# entry absorbs several categories at once, filing a formatting complaint with a
# fabricated-data bug. The partition also makes assignment far more reproducible
# — largest entry varies by ~2 members across arrival orders with it, by ~60
# without, i.e. two tenants with identical data ingested in a different order
# would otherwise see materially different feeds.
#
# Cost is a handful of small stray fragments where the scanner picked different
# categories for one bug. Kept as a switch: if the strays ever outweigh the
# mixing, this reverts without a code change.
PARTITION_BY_CATEGORY = True


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


def _retitle_from_members(cluster) -> None:
    """Rename a cluster after its MEDOID — the member nearest the members' mean.

    First-arrival titling has no relationship to what a cluster contains; the
    medoid is the most typical member by construction, so the title describes the
    group rather than an accident of ordering. Best-effort: any failure leaves
    the existing title alone, since a stale title beats a broken assignment.

    The mean is computed HERE, over these raw briefs, rather than taken from the
    stored centroid. The centroid is built from ``embedding_text``, which is the
    DISTILLED phrase; measuring a raw brief against it compares two different
    embedding spaces and the resulting "nearest" member is near-arbitrary. The
    title has to read like something a person wrote, so it must come from a raw
    brief — which means the distance that selects it has to be computed among
    raw briefs too.
    """
    # Ordered, because two things downstream depend on it and neither is
    # allowed to vary between runs: ties in the medoid distance are broken by
    # position, and the title generator samples this list evenly to cover the
    # whole group. An unordered read makes a cluster's title a coin flip.
    briefs = list(
        TraceScanIssue.objects.filter(cluster=cluster)
        .exclude(brief="")
        .order_by("created_at", "id")
        .values_list("brief", flat=True)
    )
    if len(briefs) < 2:
        return
    try:
        vectors = embed_texts(briefs)
    except Exception:
        logger.warning("retitle_embed_failed", cluster_id=cluster.cluster_id)
        return

    dim = len(vectors[0])
    centroid = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]

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




def assign_to_cluster(
    cluster_id: str,
    project_id: str,
    issue: ClusterableIssue,
    embedding: List[float],
) -> None:
    """Assign an issue to an existing cluster and update centroid incrementally."""
    cluster = TraceErrorGroup.objects.get(cluster_id=cluster_id, project_id=project_id)

    # Link issue → cluster. Both membership writes are idempotent: re-running the
    # scan over a trace updates the issue row it already owns, and matches the
    # junction row it already has.
    TraceScanIssue.objects.filter(id=issue.issue_id).update(cluster=cluster)
    ErrorClusterTraces.objects.get_or_create(
        cluster=cluster,
        trace_id=issue.trace_id,
        defaults={"scan_issue_id": issue.issue_id},
    )

    # Both membership counts are DERIVED from the rows above, never incremented.
    # An increment is not idempotent while the writes it counts are, so a re-scan
    # inflated the "occurrences" the Feed renders without adding any member —
    # measured at up to 11x on a fifth of live entries, beside a trace count that
    # stayed correct precisely because it was already recomputed here.
    cluster.error_count = TraceScanIssue.objects.filter(
        cluster=cluster, deleted=False
    ).count()
    unique = cluster.clusters.values("trace").distinct().count()
    cluster.unique_traces = unique
    # total_events is a lifetime counter and is not rendered in the feed row.
    cluster.total_events = (cluster.total_events or 0) + 1
    cluster.last_seen = timezone.now()
    cluster.save(
        update_fields=[
            "error_count",
            "total_events",
            "unique_traces",
            "last_seen",
            "updated_at",
        ]
    )

    # Incrementally update centroid in ClickHouse
    db = ClickHouseVectorDB()
    try:
        # Same ReplacingMergeTree hazard ``find_nearest_cluster`` documents above, on
        # the write side: this function INSERTs a new row per member, so every
        # historical version of a centroid coexists until a background merge. A bare
        # ``LIMIT 1`` therefore reads an arbitrary version — and because
        # ``member_count`` is the weight in ``_update_centroid``, a stale count does
        # not merely skip an update, it mis-weights every later one.
        #
        # argMax needs no schema change and no FINAL. The key is a tuple because
        # ``last_updated`` is second-granularity and two members joining one cluster
        # inside the same second are otherwise unordered; ``member_count`` grows
        # monotonically, so it orders them correctly. GROUP BY is load-bearing: an
        # aggregate without it always returns one row, so a cluster with no stored
        # centroid would come back as empty defaults instead of no rows.
        rows = db.client.execute(
            f"""
            SELECT
                argMax(centroid, (last_updated, member_count)),
                argMax(member_count, (last_updated, member_count))
            FROM {CENTROIDS_TABLE}
            WHERE project_id = %(project_id)s AND cluster_id = %(cluster_id)s
            GROUP BY cluster_id
            """,
            {"project_id": project_id, "cluster_id": cluster_id},
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
            _retitle_from_members(cluster)
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

    Root span = span with no parent_span_id. Input text comes from the root
    span's normalised ``input`` column, with the aliased input attribute as a
    fallback.
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
        # Read the normalised ``input`` COLUMN, not one convention's raw
        # attribute. The collector resolves input across every convention it
        # accepts and writes the result to that column; reading
        # ``attrs_string["input.value"]`` only ever matched producers emitting
        # the OpenInference key, which in practice was none of them — so this
        # returned an empty list for every project, no root-input embedding was
        # ever stored, and success-trace matching silently produced nothing.
        # The aliased attribute stays as a fallback for rows whose column
        # predates the extraction.
        input_text = span.input or get_attribute(
            span.attrs_string or {}, "input_value", ""
        )
        if input_text:
            input_texts[trace_id_str] = str(input_text)

    # A trace with no root input simply contributes no text (handled below).

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


# ---------------------------------------------------------------------------
# Duplicate-cluster merge pass
# ---------------------------------------------------------------------------
# assign_to_cluster is online and incremental: an issue joins the nearest centroid
# or seeds a new cluster, and there is NO merge step. Two consequences, both measured
# on 261 real production issue briefs replayed through the real assignment loop:
#
#   * ORDER DEPENDENCE — the same issues in a different arrival order produce a
#     different clustering (pairwise ARI 0.796 across 20 orderings, min 0.673). About
#     a fifth of the grouping a user sees is decided by arrival order, not content.
#   * DUPLICATES THAT CAN NEVER HEAL — 7 groups were split across 14 clusters, i.e.
#     14% of the feed, with pairs as obviously identical as "Repeated same call log
#     chain 12 times in redundant retries" and "Retried identical chain execution 11
#     times producing empty outputs" shown as two separate entries.
#
# A full re-cluster would fix both but reassigns every issue and churns every cluster
# id under live users. This is the smaller change that targets the measured defect:
# a periodic pass that merges clusters whose CENTROIDS are already within threshold.
# It needs no re-embedding (centroids are in ClickHouse), touches only genuine
# duplicates, and moves the grouping toward the order-invariant answer over time.


_TRIAGED = (FeedIssueStatus.ACKNOWLEDGED, FeedIssueStatus.RESOLVED)


def _merge_cluster_pair(keep_id: str, absorb_id: str, project_id: str) -> bool:
    """Fold ``absorb`` into ``keep``: issues, junction rows, counts, centroid.

    Refuses to dissolve a cluster a person has triaged. Status is a human field
    apart from the escalating default, so absorbing an acknowledged or resolved
    cluster into another would launder a triaged issue back into the feed by the
    back door.
    """
    with transaction.atomic():
        # _merge_pg may SWAP the pair (it refuses to dissolve a triaged cluster), so the
        # centroid ops must use the ids it actually merged, not the ones passed in.
        pair = _merge_pg(keep_id, absorb_id, project_id)
    if pair is None:
        return False
    kept, absorbed = pair
    _absorb_centroid(kept, absorbed, project_id)
    delete_centroid(absorbed, project_id)
    return True


def _merge_pg(keep_id: str, absorb_id: str, project_id: str):
    """PG side of the merge, atomic — a partial move would strand issues on a
    cluster row that the next statement deletes.

    Returns the (kept_id, absorbed_id) actually merged, or None when the pair was
    skipped because both sides are human-triaged."""
    keep = TraceErrorGroup.objects.get(cluster_id=keep_id, project_id=project_id)
    absorb = TraceErrorGroup.objects.get(cluster_id=absorb_id, project_id=project_id)
    if absorb.status in _TRIAGED:
        if keep.status in _TRIAGED:
            return None                 # both triaged: leave both alone
        keep, absorb = absorb, keep     # survive the triaged one instead
        keep_id, absorb_id = absorb_id, keep_id

    TraceScanIssue.objects.filter(cluster=absorb).update(cluster=keep)

    # The junction is unique per (cluster, trace); a trace present in both clusters
    # would violate that on a blind update, so re-point only the rows keep lacks.
    existing = set(
        ErrorClusterTraces.objects.filter(cluster=keep).values_list("trace_id", flat=True)
    )
    for row in ErrorClusterTraces.objects.filter(cluster=absorb):
        if row.trace_id in existing:
            row.delete()
        else:
            row.cluster = keep
            row.save(update_fields=["cluster"])

    # Summing is correct here BECAUSE assign_to_cluster now derives the counts it
    # sums: every issue above is re-pointed to keep, so the combined membership is
    # exactly the sum. Recomputing instead would also be right, but it would erase
    # the count of any cluster whose issue rows have since been purged by
    # retention, and the junction dedup below removes rows the count should keep.
    keep.error_count = (keep.error_count or 0) + (absorb.error_count or 0)
    keep.total_events = (keep.total_events or 0) + (absorb.total_events or 0)
    if absorb.first_seen and (not keep.first_seen or absorb.first_seen < keep.first_seen):
        keep.first_seen = absorb.first_seen
    if absorb.last_seen and (not keep.last_seen or absorb.last_seen > keep.last_seen):
        keep.last_seen = absorb.last_seen
    keep.unique_traces = keep.clusters.values("trace").distinct().count()
    keep.save(update_fields=["error_count", "total_events", "first_seen", "last_seen",
                             "unique_traces", "updated_at"])

    absorb.delete()
    return keep_id, absorb_id


def _absorb_centroid(keep_id: str, absorb_id: str, project_id: str) -> None:
    """Move the absorbed cluster's centroid MASS onto the survivor.

    Without this the survivor's centroid never moves and its member_count never grows,
    so the merged cluster under-represents up to half its own members — and repeated
    passes cannot converge on the order-invariant grouping, because the evidence needed
    to converge is exactly what got deleted. Weighted mean, matching the incremental
    update ``assign_to_cluster`` already performs.
    """
    db = ClickHouseVectorDB()
    try:
        rows = db.client.execute(
            f"""
            SELECT cluster_id, argMax(centroid, last_updated), argMax(member_count, last_updated),
                   argMax(family, last_updated)
            FROM {CENTROIDS_TABLE}
            WHERE project_id = %(p)s AND cluster_id IN (%(a)s, %(b)s)
            GROUP BY cluster_id
            """,
            {"p": str(project_id), "a": keep_id, "b": absorb_id},
        )
        by = {str(r[0]): (r[1], r[2] or 1, r[3]) for r in rows}
        k, a = by.get(keep_id), by.get(absorb_id)
        if not k or not a:
            return
        nk, na = max(int(k[1]), 1), max(int(a[1]), 1)
        merged = [(x * nk + y * na) / (nk + na) for x, y in zip(k[0], a[0])]
        db.client.execute(
            f"""
            INSERT INTO {CENTROIDS_TABLE}
            (cluster_id, project_id, centroid, member_count, family, last_updated)
            VALUES (%(cluster_id)s, %(project_id)s, %(centroid)s, %(member_count)s, %(family)s, now())
            """,
            {"cluster_id": keep_id, "project_id": str(project_id), "centroid": merged,
             "member_count": nk + na, "family": k[2]},
        )
    finally:
        db.close()


def merge_duplicate_clusters(
    project_id: str, threshold: float = COSINE_THRESHOLD, max_merges: int = 50
) -> int:
    """Merge clusters whose centroids sit within ``threshold`` cosine distance.

    Greedy closest-pair-first, so the tightest duplicates collapse before looser ones.
    The larger cluster always absorbs the smaller, which keeps the surviving id the
    one users have already seen in the feed. Returns the number of merges performed.

    Two guards keep this a duplicate merge rather than a re-clustering. Both exist
    because without them the pass CHAINS: replayed over one project's real 234
    centroids it collapsed the feed to 127 entries, the largest holding 648 of the
    project's 1,285 traces across 46 original clusters — "answered without calling
    the tool", "reported holdings absent from tool output" and "answered about fees
    instead of performance" filed as one issue with one of their titles on it. That
    is a worse feed than the fragmentation the pass exists to fix.

      * SAME CATEGORY. Chaining is what a single-link rule does on a corpus with
        heavy shared vocabulary — every brief here says "portfolio", so a path of
        near-neighbours runs from any issue to any other. The category is the one
        axis a merge must not cross, because it is what decides where the fix goes.
        Restoring it alone took the largest merged entry from 46 clusters to 19.
      * MUTUAL NEAREST NEIGHBOUR. Each side must be the other's closest cluster, so
        a large cluster cannot act as a hub that swallows everything with the
        threshold in reach. Takes the largest merged entry from 19 clusters to 11.

    Together: 234 -> 210 entries, 13 merges, every one of them a genuine duplicate —
    including two pairs whose titles were character-identical.

    ``PARTITION_BY_CATEGORY`` gates assignment the same way, so category is strict
    at BOTH stages: an issue never joins a cluster of a different category, and two
    clusters of different categories never merge. State the consequence plainly —
    one defect filed under two categories stays two entries permanently, because no
    path exists by which they reunite. That is a real cost, paid to keep the
    chaining above in check, and it is the number to weigh when revisiting either
    guard.

    ``max_merges`` bounds a single pass: this runs periodically, so it is better to
    make steady progress than to hold a long transaction over a pathological project.
    """
    db = ClickHouseVectorDB()
    try:
        ensure_centroid_table(db)
        rows = db.client.execute(
            f"""
            SELECT cluster_id, argMax(centroid, last_updated), argMax(member_count, last_updated),
                   argMax(family, last_updated)
            FROM {CENTROIDS_TABLE}
            WHERE project_id = %(project_id)s
            GROUP BY cluster_id
            """,
            {"project_id": str(project_id)},
        )
    finally:
        db.close()
    if len(rows) < 2:
        return 0

    live = set(
        TraceErrorGroup.objects.filter(project_id=project_id).values_list(
            "cluster_id", flat=True
        )
    )
    items = [
        (str(cid), vec, cnt, fam) for cid, vec, cnt, fam in rows if str(cid) in live and vec
    ]
    if len(items) < 2:
        return 0

    distances = {}
    nearest = {}
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            d = _cosine_distance(items[i][1], items[j][1])
            distances[(i, j)] = d
            for a, b in ((i, j), (j, i)):
                if a not in nearest or d < nearest[a][0]:
                    nearest[a] = (d, b)

    pairs = sorted(
        (d, i, j)
        for (i, j), d in distances.items()
        if d <= threshold
        and items[i][3] == items[j][3]
        and nearest[i][1] == j
        and nearest[j][1] == i
    )

    gone: set[str] = set()
    merged = 0
    for _d, i, j in pairs:
        if merged >= max_merges:
            break
        a, b = items[i], items[j]
        if a[0] in gone or b[0] in gone:
            continue
        keep, absorb = (a, b) if (a[2] or 0) >= (b[2] or 0) else (b, a)
        try:
            # a pair can be declined (both sides human-triaged) — only a real merge counts,
            # and only a real merge retires an id from the candidate pool
            if not _merge_cluster_pair(keep[0], absorb[0], project_id):
                continue
        except TraceErrorGroup.DoesNotExist:
            continue
        gone.add(absorb[0])
        merged += 1

    if merged:
        logger.info(
            "scan_clusters_merged", project_id=str(project_id), merged=merged,
            clusters_before=len(items),
        )
    return merged
