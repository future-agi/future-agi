"""
Service layer for eval result clustering.

Mirrors trace_scanner.cluster_issues() — orchestrates the embed → match → assign
pipeline for failing eval results.
"""

from typing import List

import structlog

from tracer.queries.eval_clustering import (
    assign_to_cluster,
    create_cluster,
    embed_texts,
    find_nearest_centroid,
    get_unclustered_eval_results,
)
from tracer.types.eval_cluster_types import EvalClusteringSummary

from tracer.ee_boundary import distill_eval_failure_phrases

logger = structlog.get_logger(__name__)

# Max eval rows clustered per activity invocation. Bounds the work unit so
# a backfilled project (tens of thousands of unclustered rows) can never
# produce a single activity that exceeds its Temporal time limit and then
# retry-loops forever. A larger backlog drains over successive bounded runs
# (see the self-continuation at the end of cluster_eval_results).
_CLUSTER_BATCH_LIMIT = 500


def cluster_eval_results(project_id: str) -> EvalClusteringSummary:
    """
    Cluster a bounded batch of unclustered failing eval results for a project.

    Online incremental: embed each explanation → cosine match against
    centroids (partitioned by eval name) → assign or create. If the batch
    cap is hit, more rows remain, so a follow-up run is scheduled to keep
    draining — bounded O(total / cap) runs instead of one unbounded one.
    """
    results = get_unclustered_eval_results(project_id, limit=_CLUSTER_BATCH_LIMIT)
    if not results:
        logger.info("no_unclustered_eval_results", project_id=project_id)
        return EvalClusteringSummary()

    # Distill each explanation to a canonical failure phrase before
    # embedding — raw explanations carry trace-specific noise (names,
    # numbers, quotes) that fragments clusters. Best-effort: a failed
    # batch leaves ``distilled`` None and the raw text is embedded.
    distill_eval_failure_phrases(results)

    texts = [r.embedding_text for r in results]
    embeddings = embed_texts(texts)

    summary = EvalClusteringSummary(fetched=len(results))

    for result, embedding in zip(results, embeddings):
        try:
            match = find_nearest_centroid(
                embedding, project_id, result.eval_name, result.target_type
            )

            if match:
                cluster_id, distance = match
                assign_to_cluster(cluster_id, project_id, result, embedding)
                summary.assigned += 1
                logger.debug(
                    "eval_result_matched",
                    eval_logger_id=result.eval_logger_id,
                    cluster_id=cluster_id,
                    distance=round(distance, 4),
                )
            else:
                create_cluster(project_id, result, embedding)
                summary.new_clusters += 1
        except Exception:
            logger.exception(
                "cluster_eval_result_failed",
                eval_logger_id=result.eval_logger_id,
                project_id=project_id,
            )

    summary.clustered = summary.new_clusters + summary.assigned
    logger.info(
        "cluster_eval_results_completed",
        project_id=project_id,
        clustered=summary.clustered,
        new_clusters=summary.new_clusters,
        assigned=summary.assigned,
    )

    # Draining past this batch is handled by the caller
    # (``cluster_eval_results_task`` loops until a batch comes back short), NOT a
    # self-continuation here. A self-continuation would necessarily use a distinct
    # workflow id (this run completes right after scheduling it), so it would run
    # concurrently with the next per-eval trigger — both claim the same unlocked
    # oldest-``_CLUSTER_BATCH_LIMIT`` rows (no row lock) and double-count on
    # ``assign_to_cluster``. Omitted on purpose: the trigger's fixed-id +
    # USE_EXISTING coalescing is the only per-project concurrency guard.
    if summary.fetched >= _CLUSTER_BATCH_LIMIT and summary.clustered == 0:
        # A full batch that clustered nothing means a downstream dependency
        # (embeddings / centroid store) is failing. Surface it rather than let the
        # caller's loop spin silently.
        logger.error(
            "eval_clustering_stuck_no_progress",
            project_id=project_id,
            fetched=summary.fetched,
        )

    return summary
