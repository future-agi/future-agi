"""Offline fixture executing the real AgentLoop with a synthetic interrupted stream."""

import asyncio
import json
import runpy
import traceback
import uuid
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

support = runpy.run_path(
    str(Path(__file__).with_name("test_analysis_legacy_snapshot_offline.py"))
)
definition, module = support["definition"], support["module"]


def stream_failure_runner(context, submitted):
    tool = SimpleNamespace(name="submit_trace_finding", description="submit finding")
    ns = {
        "asyncio": asyncio,
        "uuid": uuid,
        "json": json,
        "traceback": traceback,
        "logger": Mock(),
        "ManagedServiceError": type("ManagedServiceError", (Exception,), {}),
        "detect_mode": lambda *a: "tracing",
        "tool_registry": {tool.name: tool},
        "filter_tools_for_message": lambda tools, *a, **k: tools,
        "SELF_CORRECTION_THRESHOLD": 3,
        "REPETITION_THRESHOLD": 3,
    }
    run = definition("ee/falcon_ai/agent.py", "run", ns, "AgentLoop")
    inner_run = definition("ee/falcon_ai/agent.py", "_run", ns, "AgentLoop")
    calls = 0

    async def stream(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic provider stream interruption")
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call",
                                "function": {"name": tool.name, "arguments": "{}"},
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }

    async def execute(*args):
        submitted.append("unscored finding")
        return SimpleNamespace(content="submitted", is_error=False)

    conversation = SimpleNamespace(id="c", context_summary="")
    agent = SimpleNamespace(
        tool_context=context,
        conversation=conversation,
        MAX_ITERATIONS=3,
        MAX_TURN_TOKENS=10000,
        total_input_tokens=0,
        total_output_tokens=0,
        llm_client=SimpleNamespace(stream_with_retry=stream),
        context_manager=SimpleNamespace(
            compact_if_needed=AsyncMock(return_value=(conversation, [])),
            prepare_messages=lambda *a, **k: [],
            truncate_messages=lambda rows: rows,
            truncate_result=lambda x: x,
        ),
        _apply_precontent_to_last_user_message=lambda *a: None,
        _tools_as_openai_format=lambda: [],
        _execute_tool=execute,
        _build_result_summary=lambda *a: "submitted",
        _clean_error_message=lambda text: text,
        _maybe_generate_title=AsyncMock(return_value=None),
    )

    agent._run = MethodType(inner_run, agent)

    async def invoke(**kwargs):
        kwargs["system_prompt_override"] = "offline fixture"
        kwargs["tools_override"] = [tool]
        return await run(agent, **kwargs)

    agent.run = invoke
    invoke.agent = agent
    return invoke, module(
        "background",
        background_tools=lambda *a: [tool],
        background_tool_denial=lambda *a: None,
    )
