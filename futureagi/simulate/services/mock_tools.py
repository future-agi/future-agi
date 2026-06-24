"""Mock scenario-aligned tool returns for prompt-based simulation (TH-5642).

Competitors (Cekura's headline auto-setup) let a scenario inject deterministic tool
return values so the agent-under-test's tool-call paths are exercised without a live
backend. The prompt-based agent (PromptBasedAgentAdapter) runs the LLM itself, so we
can intercept its tool calls and feed back configured mocks instead of executing.

This module is the pure core: a resolver (tool name + args -> mock result) and a
tool loop that drives an injected LLM-call function. The integration (the adapter
provides a real LLM call that surfaces tool_calls) sits on top — so the loop logic is
fully unit-testable without an LLM.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

# A mock config maps a tool name to either a fixed result, or an args-keyed dict of
# results: {"get_weather": "Sunny, 22C"} or {"get_weather": {"London": "Rainy"}}.
MockToolReturns = dict[str, Any]

_DEFAULT_RESULT = "ok"


def resolve_mock_tool_return(
    mock_returns: MockToolReturns | None, tool_name: str, arguments: Any
) -> str:
    """Resolve the mock result for a tool call. Falls back to a generic result so a
    tool with no configured mock still completes the path deterministically."""
    if not mock_returns or tool_name not in mock_returns:
        return _DEFAULT_RESULT
    spec = mock_returns[tool_name]
    if isinstance(spec, dict):
        # Args-keyed: match on a stringified arg value (first match) or "default".
        args = arguments
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments)
            except (ValueError, TypeError):
                args = {}
        if isinstance(args, dict):
            for v in args.values():
                if str(v) in spec:
                    return _as_text(spec[str(v)])
        if "default" in spec:
            return _as_text(spec["default"])
        return _DEFAULT_RESULT
    return _as_text(spec)


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def run_mock_tool_loop(
    llm_call: Callable[[list[dict[str, Any]]], tuple[str, list[dict[str, Any]]]],
    messages: list[dict[str, Any]],
    mock_returns: MockToolReturns | None,
    *,
    max_iters: int = 5,
) -> tuple[str, int]:
    """Drive an agent's tool calls with mock returns until it produces final content.

    ``llm_call(messages) -> (content, tool_calls)`` where tool_calls is a list of
    ``{"id","function":{"name","arguments"}}`` (or empty). On each tool round we
    append the assistant tool-call message + a ``tool`` result message per call
    (resolved from ``mock_returns``) and re-call, up to ``max_iters``.

    Returns ``(final_content, tool_rounds)``.
    """
    convo = list(messages)
    rounds = 0
    content = ""
    for _ in range(max_iters):
        content, tool_calls = llm_call(convo)
        if not tool_calls:
            return content, rounds
        rounds += 1
        convo = convo + [{"role": "assistant", "tool_calls": tool_calls}]
        for tc in tool_calls:
            fn = tc.get("function") or {}
            result = resolve_mock_tool_return(
                mock_returns, fn.get("name") or "", fn.get("arguments")
            )
            convo = convo + [
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id") or "",
                    "name": fn.get("name") or "",
                    "content": result,
                }
            ]
    # Hit the cap with tools still pending — return the last content.
    return content, rounds
