"""Integration test: PromptBasedAgentAdapter drives the mock-tool loop (TH-5642).

Mocks _call_llm (so no RunPrompt/LLM/DB), verifying the adapter feeds the agent's
tool calls back with scenario-aligned mocks and returns the final content.
"""

from types import SimpleNamespace

import pytest

from simulate.services.prompt_based_agent_adapter import PromptBasedAgentAdapter


def _adapter(mock_tool_returns):
    # Build without __init__/_load_config (which needs a real PromptVersion).
    a = object.__new__(PromptBasedAgentAdapter)
    a.model = "gpt-4o-mini"
    a.tools = [{"type": "function", "function": {"name": "get_inventory"}}]
    a.tool_choice = "auto"
    a.temperature = 0.7
    a.frequency_penalty = 0.0
    a.presence_penalty = 0.0
    a.max_tokens = 800
    a.top_p = 1.0
    a.base_messages = [{"role": "system", "content": "You are a store agent."}]
    a.variable_values = {}
    a.organization_id = "org-1"
    a.workspace_id = None
    a.prompt_version = SimpleNamespace(id="pv-1")
    a.mock_tool_returns = mock_tool_returns
    return a


@pytest.mark.unit
def test_adapter_serves_mock_tool_then_returns_content(monkeypatch):
    a = _adapter({"get_inventory": "3 scooters in stock"})
    calls = []

    def fake_call_llm(messages):
        calls.append(list(messages))
        if len(calls) == 1:
            # The agent calls a tool.
            return "ignored-tool-json", {
                "metadata": {
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "get_inventory", "arguments": "{}"}}
                    ],
                    "usage": {},
                }
            }
        # After seeing the mock result, it answers.
        return "We have 3 scooters in stock.", {
            "metadata": {"tool_calls": [], "usage": {"total_tokens": 20}},
            "model": "gpt-4o-mini",
        }

    monkeypatch.setattr(a, "_call_llm", fake_call_llm)

    result = a.generate_response([{"role": "user", "content": "any scooters?"}])

    assert result["content"] == "We have 3 scooters in stock."
    assert result["usage"]["total_tokens"] == 20
    assert len(calls) == 2  # one tool round, then the final answer
    # The second LLM call saw the assistant tool_call + the mock tool result.
    second = calls[1]
    assert any(m.get("role") == "tool" and m.get("content") == "3 scooters in stock"
               for m in second)


@pytest.mark.unit
def test_factory_passes_mock_tool_returns_from_metadata(monkeypatch):
    import simulate.services.prompt_based_agent_adapter as mod

    captured = {}

    def fake_adapter(**kwargs):
        captured.update(kwargs)
        return "adapter"

    monkeypatch.setattr(mod, "PromptBasedAgentAdapter", fake_adapter)

    run_test = SimpleNamespace(
        source_type="prompt", prompt_version=SimpleNamespace(id="pv"),
        id="rt-1", metadata={"mock_tool_returns": {"get_x": "Y"}},
    )
    mod.create_adapter_from_run_test(run_test, organization_id="org")
    assert captured["mock_tool_returns"] == {"get_x": "Y"}


@pytest.mark.unit
def test_adapter_without_mocks_is_single_call(monkeypatch):
    a = _adapter({})  # no mock tool returns → single LLM call, no loop
    calls = []

    def fake_call_llm(messages):
        calls.append(messages)
        return "Hi, how can I help?", {"metadata": {"usage": {"total_tokens": 5}}}

    monkeypatch.setattr(a, "_call_llm", fake_call_llm)
    result = a.generate_response([{"role": "user", "content": "hello"}])
    assert result["content"] == "Hi, how can I help?"
    assert len(calls) == 1
