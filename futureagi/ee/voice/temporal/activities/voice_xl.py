"""
Voice-specific XL queue activities (tasks_xl).

Extracted from simulate.temporal.activities.xl — contains voice provider
interactions and voice CSAT evaluation. These are resource-intensive
operations that run on the tasks_xl queue with long timeouts.
"""

import structlog
from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio import activity

from simulate.models.test_execution import CallExecution
from simulate.temporal.types.activities import (
    CalculateVoiceCSATInput,
    CalculateVoiceCSATOutput,
    FetchClientCallInput,
    FetchClientCallOutput,
)

logger = structlog.get_logger(__name__)


@activity.defn(name="fetch_client_call_data")
async def fetch_client_call_data(input: FetchClientCallInput) -> FetchClientCallOutput:
    """
    Fetch and persist client's call data from their provider account.

    For calls where the client is using their own provider account,
    we need to fetch their call data to get their side of the conversation,
    performance metrics, and cost breakdown.

    This activity both fetches AND persists the data directly to avoid
    passing large payloads through Temporal (2MB limit).

    Timeout: 10 minutes (with heartbeats every 30 seconds)
    Queue: tasks_xl
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    from tracer.models.observability_provider import ProviderChoices

    try:
        activity.logger.info(
            f"Fetching client call data for call_id={input.call_id}, "
            f"client_provider={input.client_provider}"
        )

        # livekit_bridge calls are handled entirely on our side; there is no
        # external provider account to query, so return an empty result and
        # let the workflow continue with simulator-side transcript for CSAT.
        if input.client_provider == "livekit_bridge":
            activity.logger.info(
                f"Skipping client call data fetch for livekit_bridge provider "
                f"(call_id={input.call_id})"
            )
            return FetchClientCallOutput(
                success=True,
            )

        # Bland's transcript/recording/cost are already fetched and persisted by
        # the main fetch_and_persist path via its engine, so there is nothing
        # extra to pull here. Bland also exposes no client-side latency metrics
        # or log URL, and its engine's get_customer_metrics/find_client_call
        # raise by design — so this skip is correctness-required, not just an
        # optimization: removing it would fail closed on the enrichment call.
        if input.client_provider == ProviderChoices.BLAND.value:
            activity.logger.info(
                f"Skipping client call data fetch for bland provider "
                f"(call_id={input.call_id}); data already persisted."
            )
            return FetchClientCallOutput(
                success=True,
            )

        from ee.voice.semantics import FAGICallData
        from ee.voice.services.voice_service_manager import VoiceServiceManager
        from tfc.temporal.common.heartbeat import Heartbeater

        # Use heartbeat context manager to prevent timeout
        async with Heartbeater() as heartbeater:
            heartbeater.details = ("fetching_client_data", input.call_id)

            # Fetch our call data
            call = await CallExecution.objects.aget(id=input.call_id)

            # Convert CallExecution to FAGICallData format
            # Read provider-keyed data from call.raw_log
            provider_call_data = getattr(call, "provider_call_data", {}) or {}
            if ProviderChoices.LIVEKIT in provider_call_data:
                provider_key = ProviderChoices.LIVEKIT
            else:
                provider_key = ProviderChoices.VAPI

            our_call_data = FAGICallData(
                call_id=str(call.id),
                call_type=input.call_type,
                status=call.status,
                assistant_id=input.system_assistant_id or "",
                system_phone_number=input.system_phone_number or "",
                customer_phone_number=input.customer_phone_number or "",
                system_phone_number_id=input.system_phone_number_id or "",
                transcript_available=call.transcript_available,
                recording_available=call.recording_available,
                recording_url=call.recording_url,
                log_url=None,
                created_at=call.created_at.isoformat() if call.created_at else None,
                updated_at=call.updated_at.isoformat() if call.updated_at else None,
                # Use started_at if available, fallback to created_at for matching
                started_at=(
                    (call.started_at or call.created_at).isoformat()
                    if (call.started_at or call.created_at)
                    else None
                ),
                ended_at=call.ended_at.isoformat() if call.ended_at else None,
                duration_seconds=call.duration_seconds or 0.0,
                cost=call.cost_cents / 100.0 if call.cost_cents else 0.0,
                raw_log=call.provider_call_data or {provider_key: {}},
            )

            # Initialize VoiceServiceManager with client's API key
            voice_manager = VoiceServiceManager(
                api_key=input.client_api_key,
                system_voice_provider=input.client_provider,
            )

            # Use existing customer_call_id if already set (e.g. outbound calls),
            # otherwise find it by matching with our call data (e.g. inbound calls)
            client_call_id = call.customer_call_id
            if client_call_id:
                activity.logger.info(
                    f"Using existing customer_call_id={client_call_id} for call_id={input.call_id}"
                )
            else:
                from ee.voice.services.types.voice import FindClientCallInput

                client_call_id = await sync_to_async(
                    voice_manager.find_client_call, thread_sensitive=False
                )(
                    FindClientCallInput(
                        customer_api_key=input.client_api_key,
                        customer_assistant_id=input.client_assistant_id,
                        our_call_data=our_call_data,
                        customer_voice_service_provider=input.client_provider,
                        time_window_seconds=10,
                    )
                )

            if not client_call_id:
                activity.logger.warning(
                    f"Could not find matching client call for call_id={input.call_id}"
                )
                return FetchClientCallOutput(
                    success=False,
                    error="Could not find matching client call",
                )

            activity.logger.info(
                f"Found client call ID: {client_call_id} for call_id={input.call_id}"
            )

            heartbeater.details = ("fetching_call_details", input.call_id)

            # Fetch full call data from client's account
            client_call_data = await voice_manager.get_call_async(
                call_id=client_call_id,
                call_data_stored=True,
            )

            # Normalize client's metrics and cost breakdown
            customer_metrics_result = await sync_to_async(
                voice_manager.get_customer_metrics, thread_sensitive=False
            )(client_call_data)

            client_metrics = customer_metrics_result.system_metrics
            client_cost_breakdown = customer_metrics_result.cost_breakdown
            client_total_cost = customer_metrics_result.total_cost

            # Extract client's raw data for persistence (needed for provider_call_data storage)
            client_raw_data = client_call_data.raw_log.get(input.client_provider, {})

            # Extract recording URLs using VSM helper method
            recording_urls = voice_manager.get_recording_urls(client_raw_data)

            activity.logger.info(
                f"Fetched client call data: call_id={client_call_id}, "
                f"cost=${client_total_cost:.4f}"
            )

            # ========================================
            # PERSIST DATA DIRECTLY TO DB
            # ========================================
            heartbeater.details = ("persisting_client_data", input.call_id)

            update_fields = [
                "customer_call_id",
                "customer_cost_breakdown",
                "customer_latency_metrics",
                "provider_call_data",
            ]

            call.customer_call_id = client_call_id

            if client_cost_breakdown:
                call.customer_cost_breakdown = client_cost_breakdown

            if client_metrics:
                turn_latencies = []
                if client_call_data.performance_metrics:
                    provider_perf_metrics = client_call_data.performance_metrics.get(
                        input.client_provider, {}
                    )
                    if isinstance(provider_perf_metrics, dict):
                        turn_latencies = provider_perf_metrics.get("turnLatencies", [])

                call.customer_latency_metrics = {
                    "systemMetrics": client_metrics,
                    "turnLatencies": turn_latencies,
                }

            if client_raw_data:
                if not call.provider_call_data:
                    call.provider_call_data = {}
                existing_recording = call.provider_call_data.get(
                    input.client_provider, {}
                ).get("recording")
                call.provider_call_data[input.client_provider] = client_raw_data
                if existing_recording:
                    call.provider_call_data[input.client_provider][
                        "recording"
                    ] = existing_recording

            if client_call_data.analysis_data and not call.analysis_data:
                client_analysis = client_call_data.analysis_data.get(
                    input.client_provider, {}
                )
                if client_analysis:
                    call.analysis_data = client_analysis
                    update_fields.append("analysis_data")

            if client_call_data.summary and not call.call_summary:
                call.call_summary = client_call_data.summary
                update_fields.append("call_summary")

            if client_call_data.recording_url and not call.recording_url:
                call.recording_url = client_call_data.recording_url
                update_fields.append("recording_url")

            stereo_url = recording_urls.get("stereo")
            if stereo_url and not call.stereo_recording_url:
                call.stereo_recording_url = stereo_url
                update_fields.append("stereo_recording_url")

            if client_call_data.ended_reason and not call.ended_reason:
                call.ended_reason = client_call_data.ended_reason
                update_fields.append("ended_reason")

            if client_call_data.recording_available and not call.recording_available:
                call.recording_available = client_call_data.recording_available
                update_fields.append("recording_available")

            if client_call_data.transcript_available and not call.transcript_available:
                call.transcript_available = client_call_data.transcript_available
                update_fields.append("transcript_available")

            client_log_url = client_call_data.log_url
            if client_log_url and not call.customer_log_url:
                call.customer_log_url = client_log_url
                update_fields.append("customer_log_url")

            await call.asave(update_fields=update_fields)

            activity.logger.info(
                f"Persisted client call data for call_id={input.call_id} "
                f"with provider key={input.client_provider}"
            )

            # Extract tool call messages from client's provider data
            from simulate.temporal.activities.xl import _inject_client_tool_calls

            await _inject_client_tool_calls(call, client_raw_data)

            # Ingest client-side call logs
            if client_log_url:
                heartbeater.details = ("ingesting_client_logs", input.call_id)
                try:
                    from simulate.models import CallLogEntry
                    from ee.voice.tasks.call_log_tasks import _ingest_call_logs

                    already_ingested = await CallLogEntry.objects.filter(
                        call_execution=call,
                        source=CallLogEntry.LogSource.CUSTOMER,
                    ).aexists()
                    if not already_ingested:
                        await sync_to_async(
                            _ingest_call_logs, thread_sensitive=False
                        )(
                            str(call.id),
                            client_log_url,
                            source=CallLogEntry.LogSource.CUSTOMER,
                            call_id=client_call_id,
                            api_key=input.client_api_key,
                        )
                except Exception as log_err:  # noqa: BLE001
                    activity.logger.warning(
                        f"Failed to ingest client logs for call_id={input.call_id}: "
                        f"{log_err}"
                    )

            return FetchClientCallOutput(
                success=True,
                client_call_id=client_call_id,
                client_metrics=client_metrics,
                client_cost_breakdown=client_cost_breakdown,
                client_total_cost=client_total_cost,
                client_raw_data=None,
            )

    except CallExecution.DoesNotExist:
        activity.logger.error(f"Call not found: {input.call_id}")
        activity.logger.exception(f"Call not found: {input.call_id}")
        return FetchClientCallOutput(
            success=False,
            error=f"Call not found: {input.call_id}",
        )

    except Exception as e:
        activity.logger.error(f"Failed to fetch client call data: {str(e)}")
        activity.logger.exception(f"Failed to fetch client call data: {str(e)}")
        return FetchClientCallOutput(
            success=False,
            error=str(e),
        )


@activity.defn(name="calculate_voice_csat_score")
async def calculate_voice_csat_score(
    input: CalculateVoiceCSATInput,
) -> CalculateVoiceCSATOutput:
    """
    Calculate CSAT score (overall_score) for a voice call.

    Priority order:
    1. AgentEvaluator in agent mode with turing_large (1-10 scale based on
       customer satisfaction). Audio input is auto-detected from the
       recording URL and the model auto-upgrades to turing_large_xl for
       audio support.
    2. Fallback to VAPI's successEvaluation from analysis_data

    Timeout: 10 minutes
    Queue: tasks_xl
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    from tfc.temporal.common.heartbeat import Heartbeater
    from tracer.models.observability_provider import ProviderChoices

    activity.logger.info(f"Calculating CSAT score for call_id={input.call_id}")

    async with Heartbeater() as heartbeater:
        heartbeater.details = ("calculating_csat", input.call_id)

        try:
            call = await CallExecution.objects.aget(id=input.call_id)

            if call.overall_score is not None:
                activity.logger.info(
                    f"CSAT score already set for call {input.call_id}: {call.overall_score}, skipping"
                )
                return CalculateVoiceCSATOutput(
                    success=True,
                    csat_score=call.overall_score,
                    skipped=True,
                )

            csat_score = None

            # Priority 1: Try AgentEvaluator with recording
            if call.recording_url:
                try:
                    from ee.evals.llm.agent_evaluator.evaluator import (
                        AgentEvaluator,
                    )

                    csat_choices = [
                        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
                    ]
                    csat_rule_prompt = (
                        "Assess the overall satisfaction expressed by the customer during the interaction. "
                        "Consider explicit statements (e.g., 'thank you, this was helpful', 'this is frustrating') "
                        "as well as implicit behavioral cues such as tone, cooperation, politeness, engagement, "
                        "or dissatisfaction. Assign a single CSAT score from 1 to 10, where 1 indicates very "
                        "dissatisfied and 10 indicates very satisfied. Only use evidence present in the "
                        "interaction; do not infer beyond what is clearly communicated.\n\n"
                        "## Inputs\n\n"
                        "<output>{{output}}</output>"
                    )

                    def run_csat_evaluation():
                        evaluator = AgentEvaluator(
                            rule_prompt=csat_rule_prompt,
                            model="turing_large",
                            output_type="choices",
                            choices=csat_choices,
                            agent_mode="agent",
                        )
                        return evaluator.run(
                            output=call.recording_url,
                            required_keys=["output"],
                        )

                    heartbeater.details = ("running_evaluator", input.call_id)
                    batch_result = await sync_to_async(
                        run_csat_evaluation, thread_sensitive=False
                    )()
                    eval_result = batch_result.eval_results[0]
                    csat_score = float(eval_result["data"]["result"])

                    activity.logger.info(
                        f"CSAT score calculated via AgentEvaluator for call {input.call_id}: {csat_score}"
                    )

                except ImportError as e:
                    activity.logger.error(
                        "CSAT AgentEvaluator import failed — CSAT will not run "
                        f"for any call until this is fixed. Error: {e!r}"
                    )
                    raise

                except Exception as e:
                    activity.logger.warning(
                        f"AgentEvaluator failed for call {input.call_id}: {str(e)}, "
                        "falling back to successEvaluation"
                    )

            # Priority 2: Fall back to successEvaluation from analysis_data
            if csat_score is None and call.analysis_data:
                heartbeater.details = ("fallback_success_eval", input.call_id)
                success_eval = call.analysis_data.get("successEvaluation")

                if success_eval is not None:
                    if isinstance(success_eval, str):
                        if success_eval.lower() == "true":
                            csat_score = 1.0
                        elif success_eval.lower() == "false":
                            csat_score = 0.0
                        else:
                            try:
                                csat_score = float(success_eval)
                            except (ValueError, TypeError):
                                pass
                    elif isinstance(success_eval, (int, float)):
                        csat_score = float(success_eval)

                    if csat_score is not None:
                        activity.logger.info(
                            f"CSAT score set from successEvaluation for call {input.call_id}: {csat_score}"
                        )

            if csat_score is not None:
                heartbeater.details = ("saving_score", input.call_id)
                call.overall_score = csat_score
                await call.asave(update_fields=["overall_score"])

                return CalculateVoiceCSATOutput(
                    success=True,
                    csat_score=csat_score,
                    skipped=False,
                )

            activity.logger.warning(
                f"Could not calculate CSAT score for call {input.call_id}: "
                "no recording and no successEvaluation available"
            )
            return CalculateVoiceCSATOutput(
                success=True,
                skipped=True,
                error="No recording URL and no successEvaluation available",
            )

        except CallExecution.DoesNotExist:
            activity.logger.error(
                f"Call not found for CSAT calculation: {input.call_id}"
            )
            return CalculateVoiceCSATOutput(
                success=False,
                error=f"Call not found: {input.call_id}",
            )

        except Exception as e:
            activity.logger.error(f"Failed to calculate CSAT score: {str(e)}")
            activity.logger.exception(f"Failed to calculate CSAT score: {str(e)}")
            return CalculateVoiceCSATOutput(
                success=False,
                error=str(e),
            )
