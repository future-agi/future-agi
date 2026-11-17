"""Actual allowlisted tool + AgentLoop + raw worker, no sockets."""

import json
import runpy
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
b = runpy.run_path(str(HERE / "test_analysis_completion_witness_offline.py"))
f = runpy.run_path(str(HERE / "analysis_llm_fixture.py"))
r = runpy.run_path(str(HERE / "analysis_library_retry_fixture.py"))


class ToolCalls(b["FalconCompletion"]):
    def probe(
        self,
        mode,
        limit=4,
        *,
        report=None,
        prepare=None,
        success=None,
        sends_expected=1,
    ):
        # Each subcase represents a new admitted generation, including deadline.
        self.guard = self.Guard("project", "user", "org", "workspace", 2)
        self.context.background_analysis = self.guard
        invoke, background, submitted = self.configure("success")
        agent = invoke.agent
        agent.MAX_ITERATIONS = limit
        sends, clients = [], []

        def send(route=None, kwargs=None, **options):
            sends.append(1)
            if mode == "exception" or (mode == "library-retry" and len(sends) == 1):
                raise RuntimeError("synthetic nested transport failure")
            reason = (
                "stop" if mode in ("success", "library-retry", "bad-json") else mode
            )
            return f["response"](
                "not json"
                if mode == "bad-json"
                else report
                if report is not None
                else json.dumps(
                    {
                        "sub_flows": [
                            {
                                "name": "Answer",
                                "spans": ["span"],
                                "summary": "The step answered the query.",
                                "flags": [],
                            }
                        ],
                        "trace_overview": "The trace answers a query.",
                    }
                ),
                reason,
            )

        completion = r["real_retry_wrapper"](send, SimpleNamespace(), 2)
        module = b["boundaries"]["module"]
        self.modules["litellm"] = module("litellm", completion=completion)
        self.modules["structlog"] = module("structlog", get_logger=lambda *a: Mock())
        base, adapter, _ = f["clients"](send)
        cls = adapter["BackgroundLLM"]

        def construct(**kwargs):
            client = cls(**kwargs)
            clients.append(client)
            return client

        self.modules["ee.agenthub.traceerroragent.background_llm"] = module(
            "background_llm", BackgroundLLM=construct
        )
        # The ModelConfigs source is executed unchanged, only environment-derived
        # model identifiers are synthetic. Never substitute the provider spelling.
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"BEDROCK_HAIKU_ARN": "offline-haiku"}, clear=True):
            configs = runpy.run_path(
                str(ROOT / "agentic_eval/core/utils/model_config.py")
            )
        self.modules["agentic_eval.core.utils.model_config"] = module(
            "model_config", **configs
        )
        owned = ROOT / "ee/agenthub/traceerroragent/background_tools.py"
        if owned.exists():
            with patch.dict("sys.modules", self.modules):
                self.modules["ee.agenthub.traceerroragent.background_tools"] = module(
                    "background_tools", **runpy.run_path(str(owned))
                )
        query = Mock()
        query.select_related.return_value = query
        query.get.return_value = SimpleNamespace(id="trace", project_id="project")
        self.modules["tracer.models.trace"] = module(
            "trace",
            Trace=SimpleNamespace(
                objects=query, DoesNotExist=type("Missing", (Exception,), {})
            ),
        )
        span = SimpleNamespace(
            id="span",
            name="step",
            input="query",
            output="answer",
            observation_type="llm",
            start_time=0,
            created_at=0,
        )

        def read_spans(trace_id, *, project_id):
            self.assertEqual(project_id, "project")
            return [span]

        self.modules["tracer.services.clickhouse.v2"] = module(
            "ch",
            get_reader=lambda: nullcontext(SimpleNamespace(list_by_trace=read_spans)),
        )
        ns = f["compile_definitions"](
            ROOT / "ai_tools/tools/tracing/explore_trace.py",
            {
                "__name__": "ai_tools.tools.tracing.explore_trace",
                "json": json,
                "os": SimpleNamespace(environ={"BEDROCK_HAIKU_ARN": "offline-haiku"}),
                "strip_code_fence": lambda text: text,
                "section": lambda *args: "\n".join(str(a) for a in args),
                "BaseTool": object,
                "ExploreTraceInput": object,
                "ToolResult": lambda **kwargs: SimpleNamespace(
                    is_error=False, **kwargs
                ),
                "register_tool": lambda cls: cls,
            },
            names={"ExploreTraceTool"},
        )
        explore = ns["ExploreTraceTool"]()
        params = SimpleNamespace(trace_id="trace")
        if prepare is not None:
            explore, params = prepare(explore, ns, query, span, self.modules)
        self.tool_results = []
        policy = f["compile_definitions"](
            ROOT / "ai_tools/background.py",
            {
                "ToolResult": SimpleNamespace(
                    error=lambda *a, **k: SimpleNamespace(is_error=True)
                )
            },
        )
        self.assertIsNone(policy["background_tool_denial"](explore, self.context))
        closure = dict(
            zip(
                invoke.__code__.co_freevars,
                [c.cell_contents for c in invoke.__closure__],
                strict=True,
            )
        )
        registry = closure["run"].__globals__["tool_registry"]
        registry[explore.name] = explore
        background.background_tools = lambda *a: list(registry.values())
        calls = 0

        async def stream(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "explore",
                                        "function": {
                                            "name": explore.name,
                                            "arguments": explore._offline_wire()
                                            if hasattr(explore, "_offline_wire")
                                            else "{}",
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "id": "finding",
                                        "function": {
                                            "name": "submit_trace_finding",
                                            "arguments": "{}",
                                        },
                                    },
                                ][: 1 if explore.name == "submit_trace_finding" else 2]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            else:
                yield {
                    "choices": [
                        {"delta": {"content": "finished"}, "finish_reason": "stop"}
                    ]
                }

        agent.llm_client.stream_with_retry = stream
        original = agent._execute_tool

        async def execute(tool, args):
            if tool.name == explore.name:
                # Model the validated dispatch boundary for execute-level probes;
                # raw-argument suites install their own actual run dispatcher.
                checked = params
                if getattr(tool.execute, "__name__", "") not in {"call", "foreground"}:
                    helper = runpy.run_path(str(HERE / "analysis_payload_fixture.py"))
                    checked = helper["parameters"](params, self.guard, tool.name)
                result = tool.execute(checked, self.context)
                self.tool_results.append(result)
                return result
            return await original(tool, args)

        agent._execute_tool = execute
        self.finalizer_read = Mock()
        result, effects, marker = self.worker(None)
        witness = agent.llm_client._witness
        if success if success is not None else mode == "success" and limit > 3:
            self.assertTrue(result["success"], result)
            self.assertTrue(witness.satisfied)
            self.assertEqual(
                effects, ["completed", "embedding", "success-log", "cluster"]
            )
            self.assertEqual(len(witness.calls), 2 + sends_expected)
        else:
            self.assertFalse(result["success"], result)
            self.assertFalse(witness.satisfied)
            marker.abort.assert_called_once()
            marker.complete.assert_not_called()
            self.assertEqual(effects, [])
            self.assertEqual(self.saved, [])
            self.finalizer_read.assert_not_called()
        self.assertEqual(
            len(sends),
            sends_expected,
            "no nested retry, even under nonzero library policy",
        )
        self.assertEqual(len(clients), sends_expected)
        if clients:
            self.assertIs(clients[0]._witness, witness)
        self.assertEqual(len(witness.calls), calls + len(sends))
        return witness

    def test_shared_attempt_budget_refuses_before_next_send(self):
        import asyncio

        completion = runpy.run_path(
            str(ROOT / "tracer/services/analysis_result_completion.py")
        )
        witness = completion["CompletionWitness"](3)
        sends = []

        async def stream():
            sends.append(1)
            yield {"choices": [{"finish_reason": "stop"}]}

        first = completion["RecordingLLM"](
            SimpleNamespace(stream_completion=stream), witness
        )
        second = completion["RecordingLLM"](
            SimpleNamespace(stream_completion=stream), witness
        )

        async def one(client):
            return [chunk async for chunk in client.stream_with_retry()]

        for client in (first, second, first):
            asyncio.run(one(client))
        with self.assertRaises(RuntimeError):
            asyncio.run(one(second))
        self.assertEqual(len(sends), 3)
        self.assertEqual(len(witness.calls), 3)
        self.assertTrue(witness.failed)
        self.assertFalse(witness.satisfied)

    def test_nested_exception_aborts(self):
        self.probe("exception")

    def test_nested_bad_raw_finishes_abort(self):
        for reason in ("max_tokens", "length", None, "unknown", "content_filter"):
            with self.subTest(reason=reason):
                self.probe(reason)

    def test_nested_actual_library_retry_cannot_hide_failure(self):
        self.probe("library-retry")

    def test_nested_success_counts_against_shared_budget(self):
        self.probe("success", limit=3)

    def test_nested_normal_finish_completes(self):
        self.probe("success")

    def test_nested_parse_fallback_aborts(self):
        self.probe("bad-json")

    def test_foreground_helper_keeps_library_retry_and_fallback(self):
        from unittest.mock import patch

        sends = []

        def boundary(**kwargs):
            sends.append(kwargs)
            if len(sends) == 1:
                raise RuntimeError("synthetic first failure")
            return f["response"](
                '{"sub_flows":[],"trace_overview":"legacy"}', "unknown"
            )

        module = b["boundaries"]["module"]
        run = b["boundaries"]["definitions"](
            "ai_tools/tools/tracing/explore_trace.py",
            {"_run_chauffeur"},
            {
                "json": json,
                "os": SimpleNamespace(environ={}),
                "strip_code_fence": lambda x: x,
            },
            owner="ExploreTraceTool",
        )["_run_chauffeur"]
        with patch.dict(
            "sys.modules",
            {
                "litellm": module(
                    "litellm",
                    completion=r["real_retry_wrapper"](boundary, SimpleNamespace(), 2),
                ),
                "structlog": module("structlog", get_logger=lambda *a: Mock()),
            },
        ):
            self.assertEqual(
                run(None, "trace"), {"sub_flows": [], "trace_overview": "legacy"}
            )
        self.assertEqual(len(sends), 2)
        with patch.dict(
            "sys.modules",
            {
                "litellm": module(
                    "litellm", completion=Mock(side_effect=RuntimeError("offline"))
                ),
                "structlog": module("structlog", get_logger=lambda *a: Mock()),
            },
        ):
            self.assertEqual(
                run(None, "trace"), {"sub_flows": [], "trace_overview": ""}
            )


if __name__ == "__main__":
    unittest.main()
