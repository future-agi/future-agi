"""
Service logic for the trace scanner pipeline.

Orchestrates:
  - Scan: config check → sampling → fetch → scan → write
  - Cluster: fetch unclustered → embed → cosine match → assign/create
Queries live in tracer/queries/trace_scanner.py and tracer/queries/scan_clustering.py.
"""

from typing import List

import structlog

# Activity-aware stub: used inside Temporal trace-scanner activities.
from tfc.ee_stub import _ee_activity_stub as _ee_stub

try:
    from ee.agenthub.trace_scanner import ScanResult, TraceScanner
except ImportError:
    ScanResult = _ee_stub("ScanResult")
    TraceScanner = _ee_stub("TraceScanner")
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.queries.scan_clustering import (
    assign_to_cluster,
    create_cluster,
    embed_texts,
    find_nearest_centroid,
    find_nearest_success_trace,
    get_cluster_trace_embeddings,
    get_trace_input_data,
    get_unclustered_issues,
    store_trace_input_embeddings,
)
from tracer.queries.trace_scanner import (
    apply_sampling,
    fetch_trace_data,
    filter_already_scanned,
    get_scan_config,
    mark_traces_failed,
    write_scan_results,
)
from tracer.types.scan_types import ClusteringSummary, SuccessTraceMatch

logger = structlog.get_logger(__name__)

# Scan + write in sub-chunks of this many traces so partial progress survives an
# activity timeout (the scanner makes one LLM call per trace, serially).
_SCAN_WRITE_CHUNK = 5


def scan_and_write(
    trace_ids: List[str], project_id: str, mark_unresolved: bool = False
) -> List[ScanResult]:
    """
    Full scan pipeline for a batch of traces.

    Returns list of ScanResults (including failed ones).
    Returns empty list if scanning is disabled or all traces filtered out.

    ``mark_unresolved`` writes a terminal FAILED marker for requested traces that
    resolve no spans. The sweep sets it — its candidates are CH-confirmed roots,
    so an empty resolve is a real disagreement (deletion race) and the marker
    stops that trace from pinning the watermark. The inline path leaves it off:
    an as-yet-unreplicated trace may simply be lagging, and the sweep will pick
    it up once it lands.
    """
    # Config
    config = get_scan_config(project_id)
    if config is None:
        logger.info("scanning_disabled", project_id=project_id)
        return []

    # Sampling
    trace_ids = apply_sampling(trace_ids, config.sampling_rate)
    if not trace_ids:
        logger.info("all_traces_sampled_out", project_id=project_id)
        return []

    # Dedup
    trace_ids = filter_already_scanned(trace_ids)
    if not trace_ids:
        logger.info("all_traces_already_scanned", project_id=project_id)
        return []

    # Fetch
    traces_data = fetch_trace_data(trace_ids, project_id)

    if mark_unresolved:
        resolved = {td.trace_id for td in traces_data}
        unresolved = [t for t in map(str, trace_ids) if t not in resolved]
        if unresolved:
            mark_traces_failed(unresolved, project_id, "scan: no span data resolved")

    if not traces_data:
        logger.warning("no_trace_data_found", trace_ids=trace_ids)
        return []

    # Scan + write in small sub-chunks so partial progress is persisted even if
    # the surrounding activity hits its time limit mid-batch. Writing only after
    # scanning the whole batch (the old behavior) meant a large/slow batch that
    # exceeded the activity time_limit wrote NOTHING — silent data loss under
    # high sampling/volume.
    scanner = TraceScanner()
    results: List[ScanResult] = []
    written = 0
    for i in range(0, len(traces_data), _SCAN_WRITE_CHUNK):
        chunk = traces_data[i : i + _SCAN_WRITE_CHUNK]
        chunk_results = scanner.scan_batch([t.to_dict() for t in chunk])
        written += write_scan_results(chunk_results, project_id, config.scan_version)
        results.extend(chunk_results)

    logger.info(
        "scan_pipeline_completed",
        traces_scanned=len(results),
        issues_found=sum(len(r.issues) for r in results),
        written=written,
        failed=sum(1 for r in results if r.error),
        project_id=project_id,
    )

    _emit_scanner_billing(scanner, project_id, results)

    return results


def _emit_scanner_billing(
    scanner: TraceScanner, project_id: str, results: list
) -> None:
    """Emit a single TRACE_ERROR_ANALYSIS usage event for this scan batch.

    Cost is taken from the agentcc gateway (`x-agentcc-cost` response header)
    aggregated on the scanner, converted USD → AI credits via the billing
    config. Billing failures never break the scan pipeline.

    Emits unconditionally across deployment modes — matches the existing
    pattern used by tracer/tasks/error_analysis.py. The emitter is Redis-
    backed and fire-and-forget, safe to call in any env.
    """
    try:
        cost_usd = float(getattr(scanner, "total_cost_usd", 0.0) or 0.0)
        if cost_usd <= 0:
            return

        from tracer.models.project import Project
        try:
            from ee.usage.schemas.event_types import BillingEventType
        except ImportError:
            BillingEventType = None
        try:
            from ee.usage.schemas.events import UsageEvent
        except ImportError:
            UsageEvent = None
        try:
            from ee.usage.services.config import BillingConfig
        except ImportError:
            BillingConfig = None
        try:
            from ee.usage.services.emitter import emit
        except ImportError:
            emit = None
        try:
            from ee.usage.utils.event_properties import token_usage_properties
        except ImportError:
            token_usage_properties = lambda token_usage: {}

        project = Project.objects.select_related("organization").filter(
            id=project_id
        ).first()
        if not project or not project.organization:
            return

        credits = BillingConfig.get().calculate_ai_credits(cost_usd)
        emit(
            UsageEvent(
                org_id=str(project.organization.id),
                event_type=BillingEventType.TRACE_ERROR_ANALYSIS,
                amount=credits,
                properties={
                    "source": "trace_scanner",
                    "source_id": str(project_id),
                    "traces_scanned": len(results),
                    "issues_found": sum(len(r.issues) for r in results),
                    "raw_cost_usd": str(cost_usd),
                    "model": scanner.model_config.model_name,
                    **token_usage_properties(getattr(scanner, "token_usage", {})),
                },
            )
        )
    except Exception:
        logger.exception("scanner_billing_emit_failed", project_id=project_id)


def cluster_issues(project_id: str) -> ClusteringSummary:
    """
    Cluster all unclustered scanner issues for a project.

    Online incremental: embed each issue → cosine match against centroids →
    assign to existing cluster or create new one.
    """
    issues = get_unclustered_issues(project_id)
    if not issues:
        logger.info("no_unclustered_issues", project_id=project_id)
        return ClusteringSummary()

    # Embed all issue texts in one batch
    texts = [issue.embedding_text for issue in issues]
    embeddings = embed_texts(texts)

    summary = ClusteringSummary()

    for issue, embedding in zip(issues, embeddings):
        try:
            match = find_nearest_centroid(embedding, project_id, issue.category)

            if match:
                cluster_id, distance = match
                assign_to_cluster(cluster_id, project_id, issue, embedding)
                summary.assigned += 1
                logger.debug(
                    "issue_matched",
                    issue_id=issue.issue_id,
                    cluster_id=cluster_id,
                    distance=round(distance, 4),
                )
            else:
                create_cluster(project_id, issue, embedding)
                summary.new_clusters += 1
        except Exception:
            logger.exception(
                "cluster_issue_failed",
                issue_id=issue.issue_id,
                project_id=project_id,
            )

    summary.clustered = summary.new_clusters + summary.assigned
    logger.info(
        "cluster_issues_completed",
        project_id=project_id,
        clustered=summary.clustered,
        new_clusters=summary.new_clusters,
        assigned=summary.assigned,
    )
    return summary


def embed_trace_inputs(trace_ids: List[str], project_id: str) -> int:
    """
    Kevinify + embed root span inputs for a batch of traces, store in ClickHouse.

    Runs for ALL traces (success and failure) so KNN has both sides.
    Returns number of embeddings stored.
    """
    inputs = get_trace_input_data(trace_ids, project_id)
    if not inputs:
        logger.info("no_root_inputs_found", project_id=project_id)
        return 0

    # Kevinify then embed
    texts = [inp.kevinified_text for inp in inputs]
    embeddings = embed_texts(texts)

    stored = store_trace_input_embeddings(inputs, embeddings)
    logger.info(
        "trace_inputs_embedded",
        project_id=project_id,
        traces_with_input=len(inputs),
        stored=stored,
    )
    return stored


def match_success_traces(
    project_id: str, cluster_ids: List[str]
) -> List[SuccessTraceMatch]:
    """
    For each cluster, find the nearest success trace via KNN on root input embeddings.

    Updates TraceErrorGroup.success_trace FK for each match found.
    """
    matches = []

    for cluster_id in cluster_ids:
        try:
            # Get a representative failing trace's root input embedding
            rep = get_cluster_trace_embeddings(cluster_id, project_id)
            if not rep:
                logger.debug("no_embedding_for_cluster", cluster_id=cluster_id)
                continue

            rep_trace_id, rep_embedding = rep

            # KNN: nearest success trace
            result = find_nearest_success_trace(rep_embedding, project_id)
            if not result:
                logger.debug("no_success_trace_found", cluster_id=cluster_id)
                continue

            success_trace_id, distance = result

            # Update the cluster's success_trace FK
            TraceErrorGroup.objects.filter(
                cluster_id=cluster_id,
                project_id=project_id,
            ).update(success_trace_id=success_trace_id)

            match = SuccessTraceMatch(
                cluster_id=cluster_id,
                success_trace_id=success_trace_id,
                distance=distance,
            )
            matches.append(match)

            logger.info(
                "success_trace_matched",
                cluster_id=cluster_id,
                success_trace_id=success_trace_id,
                distance=round(distance, 4),
            )
        except Exception:
            logger.exception(
                "match_success_trace_failed",
                cluster_id=cluster_id,
                project_id=project_id,
            )

    return matches
