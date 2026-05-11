"""Transcript speaker-role resolver with an OSS-safe fallback."""

from __future__ import annotations

from typing import Any

import structlog

from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)

try:
    from ee.voice.utils.transcript_roles import SpeakerRoleResolver  # noqa: F401
except ImportError:

    class SpeakerRoleResolver:
        """Minimal local resolver used when Enterprise voice modules are absent.

        The maps mirror the universal DB convention used by voice simulations:
        assistant/agent/bot are the tested agent, and user/customer are the
        simulator/customer side.
        """

        _ROLE_MAP: dict[str, str] = {
            "bot": "tested_agent",
            "assistant": "tested_agent",
            "agent": "tested_agent",
            "user": "simulator",
            "customer": "simulator",
        }

        @staticmethod
        def detect_provider(provider_call_data: dict[str, Any] | None) -> ProviderChoices:
            if isinstance(provider_call_data, dict):
                if provider_call_data.get(ProviderChoices.LIVEKIT.value):
                    return ProviderChoices.LIVEKIT
                if provider_call_data.get(ProviderChoices.VAPI.value):
                    return ProviderChoices.VAPI
            return ProviderChoices.VAPI

        @classmethod
        def is_tested_agent(
            cls,
            role: str,
            *,
            provider: ProviderChoices,
            is_outbound: bool = False,
        ) -> bool:
            return cls._ROLE_MAP.get((role or "").lower()) == "tested_agent"

        @classmethod
        def is_simulator(
            cls,
            role: str,
            *,
            provider: ProviderChoices,
            is_outbound: bool = False,
        ) -> bool:
            return cls._ROLE_MAP.get((role or "").lower()) == "simulator"

        @classmethod
        def get_eval_role_label(
            cls,
            role: str,
            *,
            provider: ProviderChoices,
            is_outbound: bool = False,
        ) -> str:
            classification = cls._ROLE_MAP.get((role or "").lower())
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
            tested_agent = frozenset(
                role
                for role, classification in cls._ROLE_MAP.items()
                if classification == "tested_agent"
            )
            simulator = frozenset(
                role
                for role, classification in cls._ROLE_MAP.items()
                if classification == "simulator"
            )
            return tested_agent, simulator

        @classmethod
        def get_skip_decision_role_sets(
            cls,
            *,
            provider: ProviderChoices,
            is_outbound: bool = False,
        ) -> tuple[frozenset[str], frozenset[str]]:
            return cls.get_transcript_role_sets(
                provider=provider,
                is_outbound=is_outbound,
            )

        @staticmethod
        def get_conversational_roles() -> list[str]:
            from simulate.models.test_execution import CallTranscript

            return [
                CallTranscript.SpeakerRole.USER,
                CallTranscript.SpeakerRole.ASSISTANT,
            ]

        @staticmethod
        def get_displayable_roles() -> list[str]:
            from simulate.models.test_execution import CallTranscript

            return [
                CallTranscript.SpeakerRole.USER,
                CallTranscript.SpeakerRole.ASSISTANT,
                CallTranscript.SpeakerRole.SYSTEM,
            ]

        @classmethod
        def normalize_end_reason(
            cls,
            ended_reason: str,
            *,
            provider: ProviderChoices,
            is_outbound: bool = False,
        ) -> str:
            return ended_reason
