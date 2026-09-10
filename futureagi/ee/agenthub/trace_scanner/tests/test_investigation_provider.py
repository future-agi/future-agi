from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ee.agenthub.trace_scanner import investigation_provider as transport


def test_gateway_metering_and_reasoning_configuration(monkeypatch):
    monkeypatch.setattr(transport, "get_gateway_client", lambda: object())
    message = SimpleNamespace(content='{"ok":true}', tool_calls=[])
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    )
    call = Mock(return_value=SimpleNamespace(response=response, cost_usd=0.01))
    monkeypatch.setattr(transport, "call_llm_raw", call)
    provider = transport.InvestigationProvider()
    for _ in range(2):
        reply = provider([], [], 16384)
        assert reply.usage["total_tokens"] == 30
    assert provider.total_cost_usd == 0.02
    assert provider.token_usage["total_tokens"] == 60
    assert call.call_args.kwargs["max_completion_tokens"] == 16384
    assert "tool_choice" not in call.call_args.kwargs
    assert "extra_body" not in call.call_args.kwargs


def test_missing_gateway_is_not_a_content_only_fallback(monkeypatch):
    monkeypatch.setattr(transport, "get_gateway_client", lambda: None)
    with pytest.raises(RuntimeError, match="configured gateway"):
        transport.InvestigationProvider()([], [], 16384)
