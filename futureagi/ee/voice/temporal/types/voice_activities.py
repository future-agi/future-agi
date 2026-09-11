"""
Voice-specific activity data classes extracted from simulate activities.

These types define the contracts for voice-related activities used in call
execution workflows: phone management, call initiation, monitoring,
transcription, cost handling, evaluation, and WebRTC bridging.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from simulate.semantics import CallType

# =============================================================================
# Phone Number Activities
# =============================================================================


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


# =============================================================================
# Background Sound Activities
# =============================================================================


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


# =============================================================================
# Transcript & Result Persistence Activities
# =============================================================================


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


# =============================================================================
# Cost Activities
# =============================================================================


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
# Call Initiation Activities
# =============================================================================


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


# =============================================================================
# Client Call Activities
# =============================================================================


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


# =============================================================================
# WebRTC Bridge Activities
# =============================================================================


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
