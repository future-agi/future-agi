from unittest.mock import Mock, patch

from simulate.services.harness_credential_probes import probe_provider_target


@patch("simulate.services.harness_credential_probes.requests.get")
def test_retell_target_probe_reports_unknown_agent_id(get):
    get.return_value = Mock(status_code=404)

    result = probe_provider_target(
        "retell", "not-a-real-agent", {"RETELL_API_KEY": "valid-key"}
    )

    assert result is not None
    assert result.ok is False
    assert result.provider == "retell_target"
    assert result.message == (
        "Retell voice agent ID was not found or is not accessible with RETELL_API_KEY"
    )
    assert get.call_args.args[0].endswith("/get-agent/not-a-real-agent")


@patch("simulate.services.harness_credential_probes.requests.get")
def test_retell_chat_target_probe_uses_chat_agent_endpoint(get):
    get.return_value = Mock(status_code=200)

    result = probe_provider_target(
        "retell_chat", "chat-agent", {"RETELL_API_KEY": "valid-key"}
    )

    assert result is not None and result.ok is True
    assert get.call_args.args[0].endswith("/get-chat-agent/chat-agent")


@patch("simulate.services.harness_credential_probes.requests.get")
def test_vapi_target_probe_reports_unknown_assistant_id(get):
    get.return_value = Mock(status_code=404)

    result = probe_provider_target(
        "vapi", "missing/assistant", {"VAPI_API_KEY": "valid-key"}
    )

    assert result is not None and result.ok is False
    assert "Vapi assistant ID was not found" in result.message
    assert get.call_args.args[0].endswith("/assistant/missing%2Fassistant")


def test_target_probe_waits_for_the_dedicated_provider_key():
    assert probe_provider_target("retell", "agent", {}) is None
