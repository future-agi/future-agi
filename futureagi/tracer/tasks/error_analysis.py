"""
Trace Error Analysis Tasks

Tasks for:
- Analyzing single traces for errors
- Batch processing trace errors
- Clustering project errors
"""

import gc
from concurrent.futures import ThreadPoolExecutor, as_completed

import structlog
from django.conf import settings
from django.db import close_old_connections
from django.db.models import F
from django.utils import timezone

from tfc.temporal import temporal_activity
from tracer.models.trace_error_analysis import TraceErrorAnalysis

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
    from accounts.models.user import User
    try:
        from ee.agenthub.traceerroragent.traceerror import TraceErrorAnalysisAgent
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.traceerroragent.traceerror", exc_info=True)
        return None
    from tracer.models.observation_span import Trace
    from tracer.models.trace import TraceErrorAnalysisStatus
    from tracer.models.trace_error_analysis import TraceErrorAnalysis
    from tracer.queries.error_analysis import TraceErrorAnalysisDB
    from tracer.queries.helpers import get_default_workspace_for_project
    try:
        from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
    except ImportError:
        from tfc.oss_stubs.usage import APICallStatusChoices
        from tfc.oss_stubs.usage import APICallTypeChoices
    try:
        from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request, refund_cost_for_api_call
    except ImportError:
        from tfc.oss_stubs.usage import log_and_deduct_cost_for_api_request
        from tfc.oss_stubs.usage import refund_cost_for_api_call

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

        try:
            trace = Trace.objects.select_related("project__organization").get(
                id=trace_id
            )
            organization = trace.project.organization
        except Trace.DoesNotExist:
            logger.warning(f"Trace {trace_id} not found, skipping analysis.")
            return {"success": False, "trace_id": trace_id, "error": "Trace not found"}

        workspace = get_default_workspace_for_project(trace.project)

        # Pre-check usage before the LLM call
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        usage_check = check_usage(
            str(organization.id), APICallTypeChoices.TRACE_ERROR_ANALYSIS.value
        )
        if not usage_check.allowed:
            Trace.objects.filter(id=trace_id).update(
                error_analysis_status=TraceErrorAnalysisStatus.FAILED
            )
            raise ValueError(usage_check.reason or "Usage limit exceeded")

        # Log and deduct cost for the analysis upfront.
        api_call_log_row = log_and_deduct_cost_for_api_request(
            organization=organization,
            api_call_type=APICallTypeChoices.TRACE_ERROR_ANALYSIS.value,
            source="trace_error_analysis",
            workspace=workspace,
            source_id=str(trace_id),
            config={
                "trace_id": str(trace_id),
                "project_id": str(trace.project.id),
                "reference_id": str(trace.project.id),
            },
        )

        if not api_call_log_row:
            error_message = "Failed to create API call log for trace analysis."
            logger.error(error_message)
            Trace.objects.filter(id=trace_id).update(
                error_analysis_status=TraceErrorAnalysisStatus.FAILED
            )
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

        Trace.objects.filter(id=trace_id).update(
            error_analysis_status=TraceErrorAnalysisStatus.COMPLETED
        )

        if error_count > 0 and ingest_embeddings:
            error_analysis_db = TraceErrorAnalysisDB()
            error_analysis_db.ingest_trace_error_embeddings(trace_id)

        # Mark the API call as successful
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save(update_fields=["status"])

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

            actual_cost = getattr(agent, "cost", {}).get("total_cost", 0)
            if not actual_cost and hasattr(agent, "llm"):
                actual_cost = getattr(agent.llm, "cost", {}).get("total_cost", 0)
            credits = BillingConfig.get().calculate_ai_credits(actual_cost)

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
            refund_cost_for_api_call(api_call_log_row, config={"error": str(e)})
            api_call_log_row.status = APICallStatusChoices.ERROR.value
            api_call_log_row.save(update_fields=["status"])

        # Mark trace as failed
        Trace.objects.filter(id=trace_id).update(
            error_analysis_status=TraceErrorAnalysisStatus.FAILED
        )

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

    The view sets ``Trace.error_analysis_status=PROCESSING`` synchronously
    before dispatching so the first frontend poll sees the running state
    without racing the Temporal worker. ``analyze_single_trace`` flips it
    to COMPLETED/FAILED at the end.

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


def _falcon_analyze_single_trace(trace_id: str, project_id: str):
    """
    Analyze a single trace using a headless Falcon agent loop with the
    analyze-trace-errors skill. Replaces TraceErrorAnalysisAgent.
    """
    import asyncio

    # Falcon is gated on deployment mode (EE / Cloud) AND code presence.
    # In OSS mode or when ee/falcon_ai/ isn't installed, skip cleanly so
    # the enclosing task doesn't surface an ImportError.
    try:
        from ee.usage.deployment import DeploymentMode

        _is_oss = DeploymentMode.is_oss()
    except ImportError:
        _is_oss = True

    if _is_oss:
        logger.warning(
            "falcon_trace_analysis_skipped_no_ee",
            trace_id=trace_id,
            reason="Deployment is OSS — Falcon AI requires EE license or Cloud deployment",
        )
        return {
            "success": False,
            "trace_id": trace_id,
            "error": "Falcon AI not available (requires EE)",
        }

    try:
        from ee.falcon_ai.agent import AgentLoop
        from ee.falcon_ai.models import Conversation, Skill
    except ImportError:
        logger.warning(
            "falcon_trace_analysis_skipped_no_ee",
            trace_id=trace_id,
            reason="ee.falcon_ai not installed — unexpected for non-OSS deployment",
        )
        return {
            "success": False,
            "trace_id": trace_id,
            "error": "Falcon AI not available (requires EE)",
        }

    from ai_tools.base import ToolContext
    from tfc.middleware.workspace_context import set_workspace_context
    from tracer.models.trace import Trace, TraceErrorAnalysisStatus
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    close_old_connections()

    try:
        trace = Trace.objects.select_related("project__organization").get(id=trace_id)
        org = trace.project.organization

        # Find a user in this org for the conversation
        from accounts.models.user import User

        user = User.objects.filter(organization=org).first()
        if not user:
            logger.error(f"No user found for org {org.id}, skipping trace {trace_id}")
            return {"success": False, "trace_id": trace_id, "error": "No user"}

        # Get workspace
        from tracer.queries.helpers import get_default_workspace_for_project

        workspace = get_default_workspace_for_project(trace.project)

        set_workspace_context(workspace=workspace, organization=org, user=user)
        ctx = ToolContext(user=user, organization=org, workspace=workspace)

        # Load the skill
        skill = Skill.objects.filter(
            organization=org, slug="analyze-trace-errors", is_active=True
        ).first()

        # Create a hidden conversation (filtered out from user's chat list)
        conversation = Conversation.objects.create(
            user=user,
            organization=org,
            workspace=workspace,
            title=f"Auto-analysis: {trace_id[:8]}",
            metadata={"hidden": True, "type": "background_analysis"},
        )

        agent = AgentLoop(tool_context=ctx, conversation=conversation, headless=True)

        # Collect ALL events for comprehensive logging
        all_events = []
        collected_tool_calls = []
        text_deltas = []

        async def logging_callback(event):
            etype = event.get("type", "")
            all_events.append({"type": etype})

            if etype == "tool_call_start":
                collected_tool_calls.append(
                    {
                        "tool_name": event["data"].get("tool_name", ""),
                        "params": event["data"].get("params", {}),
                        "step": event["data"].get("step", 0),
                        "call_id": event["data"].get("call_id", ""),
                        "status": "started",
                        "result_summary": "",
                        "result_full": "",
                    }
                )
            elif etype == "tool_call_result":
                call_id = event["data"].get("call_id", "")
                for tc in collected_tool_calls:
                    if tc.get("call_id") == call_id:
                        tc["status"] = event["data"].get("status", "")
                        tc["result_summary"] = event["data"].get("result_summary", "")[
                            :500
                        ]
                        tc["result_full"] = event["data"].get("result_full", "")[:2000]
                        break
            elif etype == "text_delta":
                text_deltas.append(event["data"].get("delta", ""))
            elif etype == "error":
                logger.error(
                    f"Falcon agent error for trace {trace_id}: {event['data']}"
                )

        import time as _time

        start_time = _time.time()

        async def run():
            return await agent.run(
                user_message=f"Analyze trace {trace_id} for errors.",
                history_messages=[],
                send_callback=logging_callback,
                context_page="tracing",
                skill=skill,
            )

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
        finally:
            loop.close()

        elapsed = _time.time() - start_time

        # Log what happened in the agent loop
        event_types = [e["type"] for e in all_events]
        logger.info(
            f"Falcon loop for trace {trace_id[:8]}: "
            f"elapsed={elapsed:.1f}s tools={len(collected_tool_calls)} "
            f"events={len(all_events)} event_types={event_types} "
            f"response_len={len(result.get('content', '') or '')} "
            f"tokens={result.get('input_tokens', 0)}+{result.get('output_tokens', 0)}"
        )

        # Check if Falcon created an analysis — if not, create a "clean" one
        from tracer.models.trace_error_analysis import TraceErrorAnalysis

        analysis = (
            TraceErrorAnalysis.objects.filter(trace_id=trace_id)
            .order_by("-analysis_date")
            .first()
        )

        if not analysis:
            # Falcon found no errors — create a clean analysis record
            analysis = TraceErrorAnalysis.objects.create(
                trace=trace,
                project=trace.project,
                overall_score=4.0,
                total_errors=0,
                high_impact_errors=0,
                medium_impact_errors=0,
                low_impact_errors=0,
                recommended_priority="LOW",
                insights="No errors found during analysis.",
                agent_version="falcon-skill-1.0",
            )
        elif analysis.overall_score is None:
            # Falcon submitted findings but didn't call submit_trace_scores.
            # Compute fallback scores from the errors found.
            from tracer.models.trace_error_analysis import TraceErrorDetail

            details = TraceErrorDetail.objects.filter(analysis=analysis)
            high = details.filter(impact="HIGH").count()
            medium = details.filter(impact="MEDIUM").count()
            low = details.filter(impact="LOW").count()
            total = high + medium + low

            # Score: start at 5, deduct per error severity
            score = max(1.0, 5.0 - (high * 1.5) - (medium * 0.75) - (low * 0.25))
            analysis.overall_score = round(score, 1)
            analysis.factual_grounding_score = max(1.0, 5.0 - high)
            analysis.factual_grounding_reason = f"Computed from {total} error(s)"
            analysis.privacy_and_safety_score = 5.0
            analysis.privacy_and_safety_reason = "No safety issues detected"
            analysis.instruction_adherence_score = max(1.0, 5.0 - high - (medium * 0.5))
            analysis.instruction_adherence_reason = f"Computed from {total} error(s)"
            analysis.optimal_plan_execution_score = max(1.0, 5.0 - high - medium)
            analysis.optimal_plan_execution_reason = f"Computed from {total} error(s)"
            analysis.insights = (
                f"Fallback scores computed from {total} detected error(s)."
            )
            analysis.save()
            logger.info(f"Computed fallback scores for trace {trace_id}: score={score}")

        error_count = analysis.total_errors or 0

        # Always mark trace as completed
        trace.error_analysis_status = TraceErrorAnalysisStatus.COMPLETED
        trace.save(update_fields=["error_analysis_status"])

        # Ingest embeddings for clustering if there are errors
        if error_count > 0:
            db = TraceErrorAnalysisDB()
            db.ingest_trace_error_embeddings(trace_id)

        # Log complete results to ClickHouse
        try:
            import json as _json

            from tracer.services.clickhouse.client import ClickHouseClient

            ch = ClickHouseClient()
            ch.insert(
                "falcon_analysis_log",
                [
                    {
                        "conversation_id": str(conversation.id),
                        "trace_id": str(trace_id),
                        "project_id": project_id,
                        "organization_id": str(org.id),
                        "model": str(result.get("model_used") or ""),
                        "mode": str(result.get("mode") or ""),
                        "skill_slug": "analyze-trace-errors",
                        "input_tokens": int(result.get("input_tokens") or 0),
                        "output_tokens": int(result.get("output_tokens") or 0),
                        "tool_calls": _json.dumps(collected_tool_calls, default=str),
                        "response": str(result.get("content") or "")[:10000],
                        "errors_found": int(error_count),
                        "overall_score": (
                            float(analysis.overall_score)
                            if analysis.overall_score is not None
                            else None
                        ),
                        "recommended_priority": str(
                            analysis.recommended_priority or ""
                        ),
                    }
                ],
            )
        except Exception as e:
            logger.warning(f"Failed to log to ClickHouse: {str(e)}")

        logger.info(
            f"Falcon analyzed trace {trace_id}: {error_count} errors, "
            f"score={analysis.overall_score}, elapsed={elapsed:.1f}s"
        )
        return {"success": True, "trace_id": trace_id, "error_count": error_count}

    except Exception as e:
        logger.exception(f"Falcon analysis failed for trace {trace_id}: {str(e)}")
        Trace.objects.filter(id=trace_id).update(
            error_analysis_status=TraceErrorAnalysisStatus.FAILED
        )
        return {"success": False, "trace_id": trace_id, "error": str(e)}
    finally:
        close_old_connections()


@temporal_activity(
    max_retries=2,
    time_limit=3600,
    queue="agent_compass",
)
def analyze_traces_on_demand(project_id: str, trace_ids: list):
    """
    On-demand trace analysis triggered by Falcon.
    Each trace is analyzed by a headless Falcon agent loop with the
    analyze-trace-errors skill. Runs in parallel via ThreadPoolExecutor.
    """
    try:
        close_old_connections()
        successful = 0
        failed = 0
        total_errors = 0

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(_falcon_analyze_single_trace, trace_id, project_id)
                for trace_id in trace_ids
            ]

            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result.get("success"):
                        successful += 1
                        total_errors += result.get("error_count", 0)
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    logger.error(f"Error processing future: {str(e)}")

        logger.info(
            f"On-demand batch complete for project {project_id}: "
            f"{successful} successful, {failed} failed, "
            f"{total_errors} total errors found"
        )

        if successful > 0:
            cluster_project_errors.delay(project_id)

    except Exception as e:
        logger.exception(
            f"Error in on-demand analysis for project {project_id}: {str(e)}"
        )
        raise
    finally:
        close_old_connections()


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
