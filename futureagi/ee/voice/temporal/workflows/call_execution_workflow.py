"""
CallExecutionWorkflow - Individual call execution orchestration.

Orchestrates a single call's complete lifecycle from preparation through evaluation.
Replaces the Celery-based call execution in test_executor.py with a Temporal workflow.

Design:
- Minimal workflow input (scalability: parent launches 1000+ children)
- Activity-based orchestration (all I/O in activities)
- Signal-based coordination (dispatcher grants slot, parent receives completion)
- Proper error handling (retry policies, compensation logic)
"""

import asyncio
from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.workflow import ActivityCancellationType

from simulate.semantics import CallType
from simulate.temporal.constants import (
    QUEUE_L,
    QUEUE_S,
    QUEUE_XL,
)
from simulate.temporal.retry_policies import (
    DB_RETRY_POLICY,
    EVAL_RETRY_POLICY,
    NO_RETRY_POLICY,
    PROVIDER_RETRY_POLICY,
    SIGNAL_RETRY_POLICY,
)
from simulate.temporal.types.activities import (
    AcquirePhoneInput,
    AcquirePhoneOutput,
    CalculateConversationMetricsInput,
    CalculateVoiceCSATInput,
    CalculateVoiceCSATOutput,
    CheckBalanceInput,
    CheckBalanceOutput,
    DeductCostInput,
    FetchAndPersistCallResultInput,
    FetchAndPersistCallResultOutput,
    FetchClientCallInput,
    FetchClientCallOutput,
    InitiateCallInput,
    MonitorCallInput,
    MonitorCallOutput,
    PersistProcessingSkipStateInput,
    PrepareCallInput,
    PrepareCallOutput,
    ReleasePhoneInput,
    ReleasePhoneNumberSlotInput,
    ReleaseSlotInput,
    ReportErrorInput,
    RequestPhoneNumberInput,
    RequestSlotInput,
    RunBridgeInput,
    RunSimulateEvaluationsInput,
    RunToolCallEvaluationInput,
    RunToolCallEvaluationOutput,
    SignalCallAnalyzingInput,
    SignalCallCompleteInput,
    UpdateCallStatusInput,
)
from simulate.temporal.types.call_execution import (
    CallExecutionInput,
    CallExecutionOutput,
    CallStatus,
)
from ee.voice.temporal.types.phone_number_dispatcher import PhoneNumberGrantedSignal
from ee.voice.utils.processing_gating import decide_processing_skip
from tracer.models.observability_provider import ProviderChoices

# Import Django model with sandbox passthrough for status enum access
with workflow.unsafe.imports_passed_through():
    from simulate.models.test_execution import CallExecution as CallExecutionModel


@workflow.defn
class CallExecutionWorkflow:
    """
    Executes a single call's complete lifecycle.

    Orchestrates: preparation, slot acquisition, call execution, monitoring,
    data storage, evaluation, and cleanup. Signals parent on completion.

    Phases:
    1. PREPARATION: Load call data, check balance
    2. SLOT_REQUEST: Wait for dispatcher to grant rate limit slot
    2.5. ACQUIRE_PHONE: Acquire phone from pool (outbound only, after slot granted)
    3. CALL_EXECUTION: Initiate → monitor → fetch transcript
    4. DATA_STORAGE: Persist results, deduct costs
    5. CLIENT_DATA: Fetch client-side data (if applicable)
    6. EVALUATION: Run evaluations on transcript
    7. CLEANUP: Release resources, signal parent
    """

    def __init__(self):
        # Status tracking for queries
        self._status = "PENDING"
        self._provider_call_id: Optional[str] = None
        self._phone_number_id: Optional[int] = None  # DB ID for phone release
        self._system_assistant_id: Optional[str] = None  # Created in initiate_call
        self._system_phone_number: Optional[str] = (
            None  # From phone acquisition (outbound)
        )
        self._system_phone_number_id: Optional[str] = None  # VAPI phone ID (outbound)
        self._is_outbound = False
        self._client_provider: str = ProviderChoices.VAPI.value
        self._call_id: Optional[str] = None

        # Provider tracking (default LiveKit for new executions)
        self._provider: str = ProviderChoices.LIVEKIT.value
        self._initiate_provider_data: dict = {}

        # LiveKit call-ended signal state
        self._call_ended = False
        self._call_ended_status: str = ""
        self._call_ended_duration: int = 0
        self._call_ended_reason: str = ""

        # Signal handlers
        self._slot_granted = False
        self._slot_granted_event = asyncio.Event()
        self._cancelled = False

        # Resource lifecycle tracking
        self._slot_requested = False  # True after request_call_slot activity completes
        self._slot_released = False
        self._phone_released = False

        # Phone number dispatcher (signal-based path)
        self._phone_granted = False
        self._phone_requested = False

    @workflow.run
    async def run(self, input: CallExecutionInput) -> CallExecutionOutput:
        """Main workflow execution."""
        self._call_id = input.call_id

        try:
            # ========================================
            # EVAL-ONLY MODE: Skip call execution, run evaluations only
            # ========================================
            if input.eval_only:
                return await self._run_eval_only_mode(input)

            # ========================================
            # PHASE 1: BALANCE CHECK (Fail Early)
            # ========================================
            self._status = "CHECKING_BALANCE"

            # Check organization has sufficient balance before any other work
            # Use 30 minutes as default estimate (actual max comes from voice_settings)
            balance_result = await workflow.execute_activity(
                "check_call_balance",
                CheckBalanceInput(
                    org_id=input.org_id,
                    estimated_duration_minutes=30,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_S,
                result_type=CheckBalanceOutput,
            )

            if not balance_result.sufficient:
                return await self._fail(
                    input,
                    "Insufficient balance to make calls.",
                )

            # ========================================
            # PHASE 2: PREPARATION
            # ========================================
            self._status = "PREPARING"

            # Fetch call details from database
            prepare_result = await workflow.execute_activity(
                "prepare_call",
                PrepareCallInput(
                    call_id=input.call_id,
                    workspace_id=input.workspace_id,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_S,
                result_type=PrepareCallOutput,
            )

            if prepare_result.error:
                return await self._fail(
                    input, f"Preparation failed: {prepare_result.error}"
                )

            self._is_outbound = prepare_result.is_outbound

            # Version-gate provider: new executions use prepare_call's provider,
            # replaying old workflows default to VAPI.
            if workflow.patched("livekit-provider"):
                self._provider = prepare_result.provider
            else:
                self._provider = ProviderChoices.VAPI.value

            # Customer's provider (agent under test) — routes the SIP-outbound
            # trigger/monitor/fetch data plane. Defaults to VAPI for parity with
            # the historical always-VAPI outbound trigger.
            self._client_provider = (
                prepare_result.client_provider or ProviderChoices.VAPI.value
            )

            # ========================================
            # PHASE 2: SLOT REQUEST (Rate Limiting)
            # ========================================
            self._status = "WAITING_FOR_SLOT"

            # Request slot from dispatcher
            # Extract agent concurrency info from prepare_call result
            # Only set for LiveKit calls which use per-agent concurrency limits
            _agent_def_id = ""
            _agent_concurrency = 0
            if prepare_result.connection_type == "web_livekit_bridge":
                _agent_def_id = prepare_result.metadata.get("agent_definition_id", "")
                try:
                    _agent_concurrency = int(
                        prepare_result.metadata.get("livekit_max_concurrency", 0)
                    )
                except (ValueError, TypeError):
                    _agent_concurrency = 0
            await workflow.execute_activity(
                "request_call_slot",
                RequestSlotInput(
                    workflow_id=workflow.info().workflow_id,
                    call_id=input.call_id,
                    org_id=input.org_id,
                    agent_definition_id=_agent_def_id,
                    agent_concurrency_limit=_agent_concurrency,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=SIGNAL_RETRY_POLICY,
                task_queue=QUEUE_S,
            )
            self._slot_requested = True

            # Wait for SLOT_GRANTED signal from dispatcher
            await workflow.wait_condition(lambda: self._slot_granted or self._cancelled)

            if self._cancelled:
                raise asyncio.CancelledError()

            # ========================================
            # PHASE 2.5: ACQUIRE PHONE (Outbound Only)
            # ========================================
            # Acquire phone number for outbound calls AFTER slot is granted.
            # Web bridge/LiveKit calls don't need phone numbers — they connect
            # via WebSocket or direct LiveKit room, not SIP/PSTN.
            _needs_phone = prepare_result.is_outbound and not (
                prepare_result.connection_type or ""
            ).startswith("web_")
            if _needs_phone:
                if workflow.patched("phone-dispatcher"):
                    # NEW PATH: Signal-based phone acquisition via PhoneNumberDispatcherWorkflow
                    self._status = "REQUESTING_PHONE"
                    await workflow.execute_activity(
                        "request_phone_number_slot",
                        RequestPhoneNumberInput(
                            workflow_id=workflow.info().workflow_id,
                            call_id=input.call_id,
                            org_id=input.org_id,
                            call_direction="outbound",
                        ),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=SIGNAL_RETRY_POLICY,
                        task_queue=QUEUE_S,
                    )
                    self._phone_requested = True

                    # Wait for phone_number_granted signal or cancellation
                    await workflow.wait_condition(
                        lambda: self._phone_granted or self._cancelled
                    )

                    if self._cancelled:
                        raise asyncio.CancelledError()

                    workflow.logger.info(
                        f"Phone granted: {self._system_phone_number} for call {input.call_id}"
                    )
                else:
                    # OLD PATH: Direct activity-based acquisition (long-polling)
                    self._status = "ACQUIRING_PHONE"
                    phone_result = await workflow.execute_activity(
                        "acquire_phone_number",
                        AcquirePhoneInput(
                            call_id=input.call_id,
                            call_direction="outbound",
                            poll_interval_seconds=5,
                        ),
                        start_to_close_timeout=timedelta(
                            hours=1
                        ),  # Long wait for phone availability
                        heartbeat_timeout=timedelta(minutes=1),
                        retry_policy=NO_RETRY_POLICY,  # Activity handles polling internally
                        task_queue=QUEUE_S,
                        result_type=AcquirePhoneOutput,
                    )

                    if not phone_result.success:
                        return await self._fail(
                            input,
                            f"Failed to acquire phone number: {phone_result.error}",
                        )

                    self._phone_number_id = phone_result.phone_id
                    self._system_phone_number = phone_result.phone_number
                    self._system_phone_number_id = phone_result.provider_phone_id

                    workflow.logger.info(
                        f"Acquired phone {phone_result.phone_number} for call {input.call_id}"
                    )

            # ========================================
            # PHASE 3: CALL EXECUTION
            # ========================================
            self._status = "CALLING"

            # Initiate call with provider using correct method based on direction
            # For inbound: Creates assistant and calls user's phone
            # For outbound: Creates assistant, assigns to acquired phone, user calls that phone
            #
            # Inject our workflow_id into metadata so the agent worker can send
            # the call_ended signal to the correct Temporal workflow (normal runs
            # use "call-exec-{id}", reruns use "call-exec-rerun-{id}-{ts}").
            # For outbound, patch call_data with the acquired system phone
            # as the destination (not known at prepare_call time).
            call_data = prepare_result.call_data
            if self._is_outbound and self._system_phone_number:
                call_data = {
                    **call_data,
                    "customer_phone_number": self._system_phone_number,
                }

            call_metadata = {
                **(prepare_result.metadata or {}),
                "workflow_id": workflow.info().workflow_id,
            }
            initiate_result = await workflow.execute_activity(
                "initiate_call",
                InitiateCallInput(
                    call_id=input.call_id,
                    call_data=call_data,
                    system_prompt=prepare_result.system_prompt,
                    voice_settings=prepare_result.voice_settings,
                    metadata=call_metadata,
                    provider=self._provider,
                    # Phone info from acquisition (outbound) or None (inbound)
                    phone_number=self._system_phone_number,
                    provider_phone_id=self._system_phone_number_id,
                    # User credentials (outbound)
                    user_api_key=prepare_result.client_api_key,
                    user_assistant_id=prepare_result.client_assistant_id,
                    user_phone_number=prepare_result.client_phone_number,  # User's phone to call from
                    client_provider=self._client_provider,
                    connection_type=prepare_result.connection_type,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                heartbeat_timeout=timedelta(minutes=1),
                retry_policy=PROVIDER_RETRY_POLICY,
                task_queue=QUEUE_L,
            )

            if not initiate_result.get("success"):
                return await self._fail(
                    input, f"Call initiation failed: {initiate_result.get('error')}"
                )

            self._provider_call_id = initiate_result.get("provider_call_id")
            self._initiate_provider_data = initiate_result.get("provider_data", {})
            # Store assistant_id from initiate_call for outbound calls
            # This is the simulator assistant we created, needed for fetch_client_call_data
            self._system_assistant_id = self._initiate_provider_data.get("assistant_id")

            # Start bridge or direct LiveKit simulation for web calls
            bridge_task = None
            bridge_type = self._initiate_provider_data.get("bridge_type")
            if bridge_type:
                # Web bridge (Vapi, Retell, LiveKit bridge): run bridge activity in parallel
                bridge_task = workflow.start_activity(
                    "run_bridge",
                    RunBridgeInput(
                        call_id=input.call_id,
                        room_name=self._initiate_provider_data.get("room_name", ""),
                        connection_type=bridge_type,
                        customer_api_key=prepare_result.client_api_key or "",
                        customer_assistant_id=prepare_result.client_assistant_id or "",
                        max_duration_seconds=int(
                            (prepare_result.voice_settings or {}).get(
                                "max_call_duration_in_minutes", 35
                            )
                            * 60
                        ),
                        customer_livekit_url=prepare_result.metadata.get(
                            "customer_livekit_url", ""
                        ),
                        customer_livekit_api_key=prepare_result.metadata.get(
                            "customer_livekit_api_key", ""
                        ),
                        customer_livekit_api_secret=prepare_result.metadata.get(
                            "customer_livekit_api_secret", ""
                        ),
                    ),
                    start_to_close_timeout=timedelta(minutes=40),
                    heartbeat_timeout=timedelta(minutes=1),
                    retry_policy=NO_RETRY_POLICY,
                    task_queue=QUEUE_L,
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                )

            # Update call status to ONGOING (call is now running)
            # Also marks TestExecution as RUNNING if it's still PENDING (first call started)
            await workflow.execute_activity(
                "update_call_status",
                UpdateCallStatusInput(
                    call_id=input.call_id,
                    status=CallExecutionModel.CallStatus.ONGOING,
                    test_execution_id=input.test_execution_id,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_S,
            )

            # Monitor call until completion
            monitor_call_max_duration = (
                14400 if workflow.patched("monitor-call-max-duration-30min") else 1800
            )
            monitor_call_timeout = (
                timedelta(hours=5)
                if workflow.patched("monitor-call-timeout-30min")
                else timedelta(minutes=35)
            )
            self._status = "MONITORING"
            monitor_provider_config: dict = {}

            if self._provider == ProviderChoices.LIVEKIT.value:
                # LiveKit: signal-based monitoring (webhook → Temporal signal)
                # Cap at 35 min — if the agent worker crashes and never sends
                # call_ended, we don't want to wait for the full VAPI timeout.
                livekit_timeout = min(monitor_call_max_duration, 2100)
                try:
                    await workflow.wait_condition(
                        lambda: self._call_ended or self._cancelled,
                        timeout=timedelta(seconds=livekit_timeout),
                    )
                    if self._cancelled:
                        raise asyncio.CancelledError()
                    # Treat agent-reported failures as monitoring failures.
                    # The agent worker sends status="failed" via the backup
                    # Temporal signal when it encounters a close error (e.g.
                    # TTS API crash, session error).
                    call_failed = self._call_ended_status == "failed"
                    monitor_result = MonitorCallOutput(
                        success=not call_failed,
                        status=self._call_ended_status,
                        duration_seconds=self._call_ended_duration,
                        end_reason=self._call_ended_reason,
                        error=(
                            f"Agent reported failure: {self._call_ended_reason}"
                            if call_failed
                            else None
                        ),
                    )
                except asyncio.TimeoutError:
                    monitor_result = MonitorCallOutput(
                        success=False,
                        error=f"Call timeout after {livekit_timeout}s "
                        "(no call_ended signal received)",
                    )
            else:
                # For outbound calls, the provider_call_id belongs to the user's
                # VAPI account (created with user_api_key).  Pass the user's key
                # so the monitor activity can poll with the correct credentials.
                monitor_provider_config = (
                    {"api_key": prepare_result.client_api_key}
                    if self._is_outbound and prepare_result.client_api_key
                    else {}
                )

                # VAPI: polling-based monitoring (existing path)
                monitor_result = await workflow.execute_activity(
                    "monitor_call_until_complete",
                    MonitorCallInput(
                        call_id=input.call_id,
                        provider_call_id=self._provider_call_id,
                        call_type=(
                            CallType.OUTBOUND.value
                            if self._is_outbound
                            else CallType.INBOUND.value
                        ),
                        provider=self._provider,
                        client_provider=self._client_provider,
                        provider_config=monitor_provider_config,
                        poll_interval_seconds=20,
                        max_duration_seconds=monitor_call_max_duration,
                    ),
                    start_to_close_timeout=monitor_call_timeout,
                    heartbeat_timeout=timedelta(minutes=1),
                    retry_policy=NO_RETRY_POLICY,
                    task_queue=QUEUE_L,
                    result_type=MonitorCallOutput,
                )

            # Cancel the bridge activity now that monitoring is done
            if bridge_task:
                bridge_task.cancel()

            if not monitor_result.success:
                return await self._fail(
                    input, f"Call monitoring failed: {monitor_result.error}"
                )

            # ========================================
            # RELEASE SLOT AND PHONE EARLY (After call completion)
            # ========================================
            # Release resources immediately after call completes - we don't need them for
            # transcript fetch, evals, metrics, or persistence. This improves throughput
            # by allowing other calls to use resources sooner.
            self._status = "RELEASING_RESOURCES"

            # Build list of release tasks
            release_tasks = [
                workflow.execute_activity(
                    "release_call_slot",
                    ReleaseSlotInput(call_id=input.call_id),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=SIGNAL_RETRY_POLICY,
                    task_queue=QUEUE_L,
                )
            ]

            # Release phone number if acquired (outbound calls)
            if self._phone_number_id and not self._phone_released:
                if workflow.patched("phone-dispatcher"):
                    # NEW PATH: Release phone from DB + dispatcher slot
                    release_tasks.append(
                        workflow.execute_activity(
                            "release_phone_number_slot",
                            ReleasePhoneNumberSlotInput(
                                call_id=input.call_id,
                            ),
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=SIGNAL_RETRY_POLICY,
                            task_queue=QUEUE_L,
                        )
                    )
                else:
                    # OLD PATH: Direct release activity
                    release_tasks.append(
                        workflow.execute_activity(
                            "release_phone_number",
                            ReleasePhoneInput(phone_id=self._phone_number_id),
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=SIGNAL_RETRY_POLICY,
                            task_queue=QUEUE_L,
                        )
                    )

            # Run all releases in parallel
            release_results = await asyncio.gather(
                *release_tasks, return_exceptions=True
            )

            # Slot release is always the first task
            if not isinstance(release_results[0], Exception):
                self._slot_released = True
            else:
                workflow.logger.warning(
                    f"Slot release failed for call {input.call_id}: {release_results[0]}"
                )

            # Phone release is the second task (if present)
            if len(release_results) > 1:
                if not isinstance(release_results[1], Exception):
                    self._phone_released = True
                else:
                    workflow.logger.warning(
                        f"Phone release failed for call {input.call_id}: {release_results[1]}"
                    )
            else:
                self._phone_released = True

            workflow.logger.info(
                f"Slot and phone released for call {input.call_id}, "
                "continuing with transcript fetch and processing"
            )

            # Update call status to ANALYZING now that slot is released
            # This indicates call completed and we're processing results
            await workflow.execute_activity(
                "update_call_status",
                UpdateCallStatusInput(
                    call_id=input.call_id,
                    status=CallExecutionModel.CallStatus.ANALYZING,
                    test_execution_id=input.test_execution_id,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_S,
            )

            # Signal parent that this call is now analyzing
            # This allows parent to transition to EVALUATING when all calls are analyzing
            # Use QUEUE_L (same as signal_call_completed) to avoid congesting tasks_s
            await workflow.execute_activity(
                "signal_call_analyzing",
                SignalCallAnalyzingInput(
                    workflow_id=input.test_workflow_id,
                    call_id=input.call_id,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=SIGNAL_RETRY_POLICY,
                task_queue=QUEUE_L,
            )

            # ========================================
            # PHASE 4: DATA STORAGE
            # ========================================
            self._status = "STORING"

            # Fetch call data from provider and persist all results
            # This combined activity:
            # - Fetches call data from provider once (instead of twice)
            # - Saves transcript to CallTranscript table
            # - Saves all call result fields to CallExecution
            # - Converts recordings to S3
            # - Updates CreateCallExecution status
            fetch_persist_result = await workflow.execute_activity(
                "fetch_and_persist_call_result",
                FetchAndPersistCallResultInput(
                    call_id=input.call_id,
                    status=monitor_result.status,
                    provider_call_id=self._provider_call_id,
                    provider=self._provider,
                    call_type=(
                        CallType.OUTBOUND.value
                        if self._is_outbound
                        else CallType.INBOUND.value
                    ),
                    client_provider=self._client_provider,
                    duration_seconds=monitor_result.duration_seconds,
                    end_reason=monitor_result.end_reason,
                    provider_data=self._initiate_provider_data,
                    provider_config=monitor_provider_config,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                heartbeat_timeout=timedelta(
                    minutes=1
                ),  # Detect stuck activities quickly
                retry_policy=PROVIDER_RETRY_POLICY,
                task_queue=QUEUE_L,
                result_type=FetchAndPersistCallResultOutput,
            )

            # ========================================
            # PHASE 5: POST-PERSISTENCE TASKS (PARALLEL)
            # ========================================
            # Run these fast tasks in parallel after persist_call_result:
            # - deduct_call_cost: Cost deduction
            # - calculate_conversation_metrics: Conversation metrics (both inbound/outbound)
            # - fetch_client_call_data: Client's call data (outbound only, if applicable)
            # Note: update_call_status is called after CSAT calculation completes

            # Build list of parallel tasks to await
            parallel_tasks = [
                # Deduct actual cost from organization
                workflow.execute_activity(
                    "deduct_call_cost",
                    DeductCostInput(
                        call_id=input.call_id,
                        org_id=input.org_id,
                        cost=0,  # Activity will calculate from call duration
                    ),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_L,
                ),
                # Calculate conversation metrics for both inbound and outbound calls
                workflow.execute_activity(
                    "calculate_conversation_metrics",
                    CalculateConversationMetricsInput(
                        call_id=input.call_id,
                        is_outbound=self._is_outbound,
                        provider=self._provider,
                        # When the customer's account holds the call data (SIP
                        # outbound), transcript reads must dispatch to the
                        # customer's engine. Web bridge / inbound keep the system
                        # provider, so leave it unset there.
                        client_provider=(
                            self._client_provider
                            if self._is_outbound
                            and not self._initiate_provider_data.get("bridge_type")
                            else None
                        ),
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_L,
                ),
            ]

            # Fetch client's call data from their provider account.
            # Needed for: outbound calls, inbound with tool eval, and web
            # bridge calls (to get provider-native system metrics like
            # endpointing, transcriber, model, voice latencies).
            if workflow.patched("fetch-client-data-for-web-bridge"):
                _is_web_bridge = bool(self._initiate_provider_data.get("bridge_type"))
                if (
                    self._is_outbound
                    or _is_web_bridge
                    or (
                        prepare_result.enable_tool_evaluation
                        and prepare_result.client_uses_own_provider
                    )
                ):
                    parallel_tasks.append(
                        self._fetch_and_persist_client_data(input, prepare_result)
                    )
            elif workflow.patched("fetch-client-data-for-inbound-tool-eval"):
                if self._is_outbound or (
                    prepare_result.enable_tool_evaluation
                    and prepare_result.client_uses_own_provider
                ):
                    parallel_tasks.append(
                        self._fetch_and_persist_client_data(input, prepare_result)
                    )
            else:
                if self._is_outbound:
                    parallel_tasks.append(
                        self._fetch_and_persist_client_data(input, prepare_result)
                    )

            # Wait for all fast parallel tasks to complete
            # Use return_exceptions=True so one failure doesn't cancel others
            results = await asyncio.gather(*parallel_tasks, return_exceptions=True)

            # Log any failures but don't fail the workflow
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    task_names = ["deduct_call_cost", "metrics_calculation"]
                    task_name = task_names[i] if i < len(task_names) else f"task_{i}"
                    workflow.logger.warning(
                        f"Post-persistence task '{task_name}' failed for call {input.call_id}: {result}"
                    )

            # ========================================
            # PHASE 6: EVALUATION (CSAT + Evals in parallel)
            # ========================================
            self._status = "EVALUATING"
            run_csat = True
            run_evaluations = True
            skip_reason = None

            if workflow.patched("conversation-validity-gating-v2"):
                decision = decide_processing_skip(
                    message_count=int(
                        getattr(fetch_persist_result, "message_count", 0) or 0
                    ),
                    has_agent_message=bool(
                        getattr(fetch_persist_result, "has_agent_message", False)
                    ),
                    has_customer_message=bool(
                        getattr(fetch_persist_result, "has_customer_message", False)
                    ),
                    duration_seconds=monitor_result.duration_seconds,
                )
                skip_all = decision.processing_skipped
                skip_reason = decision.processing_skip_reason
                run_csat = not skip_all
                run_evaluations = not skip_all

                if skip_all:
                    workflow.logger.info(
                        f"Skipping CSAT and evaluations for call {input.call_id}: {skip_reason}"
                    )
            elif workflow.patched("silence-timeout-eval-gating"):
                # Legacy-but-current behavior for in-flight workflows started before
                # conversation-validity-gating-v2 patch.
                has_agent_message = bool(
                    getattr(fetch_persist_result, "has_agent_message", False)
                )
                has_customer_message = bool(
                    getattr(fetch_persist_result, "has_customer_message", False)
                )

                if monitor_result.end_reason == "silence-timed-out":
                    run_csat = has_agent_message and has_customer_message
                    run_evaluations = run_csat
                    if not run_csat:
                        if not has_agent_message and not has_customer_message:
                            skip_reason = "Call ended with silence timeout and no conversation was recorded."
                        elif not has_agent_message:
                            skip_reason = "Call ended with silence timeout. Agent did not respond."
                        else:
                            skip_reason = "Call ended with silence timeout before customer responded."
            else:
                # Legacy behavior: skip all evaluations on silence-timeout
                _LEGACY_SKIP_REASONS = frozenset({"silence-timed-out"})
                run_csat = monitor_result.end_reason not in _LEGACY_SKIP_REASONS
                run_evaluations = run_csat

            async_tasks = []
            task_labels = []

            if run_csat:
                async_tasks.append(
                    workflow.start_activity(
                        "calculate_voice_csat_score",
                        CalculateVoiceCSATInput(call_id=input.call_id),
                        start_to_close_timeout=timedelta(minutes=10),
                        heartbeat_timeout=timedelta(minutes=2),
                        retry_policy=EVAL_RETRY_POLICY,
                        task_queue=QUEUE_XL,
                        result_type=CalculateVoiceCSATOutput,
                    )
                )
                task_labels.append("csat")

            if run_evaluations:
                eval_timeout = (
                    timedelta(hours=3)
                    if workflow.patched("eval-timeout-3h")
                    else timedelta(minutes=30)
                )
                async_tasks.append(
                    workflow.start_activity(
                        "run_simulate_evaluations",
                        RunSimulateEvaluationsInput(call_execution_id=input.call_id),
                        start_to_close_timeout=eval_timeout,
                        heartbeat_timeout=timedelta(
                            minutes=2
                        ),  # Detect stuck activities faster
                        retry_policy=EVAL_RETRY_POLICY,
                        task_queue=QUEUE_XL,
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                    )
                )
                task_labels.append("evaluations")

                # NEW: Tool eval as separate parallel activity
                async_tasks.append(
                    workflow.start_activity(
                        "run_tool_call_evaluation",
                        RunToolCallEvaluationInput(call_execution_id=input.call_id),
                        start_to_close_timeout=eval_timeout,
                        heartbeat_timeout=timedelta(minutes=2),
                        retry_policy=EVAL_RETRY_POLICY,
                        task_queue=QUEUE_XL,
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        result_type=RunToolCallEvaluationOutput,
                    )
                )
                task_labels.append("tool_call_evaluations")

            if async_tasks:
                task_results = await asyncio.gather(
                    *async_tasks, return_exceptions=True
                )
                for label, result in zip(task_labels, task_results):
                    if label == "csat":
                        if isinstance(result, Exception):
                            workflow.logger.warning(
                                f"CSAT calculation failed for call {input.call_id}: {str(result)}"
                            )
                    if label == "evaluations":
                        if isinstance(result, Exception):
                            workflow.logger.warning(
                                f"Evaluations failed for call {input.call_id}: {str(result)}"
                            )

                    if label == "tool_call_evaluation":
                        if isinstance(result, Exception):
                            workflow.logger.warning(
                                f"Tool call evaluations failed for call {input.call_id}: {str(result)}"
                            )

            skip_all_processing = not run_csat and not run_evaluations
            await workflow.execute_activity(
                "persist_processing_skip_state",
                PersistProcessingSkipStateInput(
                    call_id=input.call_id,
                    processing_skipped=skip_all_processing,
                    processing_skip_reason=skip_reason,
                    mark_eval_outputs_skipped=skip_all_processing,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_S,
            )

            # Update call status to COMPLETED after all processing is done
            await workflow.execute_activity(
                "update_call_status",
                UpdateCallStatusInput(
                    call_id=input.call_id,
                    status=CallExecutionModel.CallStatus.COMPLETED,
                    test_execution_id=input.test_execution_id,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_S,
            )

            # ========================================
            # PHASE 7: SIGNAL COMPLETION
            # ========================================
            # Note: Phone and slot already released early (see RELEASING_RESOURCES section)
            # This allows evals to run without consuming voice call capacity

            # Signal parent workflow completion
            # Call has completed successfully - status already updated to COMPLETED in DB
            await workflow.execute_activity(
                "signal_call_completed",
                SignalCallCompleteInput(
                    workflow_id=input.test_workflow_id,
                    call_id=input.call_id,
                    status=CallExecutionModel.CallStatus.COMPLETED.value,
                    failed=False,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=SIGNAL_RETRY_POLICY,
                task_queue=QUEUE_L,
            )

            # ========================================
            # COMPLETION
            # ========================================
            self._status = CallExecutionModel.CallStatus.COMPLETED

            return CallExecutionOutput(
                status=CallExecutionModel.CallStatus.COMPLETED,
                call_id=input.call_id,
                provider_call_id=self._provider_call_id,
                duration_seconds=monitor_result.duration_seconds,
            )

        except asyncio.CancelledError:
            # Handle Temporal cancellation (from handle.cancel())
            workflow.logger.info(f"CallExecutionWorkflow cancelled: {input.call_id}")

            # Release phone number for new path.
            # _handle_cancellation has `pass` for phone-dispatcher path to prevent
            # double-release (cooperative cancel + CancelledError both call it),
            # so phone release is handled here instead.
            if workflow.patched("phone-dispatcher"):
                if self._phone_requested and not self._phone_released:
                    try:
                        await workflow.execute_activity(
                            "release_phone_number_slot",
                            ReleasePhoneNumberSlotInput(
                                call_id=input.call_id,
                            ),
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=SIGNAL_RETRY_POLICY,
                            task_queue=QUEUE_L,
                        )
                        self._phone_released = True
                    except Exception as e:
                        workflow.logger.warning(
                            f"Failed to release phone number for "
                            f"call {input.call_id}: {e}"
                        )

            return await self._handle_cancellation(input)

        except Exception as e:
            workflow.logger.warning(f"CallExecutionWorkflow failed: {str(e)}")
            # Report to Sentry via activity (fire-and-forget, don't wait for completion)
            workflow.start_activity(
                "report_workflow_error",
                ReportErrorInput(
                    workflow_name="CallExecutionWorkflow",
                    workflow_id=workflow.info().workflow_id,
                    error_message=str(e),
                    error_type=type(e).__name__,
                    context={"call_id": input.call_id, "org_id": input.org_id},
                ),
                start_to_close_timeout=timedelta(seconds=10),
                task_queue=QUEUE_S,
            )
            return await self._fail(input, str(e))

    # ========================================
    # SIGNAL HANDLERS
    # ========================================

    @workflow.signal
    async def slot_granted(self, call_id: str = None):
        """Signal from dispatcher that rate limit slot is available."""
        self._slot_granted = True
        self._slot_granted_event.set()

    @workflow.signal
    async def cancel(self):
        """Signal to cancel this call execution."""
        self._cancelled = True
        self._slot_granted_event.set()

    @workflow.signal
    async def phone_number_granted(self, phone_data: PhoneNumberGrantedSignal):
        """Signal from PhoneNumberDispatcherWorkflow that a phone has been granted."""
        self._phone_number_id = phone_data.phone_id
        self._system_phone_number = phone_data.phone_number
        self._system_phone_number_id = phone_data.phone_number_id
        self._phone_granted = True

    @workflow.signal
    async def call_ended(
        self,
        status: str,
        duration_seconds: int = 0,
        end_reason: str = "",
    ):
        """Signal from LiveKit webhook that the call has ended.

        Replaces polling-based monitoring for LiveKit provider.
        Webhook → API → Temporal signal → workflow continues.
        """
        self._call_ended = True
        self._call_ended_status = status
        self._call_ended_duration = duration_seconds
        self._call_ended_reason = end_reason

    # ========================================
    # QUERIES
    # ========================================

    @workflow.query
    def get_status(self) -> CallStatus:
        """Query current workflow status."""
        return CallStatus(
            status=self._status,
            provider_call_id=self._provider_call_id,
            phase_details=None,
        )

    # ========================================
    # HELPER METHODS
    # ========================================

    async def _run_eval_only_mode(
        self, input: CallExecutionInput
    ) -> CallExecutionOutput:
        """
        Run eval-only mode - skips call execution, runs evaluations only.

        Used for eval-only reruns where the call data already exists.
        No rate limiting, phone acquisition, or call execution needed.
        """
        workflow.logger.info(f"Running eval-only mode for call {input.call_id}")

        try:
            # ========================================
            # PHASE 1: RUN EVALUATIONS
            # ========================================
            self._status = "EVALUATING"

            # Update call status to ANALYZING in DB so the UI reflects the correct state
            if workflow.patched("eval-only-analyzing-status"):
                await workflow.execute_activity(
                    "update_call_status",
                    UpdateCallStatusInput(
                        call_id=input.call_id,
                        status=CallExecutionModel.CallStatus.ANALYZING,
                        test_execution_id=input.test_execution_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_S,
                )

            # TRY_CANCEL allows workflow to be cancelled without waiting for activity
            eval_only_timeout = (
                timedelta(hours=3)
                if workflow.patched("eval-only-timeout-3h")
                else timedelta(minutes=30)
            )
            eval_result = await workflow.execute_activity(
                "run_simulate_evaluations",
                RunSimulateEvaluationsInput(
                    call_execution_id=input.call_id,
                    skip_existing=False,  # Re-run all evaluations
                ),
                start_to_close_timeout=eval_only_timeout,
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=EVAL_RETRY_POLICY,
                task_queue=QUEUE_XL,
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )

            # Run tool evaluation (versioned — new executions only)
            tool_eval_result = None
            if workflow.patched("tool-eval-activity"):
                tool_eval_result = await workflow.execute_activity(
                    "run_tool_call_evaluation",
                    RunToolCallEvaluationInput(
                        call_execution_id=input.call_id,
                    ),
                    start_to_close_timeout=eval_only_timeout,
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=EVAL_RETRY_POLICY,
                    task_queue=QUEUE_XL,
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                    result_type=RunToolCallEvaluationOutput,
                )

            # Determine success/failure
            failed = False
            if hasattr(eval_result, "success") and not eval_result.success:
                failed = True
                workflow.logger.warning(
                    f"Eval-only failed for call {input.call_id}: {getattr(eval_result, 'error', 'Unknown error')}"
                )
            if (
                tool_eval_result is not None
                and hasattr(tool_eval_result, "success")
                and not tool_eval_result.success
            ):
                failed = True
                workflow.logger.warning(
                    f"Eval-only tool eval failed for call {input.call_id}: "
                    f"{getattr(tool_eval_result, 'error', 'Unknown error')}"
                )

            # ========================================
            # PHASE 2: UPDATE STATUS & SIGNAL COMPLETION
            # ========================================
            final_status = (
                CallExecutionModel.CallStatus.FAILED
                if failed
                else CallExecutionModel.CallStatus.COMPLETED
            )

            # Update call status in database
            await workflow.execute_activity(
                "update_call_status",
                UpdateCallStatusInput(
                    call_id=input.call_id,
                    status=final_status,
                    test_execution_id=input.test_execution_id,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_S,
            )

            # Signal parent that this call has entered analyzing state.
            # Without this, the parent's _all_analyzing() check deadlocks
            # because it waits for all calls to signal analyzing.
            await workflow.execute_activity(
                "signal_call_analyzing",
                SignalCallAnalyzingInput(
                    workflow_id=input.test_workflow_id,
                    call_id=input.call_id,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=SIGNAL_RETRY_POLICY,
                task_queue=QUEUE_L,
            )

            await workflow.execute_activity(
                "signal_call_completed",
                SignalCallCompleteInput(
                    workflow_id=input.test_workflow_id,
                    call_id=input.call_id,
                    status=final_status.value,
                    failed=failed,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=SIGNAL_RETRY_POLICY,
                task_queue=QUEUE_L,
            )

            self._status = final_status

            return CallExecutionOutput(
                status=final_status,
                call_id=input.call_id,
                error=getattr(eval_result, "error", None) if failed else None,
            )

        except asyncio.CancelledError:
            # Handle Temporal cancellation (from handle.cancel())
            workflow.logger.info(f"Eval-only workflow cancelled: {input.call_id}")
            return await self._handle_cancellation(input)

        except Exception as e:
            workflow.logger.warning(
                f"Eval-only mode failed for call {input.call_id}: {str(e)}"
            )

            # Update call status to FAILED in DB so finalize_rerun_execution
            # sees the correct state (without this, call stays at its pre-eval
            # status and counts are wrong)
            try:
                await workflow.execute_activity(
                    "update_call_status",
                    UpdateCallStatusInput(
                        call_id=input.call_id,
                        status=CallExecutionModel.CallStatus.FAILED,
                        test_execution_id=input.test_execution_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_S,
                )
            except Exception as status_error:
                workflow.logger.warning(
                    f"Failed to update call status to FAILED for {input.call_id}: {str(status_error)}"
                )

            # Persist processing skipped metadata for eval-only failures as well,
            # so UI remains consistent with regular call failure flows.
            if workflow.patched("conversation-validity-gating-v2"):
                try:
                    await workflow.execute_activity(
                        "persist_processing_skip_state",
                        PersistProcessingSkipStateInput(
                            call_id=input.call_id,
                            processing_skipped=True,
                            processing_skip_reason=(
                                "Call failed during evaluation processing, so processing was skipped."
                            ),
                            mark_eval_outputs_skipped=True,
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=DB_RETRY_POLICY,
                        task_queue=QUEUE_S,
                    )
                except Exception as outcome_error:
                    workflow.logger.warning(
                        f"Failed to persist skipped outcomes for eval-only failed call {input.call_id}: {str(outcome_error)}"
                    )

            # Signal parent that eval failed
            try:
                await workflow.execute_activity(
                    "signal_call_completed",
                    SignalCallCompleteInput(
                        workflow_id=input.test_workflow_id,
                        call_id=input.call_id,
                        status=CallExecutionModel.CallStatus.FAILED.value,
                        failed=True,
                    ),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=SIGNAL_RETRY_POLICY,
                    task_queue=QUEUE_L,
                )
            except Exception as signal_error:
                workflow.logger.warning(
                    f"Failed to signal parent for eval-only failure {input.call_id}: {str(signal_error)}"
                )

            return CallExecutionOutput(
                status=CallExecutionModel.CallStatus.FAILED,
                call_id=input.call_id,
                error=str(e),
            )

    async def _fail(self, input: CallExecutionInput, error: str) -> CallExecutionOutput:
        """Mark workflow as failed and update database."""
        self._status = CallExecutionModel.CallStatus.FAILED

        # Persist FAILED status to CallExecution in DB
        # (fetch_and_persist_call_result does NOT update CallExecution.status —
        # the workflow owns status transitions via update_call_status)
        try:
            await workflow.execute_activity(
                "update_call_status",
                UpdateCallStatusInput(
                    call_id=input.call_id,
                    status=CallExecutionModel.CallStatus.FAILED,
                    test_execution_id=input.test_execution_id,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_S,
            )
        except Exception as e:
            workflow.logger.warning(
                f"Failed to update call status to FAILED for {input.call_id}: {str(e)}"
            )

        # Release resources if acquired and not already released
        # Also release if slot was requested but not yet granted (removes from dispatcher pending queue)
        release_tasks = []

        if (self._slot_granted or self._slot_requested) and not self._slot_released:
            release_tasks.append(
                workflow.execute_activity(
                    "release_call_slot",
                    ReleaseSlotInput(call_id=input.call_id),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=SIGNAL_RETRY_POLICY,
                    task_queue=QUEUE_L,
                )
            )

        if workflow.patched("phone-dispatcher"):
            # NEW PATH: Release phone from DB + dispatcher slot
            if self._phone_requested and not self._phone_released:
                release_tasks.append(
                    workflow.execute_activity(
                        "release_phone_number_slot",
                        ReleasePhoneNumberSlotInput(
                            call_id=input.call_id,
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=SIGNAL_RETRY_POLICY,
                        task_queue=QUEUE_L,
                    )
                )
        else:
            # OLD PATH: Direct release (requires phone_number_id)
            if self._phone_number_id and not self._phone_released:
                release_tasks.append(
                    workflow.execute_activity(
                        "release_phone_number",
                        ReleasePhoneInput(phone_id=self._phone_number_id),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=SIGNAL_RETRY_POLICY,
                        task_queue=QUEUE_L,
                    )
                )

        if release_tasks:
            results = await asyncio.gather(*release_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    workflow.logger.warning(
                        f"Failed to release resource for call {input.call_id}: {str(result)}"
                    )
                    # Don't mark as released if it failed — stale slot reaper will clean up

            # Only mark released if no exceptions
            if not any(isinstance(r, Exception) for r in results):
                self._slot_released = True
                self._phone_released = True
            else:
                # Mark individually based on success
                slot_idx = (
                    0
                    if (
                        (self._slot_granted or self._slot_requested)
                        and not self._slot_released
                    )
                    else -1
                )
                phone_idx = (
                    len(release_tasks) - 1
                    if (
                        (self._phone_requested or self._phone_number_id)
                        and not self._phone_released
                    )
                    else -1
                )
                if slot_idx >= 0 and not isinstance(results[slot_idx], Exception):
                    self._slot_released = True
                if phone_idx >= 0 and not isinstance(results[phone_idx], Exception):
                    self._phone_released = True
        else:
            self._slot_released = True
            self._phone_released = True

        # Persist failed status to database (updates CallExecution and CreateCallExecution)
        fetch_persist_result: Optional[FetchAndPersistCallResultOutput] = None
        try:
            fetch_persist_result = await workflow.execute_activity(
                "fetch_and_persist_call_result",
                FetchAndPersistCallResultInput(
                    call_id=input.call_id,
                    status=CallExecutionModel.CallStatus.FAILED,
                    provider_call_id=self._provider_call_id or "",
                    provider=self._provider,
                    call_type=(
                        CallType.OUTBOUND.value
                        if self._is_outbound
                        else CallType.INBOUND.value
                    ),
                    client_provider=self._client_provider,
                    end_reason=error,
                    provider_data=self._initiate_provider_data,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                heartbeat_timeout=timedelta(minutes=1),
                retry_policy=DB_RETRY_POLICY,
                task_queue=QUEUE_L,
                result_type=FetchAndPersistCallResultOutput,
            )
        except Exception as e:
            workflow.logger.warning(
                f"Failed to persist failed status for call {input.call_id}: {str(e)}"
            )

        # Persist explicit skipped outcomes for failed calls so UI can render
        # non-empty cells with reason (instead of blank/error ambiguity).
        if workflow.patched("conversation-validity-gating-v2"):
            skip_reason = f"Call failed before evaluations: {error}"

            if fetch_persist_result:
                decision = decide_processing_skip(
                    message_count=int(fetch_persist_result.message_count or 0),
                    has_agent_message=bool(fetch_persist_result.has_agent_message),
                    has_customer_message=bool(
                        fetch_persist_result.has_customer_message
                    ),
                    duration_seconds=None,
                )
                if decision.processing_skipped and decision.processing_skip_reason:
                    skip_reason = decision.processing_skip_reason

            try:
                await workflow.execute_activity(
                    "persist_processing_skip_state",
                    PersistProcessingSkipStateInput(
                        call_id=input.call_id,
                        processing_skipped=True,
                        processing_skip_reason=skip_reason,
                        mark_eval_outputs_skipped=True,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_S,
                )
            except Exception as outcome_error:
                workflow.logger.warning(
                    f"Failed to persist skipped outcomes for failed call {input.call_id}: {str(outcome_error)}"
                )

        # Signal parent workflow that this call failed
        try:
            await workflow.execute_activity(
                "signal_call_completed",
                SignalCallCompleteInput(
                    workflow_id=input.test_workflow_id,
                    call_id=input.call_id,
                    status=CallExecutionModel.CallStatus.FAILED.value,
                    failed=True,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=SIGNAL_RETRY_POLICY,
                task_queue=QUEUE_L,
            )
        except Exception as e:
            workflow.logger.warning(
                f"Failed to signal parent for failed call {input.call_id}: {str(e)}"
            )

        return CallExecutionOutput(
            status=CallExecutionModel.CallStatus.FAILED,
            call_id=self._call_id,
            provider_call_id=self._provider_call_id,
            error=error,
        )

    async def _fetch_and_persist_client_data(
        self, input: CallExecutionInput, prepare_result: PrepareCallOutput
    ) -> None:
        """
        Fetch and persist client's call data from their provider account.

        For outbound calls: customer_call_id is already set, skips find_client_call_id.
        For inbound calls (tool eval): uses find_client_call_id to discover customer's call.

        The fetch_client_call_data activity now handles both fetching AND persisting
        to avoid passing large payloads through Temporal.
        """
        # Fetch and persist client's call data from their provider account
        # Use instance variables for data that comes from phone acquisition and initiate_call
        # (not available in prepare_result for outbound calls)
        client_data = await workflow.execute_activity(
            "fetch_client_call_data",
            FetchClientCallInput(
                call_id=input.call_id,
                call_type=(
                    CallType.OUTBOUND.value
                    if self._is_outbound
                    else CallType.INBOUND.value
                ),
                client_provider=prepare_result.client_provider,
                client_api_key=prepare_result.client_api_key,
                client_assistant_id=prepare_result.client_assistant_id,
                # For outbound, the destination is the system phone from acquisition.
                # For inbound, it's the user's phone from prepare_call.
                customer_phone_number=(
                    self._system_phone_number
                    if self._is_outbound
                    else prepare_result.customer_phone_number
                ),
                system_assistant_id=self._system_assistant_id,  # From initiate_call result
                system_phone_number=self._system_phone_number,  # From phone acquisition (outbound only)
                system_phone_number_id=self._system_phone_number_id,  # From phone acquisition (outbound only)
            ),
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=PROVIDER_RETRY_POLICY,
            task_queue=QUEUE_XL,
            result_type=FetchClientCallOutput,
        )

        # Activity now persists directly, just log if there was an issue
        if not client_data.success:
            workflow.logger.warning(
                f"Failed to fetch client call data for call {input.call_id}: {client_data.error}"
            )

    async def _handle_cancellation(
        self, input: CallExecutionInput
    ) -> CallExecutionOutput:
        """Handle workflow cancellation.

        In the Python Temporal SDK, once CancelledError is caught the workflow
        can run cleanup activities normally — no shielding scope is needed
        (CancellationScope is a Go SDK concept, not available in Python SDK).
        """
        self._status = CallExecutionModel.CallStatus.CANCELLED

        # Release resources (slot and phone if acquired)
        # Also release if slot was requested but not yet granted (removes from dispatcher pending queue)
        release_tasks = []

        if (self._slot_granted or self._slot_requested) and not self._slot_released:
            release_tasks.append(
                workflow.execute_activity(
                    "release_call_slot",
                    ReleaseSlotInput(call_id=input.call_id),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=SIGNAL_RETRY_POLICY,
                    task_queue=QUEUE_L,
                )
            )

        # Track which index in release_tasks corresponds to phone release
        phone_release_idx = -1
        if not workflow.patched("phone-dispatcher"):
            # OLD PATH: Direct release (requires phone_number_id)
            # NEW PATH: Phone number release is handled by the CancelledError
            # handler BEFORE calling _handle_cancellation, preventing
            # double-release.
            if self._phone_number_id and not self._phone_released:
                phone_release_idx = len(release_tasks)
                release_tasks.append(
                    workflow.execute_activity(
                        "release_phone_number",
                        ReleasePhoneInput(phone_id=self._phone_number_id),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=SIGNAL_RETRY_POLICY,
                        task_queue=QUEUE_L,
                    )
                )

        if release_tasks:
            results = await asyncio.gather(*release_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    workflow.logger.warning(
                        f"Failed to release resource for cancelled call {input.call_id}: {str(result)}"
                    )

            # Mark each resource individually based on success
            # Slot release is always index 0 (if present)
            if not isinstance(results[0], Exception):
                self._slot_released = True
            if phone_release_idx >= 0 and not isinstance(
                results[phone_release_idx], Exception
            ):
                self._phone_released = True

        if workflow.patched("livekit-cancel-cleanup"):
            # New path: persist status + clean up provider resources (LiveKit room/egress)
            try:
                await workflow.execute_activity(
                    "fetch_and_persist_call_result",
                    FetchAndPersistCallResultInput(
                        call_id=input.call_id,
                        status=CallExecutionModel.CallStatus.CANCELLED,
                        provider_call_id=self._provider_call_id or "",
                        provider=self._provider,
                        call_type=(
                            CallType.OUTBOUND.value
                            if self._is_outbound
                            else CallType.INBOUND.value
                        ),
                        client_provider=self._client_provider,
                        end_reason="cancelled",
                        provider_data=self._initiate_provider_data,
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    heartbeat_timeout=timedelta(minutes=1),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_L,
                    result_type=FetchAndPersistCallResultOutput,
                )
            except Exception as e:
                workflow.logger.warning(
                    f"Failed to persist cancelled status for {input.call_id}: {str(e)}"
                )
        else:
            # Old path: just update DB status directly
            try:
                await workflow.execute_activity(
                    "update_call_status",
                    UpdateCallStatusInput(
                        call_id=input.call_id,
                        status=CallExecutionModel.CallStatus.CANCELLED,
                        test_execution_id=input.test_execution_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DB_RETRY_POLICY,
                    task_queue=QUEUE_S,
                )
            except Exception as e:
                workflow.logger.warning(
                    f"Failed to update call status to CANCELLED for {input.call_id}: {str(e)}"
                )

        # Signal parent workflow that this call was cancelled
        try:
            await workflow.execute_activity(
                "signal_call_completed",
                SignalCallCompleteInput(
                    workflow_id=input.test_workflow_id,
                    call_id=input.call_id,
                    status=CallExecutionModel.CallStatus.CANCELLED.value,
                    failed=True,  # Cancelled counts as failed for completion tracking
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=SIGNAL_RETRY_POLICY,
                task_queue=QUEUE_L,
            )
        except Exception as e:
            workflow.logger.warning(
                f"Failed to signal parent for cancelled call {input.call_id}: {str(e)}"
            )

        return CallExecutionOutput(
            status=CallExecutionModel.CallStatus.CANCELLED,
            call_id=input.call_id,
            provider_call_id=self._provider_call_id,
        )
