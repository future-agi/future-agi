"""Unit tests for mock scenario-aligned tool returns (TH-5642).

The tool loop drives an INJECTED llm_call, so the deterministic-tool-path logic is
fully testable without an LLM.
"""

import pytest

from simulate.services.mock_tools import (
    resolve_mock_tool_return,
    run_mock_tool_loop,
)


@pytest.mark.unit
def test_resolve_fixed_result():
    assert resolve_mock_tool_return({"get_weather": "Sunny"}, "get_weather", "{}") == "Sunny"


@pytest.mark.unit
def test_resolve_args_keyed_and_default():
    spec = {"get_weather": {"London": "Rainy", "default": "Clear"}}
    assert resolve_mock_tool_return(spec, "get_weather", '{"city": "London"}') == "Rainy"
    assert resolve_mock_tool_return(spec, "get_weather", '{"city": "Paris"}') == "Clear"


@pytest.mark.unit
def test_resolve_unconfigured_tool_falls_back():
    assert resolve_mock_tool_return({}, "anything", "{}") == "ok"
    assert resolve_mock_tool_return(None, "anything", "{}") == "ok"


@pytest.mark.unit
def test_resolve_non_string_result_is_json():
    # A list/number fixed result is JSON-encoded.
    assert resolve_mock_tool_return({"t": [1, 2]}, "t", "{}") == "[1, 2]"
    # A bare dict is args-keyed, so a JSON-OBJECT result uses the "default" key.
    assert resolve_mock_tool_return({"t": {"default": {"x": 1}}}, "t", "{}") == '{"x": 1}'


@pytest.mark.unit
def test_loop_serves_mocks_and_continues():
    # The fake agent calls a tool on turn 1, then produces content after seeing the
    # mock result.
    calls = []

    def fake_llm(messages):
        calls.append(list(messages))
        # First call → a tool call; second call → final content.
        if len(calls) == 1:
            return "", [{"id": "c1", "function": {"name": "get_inventory",
                                                  "arguments": '{"q": "scooter"}'}}]
        return "We have 3 scooters in stock.", []

    content, rounds = run_mock_tool_loop(
        fake_llm,
        [{"role": "user", "content": "do you have scooters?"}],
        {"get_inventory": "3 in stock"},
    )
    assert content == "We have 3 scooters in stock."
    assert rounds == 1
    # The 2nd LLM call saw the appended assistant tool_call + tool result messages.
    second = calls[1]
    assert second[-1] == {
        "role": "tool", "tool_call_id": "c1", "name": "get_inventory",
        "content": "3 in stock",
    }
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"]


@pytest.mark.unit
def test_loop_no_tools_returns_immediately():
    content, rounds = run_mock_tool_loop(
        lambda m: ("hello", []), [{"role": "user", "content": "hi"}], {}
    )
    assert content == "hello"
    assert rounds == 0


@pytest.mark.unit
def test_loop_respects_max_iters():
    # An agent that calls tools forever stops at max_iters.
    def always_tool(messages):
        return "still thinking", [{"id": "c", "function": {"name": "t", "arguments": "{}"}}]

    content, rounds = run_mock_tool_loop(
        always_tool, [{"role": "user", "content": "x"}], {"t": "r"}, max_iters=3
    )
    assert rounds == 3
    assert content == "still thinking"
