from simulate.services.phone_telephony import platform_phone_telephony


def test_alk_phone_uses_existing_agent_definition_telephony(settings, monkeypatch):
    settings.LIVEKIT_URL = "wss://livekit.example.test"
    settings.LIVEKIT_API_KEY = "test-key"
    settings.LIVEKIT_API_SECRET = "test-secret"
    settings.LIVEKIT_OUTBOUND_TRUNK_ID = "ST_existing"
    settings.PSTN_CALLER_NUMBER = "+14155550123"
    monkeypatch.setenv("SIP_OUTBOUND_TRUNK_ID", "ST_legacy")
    monkeypatch.setenv("SIP_OUTBOUND_FROM_NUMBER", "+14155550000")

    assert platform_phone_telephony() == {
        "LIVEKIT_URL": "wss://livekit.example.test",
        "LIVEKIT_API_KEY": "test-key",
        "LIVEKIT_API_SECRET": "test-secret",
        "SIP_OUTBOUND_TRUNK_ID": "ST_existing",
        "SIP_OUTBOUND_FROM_NUMBER": "+14155550123",
    }


def test_alk_phone_preserves_legacy_aliases(settings, monkeypatch):
    settings.LIVEKIT_OUTBOUND_TRUNK_ID = ""
    settings.PSTN_CALLER_NUMBER = ""
    monkeypatch.delenv("LIVEKIT_OUTBOUND_TRUNK_ID", raising=False)
    monkeypatch.delenv("PSTN_CALLER_NUMBER", raising=False)
    monkeypatch.setenv("SIP_OUTBOUND_TRUNK_ID", "ST_legacy")
    monkeypatch.setenv("SIP_OUTBOUND_FROM_NUMBER", "+14155550000")

    values = platform_phone_telephony()
    assert values["SIP_OUTBOUND_TRUNK_ID"] == "ST_legacy"
    assert values["SIP_OUTBOUND_FROM_NUMBER"] == "+14155550000"
