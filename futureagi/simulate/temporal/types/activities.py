"""
Data classes for activity inputs and outputs.

These types define the contracts for all activities used in call execution workflows.
Organized by activity category (setup, call prep, execution, evaluation, etc.).
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from simulate.semantics import CallType, ProviderPayload
from tracer.models.observability_provider import ProviderChoices

# =============================================================================
# Setup Activities (TestExecutionWorkflow initialization)
# =============================================================================


@dataclass
class SetupTestInput:
    """
    Input for setup_test_execution activity.

    Activity: Validates the test configuration, loads scenarios from DB,
    fetches simulator agent and agent definition. Returns all the loaded
    config needed to create calls.
    """

    test_execution_id: str
    run_test_id: str
    scenario_ids: list[str]
    simulator_id: Optional[str] = None


@dataclass
class SetupTestOutput:
    """
    Output from setup_test_execution activity.

    Returns all the loaded configuration needed to create calls.
    All agent config comes from agent_version.configuration_snapshot.
    """

    success: bool
    error: Optional[str] = None

    # Loaded configuration
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    simulator_agent: Optional[dict[str, Any]] = None
    agent_version: Optional[dict[str, Any]] = None  # Includes configuration_snapshot
    workspace_id: Optional[str] = None  # Workspace ID from run_test.workspace


@dataclass
class CreateCallRecordsInput:
    """
    Input for create_call_execution_records activity.

    Activity: Creates CallExecution records in DB for each scenario row.
    Returns the list of call_ids that were created.
    """

    test_execution_id: str
    scenarios: list[dict[str, Any]]
    simulator_agent: Optional[dict[str, Any]] = None
    agent_definition: Optional[dict[str, Any]] = None


@dataclass
class CreateCallRecordsOutput:
    """
    Output from create_call_execution_records activity.

    Returns the list of call_ids that were created.
    """

    call_ids: list[str] = field(default_factory=list)
    total_created: int = 0
    error: Optional[str] = None


@dataclass
class StorePendingCallsInput:
    """
    Input for store_pending_call_ids activity.

    Activity: Stores call IDs in DB so workflow can resume after continue-as-new.
    Part of the scalability pattern - workflow checkpoints which calls exist.
    """

    test_execution_id: str
    call_ids: list[str]


@dataclass
class GetUnlaunchedCallsInput:
    """
    Input for get_unlaunched_call_ids activity.

    Activity: After continue-as-new, workflow needs to know which calls
    haven't been launched yet. Queries DB for calls where launched=False.
    """

    test_execution_id: str


@dataclass
class GetUnlaunchedCallsOutput:
    """
    Output from get_unlaunched_call_ids activity.

    Returns the list of call IDs that haven't been launched yet.
    """

    call_ids: list[str] = field(default_factory=list)


@dataclass
class FinalizeInput:
    """
    Input for finalize_test_execution activity.

    Activity: Updates TestExecution record with final status and counts.
    Called at the end of the workflow.
    """

    test_execution_id: str
    status: str  # COMPLETED, FAILED, CANCELLED
    completed_calls: int = 0
    failed_calls: int = 0


@dataclass
class CancelPendingCallsInput:
    """
    Input for cancel_pending_calls activity.

    Activity: Cancels all pending/ongoing CallExecution records for a test execution.
    Called when a test execution is cancelled by the user.
    """

    test_execution_id: str
    reason: str = "Cancelled by user"


@dataclass
class CancelPendingCallsOutput:
    """
    Output from cancel_pending_calls activity.

    Returns the count of calls that were cancelled.
    """

    cancelled_count: int = 0


# =============================================================================
# Call Preparation Activities
# =============================================================================


@dataclass
class PrepareCallInput:
    """
    Input for prepare_call activity.

    Activity: Fetches CallExecution from DB, builds the system prompt from
    scenario data, loads voice settings. Returns everything needed to make the call.
    """

    call_id: str
    # For multi-tenancy scoping and validation.
    # Empty string ("") means the RunTest has no workspace assigned - validation is skipped.
    workspace_id: str = ""


@dataclass
class PrepareCallOutput:
    """
    Output from prepare_call activity.

    Returns everything needed to make the call: prompt, voice settings,
    provider config, metadata, etc.
    """

    # Call configuration
    is_outbound: bool
    to_number: Optional[str] = None
    system_prompt: str = ""
    voice_settings: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Provider configuration
    provider: str = "livekit"
    provider_config: dict[str, Any] = field(default_factory=dict)

    # Limits
    max_duration_minutes: int = 15

    # Context for downstream activities
    situation_text: Optional[str] = None

    # Call data for initiation (FAGICallData as dict)
    call_data: dict[str, Any] = field(default_factory=dict)

    # Client provider data (for outbound calls using user's credentials)
    client_uses_own_provider: bool = False
    client_api_key: Optional[str] = None
    client_assistant_id: Optional[str] = None
    client_phone_number: Optional[str] = None  # User's phone to call FROM in outbound
    client_provider: str = "vapi"

    # System data for client call matching
    system_assistant_id: Optional[str] = None
    system_phone_number: Optional[str] = None
    customer_phone_number: Optional[str] = None
    system_phone_number_id: Optional[str] = None

    # Tool evaluation flag from RunTest
    enable_tool_evaluation: bool = False

    # WebRTC bridge connection type (None = SIP, "web_vapi", "web_retell")
    connection_type: Optional[str] = None

    # Error handling
    error: Optional[str] = None


@dataclass
class CheckBalanceInput:
    """
    Input for check_call_balance activity.

    Activity: Checks if org has sufficient balance for estimated call cost.
    Prevents starting calls that will fail mid-way.
    """

    org_id: str
    estimated_duration_minutes: int


@dataclass
class CheckBalanceOutput:
    """
    Output from check_call_balance activity.

    Returns whether the org has sufficient balance and cost estimates.
    """

    sufficient: bool
    current_balance: Optional[float] = None
    estimated_cost: Optional[float] = None
    error: Optional[str] = None


@dataclass
class AcquirePhoneInput:
    """
    Input for acquire_phone_number activity.

    Activity: Gets an available phone number from the pool for outbound calls.
    Continuously polls until a phone becomes available (no timeout - waits indefinitely).
    Uses heartbeats to keep the activity alive during long waits.
    """

    call_id: str
    call_direction: str  # "inbound" or "outbound"
    poll_interval_seconds: int = 15  # How often to check for available phones


@dataclass
class AcquirePhoneOutput:
    """
    Output from acquire_phone_number activity.

    Returns the acquired phone ID and number, or error if none available.
    """

    success: bool
    phone_id: Optional[str] = None  # Django model ID
    phone_number: Optional[str] = None  # Actual phone number string
    provider_phone_id: Optional[str] = None  # Provider-specific phone number ID
    error: Optional[str] = None


@dataclass
class ReleasePhoneInput:
    """
    Input for release_phone_number activity.

    Activity: Returns phone number to the pool after call ends.
    """

    phone_id: str


@dataclass
class BackgroundSoundInput:
    """
    Input for select_background_sound activity.

    Activity: Uses LLM to pick appropriate background sound based on situation
    (e.g., "office" for business call).
    """

    situation: str
    voice_settings: dict[str, Any]


@dataclass
class BackgroundSoundOutput:
    """
    Output from select_background_sound activity.

    Returns the selected background sound URL or preset name.
    """

    selected_sound: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# Call Execution Activities
# =============================================================================


@dataclass
class CreateProviderCallInput:
    """
    Input for create_provider_call activity.

    Activity: Creates the actual call via VAPI (or other provider).
    Returns the provider's call ID.
    """

    call_id: str
    is_outbound: bool
    phone_number_id: Optional[str] = None
    to_number: Optional[str] = None
    system_prompt: str = ""
    voice_settings: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    provider: str = "vapi"
    provider_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class CreateProviderCallOutput:
    """
    Output from create_provider_call activity.

    Returns the provider's call ID and initial status.
    """

    provider_call_id: str
    provider: str
    status: str
    error: Optional[str] = None


@dataclass
class MonitorCallInput:
    """
    Input for monitor_call_until_complete activity.

    Activity: Polls provider every 20s until call ends. Long-running activity
    (up to 4 hours) with heartbeats.
    """

    call_id: str
    provider_call_id: str
    call_type: str  # "inbound" or "outbound"
    provider: str
    # Customer's provider (agent under test); selects the SIP-outbound poll path.
    client_provider: str = "vapi"
    provider_config: dict[str, Any] = field(default_factory=dict)
    poll_interval_seconds: int = 20
    max_duration_seconds: int = 14400  # 4 hours


@dataclass
class MonitorCallOutput:
    """
    Output from monitor_call_until_complete activity.

    Returns only essential fields for workflow logic.
    Large data (provider_data, cost_breakdown) is stored directly in DB
    to avoid Temporal's 2MB payload limit.
    """

    success: bool
    status: Optional[str] = None  # "ended", "failed", "cancelled", etc.
    duration_seconds: Optional[int] = None
    end_reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class StoreCallDataInput:
    """
    Input for store_call_data activity.

    Activity: After call ends, fetches recordings, transcript, costs from
    provider and stores in DB.
    """

    call_id: str
    provider_call_id: str
    provider: str
    provider_config: dict[str, Any] = field(default_factory=dict)
    call_status: str = "ended"


@dataclass
class UpdateCallStatusInput:
    """
    Input for update_call_status activity.

    Activity: Simple DB update to change CallExecution.status.
    Also updates TestExecution status to RUNNING if test_execution_id is provided
    and the call status is being set to REGISTERED.
    """

    call_id: str
    status: str  # PENDING, REGISTERED, ONGOING, COMPLETED, FAILED, CANCELLED
    test_execution_id: Optional[str] = (
        None  # If provided, marks TestExecution as RUNNING
    )


@dataclass
class PersistProcessingSkipStateInput:
    """
    Input for persist_processing_skip_state activity.

    Persists post-call processing skip state in call_metadata and can optionally
    materialize skipped evaluation rows in eval_outputs so UI can render
    explicit "Skipped" states.
    """

    call_id: str
    processing_skipped: Optional[bool] = None
    processing_skip_reason: Optional[str] = None
    mark_eval_outputs_skipped: bool = False


# =============================================================================
# Evaluation Activities
# =============================================================================


@dataclass
class EvalResult:
    """
    Result of a single evaluation on a CallExecution.

    This is an in-memory data structure for passing evaluation results
    between activities and workflows. It is NOT a database model.

    Evaluation types and output format:
    - Pass/Fail: output = "passed" or "failed" (str)
    - Percentage: output = 0.0 to 100.0 (float)
    - Deterministic: output = ["option1", "option2", ...] (list[str])
    """

    eval_id: str
    eval_name: str
    status: str  # "passed", "failed", "error"

    # Eval output - type depends on eval configuration:
    # - Pass/Fail: str ("passed"/"failed")
    # - Percentage: float (0.0-100.0)
    # - Deterministic: list[str] (array of choices)
    output: Optional[str | float | list[str]] = None

    # Reason/explanation for the eval result
    reason: Optional[str] = None

    # Error message if status is "error"
    error: Optional[str] = None


@dataclass
class RunCallEvalsInput:
    """
    Input for run_call_evaluations activity.

    Activity: Runs all configured evaluations for a call. Needs transcript/
    recording from DB. Returns individual eval results.
    """

    call_id: str
    test_execution_id: str


@dataclass
class RunCallEvalsOutput:
    """
    Output from run_call_evaluations activity.

    Returns the results of all evaluations with pass/fail/error counts.
    """

    results: list[EvalResult] = field(default_factory=list)
    total_evals: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0


@dataclass
class EvalSummaryInput:
    """
    Input for generate_eval_summary activity.

    Activity: After all calls complete, generates aggregate statistics across
    all evals (pass rates, score distributions, etc.).
    """

    test_execution_id: str


# =============================================================================
# Downstream Task Activities
# =============================================================================


@dataclass
class UpdateMetricsInput:
    """
    Input for update_call_metrics activity.

    Activity: Updates aggregated metrics (pass rates, avg duration, etc.)
    after each call completes.
    """

    call_id: str
    test_execution_id: str
    eval_results: list[EvalResult] = field(default_factory=list)


@dataclass
class DownstreamTasksInput:
    """
    Input for run_downstream_tasks activity.

    Activity: Runs any custom post-call processing (webhooks, notifications,
    integrations, etc.).
    """

    call_id: str
    test_execution_id: str
    call_status: str
    eval_results: list[EvalResult] = field(default_factory=list)


# =============================================================================
# Signal Activities
# =============================================================================


@dataclass
class RequestSlotInput:
    """
    Input for request_call_slot activity.

    Activity: Signals the dispatcher to request a rate limit slot.
    Workflow waits for SLOT_GRANTED signal before proceeding.
    """

    workflow_id: str
    call_id: str
    org_id: str
    # Agent-level concurrency (for LiveKit agents)
    agent_definition_id: str = ""
    agent_concurrency_limit: int = 0  # 0 = no agent-level limit


@dataclass
class ReleaseSlotInput:
    """
    Input for release_call_slot activity.

    Activity: Signals the dispatcher to release this call's rate limit slot.
    """

    call_id: str


@dataclass
class SignalCallCompleteInput:
    """
    Input for signal_call_completed activity.

    Activity: Signals the parent workflow that this call finished.
    Uses minimal data for scalability (full results stored in DB).
    """

    workflow_id: str
    call_id: str
    status: str
    failed: bool


@dataclass
class SignalCallAnalyzingInput:
    """
    Input for signal_call_analyzing activity.

    Activity: Signals the parent workflow that this call has entered ANALYZING state.
    This allows the parent to transition to EVALUATING status when all calls are analyzing.
    """

    workflow_id: str
    call_id: str


@dataclass
class SignalSlotBatchInput:
    """
    Input for signal_slots_granted_batch activity.

    Activity: Dispatcher signals multiple workflows at once that their slots
    are granted. Batching improves efficiency vs individual signals.

    Each grant: {"workflow_id": "...", "call_id": "..."}
    """

    grants: list[dict[str, str]] = field(default_factory=list)


# =============================================================================
# XL Queue Activities (Resource-intensive operations)
# =============================================================================


@dataclass
class RunEvaluationsInput:
    """
    Input for run_evaluations activity.

    Activity: Runs all configured evaluations on call results using LLM.
    Resource-intensive, runs on tasks_xl queue with long timeout.

    NOTE: This wraps existing _run_simulate_evaluations_task from test_executor.py
    """

    call_id: str
    test_execution_id: str


@dataclass
class RunEvaluationsOutput:
    """
    Output from run_evaluations activity.

    Returns evaluation results that get stored in CallExecution.eval_outputs.
    """

    success: bool
    eval_results: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class FetchClientCallInput:
    """
    Input for fetch_client_call_data activity.

    Activity: For calls where client uses their own provider account,
    fetches their call data to get client-side metrics, costs, and performance.

    Uses VoiceServiceManager.find_client_call() and get_call() to match
    and fetch the client's call based on timing and phone number matching.
    """

    call_id: str
    client_api_key: Optional[str] = ""
    client_assistant_id: Optional[str] = ""
    client_provider: Optional[str] = "vapi"

    # Our call data for matching
    customer_phone_number: Optional[str] = ""
    call_type: str = CallType.INBOUND.value

    # System data for call matching (may be None for inbound calls)
    system_assistant_id: Optional[str] = None
    system_phone_number: Optional[str] = None
    system_phone_number_id: Optional[str] = None


@dataclass
class FetchClientCallOutput:
    """
    Output from fetch_client_call_data activity.

    Returns client's call data including metrics, costs, and raw provider data.
    """

    success: bool
    error: Optional[str] = None

    # Client call data
    client_call_id: Optional[str] = None
    client_metrics: Optional[dict[str, float]] = None  # Latency metrics
    client_cost_breakdown: Optional[dict[str, Any]] = None
    client_total_cost: float = 0.0
    client_raw_data: Optional[dict[str, Any]] = None


@dataclass
class InitiateCallInput:
    """
    Input for initiate_call activity (large.py).

    Activity: Initiates call with voice provider.

    For INBOUND calls (FutureAGI calls user's agent):
    - Creates simulator assistant with system_prompt
    - Calls user's phone (customer_phone_number) using system credentials

    For OUTBOUND calls (User's agent calls FutureAGI):
    - Creates simulator assistant with system_prompt
    - Assigns assistant to acquired phone (provider_phone_id)
    - Uses user's credentials to initiate call to acquired phone
    """

    call_id: str
    call_data: dict[str, Any]  # FAGICallData as dict
    system_prompt: str = ""
    voice_settings: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Provider
    provider: str = "livekit"

    # For outbound calls - acquired phone info
    phone_number: Optional[str] = None  # Acquired phone number string
    provider_phone_id: Optional[str] = None  # Provider-specific phone ID

    # For outbound calls - user credentials to initiate call
    user_api_key: Optional[str] = None
    user_assistant_id: Optional[str] = None
    user_phone_number: Optional[str] = None  # User's phone to call from

    # Customer's provider ("vapi"/"bland"/...) — the agent under test. Distinct
    # from `provider` above, which is the system simulator. Selects the data
    # plane for the SIP-outbound trigger.
    client_provider: str = "vapi"

    # WebRTC bridge connection type (None = SIP, "web_vapi", "web_retell")
    connection_type: Optional[str] = None


@dataclass
class InitiateCallOutput:
    """
    Output from initiate_call activity (large.py).

    Returns provider call ID and initial status.
    """

    success: bool
    provider_call_id: Optional[str] = None
    provider_data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class FetchTranscriptInput:
    """
    Input for fetch_call_transcript activity (large.py).

    Activity: Fetches transcript/messages from provider after call completion.
    """

    call_id: str
    provider_call_id: str
    call_type: str


@dataclass
class FetchTranscriptOutput:
    """
    Output from fetch_call_transcript activity (large.py).

    Activity saves transcript directly to DB to avoid large payloads.
    Returns only message count for workflow tracking.
    """

    success: bool
    message_count: int = 0
    error: Optional[str] = None


@dataclass
class PersistResultInput:
    """
    Input for persist_call_result activity (large.py).

    Activity: Fetches full call data from provider and saves all results to CallExecution.
    Handles: status, duration, recordings, costs, provider data.

    Note: Transcript is saved by fetch_and_presist_call_result activity directly to DB
    to avoid large payloads in workflow.

    DEPRECATED: Use FetchAndPersistCallResultInput instead.
    """

    call_id: str
    status: str
    provider_call_id: str  # Provider's call ID to fetch full data
    provider: (
        str  # Provider name — use str to avoid Temporal str,Enum deserialization bug
    )
    duration_seconds: Optional[float] = None
    end_reason: Optional[str] = None


@dataclass
class FetchAndPersistCallResultInput:
    """
    Input for fetch_and_persist_call_result activity (large.py).

    Combined activity that fetches call data from provider once and:
    - Saves transcript to CallTranscript table
    - Saves all call result fields to CallExecution
    - Converts recordings to S3
    - Updates CreateCallExecution status

    to reduce duplicate API calls to the voice provider.

    Timeout: 5 minutes (with heartbeats)
    Queue: tasks_l
    """

    call_id: str
    status: str
    provider_call_id: str  # Provider's call ID to fetch full data
    provider: (
        str  # Provider name — use str to avoid Temporal str,Enum deserialization bug
    )
    call_type: str  # "inbound" or "outbound" — use str to avoid Temporal str,Enum deserialization bug
    # Customer's provider (agent under test); selects the SIP-outbound fetch path.
    client_provider: str = "vapi"
    duration_seconds: Optional[float] = None
    end_reason: Optional[str] = None
    provider_data: dict[str, Any] = field(default_factory=dict)
    provider_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchAndPersistCallResultOutput:
    """
    Output from fetch_and_persist_call_result activity.

    Returns transcript count for workflow tracking.
    """

    success: bool
    message_count: int = 0
    # Speaker presence flags for evaluation gating (silence-timeout cases)
    has_agent_message: bool = False
    has_customer_message: bool = False
    error: Optional[str] = None


@dataclass
class DeductCostInput:
    """
    Input for deduct_call_cost activity (large.py).

    Activity: Deducts actual call cost from organization wallet balance.
    Uses select_for_update to prevent race conditions.
    """

    call_id: str
    org_id: str
    cost: float


# =============================================================================
# Error Reporting Activities
# =============================================================================


@dataclass
class ReportErrorInput:
    """
    Input for report_workflow_error activity.

    Activity: Reports workflow exceptions via structlog.
    Used by workflows to send errors since workflow.logger
    doesn't have error tracking integration.
    """

    workflow_name: str
    workflow_id: str
    error_message: str
    error_type: str = "WorkflowError"
    context: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Conversation Metrics Activities
# =============================================================================


@dataclass
class CalculateConversationMetricsInput:
    """
    Input for calculate_conversation_metrics activity (large.py).

    Activity: Calculates conversation metrics using ConversationMetricsCalculator.
    Works for both inbound and outbound calls - the is_outbound flag controls
    role normalization in the calculator.

    For inbound calls (is_outbound=False):
    - System side: role="bot" (simulator) becomes role="user" (simulator)
    - System side: role="user" (test agent) becomes role="bot" (test agent)

    For outbound calls (is_outbound=True):
    - Roles remain as-is from the provider data

    Timeout: 2 minutes
    Queue: tasks_l
    """

    call_id: str
    is_outbound: bool = False
    provider: str = "livekit"


# =============================================================================
# CSAT Evaluation Activities
# =============================================================================


@dataclass
class CalculateVoiceCSATInput:
    """
    Input for calculate_voice_csat_score activity (xl.py).

    Activity: Uses DeterministicEvaluator with call recording to calculate
    CSAT score (1-10). Resource-intensive LLM evaluation on audio.

    Timeout: 10 minutes
    Queue: tasks_xl
    """

    call_id: str


@dataclass
class CalculateVoiceCSATOutput:
    """
    Output from calculate_voice_csat_score activity.

    Returns the calculated CSAT score or None if evaluation failed/skipped.
    """

    success: bool
    csat_score: Optional[float] = None
    error: Optional[str] = None
    skipped: bool = False  # True if skipped (e.g., no recording or score already set)


@dataclass
class RunSimulateEvaluationsInput:
    """
    Input for run_simulate_evaluations activity (xl.py).

    Activity: Runs all configured evaluations for a call execution using
    the existing TestExecutor._run_simulate_evaluations method.

    Timeout: 30 minutes (with heartbeats)
    Queue: tasks_xl
    """

    call_execution_id: str
    eval_config_ids: Optional[list[str]] = None  # If None, runs all configs
    skip_existing: bool = False  # If True, skip evaluations that already exist


@dataclass
class RunSimulateEvaluationsOutput:
    """
    Output from run_simulate_evaluations activity.
    """

    success: bool
    error: Optional[str] = None


# =============================================================================
# Tool Call Evaluation Activity
# =============================================================================


@dataclass
class RunToolCallEvaluationInput:
    """
    Input for run_tool_call_evaluation activity (xl.py).

    Activity: Runs tool evaluation for a call execution using ToolEvalAgent.
    Checks enable_tool_evaluation flag internally and skips if disabled.

    Timeout: 30 minutes (with heartbeats)
    Queue: tasks_xl
    """

    call_execution_id: str


@dataclass
class RunToolCallEvaluationOutput:
    """
    Output from run_tool_call_evaluation activity.
    """

    success: bool
    error: Optional[str] = None


# =============================================================================
# Phone Number Dispatcher Activities
# =============================================================================


@dataclass
class RequestPhoneNumberInput:
    """Input for request_phone_number activity (signals phone number dispatcher)."""

    workflow_id: str
    call_id: str
    org_id: str
    call_direction: str = "outbound"


@dataclass
class ReleasePhoneNumberSlotInput:
    """Input for release_phone_number_slot activity.

    Signals the phone number dispatcher to release the slot. The dispatcher
    owns all DB operations — it will release the phone back to the DB pool
    if one was acquired for this call_id.
    """

    call_id: str


@dataclass
class AcquireAndSignalPhoneNumbersBatchInput:
    """
    Input for acquire_and_signal_phone_numbers_batch activity.

    Each grant: {"workflow_id": "...", "call_id": "...", "org_id": "...", "call_direction": "..."}
    """

    grants: list[dict[str, str]] = field(default_factory=list)


@dataclass
class AcquireAndSignalPhoneNumbersBatchOutput:
    """
    Output from acquire_and_signal_phone_numbers_batch activity.

    Returns counts of successful and failed phone number acquisitions,
    plus phone_id mapping for successful grants so the dispatcher can
    track which DB phone is held by which call.
    """

    success_count: int = 0
    failed_count: int = 0
    failed_call_ids: list[str] = field(default_factory=list)
    # call_id -> phone_id mapping for successful grants
    successful_grants: dict[str, str] = field(default_factory=dict)


@dataclass
class RunBridgeInput:
    """Input for the WebRTC bridge activity."""

    call_id: str
    room_name: str
    connection_type: str  # "web_vapi", "web_retell", "web_livekit_bridge"
    customer_api_key: str
    customer_assistant_id: str
    max_duration_seconds: int = 2100
    # LiveKit bridge-specific fields
    customer_livekit_url: str = ""
    customer_livekit_api_key: str = ""
    customer_livekit_api_secret: str = ""


@dataclass
class RunBridgeOutput:
    """Output from the WebRTC bridge activity."""

    success: bool
    room_name: str = ""
    bridge_latency_ms: float = 0.0
