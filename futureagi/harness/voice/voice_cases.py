from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass

from fi.alk import simulate

_COMMON_ENV = ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
_GOOGLE_PROVIDERS = {"gemini", "google", "vertex"}
_MODEL_DEFAULTS = {
    "llm": {
        "gemini": "gemini-2.5-flash-lite",
        "google": "gemini-2.5-flash-lite",
        "openai": "gpt-4o",
        "openai_compatible": "gpt-4o",
        "vertex": "gemini-2.5-flash-lite",
    },
    "stt": {
        "cartesia": "ink-2",
        "deepgram": "nova-3",
        "elevenlabs": "scribe_v2_realtime",
        "google": "latest_long",
        "openai": "gpt-4o-mini-transcribe",
        "openai_compatible": "gpt-4o-mini-transcribe",
        "vertex": "latest_long",
    },
    "tts": {
        "cartesia": "sonic-3",
        "deepgram": "aura-2-andromeda-en",
        "elevenlabs": "eleven_turbo_v2_5",
        "google": "standard",
        "openai": "gpt-4o-mini-tts",
        "openai_compatible": "gpt-4o-mini-tts",
        "vertex": "standard",
    },
}
_TTS_VOICE_DEFAULTS = {
    "cartesia": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    "deepgram": "andromeda",
    "elevenlabs": "hpp4J3VqNfWAUOO0d1Us",
    "google": "en-US-Chirp3-HD-Kore",
    "openai": "alloy",
    "openai_compatible": "alloy",
    "vertex": "en-US-Chirp3-HD-Kore",
}

# What the persona's language means to a speech stack. The persona model offers English and
# Hindi; anything else falls through to the default rather than guessing a code.
_STT_LANGUAGE = {"english": "en", "hindi": "hi"}
_GOOGLE_STT_LANGUAGE = {"english": "en-US", "hindi": "hi-IN"}


def _accent_voices() -> dict[str, dict[str, str]]:
    """Which voice each accent should speak in, per provider.

    Configuration, not a guess. A persona asking for an Indian accent is only heard as one if a
    real voice id is named for it, and inventing ids here would produce calls that fail at the
    provider or, worse, silently fall back to the default while the run reports the accent was
    honoured. Supplied as ``{"deepgram": {"indian": "<voice>"}}``; unmapped accents keep the
    provider default and are reported as unmapped rather than pretended.
    """
    try:
        held = json.loads(os.environ.get("SIMULATOR_TTS_VOICE_BY_ACCENT") or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(held, dict):
        return {}
    return {
        str(provider).lower(): {str(k).lower(): str(v) for k, v in table.items()}
        for provider, table in held.items()
        if isinstance(table, dict)
    }


def _persona_now() -> dict:
    """The persona this call is being placed for, as the harness handed it over."""
    raw = os.environ.get("HARNESS_PERSONA", "").strip()
    try:
        held = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return held if isinstance(held, dict) else {}


def _voice_for(persona: dict, provider: str) -> tuple[str, str]:
    """The voice this caller speaks in, and why it was chosen.

    An explicit ``SIMULATOR_TTS_VOICE`` always wins: an operator pinning a voice for a run means
    it. Otherwise the persona's accent selects one, if a voice has been named for that accent.
    """
    if explicit := os.environ.get("SIMULATOR_TTS_VOICE"):
        return explicit, "pinned by SIMULATOR_TTS_VOICE"
    default = _TTS_VOICE_DEFAULTS.get(provider.lower(), "alloy")
    accent = str(persona.get("accent") or "").strip()
    if not accent:
        return default, "persona names no accent"
    found = (_accent_voices().get(provider.lower()) or {}).get(accent.lower())
    if found:
        return found, f"accent {accent!r}"
    return default, f"accent {accent!r} has no voice mapped for {provider}, using the default"


def _language_for(persona: dict, provider: str) -> tuple[str, str]:
    """The language the caller is understood in, and why."""
    if explicit := os.environ.get("SIMULATOR_STT_LANGUAGE"):
        return explicit, "pinned by SIMULATOR_STT_LANGUAGE"
    google = provider.lower() in _GOOGLE_PROVIDERS
    fallback = "en-US" if google else "en"
    spoken = persona.get("languages") or []
    first = str(spoken[0]).strip().lower() if spoken else ""
    if not first:
        return fallback, "persona names no language"
    table = _GOOGLE_STT_LANGUAGE if google else _STT_LANGUAGE
    return (table.get(first, fallback), f"persona speaks {first!r}")


@dataclass(frozen=True)
class VoiceCase:
    case_id: str
    description: str
    status: str
    conversation_direction: str
    extra_env: tuple[str, ...]
    setup: str

    @property
    def required_env(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    _livekit_url_env_name(),
                    *_COMMON_ENV,
                    *_simulator_required_env(),
                    *self.extra_env,
                )
            )
        )


@dataclass(frozen=True)
class VoiceInputs:
    agent_definition: simulate.AgentDefinition
    livekit_runtime: simulate.LiveKitSimulatorRuntime
    scenario: simulate.Scenario
    simulator: simulate.SimulatorAgentDefinition
    conversation_direction: str
    max_seconds: float


CASES = {
    "1.1.1": VoiceCase(
        "1.1.1",
        "LiveKit agent · inbound · telephony",
        "proven",
        "simulator_first",
        (
            "LIVEKIT_TARGET_SYSTEM_PROMPT",
            "LIVEKIT_OUTBOUND_TRUNK_ID",
            "PSTN_CALLER_NUMBER",
            "LIVEKIT_TARGET_PHONE_NUMBER",
        ),
        "A working LiveKit outbound trunk and a phone number answered by the target LiveKit agent.",
    ),
    "1.1.2": VoiceCase(
        "1.1.2",
        "LiveKit agent · inbound · WebRTC",
        "proven",
        # The dispatched inbound target greets as soon as it joins. Starting
        # the simulator too creates two simultaneous opening turns and leaves
        # the scripted customer answering an empty prompt.
        "agent_first",
        ("LIVEKIT_TARGET_AGENT_NAME", "LIVEKIT_TARGET_SYSTEM_PROMPT"),
        "A registered LiveKit target worker reachable by LIVEKIT_TARGET_AGENT_NAME.",
    ),
    "1.2.1": VoiceCase(
        "1.2.1",
        "LiveKit agent · outbound · telephony",
        "proven",
        "agent_first",
        (
            "LIVEKIT_TARGET_AGENT_NAME",
            "LIVEKIT_TARGET_SYSTEM_PROMPT",
            "LIVEKIT_OUTBOUND_TRUNK_ID",
            "PSTN_CALLER_NUMBER",
            "LIVEKIT_INBOUND_TRUNK_ID",
            "LIVEKIT_INBOUND_DID",
        ),
        "A target worker enabled to originate SIP calls to LIVEKIT_INBOUND_DID.",
    ),
    "1.2.2": VoiceCase(
        "1.2.2",
        "LiveKit agent · outbound · WebRTC",
        "proven",
        "agent_first",
        ("LIVEKIT_TARGET_AGENT_NAME", "LIVEKIT_TARGET_SYSTEM_PROMPT"),
        "The registered target worker must speak first after dispatch.",
    ),
    "2.1.1": VoiceCase(
        "2.1.1",
        "Vapi agent · inbound · telephony",
        "proven",
        "simulator_first",
        (
            "VAPI_TARGET_SYSTEM_PROMPT",
            "VAPI_API_KEY",
            "LIVEKIT_OUTBOUND_TRUNK_ID",
            "PSTN_CALLER_NUMBER",
            "VAPI_TARGET_PHONE_NUMBER",
        ),
        "A working outbound trunk and a Vapi assistant phone number that accepts inbound PSTN calls.",
    ),
    "2.1.2": VoiceCase(
        "2.1.2",
        "Vapi agent · inbound · web",
        "proven",
        "simulator_first",
        ("VAPI_TARGET_SYSTEM_PROMPT", "VAPI_API_KEY", "VAPI_ASSISTANT_ID"),
        "A Vapi assistant with WebSocket calls enabled.",
    ),
    "2.2.1": VoiceCase(
        "2.2.1",
        "Vapi agent · outbound · telephony",
        "proven",
        "agent_first",
        (
            "VAPI_TARGET_SYSTEM_PROMPT",
            "VAPI_API_KEY",
            "VAPI_ASSISTANT_ID",
            "VAPI_PHONE_NUMBER_ID",
            "LIVEKIT_INBOUND_TRUNK_ID",
            "LIVEKIT_INBOUND_DID",
        ),
        "A caller-scoped inbound trunk and a Vapi phone number with outbound calling enabled; the configured SIP ingress route must reach this LiveKit project.",
    ),
    "2.2.2": VoiceCase(
        "2.2.2",
        "Vapi agent · outbound · web",
        "proven",
        "agent_first",
        ("VAPI_TARGET_SYSTEM_PROMPT", "VAPI_API_KEY", "VAPI_ASSISTANT_ID"),
        "The Vapi assistant must have an initial message so it speaks first.",
    ),
    "3.1.1": VoiceCase(
        "3.1.1",
        "Retell agent · inbound · telephony",
        "proven",
        "simulator_first",
        (
            "RETELL_TARGET_SYSTEM_PROMPT",
            "LIVEKIT_OUTBOUND_TRUNK_ID",
            "PSTN_CALLER_NUMBER",
            "RETELL_TARGET_PHONE_NUMBER",
        ),
        "A working outbound trunk and a Retell phone number that accepts inbound PSTN calls.",
    ),
    "3.1.2": VoiceCase(
        "3.1.2",
        "Retell agent · inbound · web",
        "proven",
        "simulator_first",
        ("RETELL_TARGET_SYSTEM_PROMPT", "RETELL_API_KEY", "RETELL_AGENT_ID"),
        "A Retell agent with web calls enabled.",
    ),
}


def missing_env(case: VoiceCase) -> list[str]:
    return [name for name in case.required_env if not os.environ.get(name, "").strip()]


def _harness_scenario() -> simulate.Scenario | None:
    """The caller the harness prepared, if this run is driving one of its scenarios.

    ``HARNESS_INSTRUCTION`` is the simulator prompt the environment step wrote with this
    scenario's values already filled in, so nothing about how a caller behaves is decided here.
    Without it the built-in acceptance persona is used and this file behaves exactly as before.
    """
    instruction = os.environ.get("HARNESS_INSTRUCTION", "").strip()
    if not instruction:
        return None
    scripted = os.environ.get("HARNESS_SCRIPTED_CALLER", "").strip()
    scripted_caller = json.loads(scripted) if scripted else None
    persona_json = os.environ.get("HARNESS_PERSONA", "").strip()
    persona = json.loads(persona_json) if persona_json else {"name": "customer"}
    persona["role"] = "customer"
    persona["initial_message"] = os.environ.get(
        "HARNESS_INITIAL_MESSAGE", ""
    ).strip()
    persona["scripted_caller"] = scripted_caller
    return simulate.Scenario(
        name=os.environ.get("HARNESS_SCENARIO", "harness"),
        dataset=[
            simulate.Persona(
                persona=persona,
                situation=instruction,
                outcome=os.environ.get("HARNESS_OUTCOME", "")
                or "Do what you came to do, or accept that you cannot.",
            )
        ],
    )


def build_inputs(case_id: str, run_id: str) -> VoiceInputs:
    case = CASES[case_id]
    room_override = os.environ.get("ACCEPTANCE_ROOM_NAME_OVERRIDE", "").strip()
    runtime = simulate.LiveKitSimulatorRuntime(
        url=_livekit_url(),
        room_name=room_override or f"acceptance-{case_id.replace('.', '-')}-{run_id}",
        room_mode="managed",
        room_name_verbatim=bool(room_override),
    )
    scenario = _harness_scenario() or simulate.Scenario(
        name=f"acceptance-{case_id}",
        dataset=[
            simulate.Persona(
                persona={"name": "Morgan", "role": "customer"},
                situation=(
                    "A delivery is late. Ask for its current status, expected arrival, "
                    "and the next action."
                ),
                outcome="Complete a natural multi-turn conversation and close politely.",
            )
        ],
    )
    llm_provider = os.environ.get("SIMULATOR_LLM_PROVIDER", "google")
    stt_provider = os.environ.get("SIMULATOR_STT_PROVIDER", "deepgram")
    tts_provider = os.environ.get("SIMULATOR_TTS_PROVIDER", "deepgram")

    # The caller's own speech, from the persona this scenario was written with. Without this the
    # accent and language are prose in the prompt and nothing else: every persona sounds the
    # same, which is exactly what "no persona variant recording" describes.
    speaking = _persona_now()
    speaks_as, voice_why = _voice_for(speaking, tts_provider)
    speaks, language_why = _language_for(speaking, stt_provider)
    print(
        f"[voice] caller speaks {speaks} ({language_why}); voice {speaks_as} ({voice_why})",
        flush=True,
    )
    simulator = simulate.SimulatorAgentDefinition(
        llm={
            "provider": llm_provider,
            "model": _model("llm", llm_provider),
            "temperature": float(os.environ.get("SIMULATOR_LLM_TEMPERATURE", "0.2")),
        },
        stt={
            "provider": stt_provider,
            "model": _model("stt", stt_provider),
            "language": speaks,
        },
        tts={
            "provider": tts_provider,
            "model": _model("tts", tts_provider),
            "voice": speaks_as,
        },
        instructions=os.environ.get("HARNESS_SIMULATOR_INSTRUCTIONS") or None,
        allow_interruptions=os.environ.get("SIMULATOR_ALLOW_INTERRUPTION", "1").lower()
        not in {"0", "false", "no"},
    )
    agent = _build_agent(case_id)
    return VoiceInputs(
        agent_definition=agent,
        livekit_runtime=runtime,
        scenario=scenario,
        simulator=simulator,
        conversation_direction=os.environ.get(
            "HARNESS_CONVERSATION_DIRECTION", case.conversation_direction
        ),
        max_seconds=float(os.environ.get("VOICE_MAX_SECONDS", "0"))
        or (
            210.0
            if {stt_provider.lower(), tts_provider.lower()} & _GOOGLE_PROVIDERS
            else 150.0
            if "telephony" in case.description.lower()
            # Transactional voice agents commonly need address confirmation,
            # option selection, payment verification, and a final read-back.
            # Two minutes cuts valid calls off before those stages complete.
            else 240.0
        ),
    )


def _simulator_required_env() -> tuple[str, ...]:
    llm_provider = os.environ.get("SIMULATOR_LLM_PROVIDER", "google").lower()
    voice_providers = {
        os.environ.get("SIMULATOR_STT_PROVIDER", "deepgram").lower(),
        os.environ.get("SIMULATOR_TTS_PROVIDER", "deepgram").lower(),
    }
    providers = {llm_provider, *voice_providers}
    required: list[str] = []
    if llm_provider in _GOOGLE_PROVIDERS:
        if os.environ.get("GEMINI_API_KEY"):
            required.append("GEMINI_API_KEY")
        elif os.environ.get("GOOGLE_API_KEY"):
            required.append("GOOGLE_API_KEY")
        else:
            required.extend(("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"))
    if (
        voice_providers & {"google", "vertex"}
        and "GOOGLE_APPLICATION_CREDENTIALS" not in required
    ):
        required.append("GOOGLE_APPLICATION_CREDENTIALS")
    if "deepgram" in providers:
        required.append("DEEPGRAM_API_KEY")
    if "cartesia" in providers:
        required.append("CARTESIA_API_KEY")
    if "openai" in providers or "openai_compatible" in providers:
        required.append(
            "SIMULATOR_LLM_API_KEY"
            if os.environ.get("SIMULATOR_LLM_API_KEY")
            else "OPENAI_API_KEY"
        )
    if "elevenlabs" in providers:
        required.append(
            "ELEVEN_API_KEY"
            if os.environ.get("ELEVEN_API_KEY")
            else "ELEVENLABS_API_KEY"
        )
    return tuple(required)


def _build_agent(case_id: str) -> simulate.AgentDefinition:
    if case_id in {"1.1.2", "1.2.2"}:
        return simulate.AgentDefinition(
            name="livekit-target",
            agent_name=_env("LIVEKIT_TARGET_AGENT_NAME"),
            system_prompt=_env("LIVEKIT_TARGET_SYSTEM_PROMPT"),
            transport={"kind": "webrtc"},
        )
    if case_id == "1.1.1":
        return _sip_outbound_agent(
            name="livekit-pstn-target",
            prompt_env="LIVEKIT_TARGET_SYSTEM_PROMPT",
            target_number_env="LIVEKIT_TARGET_PHONE_NUMBER",
        )
    if case_id == "1.2.1":
        transport: dict = {
            "kind": "sip_inbound",
            "readiness_timeout_seconds": 120,
        }
        rule_name = os.environ.get("LIVEKIT_INBOUND_DISPATCH_RULE_NAME", "").strip()
        if rule_name:
            transport["dispatch_rule_name"] = rule_name
        return simulate.AgentDefinition(
            name="livekit-originating-target",
            system_prompt=_env("LIVEKIT_TARGET_SYSTEM_PROMPT"),
            transport=transport,
        )
    if case_id in {"2.1.2", "2.2.2"}:
        return simulate.AgentDefinition(
            name="vapi-web-target",
            system_prompt=_env("VAPI_TARGET_SYSTEM_PROMPT"),
            target={
                "provider": "vapi",
                "assistant_id": _env("VAPI_ASSISTANT_ID"),
                "api_key_env": "VAPI_API_KEY",
            },
            transport={"kind": "vapi_websocket"},
            provider_evidence={
                "provider": "vapi",
                "call_id_source": "originator_response",
            },
        )
    if case_id == "2.1.1":
        agent = _sip_outbound_agent(
            name="vapi-pstn-target",
            prompt_env="VAPI_TARGET_SYSTEM_PROMPT",
            target_number_env="VAPI_TARGET_PHONE_NUMBER",
        )
        return simulate.AgentDefinition.model_validate(
            {
                **agent.model_dump(mode="json", exclude_none=True),
                "provider_evidence": {
                    "provider": "vapi",
                    "call_id_source": "polling_window",
                    "polling_window_seconds": 90,
                    "poll_deadline_seconds": 90,
                },
            }
        )
    if case_id == "2.2.1":
        return simulate.AgentDefinition(
            name="vapi-originating-target",
            system_prompt=_env("VAPI_TARGET_SYSTEM_PROMPT"),
            transport={
                "kind": "sip_inbound",
                "inbound_call_originator": "vapi",
                "readiness_timeout_seconds": 120,
            },
            provider_evidence={
                "provider": "vapi",
                "call_id_source": "originator_response",
                "poll_deadline_seconds": 90,
            },
        )
    if case_id == "3.1.1":
        return _sip_outbound_agent(
            name="retell-pstn-target",
            prompt_env="RETELL_TARGET_SYSTEM_PROMPT",
            target_number_env="RETELL_TARGET_PHONE_NUMBER",
        )
    if case_id == "3.1.2":
        return simulate.AgentDefinition(
            name="retell-web-target",
            system_prompt=_env("RETELL_TARGET_SYSTEM_PROMPT"),
            target={
                "provider": "retell",
                "agent_id": _env("RETELL_AGENT_ID"),
                "api_key_env": "RETELL_API_KEY",
            },
            transport={"kind": "retell_webcall"},
            provider_evidence={
                "provider": "retell",
                "call_id_source": "originator_response",
            },
        )
    raise KeyError(case_id)


def _sip_outbound_agent(
    *,
    name: str,
    prompt_env: str,
    target_number_env: str,
) -> simulate.AgentDefinition:
    return simulate.AgentDefinition(
        name=name,
        system_prompt=_env(prompt_env),
        transport={
            "kind": "sip_outbound",
            "sip_trunk_id": _env("LIVEKIT_OUTBOUND_TRUNK_ID"),
            "sip_number": _env("PSTN_CALLER_NUMBER"),
            "sip_call_to": _env(target_number_env),
            "participant_identity": "sip-caller-{invocation_id}-{test_case_id}",
            "answer_timeout_seconds": 60,
        },
    )


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing environment variable: {name}")
    return value


def _model(kind: str, provider: str) -> str:
    env_name = f"SIMULATOR_{kind.upper()}_MODEL"
    return os.environ.get(env_name) or _MODEL_DEFAULTS[kind].get(
        provider.lower(),
        _MODEL_DEFAULTS[kind]["openai"],
    )


def _livekit_url_env_name() -> str:
    return (
        "LIVEKIT_URL"
        if not os.environ.get("ACCEPTANCE_LIVEKIT_URL", "").strip()
        and os.environ.get("LIVEKIT_URL", "").strip()
        else "ACCEPTANCE_LIVEKIT_URL"
    )


def _livekit_url() -> str:
    name = _livekit_url_env_name()
    if name == "LIVEKIT_URL":
        warnings.warn(
            "ACCEPTANCE_LIVEKIT_URL is unset; using LIVEKIT_URL",
            RuntimeWarning,
            stacklevel=2,
        )
    return _env(name)
