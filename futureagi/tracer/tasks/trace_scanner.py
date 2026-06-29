"""
Trace scanner Temporal activities.

Activity 1: scan_traces_task — scan completed traces for issues
Activity 2: embed_trace_inputs_task — kevinify + embed root inputs for all scanned traces
Activity 3: cluster_scan_issues_task — cluster unclustered issues + match success traces
"""

import time
from datetime import timedelta
from typing import List

import structlog
from django.db.models import F

from tfc.temporal.drop_in import temporal_activity
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.models.trace_scan import TraceScanConfig
from tracer.queries.trace_scanner import (
    filter_already_scanned,
    is_trace_sampled,
    mark_traces_failed,
)
from tracer.services.clickhouse.v2 import get_reader
from tracer.utils.trace_scanner import (
    cluster_issues,
    embed_trace_inputs,
    match_success_traces,
    scan_and_write,
)

logger = structlog.get_logger(__name__)

SCAN_DELAY_SECONDS = 10

# ─── Periodic sweep policy (scan collector-ingested CH-only traces) ──────────
_SWEEP_GRACE_SECONDS = 60  # let straggler child spans settle before scanning
_SWEEP_COLD_START_SECONDS = 900  # first-sweep window when last_swept_at is NULL
_SWEEP_BATCH_SIZE = 15  # keep each scan task under its time_limit (cf. _trigger_trace_scanner)
_SWEEP_MAX_LAG_SECONDS = 86400  # cap how far the watermark lags behind a stuck trace (24h)


@temporal_activity(time_limit=600, queue="agent_compass", max_retries=1)
def scan_traces_task(trace_ids: List[str], project_id: str, from_sweep: bool = False):
    """
    Scan completed traces for issues.

    Triggered from OTLP ingestion after root span completion.
    Waits 10s for straggler child spans, then runs the full scan pipeline.
    Chain: scan → embed inputs → cluster + match success traces.

    ``from_sweep`` flags a sweep-dispatched batch: those ids are CH-confirmed
    roots, so one that resolves no spans is a real disagreement and is marked
    terminal so it can't pin the sweep watermark. Inline batches leave it off —
    an unreplicated trace may just be lagging, and the sweep catches it later.
    """
    time.sleep(SCAN_DELAY_SECONDS)

    logger.info(
        "scan_traces_task_started",
        trace_count=len(trace_ids),
        project_id=project_id,
    )

    results = scan_and_write(trace_ids, project_id, mark_unresolved=from_sweep)

    issues_found = sum(len(r.issues) for r in results)
    logger.info(
        "scan_traces_task_completed",
        trace_count=len(results),
        issues_found=issues_found,
        project_id=project_id,
    )

    # Always embed root inputs (success + failure traces needed for KNN).
    # Embed triggers clustering if there are new issues.
    embed_trace_inputs_task.apply_async(
        args=(trace_ids, project_id, issues_found > 0),
    )


@temporal_activity(time_limit=300, queue="agent_compass", max_retries=1)
def embed_trace_inputs_task(
    trace_ids: List[str], project_id: str, trigger_clustering: bool
):
    """
    Kevinify + embed root span inputs for all scanned traces.

    Stores in ClickHouse for KNN success trace matching.
    Runs for ALL traces (success and failure) so KNN has both sides.
    Chains to clustering if new issues were found.
    """
    logger.info(
        "embed_trace_inputs_task_started",
        trace_count=len(trace_ids),
        project_id=project_id,
    )

    stored = embed_trace_inputs(trace_ids, project_id)

    logger.info(
        "embed_trace_inputs_task_completed",
        project_id=project_id,
        stored=stored,
    )

    if trigger_clustering:
        cluster_scan_issues_task.apply_async(args=(project_id,))


@temporal_activity(time_limit=300, queue="agent_compass", max_retries=1)
def cluster_scan_issues_task(project_id: str):
    """
    Cluster unclustered scanner issues + match success traces for updated clusters.

    Online incremental: each issue → embed → cosine match centroids → assign or create cluster.
    After clustering, KNN matches nearest success trace per updated cluster.
    """
    logger.info("cluster_scan_issues_task_started", project_id=project_id)

    summary = cluster_issues(project_id)

    logger.info(
        "cluster_scan_issues_task_completed",
        project_id=project_id,
        clustered=summary.clustered,
        new_clusters=summary.new_clusters,
        assigned=summary.assigned,
    )

    # Match success traces for all scanner clusters in this project
    if summary.clustered > 0:
        cluster_ids = list(
            TraceErrorGroup.objects.filter(
                project_id=project_id,
                source="scanner",
            ).values_list("cluster_id", flat=True)
        )

        matches = match_success_traces(project_id, cluster_ids)
        logger.info(
            "success_trace_matching_completed",
            project_id=project_id,
            clusters_checked=len(cluster_ids),
            matches_found=len(matches),
        )


@temporal_activity(time_limit=300, queue="agent_compass", max_retries=0)
def sweep_scannable_traces():
    """Trigger scans for completed, unscanned (collector-ingested) traces.

    The collector writes spans straight to CH and bypasses the inline scanner
    trigger, so this sweep is the only trigger for CH-only traces. Per observe
    project with scanning enabled and a non-zero rate: take root candidates in
    ``[last_swept_at, now-grace]`` of ``created_at``, drop already-scanned,
    pre-sample, dispatch ``scan_traces_task`` (which chains embed -> cluster),
    and park the watermark on the OLDEST still-unscanned sampled-in trace.

    The watermark never passes a sampled-in trace until that trace has a durable
    marker — so a scan task that dies before writing one (CH/gateway outage) is
    retried next tick, not lost. Pre-sampling here (same stable verdict as
    ``scan_and_write``) is what lets the cursor lag behind only sampled-in work
    without a marker per skipped trace, and avoids dispatching no-op tasks for a
    low-sampling project. A trace stuck unscanned past ``_SWEEP_MAX_LAG_SECONDS``
    (sustained outage, or a candidate root that resolves no span data) is
    FAILED-marked so it can't pin the cursor or grow the scan window unbounded —
    bounded recovery, never silent loss. ``no_workspace_objects``: this is a
    system-wide job and must not be scoped to a leaked workspace.
    ``max_retries=0``: the next tick recovers a sweep-level failure.
    """
    configs = list(
        TraceScanConfig.no_workspace_objects.filter(
            enabled=True,
            sampling_rate__gt=0,
            project__trace_type="observe",
        )
        # Oldest watermark first (never-swept = NULL ranks first) so the most
        # behind projects are served before the tick's time limit — otherwise a
        # large fleet would always starve the same tail of projects.
        .order_by(F("last_swept_at").asc(nulls_first=True))
        .values("project_id", "sampling_rate", "last_swept_at")
    )
    if not configs:
        return

    dispatched = 0
    abandoned = 0
    with get_reader() as reader:
        now_ch = reader.ch_now()
        upper = now_ch - timedelta(seconds=_SWEEP_GRACE_SECONDS)
        cold_floor = now_ch - timedelta(seconds=_SWEEP_COLD_START_SECONDS)
        lag_floor = now_ch - timedelta(seconds=_SWEEP_MAX_LAG_SECONDS)

        for cfg in configs:
            project_id = str(cfg["project_id"])
            try:
                lower = cfg["last_swept_at"] or cold_floor
                rate = cfg["sampling_rate"]
                candidates = reader.root_trace_candidates(project_id, lower, upper)
                # Anti-join (drop durably-scanned) then pre-sample, so we dispatch
                # only what we'll scan and lag the watermark behind only sampled-in
                # work that still owes a marker.
                unscanned = set(filter_already_scanned([t for t, _ in candidates]))
                pending = [
                    (tid, ca)
                    for tid, ca in candidates
                    if tid in unscanned and is_trace_sampled(tid, rate)
                ]

                # Park the cursor on the oldest pending trace, but never lag past
                # the bound — a stuck trace would otherwise pin it forever and grow
                # the per-tick scan window without end.
                if pending:
                    new_watermark = max(min(ca for _, ca in pending), lag_floor)
                else:
                    new_watermark = upper

                live = [tid for tid, ca in pending if ca >= new_watermark]
                lost = [tid for tid, ca in pending if ca < new_watermark]

                for i in range(0, len(live), _SWEEP_BATCH_SIZE):
                    scan_traces_task.apply_async(
                        args=(live[i : i + _SWEEP_BATCH_SIZE], project_id, True)
                    )
                    dispatched += 1

                if lost:
                    abandoned += mark_traces_failed(
                        lost, project_id, "scan-sweep: abandoned (exceeded max lag)"
                    )

                TraceScanConfig.no_workspace_objects.filter(
                    project_id=cfg["project_id"]
                ).update(last_swept_at=new_watermark)
            except Exception:
                # Fail-open: one project's error must not starve the tick.
                # Watermark not advanced, so the next tick retries this window.
                logger.warning(
                    "scan_sweep_project_failed", project_id=project_id, exc_info=True
                )

    logger.info(
        "scan_sweep_completed",
        projects=len(configs),
        tasks_dispatched=dispatched,
        abandoned=abandoned,
    )
