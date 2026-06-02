"""
Service layer for eval result clustering.

Mirrors trace_scanner.cluster_issues() — orchestrates the embed → match → assign
pipeline for failing eval results.
"""

import uuid
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

    texts = [r.embedding_text for r in results]
    embeddings = embed_texts(texts)

    summary = EvalClusteringSummary()

    for result, embedding in zip(results, embeddings):
        try:
            match = find_nearest_centroid(embedding, project_id, result.eval_name)

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

    # Continue draining only when the batch was full AND we made forward
    # progress.
    #
    # The continuation must use a DISTINCT workflow id — not the fixed
    # per-project id + USE_EXISTING. That id+policy is for coalescing the
    # per-row trigger burst; reusing it here is fatal: this code runs
    # inside the still-open parent workflow, so USE_EXISTING resolves the
    # conflict against the running parent at request time, coalesces the
    # follow-up into it, the parent completes, and the backlog never
    # advances past one batch (start_delay defers execution, not conflict
    # resolution). A distinct id always starts a fresh run. The chain stays
    # bounded — exactly one continuation per completed run, strictly
    # sequential per project.
    #
    # The progress guard prevents a hot loop: a full batch with zero
    # clustered means a downstream dependency (embeddings / centroid store)
    # is failing — re-triggering would spin with no effect, so stop and
    # surface it instead.
    if len(results) >= _CLUSTER_BATCH_LIMIT:
        if summary.clustered > 0:
            try:
                from datetime import timedelta

                from tracer.tasks.eval_clustering import cluster_eval_results_task

                cluster_eval_results_task.apply_async(
                    args=(project_id,),
                    task_id=f"eval-cluster-{project_id}-cont-{uuid.uuid4().hex[:8]}",
                    start_delay=timedelta(seconds=5),
                )
                logger.info(
                    "eval_clustering_continuation_scheduled",
                    project_id=project_id,
                    drained=summary.clustered,
                )
            except Exception:
                logger.debug(
                    "eval_clustering_continuation_skipped",
                    project_id=project_id,
                    exc_info=True,
                )
        else:
            logger.error(
                "eval_clustering_stuck_no_progress",
                project_id=project_id,
                fetched=len(results),
            )

    return summary
