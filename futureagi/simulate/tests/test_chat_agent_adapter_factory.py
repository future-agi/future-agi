"""Unit tests for the chat agent-under-test factory (TH-5642).

Pins the product-decided routing gate (2026-06-05):
- external HOSTED chat provider (Retell) + assistant_id -> platform drives
  server-side via RetellChatAgentAdapter;
- SDK-capable providers (e.g. LiveKit) or a missing assistant_id -> None, i.e.
  the SDK-push path is preserved (never double-driven).
"""

from types import SimpleNamespace

import pytest

from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from simulate.services.chat_agent_adapter_factory import (
    create_chat_agent_adapter,
    is_external_hosted_chat,
)
from simulate.services.retell_chat_agent_adapter import RetellChatAgentAdapter

TEXT = AgentDefinition.AgentTypeChoices.TEXT
VOICE = AgentDefinition.AgentTypeChoices.VOICE


def _ad(provider, agent_type=TEXT, assistant_id="agent_chat_1", api_key="k", credentials=None):
    return SimpleNamespace(
        id="ad-1",
        provider=provider,
        agent_type=agent_type,
        assistant_id=assistant_id,
        api_key=api_key,
        credentials=credentials,
    )


def _run_test(source_type, agent_definition=None):
    return SimpleNamespace(source_type=source_type, agent_definition=agent_definition)


@pytest.mark.unit
def test_prompt_source_delegates_to_prompt_adapter(monkeypatch):
    sentinel = object()
    called = {}

    def fake_create(run_test, org, ws, vals):
        called["args"] = (run_test, org, ws, vals)
        return sentinel

    # Patched at the source module — the factory imports it lazily inside the call.
    import simulate.services.prompt_based_agent_adapter as pba

    monkeypatch.setattr(pba, "create_adapter_from_run_test", fake_create)
    rt = _run_test(RunTest.SourceTypes.PROMPT)
    out = create_chat_agent_adapter(rt, "org-1", "ws-1", {"v": 1})
    assert out is sentinel
    assert called["args"] == (rt, "org-1", "ws-1", {"v": 1})


@pytest.mark.unit
def test_retell_hosted_chat_drives_server_side():
    rt = _run_test(RunTest.SourceTypes.AGENT_DEFINITION, _ad("retell"))
    adapter = create_chat_agent_adapter(rt, "org-1")
    assert isinstance(adapter, RetellChatAgentAdapter)
    assert adapter.agent_id == "agent_chat_1"
    assert adapter._api_key == "k"


@pytest.mark.unit
def test_livekit_text_agent_stays_sdk_push():
    # LiveKit chat agents run the customer's own code -> SDK-push, not server-side.
    rt = _run_test(RunTest.SourceTypes.AGENT_DEFINITION, _ad("livekit"))
    assert create_chat_agent_adapter(rt, "org-1") is None


@pytest.mark.unit
def test_missing_assistant_id_stays_sdk_push():
    rt = _run_test(RunTest.SourceTypes.AGENT_DEFINITION, _ad("retell", assistant_id=""))
    assert create_chat_agent_adapter(rt, "org-1") is None


@pytest.mark.unit
def test_voice_agent_is_not_hosted_chat():
    rt = _run_test(RunTest.SourceTypes.AGENT_DEFINITION, _ad("retell", agent_type=VOICE))
    assert create_chat_agent_adapter(rt, "org-1") is None


@pytest.mark.unit
def test_is_external_hosted_chat_predicate():
    assert is_external_hosted_chat(_ad("retell"))
    assert not is_external_hosted_chat(_ad("retell", agent_type=VOICE))
    assert not is_external_hosted_chat(_ad("retell", assistant_id=""))
    assert not is_external_hosted_chat(_ad("livekit"))
    assert not is_external_hosted_chat(_ad("vapi"))  # voice bridge, not hosted chat here
    assert not is_external_hosted_chat(None)


@pytest.mark.unit
def test_api_key_prefers_encrypted_credentials():
    creds = SimpleNamespace(get_api_key=lambda: "decrypted-secret")
    rt = _run_test(
        RunTest.SourceTypes.AGENT_DEFINITION,
        _ad("retell", api_key="plain-fallback", credentials=creds),
    )
    adapter = create_chat_agent_adapter(rt, "org-1")
    assert adapter._api_key == "decrypted-secret"


@pytest.mark.unit
def test_api_key_falls_back_to_plain_field_when_creds_blank():
    creds = SimpleNamespace(get_api_key=lambda: "")
    rt = _run_test(
        RunTest.SourceTypes.AGENT_DEFINITION,
        _ad("retell", api_key="plain-fallback", credentials=creds),
    )
    adapter = create_chat_agent_adapter(rt, "org-1")
    assert adapter._api_key == "plain-fallback"
