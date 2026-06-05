"""Unit tests for the per-provider outbound dialers (TH-5642).

Mocked REST (no network; placing a real call costs money + dials a live number).
Pins the documented Retell/Bland outbound-call request shapes and the registry that
replaces the Vapi-hardcoded user-side dialer.
"""

import json

import pytest

from simulate.services import outbound_dialer as mod
from simulate.services.outbound_dialer import (
    OUTBOUND_DIALERS,
    BlandOutboundDialer,
    ElevenLabsOutboundDialer,
    OutboundDialError,
    RetellOutboundDialer,
    get_outbound_dialer,
)


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeRequests:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._resp


@pytest.mark.unit
def test_registry_has_non_vapi_dialers():
    assert OUTBOUND_DIALERS["retell"] is RetellOutboundDialer
    assert OUTBOUND_DIALERS["bland"] is BlandOutboundDialer


@pytest.mark.unit
def test_get_outbound_dialer_falls_back_for_vapi():
    # Vapi keeps its existing engine path → no dialer here (None = fall back).
    assert get_outbound_dialer("vapi", "k") is None
    assert get_outbound_dialer("unknown", "k") is None
    assert isinstance(get_outbound_dialer("retell", "k"), RetellOutboundDialer)


@pytest.mark.unit
def test_requires_api_key():
    with pytest.raises(ValueError):
        RetellOutboundDialer(api_key="")


@pytest.mark.unit
def test_retell_outbound_request_shape(monkeypatch):
    fake = _FakeRequests(_FakeResp({"call_id": "call_123", "agent_id": "agent_x"}))
    monkeypatch.setattr(mod, "requests", fake)
    dialer = RetellOutboundDialer(api_key="rt-key")
    out = dialer.create_outbound_call(
        assistant_id="agent_x", from_phone_number="+15551110000",
        to_phone_number="+15552220000", metadata={"call_id": "abc"},
    )
    assert out == {"id": "call_123"}
    call = fake.calls[0]
    assert call["url"].endswith("/v2/create-phone-call")
    assert call["headers"]["Authorization"] == "Bearer rt-key"
    assert call["json"] == {
        "from_number": "+15551110000",
        "to_number": "+15552220000",
        "override_agent_id": "agent_x",
        "metadata": {"call_id": "abc"},
    }


@pytest.mark.unit
def test_bland_outbound_request_shape(monkeypatch):
    fake = _FakeRequests(_FakeResp({"status": "success", "call_id": "bland_1"}))
    monkeypatch.setattr(mod, "requests", fake)
    dialer = BlandOutboundDialer(api_key="bl-key")
    out = dialer.create_outbound_call(
        assistant_id="pathway_1", from_phone_number="+15551110000",
        to_phone_number="+15552220000",
    )
    assert out == {"id": "bland_1"}
    call = fake.calls[0]
    assert call["url"].endswith("/v1/calls")
    # Bland uses the raw key as Authorization (no Bearer).
    assert call["headers"]["Authorization"] == "bl-key"
    assert call["json"]["phone_number"] == "+15552220000"
    assert call["json"]["pathway_id"] == "pathway_1"
    assert call["json"]["from"] == "+15551110000"


@pytest.mark.unit
def test_elevenlabs_outbound_request_shape(monkeypatch):
    fake = _FakeRequests(_FakeResp(
        {"success": True, "conversation_id": "conv_9", "callSid": "CA123"}))
    monkeypatch.setattr(mod, "requests", fake)
    dialer = ElevenLabsOutboundDialer(api_key="xi-key")
    out = dialer.create_outbound_call(
        assistant_id="agent_el", from_phone_number="phnum_id_1",
        to_phone_number="+15552220000",
    )
    # call id = conversation_id.
    assert out == {"id": "conv_9"}
    call = fake.calls[0]
    assert call["url"].endswith("/v1/convai/twilio/outbound-call")
    # ElevenLabs uses xi-api-key, and from_phone_number carries the phone_number_id.
    assert call["headers"]["xi-api-key"] == "xi-key"
    assert call["json"]["agent_id"] == "agent_el"
    assert call["json"]["agent_phone_number_id"] == "phnum_id_1"
    assert call["json"]["to_number"] == "+15552220000"


@pytest.mark.unit
def test_elevenlabs_registered_for_both_spellings():
    assert OUTBOUND_DIALERS["elevenlabs"] is ElevenLabsOutboundDialer
    assert OUTBOUND_DIALERS["eleven_labs"] is ElevenLabsOutboundDialer
    assert isinstance(get_outbound_dialer("eleven_labs", "k"), ElevenLabsOutboundDialer)


@pytest.mark.unit
def test_http_error_raises(monkeypatch):
    fake = _FakeRequests(_FakeResp({"error": "bad number"}, status_code=422))
    monkeypatch.setattr(mod, "requests", fake)
    with pytest.raises(OutboundDialError):
        RetellOutboundDialer(api_key="k").create_outbound_call(
            assistant_id="a", from_phone_number="+1", to_phone_number="+1"
        )


@pytest.mark.unit
def test_missing_call_id_raises(monkeypatch):
    fake = _FakeRequests(_FakeResp({"status": "queued"}))  # no call_id
    monkeypatch.setattr(mod, "requests", fake)
    with pytest.raises(OutboundDialError):
        BlandOutboundDialer(api_key="k").create_outbound_call(
            assistant_id="p", from_phone_number="", to_phone_number="+1"
        )
