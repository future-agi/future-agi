"""
Temporal activities for eval result clustering.

Mirrors trace_scanner tasks — cluster failing eval results into
TraceErrorGroup rows with source="eval".
"""

import structlog
from django.db import close_old_connections

from tfc.temporal import temporal_activity

logger = structlog.get_logger(__name__)

# Backstop on the per-dispatch drain loop: at most this many batches
# (× _CLUSTER_BATCH_LIMIT rows) before yielding. Every clustered row leaves a
# junction row, so the fetchable set strictly shrinks and normal drains stop on
# an empty batch well before this — it only bounds a project whose backlog is
# genuinely larger than the loop can drain in one dispatch.
_MAX_DRAIN_BATCHES = 60


def dispatch_eval_clustering(project_id) -> None:
    """Dispatch a project's clustering drain, coalesced per project.

    The single place that knows the dispatch contract, because there are two
    trigger sites that must not drift: ``run_entry`` (every eval-task eval, both
    the historical and continuous workflows) and the span-eval wrapper in
    ``tracer.utils.eval`` (feedback-driven re-evals, which never reach
    ``run_entry``).

    A fixed ``eval-cluster-{project_id}`` workflow id + USE_EXISTING collapses a
    burst of triggers onto the single in-flight drain. Fail-open, but at WARNING
    — never DEBUG: a silently swallowed dispatch is exactly what hid the cutover
    regression, and a clustering hiccup must not fail an eval that already
    produced a result.
    """
    try:
        from temporalio.common import WorkflowIDConflictPolicy

        cluster_eval_results_task.apply_async(
            args=(str(project_id),),
            task_id=f"eval-cluster-{project_id}",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
    except Exception:
        logger.warning(
            "eval_clustering_dispatch_failed",
            project_id=str(project_id),
            exc_info=True,
        )


@temporal_activity(time_limit=3600, queue="agent_compass", max_retries=1)
def cluster_eval_results_task(project_id: str):
    """Drain a project's unclustered failing eval-task results.

    Triggered per failing eval-task eval by ``run_entry`` — both the historical
    and continuous eval-task workflows drain every entry through it, so this
    covers both. Coalesced per project via the fixed ``eval-cluster-{project_id}``
    id + USE_EXISTING at the call site, so a burst of triggers collapses onto one
    run. That coalescing is also why the loop drains until a batch comes back
    EMPTY rather than merely short: a trigger that arrives while this run is
    working is folded into it and dropped, so anything committed during the final
    batch would otherwise wait for the project's next failing eval — which, for a
    finished one-shot task, never comes. Re-fetching until empty picks those rows
    up in the same run.

    Terminating on an empty fetch is safe because every row that clusters leaves
    a junction row carrying its ``eval_logger_id`` and so drops out of the next
    fetch. A row whose clustering *throws* does not — that tail is caught by
    ``clustered == 0``, which stops the loop and surfaces the failing downstream
    dependency rather than letting it spin.
    """
    from tracer.utils.eval_clustering import cluster_eval_results

    close_old_connections()

    clustered = new_clusters = assigned = 0
    for _ in range(_MAX_DRAIN_BATCHES):
        summary = cluster_eval_results(project_id)
        clustered += summary.clustered
        new_clusters += summary.new_clusters
        assigned += summary.assigned
        if summary.fetched == 0 or summary.clustered == 0:
            break
    else:
        # Ran the whole backstop without the fetchable set emptying — the backlog
        # is bigger than one dispatch can drain. The next trigger continues it,
        # but a finished one-shot task may never produce one, so this must not
        # read like a clean drain in the logs.
        logger.error(
            "eval_clustering_drain_cap_exhausted",
            project_id=project_id,
            batches=_MAX_DRAIN_BATCHES,
            clustered=clustered,
        )

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
