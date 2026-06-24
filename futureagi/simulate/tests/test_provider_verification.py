"""Tests for the provider verification harness (TH-5642)."""

import pytest

from simulate.providers import registry as reg
from simulate.services import provider_verification as pv


@pytest.mark.unit
def test_registry_matrix_covers_every_provider():
    report = pv.declared_matrix()
    providers = {c.provider for c in report.cells}
    assert providers == set(reg.agent_platform_keys())
    # chat-capable providers get a chat cell; voice providers get per-direction cells.
    by = {(c.provider, c.modality, c.direction) for c in report.cells}
    assert ("vapi", "chat", None) in by  # vapi.chat = True
    assert ("vapi", "voice", "inbound") in by
    assert ("vapi", "voice", "outbound") in by
    assert ("deepgram", "voice", "inbound") in by
    assert ("deepgram", "voice", "outbound") not in by  # deepgram inbound-only
    assert ("bland", "voice", "outbound") in by
    # Inbound implemented via the neutral SIP path (registry flip, TH-5683).
    assert ("bland", "voice", "inbound") in by


@pytest.mark.unit
def test_agora_both_directions_wired():
    # Agora outbound via AgoraOutboundDialer (ConvAI telephony); inbound now
    # implemented phone-free via the native web_agora RTC connector (TH-5682).
    report = pv.declared_matrix()
    agora = [c for c in report.cells if c.provider == "agora"]
    cells = {(c.modality, c.direction): c.status for c in agora}
    assert ("voice", "outbound") in cells and cells[("voice", "outbound")] == pv.OK
    assert ("voice", "inbound") in cells and cells[("voice", "inbound")] == pv.OK
    assert ("chat", None) not in cells  # no chat product


@pytest.mark.unit
def test_credential_status_present_and_missing():
    # vapi shape=api_key_assistant -> needs SIM_VERIFY_VAPI_API_KEY
    status, _ = pv.credential_status("vapi", env={})
    assert status == pv.MISSING
    status, detail = pv.credential_status("vapi", env={"SIM_VERIFY_VAPI_API_KEY": "x"})
    assert status == pv.OK


@pytest.mark.unit
def test_credential_status_sip_only_needs_stack():
    # 'others' shape is websocket_url; agora/twilio/bland are api_key. Find a sip_only one.
    # 'others' = websocket_url, not sip_only; sip_only providers report SKIPPED.
    # Verify the SIP-stack messaging path via a known sip_only shape if present.
    sip_providers = [
        p
        for p in reg.agent_platform_keys()
        if str(getattr(reg.get_spec(p), "credential_shape", "")) == "sip_only"
    ]
    for p in sip_providers:
        status, detail = pv.credential_status(p, env={})
        assert status == pv.SKIPPED
        assert "SIP" in detail


@pytest.mark.unit
def test_livekit_needs_three_env_vars():
    status, detail = pv.credential_status("livekit_bridge", env={})
    assert status == pv.MISSING
    assert "LIVEKIT_URL" in detail and "LIVEKIT_API_SECRET" in detail


@pytest.mark.unit
def test_connectivity_uses_injected_probe_and_marks_pass():
    calls = []

    def fake_probe(env):
        calls.append(env)
        return True, "handshake ok"

    env = {"SIM_VERIFY_DEEPGRAM_API_KEY": "x", "SIM_VERIFY_DEEPGRAM_AGENT_ID": "a"}
    report = pv.connectivity_matrix(env=env, probes={"deepgram": fake_probe})
    dg = [c for c in report.cells if c.provider == "deepgram"]
    assert dg and all(c.status == pv.OK for c in dg)
    assert calls  # probe actually invoked


@pytest.mark.unit
def test_connectivity_missing_creds_short_circuits_probe():
    def fake_probe(env):
        raise AssertionError("probe must not run without creds")

    report = pv.connectivity_matrix(env={}, probes={"deepgram": fake_probe})
    dg = [c for c in report.cells if c.provider == "deepgram"]
    assert dg and all(c.status == pv.MISSING for c in dg)


@pytest.mark.unit
def test_connectivity_no_probe_is_skipped():
    env = {"SIM_VERIFY_TWILIO_API_KEY": "x"}
    report = pv.connectivity_matrix(env=env, probes={})
    tw = [c for c in report.cells if c.provider == "twilio"]
    assert tw and all(c.status == pv.SKIPPED for c in tw)


@pytest.mark.unit
def test_probe_exception_becomes_failed():
    def boom(env):
        raise ConnectionError("dns")

    # deepgram shape=agent_id needs both API_KEY and AGENT_ID before the probe runs.
    env = {"SIM_VERIFY_DEEPGRAM_API_KEY": "x", "SIM_VERIFY_DEEPGRAM_AGENT_ID": "a"}
    report = pv.connectivity_matrix(env=env, probes={"deepgram": boom})
    dg = [c for c in report.cells if c.provider == "deepgram"]
    assert dg and all(c.status == pv.FAILED for c in dg)
    assert any("ConnectionError" in c.detail for c in dg)


@pytest.mark.unit
def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        pv.verify("nope")


@pytest.mark.unit
def test_report_to_dict_roundtrip():
    d = pv.declared_matrix().to_dict()
    assert d["mode"] == pv.MODE_REGISTRY
    assert isinstance(d["summary"], dict)
    assert d["cells"] and "provider" in d["cells"][0]
