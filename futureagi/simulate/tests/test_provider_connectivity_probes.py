"""Tests for provider connectivity probes (TH-5642).

HTTP is mocked — these assert the probe contract (endpoint, auth shape, status mapping)
without network. Real validation happens when the harness runs with live creds.
"""

import pytest

from simulate.services import provider_connectivity_probes as p


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


def _patch_get(monkeypatch, capture):
    def fake_get(url, headers=None, params=None, auth=None):
        capture.update(url=url, headers=headers or {}, params=params, auth=auth)
        return _Resp(capture.get("status", 200))

    monkeypatch.setattr(p, "_http_get", fake_get)


@pytest.mark.unit
def test_vapi_probe_ok_and_auth_header(monkeypatch):
    cap = {"status": 200}
    _patch_get(monkeypatch, cap)
    ok, detail = p.vapi_probe({"SIM_VERIFY_VAPI_API_KEY": "k"})
    assert ok
    assert cap["url"] == "https://api.vapi.ai/assistant"
    assert cap["headers"]["Authorization"] == "Bearer k"


@pytest.mark.unit
def test_vapi_missing_key():
    ok, detail = p.vapi_probe({})
    assert not ok and "not set" in detail


@pytest.mark.unit
def test_retell_rejected_credentials(monkeypatch):
    cap = {"status": 401}
    _patch_get(monkeypatch, cap)
    ok, detail = p.retell_probe({"SIM_VERIFY_RETELL_API_KEY": "bad"})
    assert not ok and "rejected credentials" in detail


@pytest.mark.unit
def test_bland_uses_raw_authorization_header(monkeypatch):
    cap = {"status": 200}
    _patch_get(monkeypatch, cap)
    ok, _ = p.bland_probe({"SIM_VERIFY_BLAND_API_KEY": "k"})
    assert ok
    # Bland uses the raw key, NOT a Bearer prefix.
    assert cap["headers"]["authorization"] == "k"


@pytest.mark.unit
def test_twilio_parses_sid_token_basic_auth(monkeypatch):
    cap = {"status": 200}
    _patch_get(monkeypatch, cap)
    ok, _ = p.twilio_probe({"SIM_VERIFY_TWILIO_API_KEY": "ACxxx:tok"})
    assert ok
    assert cap["auth"] == ("ACxxx", "tok")
    assert "ACxxx.json" in cap["url"]


@pytest.mark.unit
def test_twilio_requires_colon():
    ok, detail = p.twilio_probe({"SIM_VERIFY_TWILIO_API_KEY": "nocolon"})
    assert not ok and "AccountSid:AuthToken" in detail


@pytest.mark.unit
def test_agora_parses_key_secret_basic_auth(monkeypatch):
    cap = {"status": 200}
    _patch_get(monkeypatch, cap)
    ok, _ = p.agora_probe({"SIM_VERIFY_AGORA_API_KEY": "cust:secret"})
    assert ok
    assert cap["auth"] == ("cust", "secret")


@pytest.mark.unit
def test_agora_requires_colon():
    ok, detail = p.agora_probe({"SIM_VERIFY_AGORA_API_KEY": "nocolon"})
    assert not ok and "CustomerKey:CustomerSecret" in detail


@pytest.mark.unit
def test_classify_endpoint_error_is_verbatim(monkeypatch):
    cap = {"status": 404}
    _patch_get(monkeypatch, cap)
    ok, detail = p.vapi_probe({"SIM_VERIFY_VAPI_API_KEY": "k"})
    assert not ok and "404" in detail and "rejected" not in detail


@pytest.mark.unit
def test_default_probes_registered():
    from simulate.services import provider_verification as pv

    probes = pv.default_connectivity_probes()
    assert {
        "deepgram", "elevenlabs", "vapi", "retell", "bland", "twilio", "agora",
        "livekit_bridge", "pipecat",
    } <= set(probes)


@pytest.mark.unit
def test_livekit_probe_reports_missing_env():
    probe = p.make_livekit_probe("livekit_bridge")
    ok, detail = probe({})
    assert not ok
    assert "SIM_VERIFY_LIVEKIT_BRIDGE_LIVEKIT_URL" in detail
    assert "SIM_VERIFY_LIVEKIT_BRIDGE_LIVEKIT_API_SECRET" in detail


@pytest.mark.unit
def test_livekit_probe_reads_provider_prefixed_vars():
    # pipecat reuses the LiveKit probe under its own env prefix.
    probe = p.make_livekit_probe("pipecat")
    ok, detail = probe({"SIM_VERIFY_PIPECAT_LIVEKIT_URL": "wss://x"})
    assert not ok
    assert "SIM_VERIFY_PIPECAT_LIVEKIT_API_KEY" in detail
