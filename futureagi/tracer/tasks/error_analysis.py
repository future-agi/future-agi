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
from tracer.queries import background_analysis_state
from tracer.services.analysis_result_binding import (
    ResultOwnershipError,
    attempt_scope,
    background_attempt,
    current_attempt,
)
from tracer.services.background_analysis_dispatch import durable_background_dispatch

logger = structlog.get_logger(__name__)

# Per trace, deliberately below the enclosing batch activity's one-hour ceiling.
FALCON_ANALYSIS_TIMEOUT_SECONDS = 300


def analyze_single_trace(trace_id, task_id, ingest_embeddings: bool = True):
    """Fence every legacy batch/queued Judge before running any analysis."""
    identity = current_attempt()
    if identity is not None:
        if identity[1] != str(trace_id):
            raise ResultOwnershipError("analysis_identity_mismatch")
        return _analyze_single_trace_in_scope(trace_id, task_id, False)
    if background_analysis_state.get_state(trace_id) is not None:
        return {
            "success": False,
            "trace_id": trace_id,
            "code": "analysis_generation_required",
        }
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        root = reader.root_ids_by_trace_ids([str(trace_id)]).get(str(trace_id))
    if not root:
        return {"success": False, "trace_id": trace_id, "code": "analysis_failed"}
    try:
        with background_attempt(root[1], trace_id):
            result = _analyze_single_trace_in_scope(trace_id, task_id, False)
            if not result or not result.get("success"):
                raise ResultOwnershipError("analysis_failed")
        if ingest_embeddings:
            from tracer.queries.error_analysis import TraceErrorAnalysisDB

            TraceErrorAnalysisDB().ingest_trace_error_embeddings(trace_id)
        return result
    except ResultOwnershipError:
        return {"success": False, "trace_id": trace_id, "code": "analysis_failed"}


def _analyze_single_trace_in_scope(trace_id, task_id, ingest_embeddings: bool = True):
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
    from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
    from tracer.models.project import Project
    from tracer.queries import deep_analysis_state
    from tracer.queries.error_analysis import TraceErrorAnalysisDB
    from tracer.queries.helpers import get_default_workspace_for_project
    from tracer.services.clickhouse.v2 import get_reader

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
        from tracer.services.analysis_result_binding import current_attempt

        if current_attempt() is not None:
            from ee.agenthub.traceerroragent.background_payload_consumers import (
                evidence,
            )
            from ee.agenthub.traceerroragent.background_payload_reports import (
                require_publication,
            )

            require_publication(result)
            error_count = len(evidence(result, result._witness, "publication").errors)
        else:
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

                def llm_usage_properties(obj):
                    return {}

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
def run_deep_analysis_on_demand(
    trace_id: str, force: bool = False, *, project_id=None, generation=None
) -> dict:
    """Run a generation-owned Judge retry without deleting previous results.

    New dispatches carry an admitted generation. Pre-deploy payloads may enter
    only when no lifecycle owner exists; force is not permission to bypass it.
    The Judge writes a fresh candidate, and only this owner binds it on success.
    """
    close_old_connections()

    if generation is None:
        # Pre-deploy force payloads cannot delete or adopt a durable result.
        if background_analysis_state.get_state(trace_id) is not None:
            return {
                "success": False,
                "trace_id": trace_id,
                "code": "analysis_generation_required",
            }
        return analyze_single_trace(trace_id, None, ingest_embeddings=False)

    if not background_analysis_state.admit(project_id, trace_id, generation):
        return {
            "success": False,
            "trace_id": trace_id,
            "code": "analysis_generation_superseded",
        }
    try:
        with attempt_scope(project_id, trace_id, generation):
            result = analyze_single_trace(trace_id, None, ingest_embeddings=False)
        if result and result.get("success"):
            if background_analysis_state.complete(project_id, trace_id, generation):
                return result
            return {
                "success": False,
                "trace_id": trace_id,
                "code": "analysis_result_missing_or_superseded",
            }
        background_analysis_state.abort(
            project_id, trace_id, generation, "analysis_failed"
        )
        return result
    except BaseException:
        background_analysis_state.abort(
            project_id, trace_id, generation, "analysis_failed"
        )
        raise
    finally:
        close_old_connections()


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
    from tfc.capabilities.background_analysis import (
        SETTING,
        background_analysis_enabled,
    )

    # This scheduler admits new work even when invoked by an old schedule/retry.
    # Already-admitted batches and per-trace activities deliberately remain ungated.
    if not background_analysis_enabled():
        logger.info(f"{SETTING} is OFF; skipping new background trace-error analysis")
        return

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
    requesting_user_id=None,
    organization_id=None,
    workspace_id=None,
    generation=None,
):
    """Own admission and terminal publication, outside any timed continuation."""
    from tfc.middleware.workspace_context import workspace_context

    with workspace_context(None):
        # Direct calls / payloads queued before this migration can start an
        # attempt. New queued work always carries its dispatch-time generation.
        if generation is None:
            generation = background_analysis_state.begin(
                project_id, trace_id, only_if_absent=True
            )
        if generation is None or not background_analysis_state.admit(
            project_id, trace_id, generation
        ):
            return {
                "success": False,
                "trace_id": trace_id,
                "code": "analysis_generation_superseded",
            }
        try:
            with attempt_scope(project_id, trace_id, generation):
                result = _falcon_analyze_trace_in_scope(
                    trace_id,
                    project_id,
                    requesting_user_id,
                    organization_id,
                    workspace_id,
                )
            after_complete = result.pop("_after_complete", None)
            # The guard's final checks and timed finalizer have returned. Only
            # this owner may publish success; a late daemon finalizer cannot.
            if result.get("success"):
                if background_analysis_state.complete(project_id, trace_id, generation):
                    if after_complete is not None:
                        with attempt_scope(project_id, trace_id, generation):
                            after_complete()
                    return result
                return {
                    "success": False,
                    "trace_id": trace_id,
                    "code": "analysis_generation_superseded",
                }
            background_analysis_state.abort(
                project_id, trace_id, generation, result.get("code", "analysis_failed")
            )
            return result
        except BaseException:
            background_analysis_state.abort(
                project_id, trace_id, generation, "analysis_failed"
            )
            raise


def _falcon_analyze_trace_in_scope(
    trace_id, project_id, requesting_user_id, organization_id, workspace_id
):
    """Analyze a trace with a request-bound background Falcon agent."""
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

    from rest_framework.exceptions import PermissionDenied

    from tfc.middleware.workspace_context import set_workspace_context
    from tfc.utils.api_errors import exception_code
    from tracer.queries.error_analysis import TraceErrorAnalysisDB
    from tracer.services.falcon_analysis_guard import AnalysisAborted, AnalysisGuard
    from tracer.services.falcon_analysis_scope import resolve_analysis_scope

    close_old_connections()
    conversation = None
    guard = None

    try:
        project, ctx = resolve_analysis_scope(
            project_id, requesting_user_id, organization_id, workspace_id
        )
        user, org, workspace = ctx.user, ctx.organization, ctx.workspace
        set_workspace_context(
            workspace=workspace, organization=org, user=user, require_workspace=True
        )

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

        guard = AnalysisGuard(
            project_id,
            requesting_user_id,
            organization_id,
            workspace_id,
            FALCON_ANALYSIS_TIMEOUT_SECONDS,
        )
        ctx.background_analysis = guard
        agent = AgentLoop(tool_context=ctx, conversation=conversation)
        from tracer.services.analysis_result_completion import (
            CompletionWitness,
            RecordingLLM,
        )

        witness = CompletionWitness(agent.MAX_ITERATIONS)
        guard.completion_witness = witness
        agent.llm_client = RecordingLLM(agent.llm_client, witness, raw=True)
        from ee.agenthub.traceerroragent.background_payload_wire import (
            install_falcon_dispatch,
        )

        install_falcon_dispatch(agent, witness)

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
                witness.observe_tool_result(
                    event["data"].get("status"), event["data"].get("result_full")
                )
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
                witness.failed = True
                logger.error(
                    f"Falcon agent error for trace {trace_id}: {event['data']}"
                )

        import time as _time

        start_time = _time.time()

        async def run():
            try:
                return await asyncio.wait_for(
                    agent.run(
                        user_message=f"Analyze trace {trace_id} for errors.",
                        history_messages=[],
                        send_callback=logging_callback,
                        context_page="tracing",
                        skill=skill,
                    ),
                    timeout=guard.remaining,
                )
            except TimeoutError:
                witness.failed = True
                guard.abort("analysis_deadline_exceeded")
            except BaseException:
                witness.failed = True
                raise

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
        finally:
            loop.close()

        # Ordinary AgentLoop return is not evidence of completion. Require a
        # recorded normal terminal response, no failed call, and budget left.
        if not witness.satisfied or any(
            event["type"] == "error" for event in all_events
        ):
            guard.abort("analysis_failed")

        from ee.agenthub.traceerroragent.background_payload_reports import falcon_report
        from ee.agenthub.traceerroragent.background_payloads import require_payload

        validated_report = falcon_report(result, witness)
        report_text = require_payload(validated_report, witness, "falcon")
        elapsed = _time.time() - start_time

        # Log what happened in the agent loop
        event_types = [e["type"] for e in all_events]
        logger.info(
            f"Falcon loop for trace {trace_id[:8]}: "
            f"elapsed={elapsed:.1f}s tools={len(collected_tool_calls)} "
            f"events={len(all_events)} event_types={event_types} "
            f"response_len={len(report_text)} "
            f"tokens={result.get('input_tokens', 0)}+{result.get('output_tokens', 0)}"
        )

        def export_completed():
            # Each external effect consumes the bound result, never a draft.
            from tracer.models.trace_error_analysis import TraceErrorAnalysis

            analysis = TraceErrorAnalysis.objects.filter(
                trace_id=trace_id, pk=current_attempt()[2]
            ).first()
            if analysis is None:
                return
            error_count = analysis.total_errors or 0
            if error_count:
                guard.check()
                TraceErrorAnalysisDB().ingest_trace_error_embeddings(
                    trace_id, guard=guard
                )
                guard.check()
            # Log complete results to ClickHouse
            try:
                import json as _json

                from tracer.services.clickhouse.client import ClickHouseClient

                guard.check()
                ch = ClickHouseClient()
                guard.check()
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
                            "tool_calls": _json.dumps(
                                collected_tool_calls, default=str
                            ),
                            "response": require_payload(
                                validated_report, witness, "falcon"
                            )[:10000],
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
            except AnalysisAborted:
                raise
            except Exception as e:
                guard.check()
                logger.warning(f"Failed to log to ClickHouse: {str(e)}")

            guard.check()
            # Background clustering must not escape into an unguarded batch tail.
            if error_count > 0:
                cluster_project_errors.delay(project_id)
                guard.check()

        def after_complete():
            # Auxiliary export failure cannot rewrite an already completed
            # analysis. The guard still bounds and reauthorizes every effect.
            try:
                guard.run_sync(export_completed, cleanup=close_old_connections)
            except (AnalysisAborted, Exception):
                logger.warning("analysis_completed_export_failed", trace_id=trace_id)

        def finalize():
            # No success/fallback writes on behalf of a principal revoked during
            # the final model response (including responses without a tool call).
            guard.check()
            # Require a scored candidate, including for a legitimate clean trace.
            from tracer.models.trace_error_analysis import TraceErrorAnalysis

            analysis = (
                TraceErrorAnalysis.drafts.filter(trace_id=trace_id)
                .order_by("-analysis_date")
                .first()
            )

            guard.check()
            if not analysis or analysis.overall_score is None:
                # A stop alone cannot authorize synthetic clean/fallback scores.
                # The agent must have submitted its own complete scored result.
                witness.fallback_used = True
                guard.abort("analysis_failed")

            guard.check()
            error_count = analysis.total_errors or 0

            logger.info(
                f"Falcon analyzed trace {trace_id}: {error_count} errors, "
                f"score={analysis.overall_score}, elapsed={elapsed:.1f}s"
            )
            return {
                "success": True,
                "trace_id": trace_id,
                "error_count": error_count,
                "_after_complete": after_complete,
            }

        # Include ORM, embedding and logging finalization in the same deadline.
        # ContextVars follow the continuation; abandoned dependencies cannot
        # authorize later effects when their synchronous call finally returns.
        return guard.run_sync(finalize, cleanup=close_old_connections)

    except AnalysisAborted as exc:
        # System-owned lifecycle receipt only; do not synthesize successful
        # analysis rows, persist actor findings, or schedule embeddings here.
        if conversation is not None:
            metadata = dict(conversation.metadata or {})
            metadata["analysis_outcome"] = {"status": "aborted", "code": exc.code}
            Conversation._base_manager.filter(pk=conversation.pk).update(
                metadata=metadata
            )
        return {
            "success": False,
            "trace_id": trace_id,
            "outcome": "aborted",
            "code": exc.code,
        }
    except PermissionDenied as exc:
        return {
            "success": False,
            "trace_id": trace_id,
            "code": exception_code(exc),
            "error": str(exc.detail),
        }
    except Exception as e:
        logger.exception(f"Falcon analysis failed for trace {trace_id}: {str(e)}")
        return {"success": False, "trace_id": trace_id, "error": str(e)}
    finally:
        close_old_connections()


@durable_background_dispatch
@temporal_activity(
    max_retries=2,
    time_limit=3600,
    queue="agent_compass",
)
def analyze_traces_on_demand(
    project_id: str,
    trace_ids: list,
    *,
    requesting_user_id=None,
    organization_id=None,
    workspace_id=None,
    generations=None,
):
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
                executor.submit(
                    _falcon_analyze_single_trace,
                    trace_id,
                    project_id,
                    requesting_user_id=requesting_user_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    generation=generations[str(trace_id)]
                    if generations is not None
                    else None,
                )
                for trace_id in trace_ids
                if generations is None or str(trace_id) in generations
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

        # Each successful trace dispatches clustering under its live guard.
        # A later batch tail must not reuse a completed trace's authorization.

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
