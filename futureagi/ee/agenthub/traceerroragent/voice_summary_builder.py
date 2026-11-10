"""
VoiceSummaryBuilder — extracts structured data from a voice call's
ObservationSpan.eval_attributes for use by the Voice Compass pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from tracer.utils.otel import (
    CallAttributes,
    ConversationAttributes,
    MessageAttributes,
    PerformanceMetrics,
    SpanAttributes,
    TurnLatencyAttributes,
    WorkflowAttributes,
)

logger = structlog.get_logger(__name__)


@dataclass
class TurnLatency:
    turn_index: int
    model_latency: Optional[float] = None
    voice_latency: Optional[float] = None
    transcriber_latency: Optional[float] = None
    endpointing_latency: Optional[float] = None
    turn_latency: Optional[float] = None


@dataclass
class TranscriptTurn:
    index: int
    role: str
    content: str
    start_time: Optional[float] = None
    duration: Optional[float] = None


@dataclass
class VoiceCallData:
    """Structured representation of a voice call extracted from eval_attributes."""

    # Transcript
    transcript: list[TranscriptTurn] = field(default_factory=list)
    provider_transcript: list[dict] = field(default_factory=list)

    # Recording URLs
    recording_mono_combined: Optional[str] = None
    recording_stereo: Optional[str] = None
    recording_mono_customer: Optional[str] = None
    recording_mono_assistant: Optional[str] = None

    # Turn latencies
    turn_latencies: list[TurnLatency] = field(default_factory=list)

    # Conversation metrics
    avg_agent_latency_ms: Optional[float] = None
    user_interruption_count: Optional[int] = None
    user_interruption_rate: Optional[float] = None
    ai_interruption_count: Optional[int] = None
    ai_interruption_rate: Optional[float] = None
    user_wpm: Optional[float] = None
    bot_wpm: Optional[float] = None
    talk_ratio: Optional[float] = None
    avg_stop_time_after_interruption_ms: Optional[float] = None

    # LLM info
    llm_model_name: Optional[str] = None
    llm_provider: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    # Call metadata
    ended_reason: Optional[str] = None
    workflow_name: Optional[str] = None
    workflow_voicemail_detection: Optional[str] = None
    call_duration_seconds: Optional[int] = None

    # Flags
    has_tool_calls: bool = False
    has_audio: bool = False

    # Span Details
    span_id: Optional[str] = None

class VoiceSummaryBuilder:
    """Extracts and formats voice call data from an ObservationSpan."""

    @staticmethod
    def extract(span) -> VoiceCallData:
        """
        Extract structured VoiceCallData from an ObservationSpan.

        Args:
            span: ObservationSpan with observation_type="conversation"

        Returns:
            VoiceCallData with all available fields populated.
        """
        attrs = span.eval_attributes or {}
        data = VoiceCallData()

        # --- Transcript ---
        data.provider_transcript = attrs.get("provider_transcript", [])
        data.transcript = _extract_transcript_turns(attrs)

        # --- Recording URLs ---
        rec_prefix = ConversationAttributes.CONVERSATION_RECORDING
        data.recording_mono_combined = attrs.get(
            f"{rec_prefix}.{ConversationAttributes.MONO_COMBINED}"
        )
        data.recording_stereo = attrs.get(
            f"{rec_prefix}.{ConversationAttributes.STEREO}"
        )
        data.recording_mono_customer = attrs.get(
            f"{rec_prefix}.{ConversationAttributes.MONO_CUSTOMER}"
        )
        data.recording_mono_assistant = attrs.get(
            f"{rec_prefix}.{ConversationAttributes.MONO_ASSISTANT}"
        )
        data.has_audio = bool(
            data.recording_mono_combined or data.recording_stereo
        )

        # --- Turn latencies ---
        data.turn_latencies = _extract_turn_latencies(attrs)

        # --- Conversation metrics ---
        data.avg_agent_latency_ms = _safe_float(attrs.get("avg_agent_latency_ms"))
        data.user_interruption_count = _safe_int(attrs.get("user_interruption_count"))
        data.user_interruption_rate = _safe_float(attrs.get("user_interruption_rate"))
        data.ai_interruption_count = _safe_int(attrs.get("ai_interruption_count"))
        data.ai_interruption_rate = _safe_float(attrs.get("ai_interruption_rate"))
        data.user_wpm = _safe_float(attrs.get("user_wpm"))
        data.bot_wpm = _safe_float(attrs.get("bot_wpm"))
        data.talk_ratio = _safe_float(attrs.get("talk_ratio"))
        data.avg_stop_time_after_interruption_ms = _safe_float(
            attrs.get("avg_stop_time_after_interruption_ms")
        )

        # --- LLM info ---
        data.llm_model_name = attrs.get(SpanAttributes.LLM_MODEL_NAME)
        data.llm_provider = attrs.get(SpanAttributes.LLM_PROVIDER)
        data.prompt_tokens = _safe_int(attrs.get(SpanAttributes.LLM_TOKEN_COUNT_PROMPT))
        data.completion_tokens = _safe_int(
            attrs.get(SpanAttributes.LLM_TOKEN_COUNT_COMPLETION)
        )
        data.total_tokens = _safe_int(attrs.get(SpanAttributes.LLM_TOKEN_COUNT_TOTAL))

        # --- Call metadata ---
        data.ended_reason = attrs.get("ended_reason")
        data.workflow_name = attrs.get(WorkflowAttributes.WORKFLOW_NAME)
        data.workflow_voicemail_detection = attrs.get(
            WorkflowAttributes.WORKFLOW_VOICEMAIL_DETECTION
        )

        # Call duration — prefer the stored call.duration metric (int seconds),
        # fall back to span timestamps if not available.
        stored_duration = attrs.get(CallAttributes.DURATION)
        if stored_duration is not None:
            data.call_duration_seconds = int(stored_duration)
        elif span.start_time and span.end_time:
            delta = span.end_time - span.start_time
            data.call_duration_seconds = int(delta.total_seconds())

        # --- Tool call detection ---
        data.has_tool_calls = _detect_tool_calls(data.provider_transcript)

        data.span_id = str(span.id)

        return data

    @staticmethod
    def format_transcript_for_prompt(data: VoiceCallData) -> str:
        """Format the transcript with turn numbers for inclusion in prompts."""
        if data.transcript:
            lines = []
            for turn in data.transcript:
                timing = ""
                if turn.start_time is not None:
                    timing = f" [{turn.start_time:.1f}s"
                    if turn.duration is not None:
                        timing += f", {turn.duration:.1f}s dur"
                    timing += "]"
                lines.append(
                    f"Turn {turn.index}: [{turn.role.upper()}]{timing} {turn.content}"
                )
            return "\n".join(lines)

        # Fallback to provider_transcript (no timing info)
        if data.provider_transcript:
            lines = []
            for i, msg in enumerate(data.provider_transcript):
                role = msg.get("role", "unknown").upper()
                content = msg.get("content", "")
                lines.append(f"Turn {i}: [{role}] {content}")
            return "\n".join(lines)

        return "(No transcript available)"

    @staticmethod
    def format_metrics_for_prompt(data: VoiceCallData) -> str:
        """Format conversation and performance metrics for inclusion in prompts."""
        sections = []

        # Conversation metrics
        conv_lines = []
        if data.avg_agent_latency_ms is not None:
            conv_lines.append(
                f"- Average agent latency: {data.avg_agent_latency_ms:.0f}ms"
            )
        if data.user_interruption_count is not None:
            conv_lines.append(
                f"- User interruptions: {data.user_interruption_count}"
                + (
                    f" ({data.user_interruption_rate:.1%} rate)"
                    if data.user_interruption_rate is not None
                    else ""
                )
            )
        if data.ai_interruption_count is not None:
            conv_lines.append(
                f"- AI interruptions: {data.ai_interruption_count}"
                + (
                    f" ({data.ai_interruption_rate:.1%} rate)"
                    if data.ai_interruption_rate is not None
                    else ""
                )
            )
        if data.avg_stop_time_after_interruption_ms is not None:
            conv_lines.append(
                f"- Avg stop time after interruption: {data.avg_stop_time_after_interruption_ms:.0f}ms"
            )
        if data.user_wpm is not None:
            conv_lines.append(f"- User speaking rate: {data.user_wpm:.0f} WPM")
        if data.bot_wpm is not None:
            conv_lines.append(f"- Agent speaking rate: {data.bot_wpm:.0f} WPM")
        if data.talk_ratio is not None:
            conv_lines.append(f"- Talk ratio (agent/user): {data.talk_ratio:.2f}")

        if conv_lines:
            sections.append("### Conversation Metrics\n" + "\n".join(conv_lines))

        # Per-turn latencies
        if data.turn_latencies:
            latency_lines = []
            for tl in data.turn_latencies:
                parts = [f"Turn {tl.turn_index}:"]
                if tl.turn_latency is not None:
                    parts.append(f"total={tl.turn_latency:.0f}ms")
                if tl.model_latency is not None:
                    parts.append(f"model={tl.model_latency:.0f}ms")
                if tl.voice_latency is not None:
                    parts.append(f"voice={tl.voice_latency:.0f}ms")
                if tl.transcriber_latency is not None:
                    parts.append(f"transcriber={tl.transcriber_latency:.0f}ms")
                if tl.endpointing_latency is not None:
                    parts.append(f"endpointing={tl.endpointing_latency:.0f}ms")
                latency_lines.append("  " + " | ".join(parts))
            sections.append(
                "### Per-Turn Latencies\n" + "\n".join(latency_lines)
            )

        if not sections:
            return "(No metrics available)"

        return "\n\n".join(sections)

    @staticmethod
    def format_call_metadata_for_prompt(data: VoiceCallData) -> str:
        """Format call metadata for inclusion in prompts."""
        lines = []
        if data.call_duration_seconds is not None:
            mins = int(data.call_duration_seconds // 60)
            secs = int(data.call_duration_seconds % 60)
            lines.append(f"- Duration: {mins}m {secs}s")
        if data.ended_reason:
            lines.append(f"- Ended reason: {data.ended_reason}")
        if data.workflow_name:
            lines.append(f"- Workflow: {data.workflow_name}")
        if data.workflow_voicemail_detection:
            lines.append(
                f"- Voicemail detection: {data.workflow_voicemail_detection}"
            )
        if data.llm_model_name:
            lines.append(f"- LLM model: {data.llm_model_name}")
        if data.llm_provider:
            lines.append(f"- LLM provider: {data.llm_provider}")
        if data.total_tokens is not None:
            lines.append(f"- Total tokens: {data.total_tokens}")
        lines.append(f"- Has audio recording: {'Yes' if data.has_audio else 'No'}")
        lines.append(
            f"- Has tool calls: {'Yes' if data.has_tool_calls else 'No'}"
        )
        lines.append(f"- Transcript turns: {len(data.transcript) or len(data.provider_transcript)}")

        return "\n".join(lines) if lines else "(No metadata available)"

    @staticmethod
    def get_best_audio_url(data: VoiceCallData) -> Optional[str]:
        """Return the best available audio URL, preferring mono combined."""
        return (
            data.recording_mono_combined
            or data.recording_stereo
            or data.recording_mono_customer
            or data.recording_mono_assistant
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_transcript_turns(attrs: dict[str, Any]) -> list[TranscriptTurn]:
    """Extract structured transcript turns from flattened eval_attributes."""
    turns: list[TranscriptTurn] = []
    prefix = ConversationAttributes.CONVERSATION_TRANSCRIPT
    idx = 0

    while True:
        role_key = f"{prefix}.{idx}.{MessageAttributes.MESSAGE_ROLE}"
        content_key = f"{prefix}.{idx}.{MessageAttributes.MESSAGE_CONTENT}"

        role = attrs.get(role_key)
        content = attrs.get(content_key)

        if role is None and content is None:
            break

        start_time_key = f"{prefix}.{idx}.{MessageAttributes.MESSAGE_START_TIME}"
        duration_key = f"{prefix}.{idx}.{MessageAttributes.MESSAGE_DURATION}"

        turns.append(
            TranscriptTurn(
                index=idx,
                role=role or "unknown",
                content=content or "",
                start_time=_safe_float(attrs.get(start_time_key)),
                duration=_safe_float(attrs.get(duration_key)),
            )
        )
        idx += 1

    return turns


def _extract_turn_latencies(attrs: dict[str, Any]) -> list[TurnLatency]:
    """Extract per-turn latency data from flattened eval_attributes."""
    latencies: list[TurnLatency] = []
    prefix = PerformanceMetrics.TURN_LATENCIES
    idx = 0

    while True:
        # Check if this turn index exists
        model_key = f"{prefix}.{idx}.{TurnLatencyAttributes.MODEL_LATENCY}"
        turn_key = f"{prefix}.{idx}.{TurnLatencyAttributes.TURN_LATENCY}"

        model_val = attrs.get(model_key)
        turn_val = attrs.get(turn_key)

        if model_val is None and turn_val is None:
            break

        latencies.append(
            TurnLatency(
                turn_index=idx,
                model_latency=_safe_float(model_val),
                voice_latency=_safe_float(
                    attrs.get(f"{prefix}.{idx}.{TurnLatencyAttributes.VOICE_LATENCY}")
                ),
                transcriber_latency=_safe_float(
                    attrs.get(
                        f"{prefix}.{idx}.{TurnLatencyAttributes.TRANSCRIBER_LATENCY}"
                    )
                ),
                endpointing_latency=_safe_float(
                    attrs.get(
                        f"{prefix}.{idx}.{TurnLatencyAttributes.ENDPOINTING_LATENCY}"
                    )
                ),
                turn_latency=_safe_float(turn_val),
            )
        )
        idx += 1

    return latencies


def _detect_tool_calls(provider_transcript: list[dict]) -> bool:
    """Check if any transcript entry indicates tool calls were made."""
    for msg in provider_transcript:
        role = msg.get("role", "")
        if role in ("tool_calls", "tool_call_result", "function"):
            return True
    return False


def _safe_float(val: Any) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    """Safely convert a value to int, returning None on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
