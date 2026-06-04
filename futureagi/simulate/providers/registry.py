"""ProviderSpec — the single source of truth for simulation providers (TH-5642).

Design: internal-docs/multi-provider-simulation/DESIGN.md §4.

A "provider" is never one thing: it plays one or more of five *non-interchangeable*
roles. Today the same provider string must be registered independently in ~8
non-centralised places (ProviderChoices, SupportedProviders, ProviderCredentials,
serializer choices, the ``f"web_{provider}"`` interpolation, SpeakerRole maps, the
frontend list…), which is the source of the historical "silent Vapi" string-drift
bugs. This module declares each provider ONCE so those surfaces can derive from it.

This module is intentionally dependency-free (pure dataclasses/enums, no Django
imports) so it is importable in isolation and trivially testable. Call sites adopt
it incrementally; nothing is rewired by simply adding this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """A role a provider plays. Enum/component presence != tested-platform support."""

    AGENT_PLATFORM = "agent_platform"  # a customer agent we TEST (agent-under-test)
    SYSTEM_ENGINE = "system_engine"    # infra that runs OUR simulated caller
    TRANSPORT = "transport"            # how we reach the agent (SIP / WebRTC / PSTN)
    STT = "stt"                        # simulator-side speech-to-text component
    TTS = "tts"                        # simulator-side text-to-speech component
    CHAT_ENGINE = "chat_engine"        # text-simulation engine


class Transport(StrEnum):
    """How the simulator reaches the agent-under-test."""

    WEBRTC_BRIDGE = "webrtc_bridge"  # join via our LiveKit bridge connector (web_*)
    SIP = "sip"                      # dial via our LiveKit SIP trunk (provider-neutral)
    DIRECT_WS = "direct_ws"          # raw provider WebSocket, Vapi-style
    AGORA_RTC = "agora_rtc"          # Agora's proprietary RTC (NOT LiveKit-compatible)
    NONE = "none"


class CredentialShape(StrEnum):
    """The credential field-group a provider needs (drives the agent-def form)."""

    API_KEY_ASSISTANT = "api_key_assistant"  # api_key + assistant/agent_id
    LIVEKIT_SERVER = "livekit_server"        # url + api_key + api_secret + agent_name
    AGENT_ID = "agent_id"                    # api_key + agent_id (ElevenLabs/Deepgram)
    SIP_ONLY = "sip_only"                    # just a phone number
    WEBSOCKET_URL = "websocket_url"          # custom ws endpoint
    NONE = "none"


class Status(StrEnum):
    GA = "ga"                          # wired & working as a tested platform today
    PLANNED = "planned"                # designed (DESIGN.md §5), not yet built
    TRANSPORT_ONLY = "transport_only"  # never an agent platform (e.g. Twilio)
    INTERNAL = "internal"              # our own engine, not a tested platform


@dataclass(frozen=True)
class ProviderSpec:
    key: str                              # canonical client-provider string
    label: str
    roles: frozenset[Role]
    transport: Transport = Transport.NONE
    connector_key: str | None = None      # WebRTC bridge registry key (web_*), if any
    credential_shape: CredentialShape = CredentialShape.NONE
    chat: bool = False                    # has / will have a named chat engine
    observability_key: str | None = None  # maps to ProviderChoices value, if any
    status: Status = Status.PLANNED

    @property
    def is_agent_platform(self) -> bool:
        return Role.AGENT_PLATFORM in self.roles

    @property
    def is_ga(self) -> bool:
        return self.status in (Status.GA, Status.TRANSPORT_ONLY, Status.INTERNAL)


# Declarative registry — the ONLY place a provider is declared. See DESIGN.md §1/§5.
_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "vapi", "Vapi",
        roles=frozenset({Role.AGENT_PLATFORM, Role.SYSTEM_ENGINE, Role.CHAT_ENGINE}),
        transport=Transport.WEBRTC_BRIDGE, connector_key="web_vapi",
        credential_shape=CredentialShape.API_KEY_ASSISTANT, chat=True,
        observability_key="vapi", status=Status.GA,
    ),
    ProviderSpec(
        "retell", "Retell",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.WEBRTC_BRIDGE, connector_key="web_retell",
        credential_shape=CredentialShape.API_KEY_ASSISTANT,
        observability_key="retell", status=Status.GA,
    ),
    # The customer's own LiveKit agent (distinct from the SYSTEM 'livekit' engine).
    ProviderSpec(
        "livekit_bridge", "LiveKit (agent)",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.WEBRTC_BRIDGE, connector_key="web_livekit_bridge",
        credential_shape=CredentialShape.LIVEKIT_SERVER,
        observability_key="livekit", status=Status.GA,
    ),
    # Provider-neutral catch-all: custom agents reached by phone (SIP) or websocket.
    ProviderSpec(
        "others", "Others (custom / SIP)",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.SIP, credential_shape=CredentialShape.WEBSOCKET_URL,
        observability_key="others", status=Status.GA,
    ),
    # --- Planned tested platforms (DESIGN.md §5) ---
    ProviderSpec(
        "elevenlabs", "ElevenLabs Conversational AI",
        roles=frozenset({Role.AGENT_PLATFORM, Role.TTS}),
        transport=Transport.DIRECT_WS, connector_key="web_elevenlabs",
        credential_shape=CredentialShape.AGENT_ID, chat=True,
        observability_key="eleven_labs", status=Status.PLANNED,
    ),
    ProviderSpec(
        "deepgram", "Deepgram Voice Agent",
        roles=frozenset({Role.AGENT_PLATFORM, Role.STT}),
        transport=Transport.DIRECT_WS, connector_key="web_deepgram",
        credential_shape=CredentialShape.AGENT_ID, status=Status.PLANNED,
    ),
    ProviderSpec(
        "agora", "Agora Conversational AI",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.AGORA_RTC, connector_key="web_agora",
        credential_shape=CredentialShape.API_KEY_ASSISTANT, status=Status.PLANNED,
    ),
    # Pipecat-on-LiveKit reuses the existing LiveKit bridge connector (DESIGN.md §5.5).
    ProviderSpec(
        "pipecat", "Pipecat (LiveKit transport)",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.WEBRTC_BRIDGE, connector_key="web_livekit_bridge",
        credential_shape=CredentialShape.LIVEKIT_SERVER, status=Status.PLANNED,
    ),
    # Bland.ai agents are reached via the provider-neutral SIP/phone path (no
    # WebRTC connector needed) — DESIGN.md §3/§6.
    ProviderSpec(
        "bland", "Bland.ai",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.SIP, credential_shape=CredentialShape.API_KEY_ASSISTANT,
        status=Status.PLANNED,
    ),
    # --- Non-agent-platform roles ---
    ProviderSpec(
        "twilio", "Twilio",
        roles=frozenset({Role.TRANSPORT}),
        transport=Transport.SIP, credential_shape=CredentialShape.SIP_ONLY,
        status=Status.TRANSPORT_ONLY,
    ),
    ProviderSpec(
        "livekit", "LiveKit (system engine)",
        roles=frozenset({Role.SYSTEM_ENGINE, Role.TRANSPORT}),
        transport=Transport.SIP, observability_key="livekit", status=Status.INTERNAL,
    ),
    ProviderSpec(
        "futureagi", "FutureAGI (internal simulator)",
        roles=frozenset({Role.SYSTEM_ENGINE, Role.CHAT_ENGINE}),
        status=Status.INTERNAL,
    ),
)

PROVIDER_REGISTRY: dict[str, ProviderSpec] = {s.key: s for s in _SPECS}


def get_spec(key: str | None) -> ProviderSpec | None:
    """Return the ProviderSpec for a client-provider string, or None."""
    return PROVIDER_REGISTRY.get(key or "")


def connector_key_for(key: str | None) -> str | None:
    """The WebRTC-bridge connector key (web_*) for a provider, or None (→ SIP).

    Replaces the fragile ``f"web_{client_provider}"`` interpolation — a lookup,
    not string math, so a typo can't silently become an unknown connection_type.
    """
    spec = PROVIDER_REGISTRY.get(key or "")
    return spec.connector_key if spec else None


def is_agent_platform(key: str | None) -> bool:
    spec = PROVIDER_REGISTRY.get(key or "")
    return bool(spec and spec.is_agent_platform)


def agent_platform_keys(*, include_planned: bool = True) -> list[str]:
    """Provider keys that can be an agent-under-test."""
    return [
        s.key
        for s in _SPECS
        if s.is_agent_platform and (include_planned or s.is_ga)
    ]


def provider_choices(*, include_planned: bool = False) -> list[tuple[str, str]]:
    """Django ``choices=`` for AgentDefinition.provider, derived from the registry.

    Defaults to GA agent platforms so the free-form CharField can be constrained
    without rejecting in-flight definitions (DESIGN.md §4.1, §7 Q2).
    """
    return [
        (s.key, s.label)
        for s in _SPECS
        if s.is_agent_platform and (include_planned or s.is_ga)
    ]
