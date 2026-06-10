"""Tests for the ProviderSpec SSOT registry (TH-5642, DESIGN.md §4).

The registry is the single place a provider is declared; these tests pin the
taxonomy (roles are not interchangeable) and that derived values stay in sync
with the real connector registry / ProviderChoices.
"""

import pytest

from simulate.providers import (
    PROVIDER_REGISTRY,
    Direction,
    Role,
    Status,
    Transport,
    agent_platform_keys,
    connector_key_for,
    get_spec,
    implements_direction,
    is_agent_platform,
    provider_choices,
    supports_direction,
)

# The bridge connector keys that exist TODAY (ee/voice/.../bridge/connector.py).
CURRENT_BRIDGE_KEYS = {"web_vapi", "web_retell", "web_livekit_bridge"}


class TestRegistryShape:
    @pytest.mark.unit
    def test_registry_keyed_by_canonical_key(self):
        for key, spec in PROVIDER_REGISTRY.items():
            assert spec.key == key

    @pytest.mark.unit
    def test_specs_are_frozen(self):
        import dataclasses

        spec = get_spec("vapi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.key = "mutated"  # frozen dataclass

    @pytest.mark.unit
    def test_expected_providers_present(self):
        for key in [
            "vapi", "retell", "livekit_bridge", "others",
            "elevenlabs", "deepgram", "agora", "pipecat", "bland",
            "twilio", "livekit", "futureagi",
        ]:
            assert get_spec(key) is not None, key


class TestTaxonomy:
    @pytest.mark.unit
    def test_twilio_is_both_transport_and_agent_platform(self):
        # Twilio is a carrier substrate AND a platform customers build agents on
        # (ConversationRelay/TwiML) — testable inbound (SIP) + outbound (Calls.json).
        spec = get_spec("twilio")
        assert Role.TRANSPORT in spec.roles
        assert spec.is_agent_platform
        assert spec.supported_directions and spec.implemented_directions

    @pytest.mark.unit
    def test_system_livekit_distinct_from_livekit_bridge(self):
        # 'livekit' = our SYSTEM engine; 'livekit_bridge' = the customer's agent.
        system = get_spec("livekit")
        bridge = get_spec("livekit_bridge")
        assert Role.SYSTEM_ENGINE in system.roles
        assert not system.is_agent_platform
        assert bridge.is_agent_platform
        assert bridge.connector_key == "web_livekit_bridge"

    @pytest.mark.unit
    def test_component_presence_is_not_platform_support(self):
        # ElevenLabs/Deepgram are STT/TTS components but only PLANNED as platforms.
        assert Role.TTS in get_spec("elevenlabs").roles
        assert Role.STT in get_spec("deepgram").roles
        assert get_spec("elevenlabs").status is Status.PLANNED
        assert get_spec("deepgram").status is Status.PLANNED

    @pytest.mark.unit
    def test_agora_rides_sip_path_like_bland_no_rtc_sdk(self):
        # Agora Conversational AI exposes agents over SIP/PSTN (Elastic SIP Trunk),
        # so it is reachable via the provider-neutral SIP path with NO Agora RTC SDK
        # — modeled like Bland/Twilio, not via a (SDK-gated) web_agora connector.
        agora = get_spec("agora")
        assert agora.transport is Transport.SIP
        assert agora.connector_key is None  # no WebRTC bridge connector needed
        assert agora.is_agent_platform
        # Parity with the other SIP-reachable agent platform.
        assert get_spec("bland").transport is Transport.SIP

    @pytest.mark.unit
    def test_pipecat_reuses_livekit_bridge_connector(self):
        assert get_spec("pipecat").connector_key == "web_livekit_bridge"
        assert get_spec("pipecat").transport is Transport.WEBRTC_BRIDGE

    @pytest.mark.unit
    def test_livekit_bridge_credentials_path_predicate(self):
        # voice_small.py routes any provider whose connector IS the LiveKit
        # bridge through the bridge-credentials path. This pins exactly which
        # providers that is (Phase 1 wires Pipecat onto it).
        bridge_path = {
            s.key
            for s in PROVIDER_REGISTRY.values()
            if s.connector_key == "web_livekit_bridge"
        }
        assert bridge_path == {"livekit_bridge", "pipecat"}


class TestDerivations:
    @pytest.mark.unit
    def test_connector_key_for_replaces_string_interpolation(self):
        assert connector_key_for("vapi") == "web_vapi"
        assert connector_key_for("retell") == "web_retell"
        assert connector_key_for("livekit_bridge") == "web_livekit_bridge"
        # SIP-only / unknown → None (routes to SIP, not an unknown connection_type)
        assert connector_key_for("others") is None
        assert connector_key_for("twilio") is None
        assert connector_key_for("nonsense") is None
        assert connector_key_for(None) is None

    @pytest.mark.unit
    def test_ga_webrtc_connector_keys_match_current_bridge_registry(self):
        ga_webrtc = {
            s.connector_key
            for s in PROVIDER_REGISTRY.values()
            if s.is_ga and s.transport is Transport.WEBRTC_BRIDGE and s.connector_key
        }
        assert ga_webrtc == CURRENT_BRIDGE_KEYS

    @pytest.mark.unit
    def test_agent_platform_keys(self):
        ga = set(agent_platform_keys(include_planned=False))
        assert ga == {"vapi", "retell", "livekit_bridge", "others"}
        allp = set(agent_platform_keys(include_planned=True))
        assert {"elevenlabs", "deepgram", "agora", "pipecat", "bland", "twilio"} <= allp
        # system engines are never agent platforms (twilio now is, via ConvRelay)
        assert "livekit" not in allp
        assert "futureagi" not in allp

    @pytest.mark.unit
    def test_provider_choices_ga_only_by_default(self):
        ga = dict(provider_choices())
        assert set(ga) == {"vapi", "retell", "livekit_bridge", "others"}
        withp = dict(provider_choices(include_planned=True))
        assert "elevenlabs" in withp and "pipecat" in withp

    @pytest.mark.unit
    def test_is_agent_platform(self):
        assert is_agent_platform("retell")
        assert is_agent_platform("twilio")  # now an agent platform (ConvRelay/TwiML)
        assert not is_agent_platform("unknown")

    @pytest.mark.unit
    def test_observability_keys_are_valid_provider_choices(self):
        from tracer.models.observability_provider import ProviderChoices

        valid = {c.value for c in ProviderChoices}
        for spec in PROVIDER_REGISTRY.values():
            if spec.observability_key is not None:
                assert spec.observability_key in valid, spec.key

    @pytest.mark.unit
    def test_observability_key_mapping_for_voice_obs_wiring(self):
        # The agent-def observability wiring (views/agent_definition.py) maps the
        # client provider string to its canonical ObservabilityProvider via
        # these keys (TH-5642 step 1). livekit_bridge -> livekit is the crux.
        assert get_spec("livekit_bridge").observability_key == "livekit"
        assert get_spec("elevenlabs").observability_key == "eleven_labs"
        assert get_spec("vapi").observability_key == "vapi"
        assert get_spec("retell").observability_key == "retell"
        # TH-5642: observability now registered for ALL agent platforms — every key
        # resolves to a tracer ProviderChoices value so each platform can bind to a
        # provider-named observability project (trace emission is provider-agnostic).
        assert get_spec("pipecat").observability_key == "pipecat"
        assert get_spec("deepgram").observability_key == "deepgram"
        assert get_spec("agora").observability_key == "agora"
        assert get_spec("bland").observability_key == "bland"
        assert get_spec("twilio").observability_key == "twilio"

        from tracer.models.observability_provider import ProviderChoices

        valid = set(ProviderChoices.values)
        for key in agent_platform_keys():
            obs = get_spec(key).observability_key
            assert obs in valid, f"{key} observability_key {obs!r} not a ProviderChoices value"


class TestCallDirection:
    @pytest.mark.unit
    def test_direction_values_mirror_calltype(self):
        # The registry mirrors semantics.CallType by value (it can't import the
        # Django-bound enum). Pin the equality so they can't drift.
        from simulate.semantics import CallType

        assert Direction.INBOUND.value == CallType.INBOUND.value
        assert Direction.OUTBOUND.value == CallType.OUTBOUND.value

    @pytest.mark.unit
    def test_implemented_is_subset_of_supported(self):
        # You can never implement a direction the provider can't support.
        for spec in PROVIDER_REGISTRY.values():
            assert spec.implemented_directions <= spec.supported_directions, spec.key

    @pytest.mark.unit
    def test_per_provider_direction_capability_matches_audit(self):
        IN, OUT = Direction.INBOUND, Direction.OUTBOUND
        # (key, supported, implemented) — from the direction audit (TH-5642).
        expected = {
            "vapi": ({IN, OUT}, {IN, OUT}),
            "retell": ({IN, OUT}, {IN, OUT}),     # outbound wired via RetellOutboundDialer
            "livekit_bridge": ({IN, OUT}, {IN, OUT}),  # bridge: outbound = speaking-order
            "others": ({IN, OUT}, {IN, OUT}),
            "elevenlabs": ({IN, OUT}, {IN, OUT}),  # outbound via ElevenLabsOutboundDialer
            "deepgram": ({IN, OUT}, {IN}),         # no native outbound (BYO-SIP only)
            "agora": ({IN, OUT}, {OUT}),          # outbound wired via AgoraOutboundDialer (ConvAI telephony)
            "pipecat": ({IN, OUT}, {IN, OUT}),     # reuses bridge → outbound = speaking-order
            # Inbound flipped 2026-06-10: a Bland inbound number answers any
            # PSTN caller, so the neutral SIP path reaches it (TH-5683).
            "bland": ({IN, OUT}, {IN, OUT}),
            "twilio": ({IN, OUT}, {IN, OUT}),     # SIP inbound + TwilioOutboundDialer
            "futureagi": (set(), set()),          # internal chat
        }
        for key, (sup, impl) in expected.items():
            spec = get_spec(key)
            assert set(spec.supported_directions) == sup, f"{key} supported"
            assert set(spec.implemented_directions) == impl, f"{key} implemented"

    @pytest.mark.unit
    def test_supports_and_implements_helpers(self):
        # Deepgram supports outbound but has not wired it — the distinction that lets
        # dispatch fail loudly instead of silently running inbound.
        assert supports_direction("deepgram", Direction.OUTBOUND)
        assert not implements_direction("deepgram", Direction.OUTBOUND)
        assert implements_direction("deepgram", Direction.INBOUND)
        # Vapi and Retell: outbound both supported and wired (dialer registry).
        assert implements_direction("vapi", Direction.OUTBOUND)
        assert implements_direction("retell", Direction.OUTBOUND)
        # Twilio is now an agent platform (both directions); only unknown is empty.
        assert not supports_direction("nonsense", Direction.INBOUND)
        assert supports_direction("twilio", Direction.INBOUND)

    @pytest.mark.unit
    def test_direction_filtered_selectors(self):
        # Every GA agent platform supports both directions, so a direction filter on
        # GA choices doesn't drop any — but it MUST exclude transport/internal rows.
        out_keys = set(agent_platform_keys(direction=Direction.OUTBOUND))
        assert {"vapi", "retell", "livekit_bridge", "others", "twilio"} <= out_keys
        assert "futureagi" not in out_keys  # system engine, never an agent platform
        ga_out = dict(provider_choices(direction=Direction.OUTBOUND))
        assert set(ga_out) == {"vapi", "retell", "livekit_bridge", "others"}


class TestBridgeRegistryParity:
    @pytest.mark.unit
    def test_cross_check_against_real_connector_registry_if_importable(self):
        # Best-effort: the real bridge registry pulls livekit SDK deps that may be
        # absent in the backend venv — skip if it can't import.
        connector = pytest.importorskip(
            "ee.voice.services.livekit.bridge.connector"
        )
        real_keys = set(connector.get_connector_registry().keys())
        ga_webrtc = {
            s.connector_key
            for s in PROVIDER_REGISTRY.values()
            if s.is_ga and s.transport is Transport.WEBRTC_BRIDGE and s.connector_key
        }
        assert ga_webrtc <= real_keys
