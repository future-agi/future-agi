"""Provider- and direction-aware transcript speaker-role resolution.

DB shape (verified against staging):

  Provider | Direction | assistant / bot | user
  ---------|-----------|-----------------|-----------------
  VAPI     | inbound   | FAGI simulator  | tested agent
  VAPI     | outbound  | tested agent    | FAGI simulator
  LiveKit  | both      | tested agent    | FAGI simulator

Direction is tested-agent-perspective: inbound = tested agent receives, outbound
= tested agent dials out. LiveKit rows are pre-normalised at the agent worker.
"""

from __future__ import annotations

from typing import Any

import structlog

from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)


class SpeakerRoleResolver:
    """Interprets CallTranscript.speaker_role by provider + call direction.

    READ-SIDE ONLY. This class translates raw provider role labels stored
    in the DB into the platform-perspective convention that the FE, LLM
    eval inputs, and downstream analytics expect. Do NOT call it from
    write paths: transcript rows, recording URLs and ended_reason are
    all persisted in their raw provider shape (with the single documented
    exception of ended_reason for VAPI inbound, handled inline by
    voice_large.py). Using the resolver at write time re-introduces the
    "coincidental no-op that flips the moment the map is corrected" bug
    that motivated this class in the first place.

    The only compute-time use that IS legitimate lives in
    ee/voice/services/conversation_metrics._normalize_roles_for_test_agent,
    which computes derived metric columns (bot_wpm, user_wpm, talk_ratio,
    interruption counts) whose field-name contract commits bot_* to the
    tested agent. That is a derivation, not a raw-storage decision.
    """

    _VAPI_INBOUND: dict[str, str] = {
        "bot": "simulator",
        "assistant": "simulator",
        "agent": "simulator",
        "user": "tested_agent",
        "customer": "tested_agent",
    }

    _VAPI_OUTBOUND: dict[str, str] = {
        "bot": "tested_agent",
        "assistant": "tested_agent",
        "agent": "tested_agent",
        "user": "simulator",
        "customer": "simulator",
    }

    _LIVEKIT_INBOUND: dict[str, str] = {
        "bot": "tested_agent",
        "assistant": "tested_agent",
        "agent": "tested_agent",
        "user": "simulator",
        "customer": "simulator",
    }
    _LIVEKIT_OUTBOUND: dict[str, str] = _LIVEKIT_INBOUND

    # VAPI end reasons use raw provider words; swap only for VAPI inbound
    # so the reviewer sees the same actor as in the transcript.
    _VAPI_END_REASON_ROLE_WORDS = ("assistant", "customer")

    @staticmethod
    def detect_provider(
        provider_call_data: dict[str, Any] | None,
    ) -> ProviderChoices:
        """Detect provider from CallExecution.provider_call_data; default VAPI."""
        if not isinstance(provider_call_data, dict):
            if provider_call_data is not None:
                logger.error(
                    "speaker_role_resolver_invalid_provider_call_data",
                    provider_call_data_type=type(provider_call_data).__name__,
                )
            return ProviderChoices.VAPI
        if provider_call_data.get(ProviderChoices.LIVEKIT.value):
            return ProviderChoices.LIVEKIT
        if provider_call_data.get(ProviderChoices.VAPI.value):
            return ProviderChoices.VAPI
        logger.error(
            "speaker_role_resolver_unknown_provider",
            provider_call_data_keys=list(provider_call_data.keys()),
        )
        return ProviderChoices.VAPI

    @staticmethod
    def detect_is_outbound(call_execution: Any) -> bool:
        """True when the tested agent initiated the call."""
        if call_execution is None:
            return False
        call_metadata = getattr(call_execution, "call_metadata", None) or {}
        call_direction = str(call_metadata.get("call_direction", "")).strip().lower()
        if call_direction == "outbound":
            return True
        if call_direction == "inbound":
            return False
        call_type = str(getattr(call_execution, "call_type", "") or "").strip().lower()
        return "outbound" in call_type

    @classmethod
    def _get_map(
        cls,
        *,
        provider: ProviderChoices,
        is_outbound: bool = False,
    ) -> dict[str, str]:
        if provider == ProviderChoices.VAPI:
            return cls._VAPI_OUTBOUND if is_outbound else cls._VAPI_INBOUND
        if provider == ProviderChoices.LIVEKIT:
            return cls._LIVEKIT_OUTBOUND if is_outbound else cls._LIVEKIT_INBOUND
        logger.error(
            "speaker_role_resolver_unsupported_provider",
            provider=str(provider),
            is_outbound=is_outbound,
        )
        return cls._VAPI_INBOUND

    @classmethod
    def is_tested_agent(
        cls,
        role: str,
        *,
        provider: ProviderChoices,
        is_outbound: bool = False,
    ) -> bool:
        role_map = cls._get_map(provider=provider, is_outbound=is_outbound)
        return role_map.get((role or "").lower()) == "tested_agent"

    @classmethod
    def is_simulator(
        cls,
        role: str,
        *,
        provider: ProviderChoices,
        is_outbound: bool = False,
    ) -> bool:
        role_map = cls._get_map(provider=provider, is_outbound=is_outbound)
        return role_map.get((role or "").lower()) == "simulator"

    @classmethod
    def get_eval_role_label(
        cls,
        role: str,
        *,
        provider: ProviderChoices,
        is_outbound: bool = False,
    ) -> str:
        """Return "agent" | "customer" | passthrough for LLM eval transcripts."""
        role_map = cls._get_map(provider=provider, is_outbound=is_outbound)
        classification = role_map.get((role or "").lower())
        if classification == "tested_agent":
            return "agent"
        if classification == "simulator":
            return "customer"
        return role

    @classmethod
    def get_transcript_role_sets(
        cls,
        *,
        provider: ProviderChoices,
        is_outbound: bool = False,
    ) -> tuple[frozenset[str], frozenset[str]]:
        """(tested_agent_roles, simulator_roles) as raw DB speaker_role strings."""
        role_map = cls._get_map(provider=provider, is_outbound=is_outbound)
        ta = frozenset(k for k, v in role_map.items() if v == "tested_agent")
        sim = frozenset(k for k, v in role_map.items() if v == "simulator")
        return ta, sim

    @classmethod
    def get_skip_decision_role_sets(
        cls,
        *,
        provider: ProviderChoices,
        is_outbound: bool = False,
    ) -> tuple[frozenset[str], frozenset[str]]:
        return cls.get_transcript_role_sets(provider=provider, is_outbound=is_outbound)

    @staticmethod
    def get_conversational_roles() -> list[str]:
        """User + assistant turns only; excludes system, tool_calls, unknown."""
        from simulate.models.test_execution import CallTranscript

        return [
            CallTranscript.SpeakerRole.USER,
            CallTranscript.SpeakerRole.ASSISTANT,
        ]

    @staticmethod
    def get_displayable_roles() -> list[str]:
        """Same as conversational; system prompt is hidden from the transcript view."""
        from simulate.models.test_execution import CallTranscript

        return [
            CallTranscript.SpeakerRole.USER,
            CallTranscript.SpeakerRole.ASSISTANT,
        ]

    @classmethod
    def normalize_end_reason(
        cls,
        ended_reason: str,
        *,
        provider: ProviderChoices,
        is_outbound: bool = False,
    ) -> str:
        """Rewrite ended_reason to the reviewer's perspective (agent/customer)."""
        if not ended_reason:
            return ended_reason
        if provider != ProviderChoices.VAPI or is_outbound:
            return ended_reason
        word_a, word_b = cls._VAPI_END_REASON_ROLE_WORDS
        swapped = ended_reason.replace(word_a, "__PH__")
        swapped = swapped.replace(word_b, word_a)
        return swapped.replace("__PH__", word_b)
