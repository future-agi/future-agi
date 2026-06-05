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
    AgoraOutboundDialer,
    BlandOutboundDialer,
    ElevenLabsOutboundDialer,
    OutboundDialError,
    RetellOutboundDialer,
    TwilioOutboundDialer,
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

    def post(self, url, headers=None, json=None, data=None, auth=None, timeout=None):
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "data": data, "auth": auth}
        )
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
def test_agora_registered_and_resolved():
    assert OUTBOUND_DIALERS["agora"] is AgoraOutboundDialer
    assert isinstance(get_outbound_dialer("agora", "ck:cs"), AgoraOutboundDialer)


@pytest.mark.unit
def test_agora_outbound_shape_basic_auth_and_agent_id(monkeypatch):
    monkeypatch.setenv("AGORA_APP_ID", "app-123")
    fake = _FakeRequests(_FakeResp({"agent_id": "agt_9", "status": "STARTING"}))
    monkeypatch.setattr(mod, "requests", fake)
    dialer = AgoraOutboundDialer(api_key="cust_key:cust_secret")
    out = dialer.create_outbound_call(
        assistant_id="studio-agent-1",
        from_phone_number="+15550000001",
        to_phone_number="+15550000099",
    )
    assert out == {"id": "agt_9"}
    call = fake.calls[0]
    assert "app-123" in call["url"]               # app id in path
    assert call["auth"] == ("cust_key", "cust_secret")  # Basic auth, not header key
    assert call["json"]["sip"]["called_number"] == "+15550000099"
    assert call["json"]["sip"]["caller_id"] == "+15550000001"
    assert call["json"]["properties"]["agent_id"] == "studio-agent-1"


@pytest.mark.unit
def test_agora_requires_app_id(monkeypatch):
    monkeypatch.delenv("AGORA_APP_ID", raising=False)
    monkeypatch.setattr(mod, "requests", _FakeRequests(_FakeResp({})))
    with pytest.raises(OutboundDialError):
        AgoraOutboundDialer(api_key="k:s").create_outbound_call(
            assistant_id="a", from_phone_number="+1", to_phone_number="+2"
        )


@pytest.mark.unit
def test_agora_requires_key_secret_pair(monkeypatch):
    monkeypatch.setenv("AGORA_APP_ID", "app-1")
    with pytest.raises(ValueError):
        AgoraOutboundDialer(api_key="nocolon").create_outbound_call(
            assistant_id="a", from_phone_number="+1", to_phone_number="+2"
        )


@pytest.mark.unit
def test_agora_outbound_url_override(monkeypatch):
    monkeypatch.setenv("AGORA_APP_ID", "app-1")
    monkeypatch.setenv("AGORA_OUTBOUND_CALL_URL", "https://custom.agora/outbound")
    fake = _FakeRequests(_FakeResp({"agent_id": "x"}))
    monkeypatch.setattr(mod, "requests", fake)
    AgoraOutboundDialer(api_key="k:s").create_outbound_call(
        assistant_id="a", from_phone_number="+1", to_phone_number="+2"
    )
    assert fake.calls[0]["url"] == "https://custom.agora/outbound"


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
def test_twilio_outbound_request_shape(monkeypatch):
    fake = _FakeRequests(_FakeResp({"sid": "CA999", "status": "queued"}))
    monkeypatch.setattr(mod, "requests", fake)
    # api_key carries "AccountSid:AuthToken"; assistant_id is a TwiML URL here.
    dialer = TwilioOutboundDialer(api_key="ACsid:tok")
    out = dialer.create_outbound_call(
        assistant_id="https://example.com/twiml",
        from_phone_number="+15551110000", to_phone_number="+15552220000",
    )
    assert out == {"id": "CA999"}
    call = fake.calls[0]
    assert call["url"].endswith("/2010-04-01/Accounts/ACsid/Calls.json")
    assert call["auth"] == ("ACsid", "tok")  # HTTP Basic
    assert call["data"]["To"] == "+15552220000"
    assert call["data"]["From"] == "+15551110000"
    assert call["data"]["Url"] == "https://example.com/twiml"  # URL → Url


@pytest.mark.unit
def test_twilio_application_sid_when_not_a_url(monkeypatch):
    fake = _FakeRequests(_FakeResp({"sid": "CA1"}))
    monkeypatch.setattr(mod, "requests", fake)
    TwilioOutboundDialer(api_key="ACsid:tok").create_outbound_call(
        assistant_id="AP123app", from_phone_number="+1", to_phone_number="+1",
    )
    # Non-URL assistant_id → ApplicationSid, not Url.
    assert fake.calls[0]["data"]["ApplicationSid"] == "AP123app"
    assert "Url" not in fake.calls[0]["data"]


@pytest.mark.unit
def test_twilio_requires_sid_and_token():
    with pytest.raises(ValueError):
        TwilioOutboundDialer(api_key="just-one-value").create_outbound_call(
            assistant_id="x", from_phone_number="+1", to_phone_number="+1"
        )


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
