"""
Large queue activities (tasks_l).

Standard operations for call lifecycle management, provider interactions,
and result persistence. These activities run on the tasks_l queue with
moderate timeouts (5-10 minutes).

All activities use async functions with Django's async ORM for non-blocking operations.

Uses VoiceServiceManager for all voice provider operations:
- initiate_call: Creates assistant and initiates calls (inbound/outbound)
- monitor_call_until_complete: Polls call status until completion
- fetch_and_persist_call_result: Retrieves transcript via get_call() and  Saves results to database

IMPORTANT: Each activity calls _close_old_connections() at the start to prevent
connection pool exhaustion when using PgBouncer. Without this, connections
accumulate and hit PgBouncer's pool limit (~20 by default).
"""

# Pre-import transformers at module level (worker startup) to avoid race conditions.
# The import chain VoiceServiceManager -> VapiService -> ConversationGraphGenerator
# -> SyntheticDataAgent uses AutoTokenizer. When imported lazily in thread pool
# workers (thread_sensitive=False), concurrent imports can fail due to Python's
# import system not being fully thread-safe for complex packages.
try:
    import transformers  # noqa: F401
except ImportError:
    pass

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio import activity

from ee.voice.exceptions import VapiApiError
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.semantics import CallType
from simulate.temporal.types.activities import (
    CalculateConversationMetricsInput,
    DeductCostInput,
    FetchAndPersistCallResultInput,
    FetchAndPersistCallResultOutput,
    FetchTranscriptInput,
    FetchTranscriptOutput,
    InitiateCallInput,
    InitiateCallOutput,
    MonitorCallInput,
    MonitorCallOutput,
    PersistResultInput,
)


@activity.defn(name="initiate_call")
async def initiate_call(input: InitiateCallInput) -> InitiateCallOutput:
    """
    Initiate a call with the voice provider.

    For INBOUND calls (FutureAGI calls user's agent):
    - Creates simulator assistant with the persona
    - Initiates call to user's phone number using initiate_inbound_call()

    For OUTBOUND calls (User's agent calls FutureAGI):
    - Creates simulator assistant with SYSTEM credentials
    - Assigns assistant to acquired phone number
    - Creates outbound call using USER's credentials to call the acquired phone

    Timeout: 2 minutes (with background heartbeats via Heartbeater)
    Queue: tasks_l
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    from tfc.temporal.common.heartbeat import Heartbeater

    # Use Heartbeater to send background heartbeats during external API calls
    # factor=4 with 1-minute heartbeat_timeout = heartbeat every 15 seconds
    async with Heartbeater(factor=4) as heartbeater:
        heartbeater.details = (f"initiating call {input.call_id}",)

        try:
            from ee.voice.services.voice_service_manager import VoiceServiceManager

            call_data = input.call_data
            call_type = call_data.get("call_type")
            is_inbound = call_type in ["inbound", "INBOUND"]

            activity.logger.info(
                f"Initiating call: call_id={input.call_id}, "
                f"type={call_type}, is_inbound={is_inbound}"
            )

            # Common variables for both inbound and outbound
            system_prompt = input.system_prompt
            voice_settings = input.voice_settings
            metadata = input.metadata
            background_sound = voice_settings.get("background_sound")
            language = voice_settings.get("language", "en-US")

            # Create system VoiceServiceManager with provider from input
            from tracer.models.observability_provider import ProviderChoices

            provider_enum = ProviderChoices(input.provider)
            system_vsm = VoiceServiceManager(system_voice_provider=provider_enum)

            connection_type = input.connection_type

            # Web bridge calls use the same setup regardless of direction
            _is_web_bridge = (connection_type or "").startswith("web_")

            if is_inbound or _is_web_bridge:
                # INBOUND (or web bridge): Creates simulator assistant + calls
                # user's phone (SIP) or starts WebRTC bridge (web call).

                user_phone_number = call_data.get("customer_phone_number")

                # Only require phone for SIP calls
                if not connection_type and not user_phone_number:
                    raise ValueError(
                        "No customer phone number provided for inbound SIP call"
                    )

                from ee.voice.services.types.voice import InboundCallInput

                result = await sync_to_async(
                    system_vsm.initiate_inbound_call, thread_sensitive=False
                )(
                    InboundCallInput(
                        call_id=input.call_id,
                        user_phone_number=user_phone_number,
                        system_prompt=system_prompt,
                        voice_settings=voice_settings,
                        metadata={
                            **(metadata or {}),
                            "customer_api_key": input.user_api_key or "",
                            "customer_assistant_id": input.user_assistant_id or "",
                        },
                        connection_type=connection_type,
                    )
                )

                if not result.success:
                    raise ValueError(
                        f"Inbound call initiation failed: {result.error or 'Unknown error'}"
                    )

                activity.logger.info(
                    f"Successfully initiated inbound call {input.call_id}, "
                    f"provider_call_id={result.provider_call_id}"
                )

                return InitiateCallOutput(
                    success=True,
                    provider_call_id=result.provider_call_id,
                    provider_data=result.provider_data or {},
                )

            else:
                # OUTBOUND: User's agent calls FutureAGI to test their outbound agents
                # 1. System-side setup (provider-agnostic via initiate_outbound_call)
                # 2. Trigger outbound call using USER's credentials

                # Validate required outbound parameters
                user_api_key = input.user_api_key or None
                user_assistant_id = input.user_assistant_id or None
                user_phone = input.user_phone_number or None
                system_phone_number = input.phone_number

                missing_fields = []
                if not system_phone_number:
                    missing_fields.append("phone_number (from phone acquisition)")
                if not user_api_key:
                    missing_fields.append(
                        "user_api_key (from configuration_snapshot.api_key)"
                    )
                if not user_assistant_id:
                    missing_fields.append(
                        "user_assistant_id (from configuration_snapshot.assistant_id)"
                    )
                if not user_phone:
                    missing_fields.append(
                        "user_phone_number (from configuration_snapshot.contact_number)"
                    )

                if missing_fields:
                    raise ValueError(
                        f"Missing required fields for outbound call: {', '.join(missing_fields)}"
                    )

                # Step 1: System-side setup (polymorphic — VAPI or LiveKit)
                from ee.voice.services.types.voice import OutboundCallInput

                setup_result = await sync_to_async(
                    system_vsm.initiate_outbound_call, thread_sensitive=False
                )(
                    OutboundCallInput(
                        call_execution_id=input.call_id,
                        system_prompt=system_prompt,
                        voice_settings=voice_settings,
                        phone_number=system_phone_number,
                        provider_phone_id=input.provider_phone_id,
                        metadata=metadata,
                    )
                )

                if not setup_result.success:
                    raise ValueError(
                        f"Outbound call setup failed: {setup_result.error or 'System-side setup failed'}"
                    )

                # Step 2: Trigger the customer's agent to dial from their own
                # provider account — the data plane for SIP outbound. The engine
                # is selected from the registry by client_provider (prepare_call
                # already restricts SIP outbound to supported providers), so VAPI
                # and Bland dispatch through one call. Each engine's
                # create_outbound_call returns a payload with an "id" key.
                from tracer.models.observability_provider import ProviderChoices

                try:
                    user_vsm = VoiceServiceManager(
                        api_key=user_api_key,
                        system_voice_provider=ProviderChoices(input.client_provider),
                    )
                    trigger_response = await sync_to_async(
                        user_vsm.engine.create_outbound_call,
                        thread_sensitive=False,
                    )(
                        assistant_id=user_assistant_id,
                        from_phone_number=user_phone,
                        to_phone_number=setup_result.phone_number,
                        metadata={
                            "test_type": "outbound_simulation",
                            "call_id": input.call_id,
                            **metadata,
                        },
                    )
                    provider_call_id = trigger_response.get("id")

                    if not provider_call_id:
                        raise ValueError(
                            "No call ID returned from customer's provider account"
                        )

                    activity.logger.info(
                        f"Successfully initiated outbound call {input.call_id}, "
                        f"provider_call_id={provider_call_id}, "
                        f"acquired_phone={setup_result.phone_number}"
                    )

                    return InitiateCallOutput(
                        success=True,
                        provider_call_id=provider_call_id,
                        provider_data={
                            "assistant_id": setup_result.assistant_id,
                            "phone_number": setup_result.phone_number,
                            "trigger_response": trigger_response,
                        },
                    )

                except VapiApiError as e:
                    activity.logger.exception(f"Failed to create outbound call: {e}")
                    raise
                except Exception as e:
                    activity.logger.exception(f"Failed to create outbound call: {e}")
                    raise

        except Exception as e:
            activity.logger.error(f"Failed to initiate call {input.call_id}: {str(e)}")
            activity.logger.exception(
                f"Failed to initiate call {input.call_id}: {str(e)}"
            )
            # Re-raise so Temporal's retry policy can retry on transient errors
            raise


@activity.defn(name="monitor_call_until_complete")
async def monitor_call_until_complete(input: MonitorCallInput) -> MonitorCallOutput:
    """
    Monitor call status with provider until completion.

    Polls the voice provider at regular intervals to check call status.
    Uses Heartbeater to send background heartbeats during blocking API calls.
    Returns final call status when call completes.

    Uses VoiceServiceManager.get_call() which returns normalized FAGICallData.

    This is a long-running activity that can take up to 4 hours for voice calls.

    Timeout: Relies on heartbeat_timeout (not start_to_close_timeout)
    Queue: tasks_l
    """
    import asyncio
    import time

    from simulate.semantics import CallExecutionStatus
    from tfc.temporal.common.heartbeat import Heartbeater

    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(
            f"Monitoring call: call_id={input.call_id}, "
            f"provider_call_id={input.provider_call_id}, "
            f"poll_interval={input.poll_interval_seconds}s, "
            f"max_duration={input.max_duration_seconds}s"
        )

        from ee.voice.services.voice_service_manager import VoiceServiceManager
        from tracer.models.observability_provider import ProviderChoices

        # Only OUTBOUND calls poll the customer's provider account: the
        # provider_call_id belongs there and we poll with the customer's API key
        # (via provider_config). Inbound flows through the VAPI simulator, so its
        # call id lives in our VAPI account — poll with the system engine. An
        # unknown/unregistered client_provider (web_<provider>, livekit_bridge)
        # falls back to VAPI, preserving today's behavior; polling Bland with a
        # VAPI call-id would throw every iteration and spin the monitor to its
        # 4h cap.
        api_key = (
            input.provider_config.get("api_key") if input.provider_config else None
        )
        try:
            client_enum = ProviderChoices(input.client_provider)
        except ValueError:
            client_enum = None
        poll_provider = (
            client_enum
            if input.call_type == CallType.OUTBOUND.value
            and client_enum in VoiceServiceManager.ENGINE_REGISTRY
            else ProviderChoices.VAPI
        )
        voice_manager = VoiceServiceManager(
            api_key=api_key or "", system_voice_provider=poll_provider
        )
        terminal_statuses = {
            CallExecutionStatus.ANALYZING,
            CallExecutionStatus.FAILED,
            CallExecutionStatus.CANCELLED,
        }
        start_time = time.time()

        # Use Heartbeater to send background heartbeats during blocking API calls
        # This prevents heartbeat timeout when get_call() is slow
        # factor=4 with 1-minute heartbeat_timeout = heartbeat every 15 seconds
        async with Heartbeater(factor=4) as heartbeater:
            while True:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > input.max_duration_seconds:
                    activity.logger.error(
                        f"Call monitoring timeout after {elapsed:.0f}s: {input.call_id}"
                    )
                    return MonitorCallOutput(
                        success=False,
                        error=f"Monitoring timeout after {input.max_duration_seconds}s",
                    )

                # Update heartbeat details for debugging (must be a tuple, not string)
                heartbeater.details = (
                    f"monitoring {input.call_id}, elapsed={elapsed:.0f}s",
                )

                # Poll provider for call status using VoiceServiceManager.get_call_async()
                # Returns FAGICallData with normalized status
                # Heartbeater sends heartbeats in background while this runs
                try:
                    # Async version avoids thread-pool exhaustion. Returns
                    # FAGICallData with normalized status/duration/ended_reason.
                    fagi_call_data = await voice_manager.get_call_async(
                        call_id=input.provider_call_id,
                        call_data_stored=False,
                    )
                    call_status = fagi_call_data.status
                    duration_seconds = fagi_call_data.duration_seconds
                    ended_reason = fagi_call_data.ended_reason
                except Exception as e:
                    activity.logger.warning(
                        f"Error fetching call status: {str(e)}, retrying..."
                    )
                    await asyncio.sleep(input.poll_interval_seconds)
                    continue

                activity.logger.debug(
                    f"Call {input.call_id} status: {call_status} (elapsed: {elapsed:.0f}s)"
                )

                # Check if call reached terminal state
                if call_status in terminal_statuses:
                    activity.logger.info(
                        f"Call monitoring complete: call_id={input.call_id}, "
                        f"status={call_status}, duration={duration_seconds}s, "
                        f"ended_reason={ended_reason}"
                    )

                    # Return monitoring result
                    return MonitorCallOutput(
                        success=True,
                        status=call_status.value,
                        duration_seconds=(
                            int(duration_seconds)
                            if duration_seconds is not None
                            else None
                        ),
                        end_reason=ended_reason,
                    )

                # Wait before next poll
                await asyncio.sleep(input.poll_interval_seconds)

    except Exception as e:
        activity.logger.error(f"Failed to monitor call {input.call_id}: {str(e)}")
        activity.logger.exception(f"Failed to monitor call {input.call_id}: {str(e)}")
        return MonitorCallOutput(
            success=False,
            error=str(e),
        )


@activity.defn(name="fetch_and_persist_call_result")
async def fetch_and_persist_call_result(
    input: FetchAndPersistCallResultInput,
) -> FetchAndPersistCallResultOutput:
    """
    Fetch call data from provider and persist all results to database.

    Delegates provider-specific logic to engine methods via VoiceServiceManager:
    - fetch_and_store_call_data: Fetches/stores provider data + transcripts
    - extract_and_persist_recordings: Extracts recordings and persists to S3
    - extract_costs: Extracts cost breakdown from provider data

    Generic orchestration (provider-agnostic):
    - Perspective swap on ended_reason for inbound tests
    - Recording URL + cost fields on CallExecution
    - CreateCallExecution status update
    - TestExecution completion check

    Timeout: 5 minutes (with background heartbeats via Heartbeater)
    Queue: tasks_l
    """
    from tfc.temporal.common.heartbeat import Heartbeater

    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(
            f"Fetching and persisting call result: call_id={input.call_id}, "
            f"status={input.status}, provider_call_id={input.provider_call_id}, "
            f"call_type={input.call_type}, provider={input.provider}"
        )

        from simulate.models.run_test import CreateCallExecution
        from ee.voice.services.voice_service_manager import VoiceServiceManager
        from tracer.models.observability_provider import ProviderChoices

        async with Heartbeater(factor=4) as heartbeater:
            heartbeater.details = (f"starting fetch_and_persist for {input.call_id}",)

            # Initialize provider-aware voice manager.
            provider_enum = ProviderChoices(input.provider)
            is_outbound = input.call_type == CallType.OUTBOUND.value
            is_web_bridge = bool(
                input.provider_data and input.provider_data.get("bridge_type")
            )
            # SIP outbound: the customer's own account holds the call data, so
            # fetch from their provider engine (selected from the registry by
            # client_provider) with their API key. Inbound / web bridge / LiveKit
            # keep the system engine — transcripts and recordings live on our
            # server. Every engine exposes the same fetch/recordings/costs
            # methods the steps below call, so the rest of the activity is
            # provider-agnostic.
            use_customer_account = is_outbound and not is_web_bridge

            if use_customer_account:
                api_key = (
                    input.provider_config.get("api_key")
                    if input.provider_config
                    else None
                )
                voice_manager = VoiceServiceManager(
                    api_key=api_key or "",
                    system_voice_provider=ProviderChoices(input.client_provider),
                )
            else:
                voice_manager = VoiceServiceManager(system_voice_provider=provider_enum)

            # ----------------------------------------------------------
            # Step 1: Fetch + store call data and transcripts (engine)
            # ----------------------------------------------------------
            heartbeater.details = (
                f"fetching and storing call data for {input.call_id}",
            )

            (
                message_count,
                has_agent_message,
                has_customer_message,
            ) = await voice_manager.fetch_and_store_call_data(
                call_execution_id=input.call_id,
                provider_call_id=input.provider_call_id,
                status=input.status,
                duration_seconds=input.duration_seconds,
                end_reason=input.end_reason,
                provider_data=input.provider_data,
            )

            activity.logger.info(
                f"Stored call data for {input.call_id}: "
                f"{message_count} messages, agent={has_agent_message}, "
                f"customer={has_customer_message}"
            )

            activity.heartbeat(f"call data stored for {input.call_id}")

            # ----------------------------------------------------------
            # Step 2: Perspective swap on ended_reason (VAPI inbound only).
            # LiveKit end reasons are already normalised at source by the
            # agent worker. VAPI outbound comes from the tested agent's
            # own account, so its raw labels already match the platform
            # perspective. Kept as a plain string swap so writes stay
            # byte-identical to what production has been persisting.
            # ----------------------------------------------------------
            is_outbound = input.call_type == CallType.OUTBOUND
            call = await CallExecution.objects.select_related("test_execution").aget(
                id=input.call_id
            )
            update_fields: list[str] = []

            if call.ended_reason:
                provider_enum = ProviderChoices(input.provider)
                if provider_enum == ProviderChoices.VAPI and not is_outbound:
                    swapped = (
                        call.ended_reason.replace("assistant", "\x00")
                        .replace("customer", "assistant")
                        .replace("\x00", "customer")
                    )
                    if swapped != call.ended_reason:
                        call.ended_reason = swapped
                        update_fields.append("ended_reason")
            # For outbound calls, customer_call_id = provider call ID
            if is_outbound:
                call.customer_call_id = input.provider_call_id
                update_fields.append("customer_call_id")

            # ----------------------------------------------------------
            # Step 3: Extract and persist recordings (engine)
            # ----------------------------------------------------------
            heartbeater.details = (f"extracting recordings for {input.call_id}",)

            recording_urls = await voice_manager.extract_and_persist_recordings(
                input.call_id
            )

            if recording_urls.recording_url:
                call.recording_url = recording_urls.recording_url
                update_fields.append("recording_url")
            if recording_urls.stereo_recording_url:
                call.stereo_recording_url = recording_urls.stereo_recording_url
                update_fields.append("stereo_recording_url")
            if recording_urls.recording_url or recording_urls.stereo_recording_url:
                call.recording_available = True
                update_fields.append("recording_available")

            # Store provider payload updates with the other CallExecution fields.
            rec = {}
            if recording_urls.customer_recording_url:
                rec["customer"] = recording_urls.customer_recording_url
            if recording_urls.assistant_recording_url:
                rec["assistant"] = recording_urls.assistant_recording_url
            if recording_urls.stereo_recording_url:
                rec["stereo"] = recording_urls.stereo_recording_url
            if recording_urls.recording_url:
                rec["combined"] = recording_urls.recording_url
            if recording_urls.provider_call_data is not None:
                call.provider_call_data = recording_urls.provider_call_data
                if "provider_call_data" not in update_fields:
                    update_fields.append("provider_call_data")
            elif rec:
                provider_data = call.provider_call_data or {}
                # Store under the account that actually holds the call data. For
                # SIP outbound that is the customer's provider key (already
                # written by fetch_and_store — "bland" for Bland); using the
                # system provider here would create a stray "vapi" key that flips
                # the drawer's provider chip depending on whether the rehost
                # succeeded.
                recording_provider = (
                    input.client_provider if use_customer_account else input.provider
                )
                pdata = provider_data.setdefault(recording_provider, {})
                pdata["recording"] = rec
                call.provider_call_data = provider_data
                if "provider_call_data" not in update_fields:
                    update_fields.append("provider_call_data")

            activity.heartbeat(f"recordings persisted for {input.call_id}")

            # ----------------------------------------------------------
            # Step 4: Extract and store costs (engine)
            # ----------------------------------------------------------
            heartbeater.details = (f"extracting costs for {input.call_id}",)

            costs = await voice_manager.extract_costs(input.call_id)

            if costs.total is not None:
                call.cost_cents = int(round(costs.total * 100))
                update_fields.append("cost_cents")
            if costs.stt is not None:
                call.stt_cost_cents = int(round(costs.stt * 100))
                update_fields.append("stt_cost_cents")
            if costs.llm is not None:
                call.llm_cost_cents = int(round(costs.llm * 100))
                update_fields.append("llm_cost_cents")
            if costs.tts is not None:
                call.tts_cost_cents = int(round(costs.tts * 100))
                update_fields.append("tts_cost_cents")
            if costs.transport is not None:
                call.vapi_cost_cents = int(round(costs.transport * 100))
                update_fields.append("vapi_cost_cents")
            if costs.storage is not None:
                call.storage_cost_cents = round(costs.storage * 100, 4)
                update_fields.append("storage_cost_cents")

            # Build customer_cost_breakdown JSON from usage data + computed costs.
            # The serializer reads this field for the API cost_breakdown response.
            if costs.total is not None:
                provider_data = call.provider_call_data or {}
                cost_breakdown = {}

                if "livekit" in provider_data:
                    usage = provider_data["livekit"].get("usage", {})
                    for category in ("stt", "llm", "tts"):
                        cat_data = usage.get(category, {})
                        cost_value = getattr(costs, category, 0.0)
                        if cat_data or cost_value:
                            cost_breakdown[category] = {
                                **cat_data,
                                "cost": float(cost_value),
                            }
                elif "vapi" in provider_data and hasattr(
                    voice_manager.engine, "build_customer_metrics_from_provider_data"
                ):
                    # Per-stage cost breakdown is a VAPI-only capability. Bland
                    # exposes no per-stage cost, so it degrades to total-only.
                    vapi_metrics = (
                        voice_manager.engine.build_customer_metrics_from_provider_data(
                            provider_data["vapi"]
                        )
                    )
                    cost_breakdown = vapi_metrics.cost_breakdown or {}

                if cost_breakdown:
                    call.customer_cost_breakdown = cost_breakdown
                    update_fields.append("customer_cost_breakdown")

                call.customer_cost_cents = int(round(costs.total * 100))
                update_fields.append("customer_cost_cents")

            # ----------------------------------------------------------
            # Step 5: Save updates + generic bookkeeping
            # ----------------------------------------------------------
            if update_fields:
                await call.asave(update_fields=update_fields)

            heartbeater.details = (
                f"updating CreateCallExecution status for {input.call_id}",
            )

            # Update CreateCallExecution status to match CallExecution final status
            status_value = (
                input.status.value if hasattr(input.status, "value") else input.status
            )
            create_call_status = CreateCallExecution.CallStatus.COMPLETED
            if status_value in [
                CallExecution.CallStatus.FAILED,
                CallExecution.CallStatus.FAILED.value,
                "FAILED",
                "failed",
            ]:
                create_call_status = CreateCallExecution.CallStatus.FAILED
            elif status_value in [
                CallExecution.CallStatus.CANCELLED,
                CallExecution.CallStatus.CANCELLED.value,
                "CANCELLED",
                "cancelled",
            ]:
                create_call_status = CreateCallExecution.CallStatus.CANCELLED

            await CreateCallExecution.objects.filter(
                call_execution_id=input.call_id
            ).aupdate(status=create_call_status)

            activity.logger.info(
                f"Persisted call result: call_id={input.call_id}, "
                f"CreateCallExecution status={create_call_status}, "
                f"transcript_messages={message_count}"
            )

            heartbeater.details = (
                f"checking test execution status for {input.call_id}",
            )

            # Check if all calls in TestExecution have completed and all failed
            test_execution = call.test_execution
            if test_execution:
                from django.db.models import Count, Q

                terminal_statuses = [
                    CallExecution.CallStatus.COMPLETED,
                    CallExecution.CallStatus.FAILED,
                    CallExecution.CallStatus.CANCELLED,
                ]

                stats = await sync_to_async(
                    lambda: CallExecution.objects.filter(
                        test_execution_id=test_execution.id
                    ).aggregate(
                        total=Count("id"),
                        completed=Count("id", filter=Q(status__in=terminal_statuses)),
                        failed=Count(
                            "id", filter=Q(status=CallExecution.CallStatus.FAILED)
                        ),
                    )
                )()

                total_calls = stats["total"]
                completed_calls = stats["completed"]
                failed_calls = stats["failed"]

                # If all calls are done and all are failed, mark TestExecution as FAILED
                if (
                    total_calls > 0
                    and completed_calls == total_calls
                    and failed_calls == total_calls
                ):
                    await TestExecution.objects.filter(id=test_execution.id).aupdate(
                        status=TestExecution.ExecutionStatus.FAILED
                    )
                    activity.logger.info(
                        f"Marked TestExecution {test_execution.id} as FAILED "
                        f"(all {total_calls} calls failed)"
                    )

        return FetchAndPersistCallResultOutput(
            success=True,
            message_count=message_count,
            has_agent_message=has_agent_message,
            has_customer_message=has_customer_message,
        )

    except CallExecution.DoesNotExist:
        activity.logger.error(f"Call not found: {input.call_id}")
        return FetchAndPersistCallResultOutput(
            success=False,
            error=f"Call not found: {input.call_id}",
        )

    except Exception as e:
        activity.logger.error(f"Failed to fetch and persist call result: {str(e)}")
        activity.logger.exception(f"Failed to fetch and persist call result: {str(e)}")
        return FetchAndPersistCallResultOutput(
            success=False,
            error=str(e),
        )


@activity.defn(name="deduct_call_cost")
async def deduct_call_cost(input: DeductCostInput) -> None:
    """
    Emit usage event for call billing (voice_call / text_call).

    Replaces the old wallet-deduction model with postpaid usage tracking.
    Emits to the Redis usage stream, consumed by UsageConsumerWorkflow.

    Timeout: 1 minute
    Queue: tasks_l
    """
    import math

    close_old_connections()

    try:
        activity.logger.info(
            f"Processing call usage: call_id={input.call_id}, org_id={input.org_id}"
        )

        call = await CallExecution.objects.select_related(
            "test_execution__run_test__organization",
            "test_execution__run_test__workspace",
        ).aget(id=input.call_id)

        if not call.duration_seconds:
            activity.logger.warning(
                f"No duration for call {input.call_id}, skipping usage emit"
            )
            return

        # Emit usage event for the new billing pipeline
        try:
            from ee.usage.schemas.event_types import BillingEventType
        except ImportError:
            BillingEventType = None
        try:
            from ee.usage.schemas.events import UsageEvent
        except ImportError:
            UsageEvent = None
        try:
            from ee.usage.services.emitter import emit
        except ImportError:
            emit = None

        org_id = str(call.test_execution.run_test.organization_id)
        is_voice = call.simulation_call_type == CallExecution.SimulationCallType.VOICE
        duration_minutes = math.ceil(call.duration_seconds / 60)

        if is_voice:
            emit(
                UsageEvent(
                    org_id=org_id,
                    event_type=BillingEventType.VOICE_CALL,
                    amount=max(1, duration_minutes),
                    properties={
                        "source": "simulate",
                        "source_id": str(call.id),
                        "duration_seconds": call.duration_seconds,
                    },
                )
            )
        else:
            emit(
                UsageEvent(
                    org_id=org_id,
                    event_type=BillingEventType.TEXT_CALL,
                    amount=1,
                    properties={
                        "source": "simulate",
                        "source_id": str(call.id),
                    },
                )
            )

        activity.logger.info(
            f"Emitted {'voice_call' if is_voice else 'text_call'} usage: "
            f"call_id={input.call_id}, duration={call.duration_seconds}s, "
            f"amount={max(1, duration_minutes) if is_voice else 1}"
        )

    except CallExecution.DoesNotExist:
        activity.logger.error(f"Call not found for usage emit: {input.call_id}")
        raise Exception(f"Call not found: {input.call_id}")

    except Exception as e:
        activity.logger.error(f"Failed to emit call usage: {str(e)}")
        activity.logger.exception(f"Failed to emit call usage: {str(e)}")
        raise


@activity.defn(name="calculate_conversation_metrics")
async def calculate_conversation_metrics(
    input: CalculateConversationMetricsInput,
) -> None:
    """
    Calculate conversation metrics for both inbound and outbound calls.

    Uses engine's get_normalized_transcript_data() to fetch provider-agnostic
    transcript data, then ConversationMetricsCalculator.calculate_metrics_from_normalized()
    to compute metrics. The is_outbound flag controls role normalization.

    Timeout: 2 minutes
    Queue: tasks_l
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        call_type = "outbound" if input.is_outbound else "inbound"
        activity.logger.info(
            f"Calculating conversation metrics for call_id={input.call_id}, "
            f"is_outbound={input.is_outbound}, provider={input.provider}"
        )

        from ee.voice.services.conversation_metrics import ConversationMetricsCalculator
        from ee.voice.services.voice_service_manager import VoiceServiceManager
        from tracer.models.observability_provider import ProviderChoices

        # Initialize a provider-aware voice manager. When the customer's own
        # account holds the call data (SIP outbound), transcript reads must
        # dispatch to the customer's engine; otherwise the system provider owns
        # the data. The workflow sets client_provider only in that first case.
        engine_provider = (
            ProviderChoices(input.client_provider)
            if input.client_provider
            else ProviderChoices(input.provider)
        )
        voice_manager = VoiceServiceManager(system_voice_provider=engine_provider)

        # Fetch provider-agnostic transcript + usage data via engine
        normalized_data = await voice_manager.get_normalized_transcript_data(
            input.call_id
        )

        if not normalized_data.messages:
            activity.logger.warning(
                f"No transcript data available for {call_type} call {input.call_id}, "
                "skipping metrics calculation"
            )
            return

        # Calculate metrics using provider-agnostic calculator
        calculator = ConversationMetricsCalculator(voice_service_provider=engine_provider)
        metrics = calculator.calculate_metrics_from_normalized(
            normalized_data, input.is_outbound
        )

        # Extract token counts from normalized transcript data
        token_usage = normalized_data.token_usage
        llm_usage = token_usage.get("llm", {})
        prompt_tokens = llm_usage.get("prompt_tokens", 0)
        completion_tokens = llm_usage.get("completion_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens

        # Build detailed_data with token information for frontend
        detailed_data = metrics.detailed_data or {}
        detailed_data["total_tokens"] = total_tokens
        detailed_data["input_tokens"] = prompt_tokens
        detailed_data["output_tokens"] = completion_tokens

        # Convert ConversationMetrics dataclass to dict for storage
        metrics_dict = {
            "avg_agent_latency_ms": metrics.avg_agent_latency_ms,
            "user_interruption_count": metrics.user_interruption_count,
            "user_interruption_rate": metrics.user_interruption_rate,
            "ai_interruption_count": metrics.ai_interruption_count,
            "ai_interruption_rate": metrics.ai_interruption_rate,
            "user_wpm": metrics.user_wpm,
            "bot_wpm": metrics.bot_wpm,
            "talk_ratio": metrics.talk_ratio,
            "avg_stop_time_after_interruption_ms": metrics.avg_stop_time_after_interruption_ms,
            "detailed_data": detailed_data,
        }

        # Fetch call and store metrics
        call = await CallExecution.objects.aget(id=input.call_id)

        # Merge provider-native pipeline latency metrics (endpointing,
        # transcriber, model, voice, turn) so the frontend System Metrics
        # panel is populated.
        provider_call_data = call.provider_call_data or {}

        # VAPI: extract from performanceMetrics in provider data. Guard on the
        # engine capability — a Bland-outbound manager's engine has no
        # build_customer_metrics_from_provider_data (and no "vapi" key anyway).
        vapi_data = provider_call_data.get("vapi")
        if vapi_data and hasattr(
            voice_manager.engine, "build_customer_metrics_from_provider_data"
        ):
            vapi_metrics = (
                voice_manager.engine.build_customer_metrics_from_provider_data(
                    vapi_data
                )
            )
            if vapi_metrics.system_metrics:
                metrics_dict.update(vapi_metrics.system_metrics)

        # LiveKit: adjust avg_agent_latency_ms for bridge overhead.
        # The raw latency includes our simulator's STT pipeline delay
        # (endpointing + transcription). Subtract it so the metric
        # reflects the customer agent's actual response time.
        livekit_data = provider_call_data.get("livekit", {})
        sim_metrics = livekit_data.get("usage", {}).get("simulator_metrics")
        if sim_metrics and isinstance(sim_metrics, dict):
            raw_latency = metrics_dict.get("avg_agent_latency_ms")
            if raw_latency is not None:
                bridge_overhead_ms = sim_metrics.get(
                    "endpointing", 0
                ) + sim_metrics.get("transcriber", 0)
                adjusted = int(raw_latency - bridge_overhead_ms)
                if adjusted > 0:
                    metrics_dict["avg_agent_latency_ms"] = adjusted

        # Store metrics in customer_latency_metrics JSON field.
        # Keep the same nested payload shape used by test_executor/xl paths:
        # {"systemMetrics": {...}, "turnLatencies": [...]}
        call.customer_latency_metrics = {
            "systemMetrics": metrics_dict,
            "turnLatencies": [],
        }

        # Also store in individual model fields (for frontend compatibility)
        # Use metrics_dict value which may have been adjusted for bridge overhead
        call.avg_agent_latency_ms = metrics_dict.get("avg_agent_latency_ms")
        call.user_interruption_count = metrics.user_interruption_count
        call.user_interruption_rate = metrics.user_interruption_rate
        call.user_wpm = metrics.user_wpm
        call.bot_wpm = metrics.bot_wpm
        call.talk_ratio = metrics.talk_ratio
        call.ai_interruption_count = metrics.ai_interruption_count
        call.ai_interruption_rate = metrics.ai_interruption_rate
        call.avg_stop_time_after_interruption_ms = (
            metrics.avg_stop_time_after_interruption_ms
        )
        call.conversation_metrics_data = detailed_data

        await call.asave(
            update_fields=[
                "customer_latency_metrics",
                "avg_agent_latency_ms",
                "user_interruption_count",
                "user_interruption_rate",
                "user_wpm",
                "bot_wpm",
                "talk_ratio",
                "ai_interruption_count",
                "ai_interruption_rate",
                "avg_stop_time_after_interruption_ms",
                "conversation_metrics_data",
            ]
        )

        activity.logger.info(
            f"Calculated and stored {call_type} metrics for call_id={input.call_id}: "
            f"latency={metrics.avg_agent_latency_ms}ms, "
            f"interruptions={metrics.user_interruption_count}/{metrics.ai_interruption_count}"
        )

    except CallExecution.DoesNotExist:
        activity.logger.error(
            f"Call not found for conversation metrics: {input.call_id}"
        )
        activity.logger.exception(f"Call not found: {input.call_id}")
        raise Exception(f"Call not found: {input.call_id}")

    except Exception as e:
        activity.logger.error(f"Failed to calculate conversation metrics: {str(e)}")
        activity.logger.exception(f"Failed to calculate conversation metrics: {str(e)}")
        raise
