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


class Direction(StrEnum):
    """A telephony call direction (from the agent-under-test's call perspective).

    Mirrors ``simulate.semantics.CallType`` (inbound/outbound) by VALUE — the
    registry is intentionally Django-free, and CallType lives behind a Django import,
    so we mirror rather than import; ``test_provider_registry`` pins the equality.
    """

    INBOUND = "inbound"    # FutureAGI's simulator CALLS the agent (agent receives)
    OUTBOUND = "outbound"  # the agent CALLS FutureAGI (our simulator answers)


_BOTH = frozenset({Direction.INBOUND, Direction.OUTBOUND})
_IN = frozenset({Direction.INBOUND})
_OUT = frozenset({Direction.OUTBOUND})
_NONE: frozenset[Direction] = frozenset()


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
    # Call directions the provider's API can do AND we can drive (capability).
    supported_directions: frozenset[Direction] = _IN
    # Subset of supported_directions actually WIRED in our orchestration today.
    # Kept separate from `supported` (mirrors Status's planned-vs-GA intent): the
    # orchestration must FAIL LOUDLY if a direction is requested that's supported but
    # not yet implemented, instead of silently defaulting to inbound (audit gap).
    implemented_directions: frozenset[Direction] = _NONE

    @property
    def is_agent_platform(self) -> bool:
        return Role.AGENT_PLATFORM in self.roles

    @property
    def is_ga(self) -> bool:
        return self.status in (Status.GA, Status.TRANSPORT_ONLY, Status.INTERNAL)

    def supports(self, direction: Direction) -> bool:
        return direction in self.supported_directions

    def implements(self, direction: Direction) -> bool:
        return direction in self.implemented_directions


# Declarative registry — the ONLY place a provider is declared. See DESIGN.md §1/§5.
_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "vapi", "Vapi",
        roles=frozenset({Role.AGENT_PLATFORM, Role.SYSTEM_ENGINE, Role.CHAT_ENGINE}),
        transport=Transport.WEBRTC_BRIDGE, connector_key="web_vapi",
        credential_shape=CredentialShape.API_KEY_ASSISTANT, chat=True,
        observability_key="vapi", status=Status.GA,
        # The only provider whose outbound (agent-dials-us) path is wired today.
        supported_directions=_BOTH, implemented_directions=_BOTH,
    ),
    ProviderSpec(
        "retell", "Retell",
        roles=frozenset({Role.AGENT_PLATFORM, Role.CHAT_ENGINE}),
        transport=Transport.WEBRTC_BRIDGE, connector_key="web_retell",
        credential_shape=CredentialShape.API_KEY_ASSISTANT, chat=True,
        observability_key="retell", status=Status.GA,
        # Inbound via the web_retell bridge; outbound now wired via the
        # RetellOutboundDialer (/v2/create-phone-call). "implemented" = wired, not
        # yet live-verified e2e (a real outbound call needs a Retell number + stack).
        supported_directions=_BOTH, implemented_directions=_BOTH,
    ),
    # The customer's own LiveKit agent (distinct from the SYSTEM 'livekit' engine).
    ProviderSpec(
        "livekit_bridge", "LiveKit (agent)",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.WEBRTC_BRIDGE, connector_key="web_livekit_bridge",
        credential_shape=CredentialShape.LIVEKIT_SERVER,
        observability_key="livekit", status=Status.GA,
        # WebRTC bridge has NO PSTN leg: we connect to the agent the same way in
        # both directions; the only difference is who speaks first, now handled by
        # first_message_mode for all transports. So outbound = inbound bridge +
        # agent-speaks-first → both wired.
        supported_directions=_BOTH, implemented_directions=_BOTH,
    ),
    # Provider-neutral catch-all: custom agents reached by phone (SIP) or websocket.
    ProviderSpec(
        "others", "Others (custom / SIP)",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.SIP, credential_shape=CredentialShape.WEBSOCKET_URL,
        observability_key="others", status=Status.GA,
        # Plain-E.164 SIP works in both directions today.
        supported_directions=_BOTH, implemented_directions=_BOTH,
    ),
    # --- Planned tested platforms (DESIGN.md §5) ---
    ProviderSpec(
        "elevenlabs", "ElevenLabs Conversational AI",
        roles=frozenset({Role.AGENT_PLATFORM, Role.TTS}),
        transport=Transport.DIRECT_WS, connector_key="web_elevenlabs",
        credential_shape=CredentialShape.AGENT_ID, chat=True,
        observability_key="eleven_labs", status=Status.PLANNED,
        # Inbound via the connect()-only WS connector; outbound now wired via
        # ElevenLabsOutboundDialer (POST /v1/convai/twilio/outbound-call).
        supported_directions=_BOTH, implemented_directions=_BOTH,
    ),
    ProviderSpec(
        "deepgram", "Deepgram Voice Agent",
        roles=frozenset({Role.AGENT_PLATFORM, Role.STT}),
        transport=Transport.DIRECT_WS, connector_key="web_deepgram",
        credential_shape=CredentialShape.AGENT_ID, status=Status.PLANNED,
        supported_directions=_BOTH, implemented_directions=_IN,
    ),
    # Agora Conversational AI Engine exposes its agents over SIP/PSTN via an
    # Elastic SIP Trunk (import a number, assign to the agent for inbound/outbound)
    # — so we reach it through the SAME provider-neutral SIP path as Bland/Twilio,
    # with NO Agora RTC SDK. Native Agora-RTC (Transport.AGORA_RTC, the unbuilt
    # web_agora connector) is only the optional WebRTC alternative and stays
    # SDK-gated (DESIGN.md §5.4). Modeling SIP as the primary transport matches the
    # only path achievable on our infra today.
    ProviderSpec(
        "agora", "Agora Conversational AI",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.SIP,
        credential_shape=CredentialShape.API_KEY_ASSISTANT, status=Status.PLANNED,
        # SIP/PSTN both directions per Agora's Elastic SIP Trunk. Outbound now wired via
        # AgoraOutboundDialer (ConvAI telephony API; agent SIP-dials our pool number);
        # inbound (we SIP-call an Agora number) is not wired yet — same shape as Bland.
        supported_directions=_BOTH, implemented_directions=_OUT,
    ),
    # Pipecat-on-LiveKit reuses the existing LiveKit bridge connector (DESIGN.md §5.5).
    ProviderSpec(
        "pipecat", "Pipecat (LiveKit transport)",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.WEBRTC_BRIDGE, connector_key="web_livekit_bridge",
        credential_shape=CredentialShape.LIVEKIT_SERVER, status=Status.PLANNED,
        # Reuses the LiveKit bridge → same as livekit_bridge: outbound is just the
        # agent-speaks-first opener over the same bridge connection. Both wired.
        supported_directions=_BOTH, implemented_directions=_BOTH,
    ),
    # Bland.ai agents are reached via the provider-neutral SIP/phone path (no
    # WebRTC connector needed) — DESIGN.md §3/§6.
    ProviderSpec(
        "bland", "Bland.ai",
        roles=frozenset({Role.AGENT_PLATFORM}),
        transport=Transport.SIP, credential_shape=CredentialShape.API_KEY_ASSISTANT,
        status=Status.PLANNED,
        # Bland is outbound-first: outbound now wired via BlandOutboundDialer
        # (/v1/calls). Inbound (receiving on a Bland number) is not wired yet.
        supported_directions=_BOTH, implemented_directions=_OUT,
    ),
    # --- Non-agent-platform roles ---
    ProviderSpec(
        "twilio", "Twilio",
        # Twilio is BOTH a carrier substrate AND a platform customers build agents on
        # (ConversationRelay / Media Streams / TwiML routed to their logic).
        roles=frozenset({Role.AGENT_PLATFORM, Role.TRANSPORT}),
        transport=Transport.SIP, credential_shape=CredentialShape.API_KEY_ASSISTANT,
        status=Status.PLANNED,
        # Inbound = dial the Twilio number over our SIP path; outbound = the
        # TwilioOutboundDialer (Calls.json). Both wired.
        supported_directions=_BOTH, implemented_directions=_BOTH,
    ),
    ProviderSpec(
        "livekit", "LiveKit (system engine)",
        roles=frozenset({Role.SYSTEM_ENGINE, Role.TRANSPORT}),
        transport=Transport.SIP, observability_key="livekit", status=Status.INTERNAL,
        # The system engine drives both directions of the simulated call.
        supported_directions=_BOTH, implemented_directions=_BOTH,
    ),
    ProviderSpec(
        "futureagi", "FutureAGI (internal simulator)",
        roles=frozenset({Role.SYSTEM_ENGINE, Role.CHAT_ENGINE}),
        status=Status.INTERNAL,
        # Internal chat engine — telephony direction not applicable.
        supported_directions=_NONE, implemented_directions=_NONE,
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


def supports_direction(key: str | None, direction: Direction) -> bool:
    """True if the provider's API can do this direction AND we can drive it."""
    spec = PROVIDER_REGISTRY.get(key or "")
    return bool(spec and direction in spec.supported_directions)


def implements_direction(key: str | None, direction: Direction) -> bool:
    """True if this direction is actually WIRED for the provider today.

    Dispatch should check this (not just ``supports_direction``) and fail loudly
    when a supported-but-unimplemented direction is requested, rather than silently
    running the inbound path (audit: the ``is_outbound=False`` default bug).
    """
    spec = PROVIDER_REGISTRY.get(key or "")
    return bool(spec and direction in spec.implemented_directions)


def implemented_directions_for(key: str | None) -> frozenset[Direction]:
    spec = PROVIDER_REGISTRY.get(key or "")
    return spec.implemented_directions if spec else _NONE


def agent_platform_keys(
    *, include_planned: bool = True, direction: Direction | None = None
) -> list[str]:
    """Provider keys that can be an agent-under-test, optionally for a direction."""
    return [
        s.key
        for s in _SPECS
        if s.is_agent_platform
        and (include_planned or s.is_ga)
        and (direction is None or direction in s.supported_directions)
    ]


def provider_choices(
    *, include_planned: bool = False, direction: Direction | None = None
) -> list[tuple[str, str]]:
    """Django ``choices=`` for AgentDefinition.provider, derived from the registry.

    Defaults to GA agent platforms so the free-form CharField can be constrained
    without rejecting in-flight definitions (DESIGN.md §4.1, §7 Q2). Pass
    ``direction`` to constrain the per-provider inbound/outbound toggle in the form.
    """
    return [
        (s.key, s.label)
        for s in _SPECS
        if s.is_agent_platform
        and (include_planned or s.is_ga)
        and (direction is None or direction in s.supported_directions)
    ]
