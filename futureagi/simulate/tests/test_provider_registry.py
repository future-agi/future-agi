"""Tests for the ProviderSpec SSOT registry (TH-5642, DESIGN.md §4).

The registry is the single place a provider is declared; these tests pin the
taxonomy (roles are not interchangeable) and that derived values stay in sync
with the real connector registry / ProviderChoices.
"""

import pytest

from simulate.providers import (
    PROVIDER_REGISTRY,
    Role,
    Status,
    Transport,
    agent_platform_keys,
    connector_key_for,
    get_spec,
    is_agent_platform,
    provider_choices,
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
    def test_twilio_is_transport_not_agent_platform(self):
        spec = get_spec("twilio")
        assert Role.TRANSPORT in spec.roles
        assert not spec.is_agent_platform
        assert spec.status is Status.TRANSPORT_ONLY

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
        assert {"elevenlabs", "deepgram", "agora", "pipecat", "bland"} <= allp
        # transport-only / system engines are never agent platforms
        assert "twilio" not in allp
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
        assert not is_agent_platform("twilio")
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
        # Providers without a push/API observability path are skipped (None).
        assert get_spec("pipecat").observability_key is None
        assert get_spec("deepgram").observability_key is None


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
