"""
Temporal activities for eval result clustering.

Mirrors trace_scanner tasks — cluster failing eval results into
TraceErrorGroup rows with source="eval".
"""

from typing import List

import structlog
from django.db import close_old_connections

from tfc.temporal import temporal_activity

logger = structlog.get_logger(__name__)

# Backstop on the per-dispatch drain loop: at most this many batches
# (× _CLUSTER_BATCH_LIMIT rows) before yielding. Normal drains stop earlier on a
# short batch; this only bounds the pathological case of a full batch of
# assigned-but-not-junctioned rows that would otherwise re-fetch forever.
_MAX_DRAIN_BATCHES = 60


@temporal_activity(time_limit=3600, queue="agent_compass", max_retries=1)
def cluster_eval_results_task(project_id: str):
    """Drain a project's unclustered failing eval-task results.

    Triggered per failing eval-task eval by ``run_entry`` — both the historical
    and continuous eval-task workflows drain every entry through it, so this
    covers both. Loops ``cluster_eval_results`` until a batch comes back short:
    one dispatch fully drains the project's current backlog, which is what lets
    us drop the old self-continuation (its distinct-id follow-up raced concurrent
    triggers). Coalesced per project via the fixed ``eval-cluster-{project_id}``
    id + USE_EXISTING at the call site, so a burst of triggers collapses onto one
    run.

    Termination keys on ``fetched``, NOT ``clustered``: a failing eval can be
    "assigned" without producing a new junction row (trace/session-level dedup),
    so it re-fetches every pass and ``clustered`` never reaches 0 on its own. A
    batch shorter than the cap means the fetchable set is drained;
    ``_MAX_DRAIN_BATCHES`` backstops a full batch of such re-fetched rows.
    """
    from tracer.utils.eval_clustering import (
        _CLUSTER_BATCH_LIMIT,
        cluster_eval_results,
    )

    close_old_connections()

    clustered = new_clusters = assigned = 0
    for _ in range(_MAX_DRAIN_BATCHES):
        summary = cluster_eval_results(project_id)
        clustered += summary.clustered
        new_clusters += summary.new_clusters
        assigned += summary.assigned
        if summary.fetched < _CLUSTER_BATCH_LIMIT or summary.clustered == 0:
            break

    logger.info(
        "cluster_eval_results_task_completed",
        project_id=project_id,
        clustered=clustered,
        new_clusters=new_clusters,
        assigned=assigned,
    )
    return {
        "clustered": clustered,
        "new_clusters": new_clusters,
        "assigned": assigned,
    }


@temporal_activity(time_limit=600, queue="agent_compass", max_retries=1)
def cluster_eval_results_for_projects(project_ids: List[str]):
    """
    Cluster eval results across multiple projects.

    Convenience wrapper for batch/sweep scenarios.
    """
    from tracer.utils.eval_clustering import cluster_eval_results

    close_old_connections()

    total = 0
    for project_id in project_ids:
        try:
            summary = cluster_eval_results(project_id)
            total += summary.clustered
        except Exception:
            logger.exception(
                "cluster_eval_results_project_failed",
                project_id=project_id,
            )

    logger.info(
        "cluster_eval_results_for_projects_completed",
        projects=len(project_ids),
        total_clustered=total,
    )
    return {"projects": len(project_ids), "total_clustered": total}
