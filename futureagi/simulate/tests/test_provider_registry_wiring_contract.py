"""Provider-wiring contract for the multi-provider simulation platform (TH-5642).

These assert that the ProviderSpec registry (the single source of truth) stays
consistent with the code that actually drives each provider — chat adapters,
outbound dialers, and the observability ProviderChoices enum. They lock the
*wiring* as an enforced invariant so a provider can't silently drift out of sync
or claim a capability whose code isn't registered.

Scope note: this verifies that what each provider claims is BACKED by registered
code — NOT that a live provider call succeeds (that needs creds + runtime and is
covered by the live/integration paths). A provider being correctly wired here and
still gated on an external dependency (deploy / SIP runtime / provider account)
are two different things, and this test only asserts the first.
"""
import pytest

from simulate.providers.registry import (
    _SPECS,
    Direction,
    PROVIDER_REGISTRY,
    Role,
    Transport,
)
from simulate.services.chat_agent_adapter_factory import EXTERNAL_HOSTED_CHAT_ADAPTERS
from simulate.services.outbound_dialer import OUTBOUND_DIALERS
from tracer.models.observability_provider import ProviderChoices

# The 9 providers the TH-5642 matrix must keep modeled (+ system engines).
TH5642_PROVIDERS = {
    "vapi", "retell", "elevenlabs", "deepgram", "agora",
    "pipecat", "twilio", "bland", "livekit_bridge",
}


@pytest.mark.unit
def test_all_target_providers_present_in_registry():
    missing = TH5642_PROVIDERS - set(PROVIDER_REGISTRY)
    assert not missing, f"providers dropped from the registry: {missing}"


@pytest.mark.unit
def test_implemented_directions_subset_of_supported():
    for s in _SPECS:
        assert s.implemented_directions <= s.supported_directions, (
            f"{s.key}: implements {s.implemented_directions} not in supported "
            f"{s.supported_directions}"
        )


@pytest.mark.unit
def test_chat_flag_matches_registered_chat_adapter():
    """A provider may claim chat ONLY if a hosted chat adapter is registered for
    it, and must not be silently in the adapter map without claiming chat."""
    for s in _SPECS:
        if s.chat:
            assert s.key in EXTERNAL_HOSTED_CHAT_ADAPTERS, (
                f"{s.key} claims chat but has no EXTERNAL_HOSTED_CHAT_ADAPTERS entry"
            )
    # Inverse: every adapter key resolves to a chat=True spec (allowing the two
    # elevenlabs spellings the factory accepts).
    for key in EXTERNAL_HOSTED_CHAT_ADAPTERS:
        spec = PROVIDER_REGISTRY.get(key) or PROVIDER_REGISTRY.get(
            "elevenlabs" if key == "eleven_labs" else key
        )
        assert spec is not None and spec.chat, (
            f"chat adapter '{key}' has no chat=True ProviderSpec"
        )


@pytest.mark.unit
def test_observability_key_maps_to_real_provider_choice():
    valid = {c.value for c in ProviderChoices}
    for s in _SPECS:
        if s.observability_key:
            assert s.observability_key in valid, (
                f"{s.key}: observability_key '{s.observability_key}' is not a "
                f"ProviderChoices value"
            )


@pytest.mark.unit
def test_sip_agent_platforms_with_outbound_have_a_dialer():
    """Any NAMED external agent-platform reached over SIP that claims OUTBOUND
    must have a registered outbound dialer. WebRTC-bridge providers do outbound
    via the bridge (not a dialer); the generic ``others`` catch-all + system
    engines (livekit/futureagi) use the platform's generic SIP path, so only the
    named external providers (the TH-5642 set) are in scope here."""
    for s in _SPECS:
        if (
            s.key in TH5642_PROVIDERS
            and Role.AGENT_PLATFORM in s.roles
            and s.transport == Transport.SIP
            and Direction.OUTBOUND in s.implemented_directions
        ):
            assert s.key in OUTBOUND_DIALERS, (
                f"{s.key}: SIP agent-platform implements OUTBOUND but has no "
                f"OUTBOUND_DIALERS entry"
            )


@pytest.mark.unit
def test_webrtc_bridge_providers_have_web_connector_key():
    for s in _SPECS:
        if s.transport == Transport.WEBRTC_BRIDGE:
            assert s.connector_key and s.connector_key.startswith("web_"), (
                f"{s.key}: WEBRTC_BRIDGE transport must carry a web_* connector_key"
            )
