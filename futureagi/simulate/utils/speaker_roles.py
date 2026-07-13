"""Provider- and direction-aware transcript speaker-role resolution.

Provider raw shape:

  Provider | Direction | assistant / bot | user
  ---------|-----------|-----------------|-----------------
  VAPI     | inbound   | simulator       | tested agent
  VAPI     | outbound  | tested agent    | simulator
  LiveKit  | both      | tested agent    | simulator

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

    READ-SIDE ONLY. Translates raw provider role labels stored in the DB
    into the platform-perspective convention that the FE, LLM eval inputs,
    and downstream analytics expect. Do NOT call from write paths:
    transcript rows, recording URLs and ended_reason are all persisted in
    their raw provider shape. Using the resolver at write time
    re-introduces the coincidental-no-op-that-flips-when-the-map-is-fixed
    bug that motivated this class.

    The single legitimate compute-time use is derived-metric normalization
    (bot_wpm / user_wpm / talk_ratio / interruption counts) whose column
    contract binds bot_* to the tested agent. That is a derivation, not a
    raw-storage decision.
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
        """True when the tested agent initiated the call.

        Defaults to True on indeterminate direction. For VAPI, True selects the
        outbound map (no swap); the raw outbound shape already matches the
        platform convention, so a records missing both call_direction and a
        parseable call_type still reads correctly. A False default would
        silently invert outbound records that lost their metadata.
        """
        if call_execution is None:
            return True
        call_metadata = getattr(call_execution, "call_metadata", None) or {}
        call_direction = str(call_metadata.get("call_direction", "")).strip().lower()
        if call_direction == "outbound":
            return True
        if call_direction == "inbound":
            return False
        call_type = str(getattr(call_execution, "call_type", "") or "").strip().lower()
        if "outbound" in call_type:
            return True
        if "inbound" in call_type:
            return False
        return True

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
    def align_transcript_rows(
        cls,
        rows: list[dict[str, Any]],
        *,
        provider: ProviderChoices,
        is_outbound: bool = False,
    ) -> list[dict[str, Any]]:
        """Rewrite each row's speaker_role to platform convention in place."""
        for row in rows:
            raw = row.get("speaker_role")
            if cls.is_tested_agent(raw, provider=provider, is_outbound=is_outbound):
                row["speaker_role"] = "assistant"
            elif cls.is_simulator(raw, provider=provider, is_outbound=is_outbound):
                row["speaker_role"] = "user"
        return rows

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
