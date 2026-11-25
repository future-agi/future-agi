"""
Trace Error Analysis Tasks

Tasks for:
- Analyzing single traces for errors
- Batch processing trace errors
- Clustering project errors
"""

import gc
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from django.conf import settings
from django.db import close_old_connections
from django.db.models import F
from django.utils import timezone

from tfc.temporal import temporal_activity
from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.services.falcon_background_analysis import (
    BackgroundAnalysisUnavailable,
    require_background_analysis_ready,
)

if TYPE_CHECKING:
    from ai_tools.base import BackgroundExecutionScope

logger = structlog.get_logger(__name__)


def analyze_single_trace(trace_id, task_id, ingest_embeddings: bool = True):
    """
    Helper function to analyze a single trace with a deduct-and-refund billing model.

    ``ingest_embeddings`` controls whether per-error vectors are pushed to
    ClickHouse after a successful run. The legacy compass clustering
    pipeline consumes these, but the Feed Revamp's on-demand Deep
    Analysis flow does not — it reads ``TraceErrorDetail`` rows directly
    and has no use for the embeddings. Pass ``False`` from that path to
    avoid doing work no one reads (and to sidestep the orphan-rows-on-
    re-run problem).
    """
    try:
        from ee.agenthub.traceerroragent.traceerror import TraceErrorAnalysisAgent
    except ImportError:
        if settings.DEBUG:
            logger.warning(
                "Could not import ee.agenthub.traceerroragent.traceerror", exc_info=True
            )
        return None
    from tracer.models.project import Project
    from tracer.models.trace_error_analysis import TraceErrorAnalysis
    from tracer.queries import deep_analysis_state
    from tracer.queries.error_analysis import TraceErrorAnalysisDB
    from tracer.queries.helpers import get_default_workspace_for_project
    from tracer.services.clickhouse.v2 import get_reader
    from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices

    try:
        from ee.usage.utils.usage_entries import (
            log_and_deduct_cost_for_api_request,
            refund_cost_for_api_call,
        )
    except ImportError:
        log_and_deduct_cost_for_api_request = None
        refund_cost_for_api_call = None

    api_call_log_row = None
    organization = None

    try:
        close_old_connections()

        # Check if this trace has already been analyzed
        existing_analysis = TraceErrorAnalysis.objects.filter(trace_id=trace_id).first()
        if existing_analysis:
            logger.warning(f"Trace {trace_id} already analyzed, skipping.")
            return {
                "success": True,
                "trace_id": trace_id,
                "error_count": 0,
                "skipped": True,
            }

        # Trace lives only in CH post-cutover; resolve its project (org + billing)
        # from the CH root span's project_id, then load the PG Project (kept).
        with get_reader() as reader:
            roots = reader.root_ids_by_trace_ids([str(trace_id)])
        root = roots.get(str(trace_id))
        project_id = root[1] if root else None
        project = (
            Project.objects.select_related("organization").filter(id=project_id).first()
            if project_id
            else None
        )
        if project is None:
            logger.warning(f"Trace {trace_id} not found, skipping analysis.")
            return {"success": False, "trace_id": trace_id, "error": "Trace not found"}
        organization = project.organization

        workspace = get_default_workspace_for_project(project)

        # Pre-check usage before the LLM call
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        if check_usage is not None:
            usage_check = check_usage(
                str(organization.id), APICallTypeChoices.TRACE_ERROR_ANALYSIS.value
            )
            if not usage_check.allowed:
                deep_analysis_state.set_failed(str(trace_id))
                raise ValueError(usage_check.reason or "Usage limit exceeded")

        # Log and deduct cost for the analysis upfront.
        if log_and_deduct_cost_for_api_request is not None:
            api_call_log_row = log_and_deduct_cost_for_api_request(
                organization=organization,
                api_call_type=APICallTypeChoices.TRACE_ERROR_ANALYSIS.value,
                source="trace_error_analysis",
                workspace=workspace,
                source_id=str(trace_id),
                config={
                    "trace_id": str(trace_id),
                    "project_id": str(project.id),
                    "reference_id": str(project.id),
                },
            )

            if not api_call_log_row:
                error_message = "Failed to create API call log for trace analysis."
                logger.error(error_message)
                deep_analysis_state.set_failed(str(trace_id))
                return {"success": False, "trace_id": trace_id, "error": error_message}

        agent = TraceErrorAnalysisAgent(
            trace_id=str(trace_id),
            enable_memory=True,
            save_to_db=True,
            token_budget=500000,
        )

        result = agent.summarize()

        error_count = len(result.get("errors", []))
        logger.info(
            f"Successfully analyzed trace {trace_id} - found {error_count} errors"
        )

        # Success is recorded by the TraceErrorAnalysis row the agent wrote
        # (save_to_db=True) — the feed derives "done" from it. Just release the
        # transient running marker.
        deep_analysis_state.clear(str(trace_id))

        if error_count > 0 and ingest_embeddings:
            error_analysis_db = TraceErrorAnalysisDB()
            error_analysis_db.ingest_trace_error_embeddings(trace_id)

        # Mark the API call as successful
        if api_call_log_row:
            api_call_log_row.status = APICallStatusChoices.SUCCESS.value
            api_call_log_row.save(update_fields=["status", "updated_at"])

        # Dual-write: emit usage event for new billing system (cost-based)
        try:
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
                from ee.usage.utils.event_properties import llm_usage_properties
            except ImportError:
                llm_usage_properties = lambda obj: {}

            actual_cost = getattr(agent, "cost", {}).get("total_cost", 0)
            if not actual_cost and hasattr(agent, "llm"):
                actual_cost = getattr(agent.llm, "cost", {}).get("total_cost", 0)

            credits = 0
            if BillingConfig is not None:
                credits = BillingConfig.get().calculate_ai_credits(actual_cost)

            if (
                emit is not None
                and UsageEvent is not None
                and BillingEventType is not None
            ):
                emit(
                    UsageEvent(
                        org_id=str(organization.id),
                        event_type=BillingEventType.TRACE_ERROR_ANALYSIS,
                        amount=credits,
                        properties={
                            "source": "trace_error_analysis",
                            "source_id": str(trace_id),
                            "errors_found": error_count,
                            "raw_cost_usd": str(actual_cost),
                            **llm_usage_properties(agent),
                        },
                    )
                )
        except Exception:
            pass

        return {"success": True, "trace_id": trace_id, "error_count": error_count}

    except Exception as e:
        logger.exception(f"Failed to analyze trace {trace_id}: {str(e)}")

        # If something went wrong, refund the cost.
        if api_call_log_row:
            logger.info(f"Refunding cost for failed trace analysis {trace_id}.")
            if refund_cost_for_api_call is not None:
                refund_cost_for_api_call(api_call_log_row, config={"error": str(e)})
            api_call_log_row.status = APICallStatusChoices.ERROR.value
            api_call_log_row.save(update_fields=["status", "updated_at"])

        # Mark the run failed (transient marker; TTLs back to idle).
        deep_analysis_state.set_failed(str(trace_id))

        return {"success": False, "trace_id": trace_id, "error": str(e)}
    finally:
        gc.collect()
        close_old_connections()


@temporal_activity(
    max_retries=1,
    time_limit=3600,  # 1 hour ceiling; typical run is ~1 minute
    queue="agent_compass",
)
def run_deep_analysis_on_demand(trace_id: str, force: bool = False) -> dict:
    """Run deep analysis on a single trace, triggered by a user clicking
    the Error Feed's "Run Deep Analysis" button.

    Wraps ``analyze_single_trace`` with re-run semantics:

    - ``force=False`` (first click on an un-analyzed trace): delegates
      straight to ``analyze_single_trace`` which no-ops if an analysis
      already exists. The view layer is responsible for deciding
      whether to dispatch at all.
    - ``force=True`` (explicit Re-run click): deletes the existing
      ``TraceErrorAnalysis`` row (cascades to ``TraceErrorDetail`` via
      FK CASCADE) so the downstream agent produces fresh results.

    The dispatcher claims a ``deep_analysis_state`` running marker before
    dispatching so the first frontend poll sees the running state without
    racing the Temporal worker; ``analyze_single_trace`` clears it on success
    (done derives from the ``TraceErrorAnalysis`` row) or marks it failed.

    Embeddings are deliberately skipped — the Feed Revamp reads
    ``TraceErrorDetail`` rows directly and has no use for the ClickHouse
    vectors that ``ingest_trace_error_embeddings`` produces. The legacy
    compass clustering pipeline is the only consumer of those vectors
    and it's on death row in Phase 5.
    """
    close_old_connections()

    if force:
        TraceErrorAnalysis.objects.filter(trace_id=trace_id).delete()

    return analyze_single_trace(trace_id, None, ingest_embeddings=False)


@temporal_activity(
    max_retries=2,
    time_limit=3600 * 3,
    queue="agent_compass",
)
def check_and_process_trace_errors():
    """
    Periodic beat task that runs every minute to check for new traces
    and process them based on sampling rate.
    """
    from tracer.models.trace_error_analysis_task import TraceErrorTaskStatus
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    close_old_connections()
    error_analysis_db = TraceErrorAnalysisDB()

    created_count = error_analysis_db.ensure_all_projects_have_tasks()

    if created_count > 0:
        logger.info(f"Auto-created {created_count} trace error tasks")

    active_tasks = error_analysis_db.get_active_tasks()
    logger.info(f"Checking {active_tasks.count()} active trace error tasks")

    for task in active_tasks:
        try:
            if task.status == TraceErrorTaskStatus.RUNNING:
                continue

            selected_trace_ids = error_analysis_db.get_traces_to_process_for_task(
                task, days_back=1
            )

            if not selected_trace_ids:
                continue

            error_analysis_db.update_task_status(
                task, TraceErrorTaskStatus.RUNNING, last_run_at=timezone.now()
            )

            # Process only 1 batch per task per beat to avoid overwhelming the server
            batch_size = 10
            batch_ids = selected_trace_ids[:batch_size]

            process_trace_error_batch.delay(str(task.id), batch_ids)

            logger.info(
                f"Project {task.project.name}: Scheduled {len(batch_ids)} traces "
                f"(out of {len(selected_trace_ids)} pending)"
            )

        except Exception as e:
            logger.exception(f"Error checking traces for task {task.id}: {str(e)}")
            continue


@temporal_activity(
    max_retries=3,
    time_limit=1800,  # 30 minutes
    queue="agent_compass",
)
def process_trace_error_batch(task_id: str, trace_ids: list):
    """
    Process a batch of traces for error analysis using ThreadPoolExecutor.
    """
    from tracer.models.observation_span import Trace
    from tracer.models.trace_error_analysis_task import (
        TraceErrorAnalysisTask,
        TraceErrorTaskStatus,
    )
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    try:
        task = TraceErrorAnalysisTask.objects.get(id=task_id)
        project_id = str(task.project.id)
        error_analysis_db = TraceErrorAnalysisDB()
        successful = 0
        failed = 0
        total_errors = 0

        close_old_connections()
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(analyze_single_trace, trace_id, task_id)
                for trace_id in trace_ids
            ]

            for future in as_completed(futures):
                try:
                    result = future.result()

                    if result["success"]:
                        successful += 1
                        total_errors += result["error_count"]
                        task.last_trace_analyzed_id = str(result["trace_id"])
                    else:
                        failed += 1
                        try:
                            trace = Trace.objects.get(id=result["trace_id"])
                            task.failed_traces.add(trace)
                        except Exception:
                            pass

                except Exception as e:
                    failed += 1
                    logger.error(f"Error processing future: {str(e)}")

        task.total_traces_analyzed = F("total_traces_analyzed") + successful
        task.failed_analyses = F("failed_analyses") + failed
        task.total_errors_found = F("total_errors_found") + total_errors

        task.save(
            update_fields=[
                "total_traces_analyzed",
                "failed_analyses",
                "total_errors_found",
                "last_trace_analyzed_id",
            ]
        )
        error_analysis_db.update_task_status(task, TraceErrorTaskStatus.WAITING)

        task.refresh_from_db()

        logger.info(
            f"Batch complete for task {task_id}: "
            f"{successful} successful, {failed} failed, "
            f"{total_errors} total errors found, "
            f"processed in parallel with {min(5, len(trace_ids))} threads"
        )

        # Always trigger clustering after batch processing
        logger.info(
            f"Triggering clustering for project {task.project_id} - found {total_errors} errors"
        )
        cluster_project_errors.delay(project_id)

    except TraceErrorAnalysisTask.DoesNotExist:
        logger.error(f"Task {task_id} not found")
    except Exception as e:
        logger.exception(f"Error processing batch for task {task_id}: {str(e)}")
        raise  # Re-raise for Temporal to handle retry
    finally:
        close_old_connections()


def _falcon_analyze_single_trace(
    trace_id: str,
    project_id: str,
    *,
    background_execution_scope: "BackgroundExecutionScope | None" = None,
    analysis_attempt_id: UUID | None = None,
):
    """Refuse background execution, including previously validated assignments."""

    try:
        require_background_analysis_ready()
    except BackgroundAnalysisUnavailable as exc:
        return {
            "success": False,
            "trace_id": trace_id,
            "error_code": exc.code,
            "error": str(exc),
        }


@temporal_activity(
    max_retries=2,
    time_limit=3600,
    queue="agent_compass",
)
def analyze_traces_on_demand(project_id: str, trace_ids: list):
    """Reject previously queued batches; no background work is admitted."""
    # Jobs already in the queue bypass the tool. Fail the activity itself,
    # without fan-out or mutating claims (these messages have no owner token).
    try:
        require_background_analysis_ready()
    except BackgroundAnalysisUnavailable as exc:
        from temporalio.exceptions import ApplicationError

        raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc


@temporal_activity(
    queue="agent_compass",
)
def cluster_project_errors(project_id: str):
    """
    Cluster unclustered errors for a project using append-only approach.
    Run periodically or after batch analysis.

    Args:
        project_id: The project ID to cluster errors for

    Returns:
        Dict with clustering results
    """
    from tracer.queries.error_clustering import ErrorClusteringDB

    try:
        logger.info(f"Starting clustering for project {project_id}")

        clustering_db = ErrorClusteringDB(
            euclidean_threshold=0.6,
        )

        result = clustering_db.cluster_unclustered_errors(
            project_id=project_id,
            include_singletons=True,
            min_cluster_size=2,
            min_samples=1,
        )

        logger.info(
            f"Clustering complete for project {project_id}: "
            f"{result['matched_to_existing']} matched to existing clusters, "
            f"{result['new_clusters']} new clusters created"
        )

        if result["errors"]:
            logger.warning(f"Clustering errors: {result['errors']}")

        return result

    except Exception as e:
        logger.exception(f"Error clustering project {project_id}: {str(e)}")
        raise
    finally:
        close_old_connections()
