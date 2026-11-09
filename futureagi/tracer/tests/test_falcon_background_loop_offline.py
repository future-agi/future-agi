"""Socket-blocked full AgentLoop + worker timeout wrapper capability tests."""

import asyncio
import json
import re
import runpy
import time
import traceback
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

support = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_background_capabilities_offline.py"))
)
definitions, module, nested = (support[n] for n in ("definitions", "module", "nested"))


class LoopTests(unittest.TestCase):
    setUp = support["CapabilityTests"].setUp
    install = support["CapabilityTests"].install
    tools = support["CapabilityTests"].tools

    def exercise(self, *, revoke=False, stall=False):
        base = self.tools()
        effects = []
        self.context.interactive_falcon_socket = False
        self.context.user.email = "offline"
        self.context.workspace.name = "offline"
        registered = {}

        def execute(tool, params, context):
            effects.append(tool.name)
            if revoke:
                self.active = False
            return base["ToolResult"](content="recorded")

        for name, identity in support["ALLOWED"].items():
            module_name, class_name = identity.rsplit(".", 1)
            registered[name] = type(
                class_name,
                (base["BaseTool"],),
                {
                    "__module__": module_name,
                    "name": name,
                    "description": "offline",
                    "input_model": SimpleNamespace(
                        model_validate=lambda p: p, model_json_schema=lambda: {}
                    ),
                    "execute": execute,
                },
            )()
        forbidden = SimpleNamespace(
            name="evaluate_with_agent",
            description="forbidden",
            run=Mock(side_effect=AssertionError("dispatched nested orchestrator")),
        )
        registered[forbidden.name] = forbidden
        self.modules.update(
            {
                "accounts.models.user": module("user", User=Mock()),
                "ee.falcon_ai.mcp_tools": module(
                    "mcp", MCPToolWrapper=type("MCP", (), {})
                ),
            }
        )
        self.install()
        turns = []

        async def stream(*args, **kwargs):
            turns.append(1)
            if stall:
                await asyncio.sleep(0.15)
            if len(turns) == 1:
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "denied",
                                        "function": {
                                            "name": forbidden.name,
                                            "arguments": "{}",
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "id": "allowed",
                                        "function": {
                                            "name": "submit_trace_scores",
                                            "arguments": "{}",
                                        },
                                    },
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            else:
                yield {
                    "choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}]
                }

        async def compact(conversation, history, client):
            return conversation, history

        cm = SimpleNamespace(
            compact_if_needed=compact,
            prepare_messages=lambda *a, **k: [],
            truncate_messages=lambda m: m,
            truncate_result=lambda r: r,
        )
        ns = definitions(
            "ee/falcon_ai/agent.py",
            {"AgentLoop"},
            {
                "asyncio": asyncio,
                "json": json,
                "re": re,
                "traceback": traceback,
                "uuid": uuid,
                "logger": Mock(),
                "database_sync_to_async": support["support"]["db_async"],
                "ToolContext": object,
                "ToolResult": base["ToolResult"],
                "authenticated_tool_scope": lambda ctx: nullcontext(),
                "FalconLLMClient": lambda: SimpleNamespace(stream_with_retry=stream),
                "PromptBuilder": lambda: Mock(build=lambda **k: "prompt"),
                "ContextManager": lambda: cm,
                "tool_registry": SimpleNamespace(get=registered.get),
                "detect_mode": lambda *a: "tracing",
                "load_tools_for_mode": Mock(
                    side_effect=AssertionError("loaded broad mode tools")
                ),
                "filter_tools_for_message": lambda tools, *a, **k: tools,
                "ManagedServiceError": type("ManagedServiceError", (Exception,), {}),
                "SELF_CORRECTION_THRESHOLD": 3,
                "REPETITION_THRESHOLD": 3,
                "_extract_primary_entity_id": lambda s: None,
            },
        )
        conversation = SimpleNamespace(
            id="conversation", title="Auto-analysis", context_summary=""
        )
        agent = ns["AgentLoop"](self.context, conversation)
        agent._load_mcp_tools = AsyncMock(
            side_effect=AssertionError("discovered connectors")
        )
        agent._load_memories = AsyncMock(return_value=[])
        if stall:
            self.guard.deadline = time.monotonic() + 0.04
        run = nested(
            "tracer/tasks/error_analysis.py",
            "run",
            {
                "asyncio": asyncio,
                "agent": agent,
                "trace_id": "trace",
                "skill": None,
                "logging_callback": AsyncMock(),
                "guard": self.guard,
                "witness": SimpleNamespace(failed=False),
            },
        )
        try:
            result = asyncio.run(run())
        except self.Aborted as exc:
            result = {"code": exc.code}
        agent._load_mcp_tools.assert_not_called()
        forbidden.run.assert_not_called()
        self.assertEqual({tool.name for tool in agent.tools}, set(support["ALLOWED"]))
        return result, effects, len(turns)

    def test_full_loop_denies_registry_escape_and_keeps_allowed_analysis(self):
        result, effects, turns = self.exercise()
        self.assertEqual(result["content"], "Done")
        self.assertEqual(effects, ["submit_trace_scores"])
        self.assertEqual(turns, 2)

    def test_full_loop_terminal_stop_prevents_next_model_turn(self):
        result, effects, turns = self.exercise(revoke=True)
        self.assertEqual(result.get("code"), "analysis_access_revoked")
        self.assertEqual(effects, ["submit_trace_scores"])
        self.assertEqual(turns, 1)

    def test_worker_timeout_still_catches_baseexception_abort(self):
        result, effects, turns = self.exercise(stall=True)
        self.assertEqual(result.get("code"), "analysis_deadline_exceeded")
        self.assertEqual(effects, [])
        self.assertEqual(turns, 1)


if __name__ == "__main__":
    unittest.main()
