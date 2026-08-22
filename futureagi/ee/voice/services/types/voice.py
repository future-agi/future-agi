"""Pydantic input/output models for the VoiceServiceBlueprint engine contract.

These types define the structured interface between VoiceServiceManager
(thin router) and the engine implementations (VapiService, LivekitService, etc.).
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Call Retrieval
# ---------------------------------------------------------------------------


class GetCallInput(BaseModel):
    """Input for fetching and normalizing a call from the provider."""

    call_id: str
    call_data_stored: bool = False


# ---------------------------------------------------------------------------
# Inbound Call (we call user's agent)
# ---------------------------------------------------------------------------


class InboundCallInput(BaseModel):
    """Input for initiating an inbound call.

    Creates simulator assistant internally — no separate create_assistant step needed.
    """

    call_id: str = Field(..., description="CallExecution ID (used for naming)")
    user_phone_number: Optional[str] = None
    system_prompt: str
    voice_settings: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    connection_type: Optional[str] = (
        None  # None/"sip" = phone, "web_vapi", "web_retell"
    )


class CallResult(BaseModel):
    """Result of initiating a call (inbound or outbound trigger)."""

    success: bool
    provider_call_id: Optional[str] = None
    assistant_id: Optional[str] = None
    provider_data: Optional[dict[str, Any]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Outbound Call (user's agent calls us)
# ---------------------------------------------------------------------------


class OutboundCallInput(BaseModel):
    """Input for outbound call setup.

    Phone is already acquired by the workflow. Each provider handles
    its own system-side setup (VAPI: create assistant + assign to phone,
    LiveKit: store call metadata for agent worker).
    """

    call_execution_id: str = Field(..., description="UUID of CallExecution")
    system_prompt: str
    voice_settings: Optional[dict[str, Any]] = None
    phone_number: Optional[str] = None  # Acquired system phone (E.164)
    provider_phone_id: Optional[str] = None  # Provider-specific phone ID
    metadata: Optional[dict[str, Any]] = (
        None  # Workflow metadata (includes workflow_id)
    )


class OutboundCallResult(BaseModel):
    """Result of outbound call setup."""

    success: bool
    assistant_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    phone_number: Optional[str] = None
    provider_data: Optional[dict[str, Any]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# End Call
# ---------------------------------------------------------------------------


class EndCallInput(BaseModel):
    """Input for terminating an active call."""

    provider_call_payload: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Client Call Matching
# ---------------------------------------------------------------------------


class FindClientCallInput(BaseModel):
    """Input for finding a matching call in the customer's provider account."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    customer_api_key: str
    customer_assistant_id: str
    our_call_data: Any = Field(..., description="FAGICallData instance")
    customer_voice_service_provider: Optional[str] = None
    time_window_seconds: int = 10


# ---------------------------------------------------------------------------
# Customer Metrics
# ---------------------------------------------------------------------------


class CustomerMetrics(BaseModel):
    """Normalized customer call metrics and cost breakdown."""

    system_metrics: Optional[dict[str, float]] = None
    cost_breakdown: dict[str, dict[str, Any]] = Field(default_factory=dict)
    total_cost: float = 0.0


# ---------------------------------------------------------------------------
# Audio Persistence
# ---------------------------------------------------------------------------


class PersistAudioInput(BaseModel):
    """Input for downloading provider audio and re-uploading to S3."""

    call_id: str
    audio_url: str
    url_type: str = "audio"


# ---------------------------------------------------------------------------
# Provider-Agnostic Transcript + Usage Data
# ---------------------------------------------------------------------------


@dataclass
class TranscriptMessage:
    """Single message in a conversation, provider-agnostic.

    Roles:
      - "bot" / "user": regular conversation messages
      - "tool_call": content = tool name, tool_call_id set, arguments set
      - "tool_call_result": content = result text/JSON, tool_call_id links back
    """

    role: str  # "bot", "user", "tool_call", "tool_call_result"
    content: str  # for tool_call: tool name, for tool_call_result: result text
    time: float  # start time in seconds from call start
    end_time: float | None = None  # end time in seconds
    duration: float | None = None  # duration in seconds
    # Tool call fields (populated when role is tool_call or tool_call_result)
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None


@dataclass
class NormalizedTranscriptData:
    """Provider-agnostic transcript + usage data for metrics calculation.

    Used by ConversationMetricsCalculator and calculate_conversation_metrics
    activity. Each engine builds this from its own data source:
    - VAPI: from raw_log["messages"] (API response)
    - LiveKit: from CallTranscript DB records (written by agent worker)
    """

    messages: list[TranscriptMessage] = field(default_factory=list)
    token_usage: dict[str, Any] = field(default_factory=dict)
    # token_usage format: {"llm": {"prompt_tokens": N, "completion_tokens": M}, ...}


@dataclass
class CostBreakdown:
    """Provider-agnostic cost breakdown for a call.

    All values in dollars (not cents). The activity converts to cents
    when storing to CallExecution model fields.
    """

    total: float = 0.0
    stt: float = 0.0
    llm: float = 0.0
    tts: float = 0.0
    transport: float = 0.0  # SIP/telephony costs (VAPI "vapi" cost, LiveKit SIP)
    storage: float = 0.0  # S3 recording storage


@dataclass
class RecordingUrls:
    """Provider-agnostic recording URLs persisted to S3.

    Fields match RecordingTypes in simulate/semantics.py:
    {"stereo", "assistant", "customer", "combined"}

    - recording_url / stereo_recording_url map to CallExecution model fields.
    - assistant_recording_url / customer_recording_url are stored in
      provider_call_data (no dedicated model fields).
    """

    recording_url: str | None = None  # combined/main recording
    stereo_recording_url: str | None = None
    assistant_recording_url: str | None = None
    customer_recording_url: str | None = None
    provider_call_data: dict[str, Any] | None = None
