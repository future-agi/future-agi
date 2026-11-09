"""R3a capability/terminal-stop regressions. Run with python3 -I this_file.

Real definitions, synthetic I/O, sockets denied. No Django/DB integration claim.
The fixed allowlist is intentionally an independent oracle, not read from policy.
"""

import ast
import asyncio
import json
import logging
import runpy
import time
import unittest
from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from threading import Event, current_thread
from types import MethodType, SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import Mock

support = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_background_boundaries_offline.py"))
)
ROOT, definitions, module = (support[n] for n in ("ROOT", "definitions", "module"))
ALLOWED = {
    "explore_trace_legacy": "ai_tools.tools.tracing.explore_trace.ExploreTraceTool",
    "read_trace_span": "ai_tools.tools.tracing.read_trace_span.ReadTraceSpanTool",
    "submit_trace_finding": "ai_tools.tools.tracing.submit_trace_finding.SubmitTraceFindingTool",
    "submit_trace_scores": "ai_tools.tools.tracing.submit_trace_scores.SubmitTraceScoresTool",
}


def nested(path, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in ast.walk(tree) if getattr(n, "name", None) == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace[name]


class CapabilityTests(unittest.TestCase):
    setUp = support["BoundaryTests"].setUp
    install = support["BoundaryTests"].install
    worker = support["BoundaryTests"].worker

    def tools(self):
        ns = definitions(
            "ai_tools/base.py",
            {"ToolResult", "BaseTool"},
            {
                "ABC": ABC,
                "abstractmethod": abstractmethod,
                "dataclass": dataclass,
                "Any": Any,
                "ClassVar": ClassVar,
                "PydanticBaseModel": object,
                "EmptyInput": object,
                "ToolContext": object,
                "logger": logging.getLogger(__name__),
                "__name__": "offline_tools",
            },
        )
        self.modules["ai_tools.base"] = module("base", **ns)
        self.modules["tfc.middleware.workspace_context"] = module(
            "workspace", workspace_context=lambda **kw: nullcontext()
        )
        self.modules["ai_tools.error_codes"] = module(
            "codes", code_from_exception=lambda exc: "INTERNAL_ERROR"
        )
        policy_path = ROOT / "ai_tools/background.py"
        if policy_path.exists():
            nodes = [
                n
                for n in ast.parse(policy_path.read_text()).body
                if not isinstance(n, (ast.Import, ast.ImportFrom))
            ]
            policy = {"ToolResult": ns["ToolResult"]}
            exec(
                compile(
                    ast.Module(body=nodes, type_ignores=[]), str(policy_path), "exec"
                ),
                policy,
            )
            self.modules["ai_tools.background"] = module("background", **policy)
        self.install()
        return ns

    def test_a1_nested_orchestrator_cannot_swallow_terminal_abort(self):
        ns = definitions(
            "ee/agenthub/eval_orchestrator/orchestrator.py",
            {"run", "_execute_tool", "_force_final_submission"},
            {"logger": Mock(), "json": json, "ORCHESTRATOR_SYSTEM_PROMPT": ""},
            owner="EvalOrchestrator",
        )
        effects = []
        orch = SimpleNamespace(
            MAX_TURNS=3,
            _investigation_complete=False,
            _eval_calls=0,
            _build_user_prompt=lambda: "",
            _compact_old_tool_results=lambda m: None,
            _build_final_result=lambda: {},
        )
        for name, fn in ns.items():
            if name in ("run", "_execute_tool", "_force_final_submission"):
                setattr(orch, name, MethodType(fn, orch))
        calls = iter(
            [
                {"tool": "call_mcp_tool", "params": {}},
                {"tool": "run_eval", "params": {}},
                None,
                None,
            ]
        )

        def model(*args, **kwargs):
            effects.append("model")
            return next(calls)

        orch._call_orchestrator_llm = model
        orch._parse_tool_call_from_response = lambda content: content
        orch._tool_call_mcp = lambda **kw: self.guard.abort()
        orch._tool_run_eval = lambda **kw: effects.append("dispatch")
        for name in (
            "inspect_data",
            "search_data",
            "search_kb",
            "get_feedback",
            "formalize_criteria",
            "submit_result",
        ):
            setattr(orch, "_tool_" + name, lambda **kw: {})
        with self.assertRaises(self.Aborted):
            orch.run()
        self.assertEqual(effects, ["model"])

    def test_a2_direct_run_refuses_child_dispatch_before_blocking_read(self):
        ns = self.tools()
        effects = []

        # The continuation models the finding's read -> marker -> task sequence.
        # None of it may be entered, even while the job is still authorized.
        def execute(tool, params, context):
            effects.append("read")
            self.active = False
            effects.extend(["set_running", "task_dispatch"])
            return ns["ToolResult"](content="started")

        for name in (
            "analyze_project_traces",
            "evaluate_with_agent",
            "mcp_oauth_send",
            "tool_search",
            "unknown_future_tool",
        ):
            with self.subTest(name=name):
                effects.clear()
                self.active = True
                tool = type(
                    "Forbidden",
                    (ns["BaseTool"],),
                    {
                        "name": name,
                        "execute": execute,
                        "input_model": SimpleNamespace(model_validate=lambda p: p),
                    },
                )()
                result = tool.run({}, self.context)
                self.assertEqual(effects, [])
                self.assertTrue(result.is_error)
                self.assertEqual(result.error_code, "background_tool_not_allowed")

    def test_sync_and_async_agent_paths_cannot_bypass_policy(self):
        ns = self.tools()
        self.context.interactive_falcon_socket = False
        self.modules["accounts.models.user"] = module("user", User=Mock())
        self.modules["ee.falcon_ai.mcp_tools"] = module(
            "mcp", MCPToolWrapper=type("MCP", (), {})
        )
        self.install()
        effects = []

        async def async_execute(*args):
            effects.append("oauth")
            return ns["ToolResult"](content="bad")

        tool = SimpleNamespace(
            name="evaluate_with_agent",
            run=lambda *a: effects.append("orchestrator"),
            async_execute=async_execute,
            input_model=SimpleNamespace(model_validate=lambda p: p),
        )
        agent = SimpleNamespace(tool_context=self.context)
        execute = nested(
            "ee/falcon_ai/agent.py",
            "_execute_tool",
            {
                "authenticated_tool_scope": lambda ctx: nullcontext(),
                "ToolResult": ns["ToolResult"],
                "logger": Mock(),
            },
        )
        with self.subTest(path="sync"):
            result = execute(agent, tool, {})
            self.assertEqual(effects, [])
            self.assertEqual(result.error_code, "background_tool_not_allowed")
        effects.clear()
        runner = nested(
            "ee/falcon_ai/agent.py",
            "_run_tool",
            {
                "self": agent,
                "background_guard": self.guard,
                "asyncio": asyncio,
            },
        )
        content, failed = asyncio.run(
            runner({"tool": tool, "args": {}, "tool_name": tool.name})
        )
        self.assertTrue(failed)
        self.assertIn("background", content.lower())
        self.assertEqual(effects, [])

    def test_a3_late_finalizer_does_not_erase_watchdog_abort(self):
        # Completion moved out of the timed continuation entirely. Block an
        # actual finalizer read, let the watchdog abort, then release the old
        # continuation and prove it never calls the durable completion owner.
        entered, release = Event(), Event()
        threads = []

        def blocked_read():
            threads.append(current_thread())
            entered.set()
            if not release.wait(1):
                raise AssertionError("test did not release finalizer")

        self.finalizer_read = blocked_read
        self.guard.deadline = time.monotonic() + 0.08
        try:
            result, _, lifecycle = self.worker(None)
            self.assertTrue(entered.is_set())
            self.assertEqual(result.get("code"), "analysis_deadline_exceeded")
            lifecycle.abort.assert_called_once_with(
                "project", "trace", "generation", "analysis_deadline_exceeded"
            )
            lifecycle.complete.assert_not_called()
            # Read the real legacy-compatible feed projection of the durable
            # receipt; a cache marker is no longer publication authority.
            tree = ast.parse(
                (ROOT / "tracer/queries/deep_analysis_state.py").read_text()
            )
            state = SimpleNamespace(
                **definitions(
                    "tracer/queries/deep_analysis_state.py",
                    {n.name for n in tree.body if isinstance(n, ast.FunctionDef)},
                    {
                        "cache": Mock(),
                        "_TTL_SECONDS": 3600,
                        "background_analysis_state": SimpleNamespace(
                            get_state=lambda *a, **k: lifecycle.receipt
                        ),
                    },
                )
            )
            self.assertEqual(state.status("trace", has_analysis=True), "failed")
        finally:
            release.set()
            for thread in threads:
                thread.join(1)
                self.assertFalse(thread.is_alive())
        lifecycle.complete.assert_not_called()
        lifecycle.abort.assert_called_once()
        self.assertEqual(state.status("trace", has_analysis=True), "failed")
        self.assertEqual(state.failure_code("trace"), "analysis_deadline_exceeded")

    def test_exploration_rechecks_blocking_reads_before_summary_provider(self):
        ns = self.tools()
        effects = []
        trace = SimpleNamespace(id="trace")
        traces = Mock()
        traces.select_related.return_value.get.return_value = trace

        def read(*args):
            self.active = False
            return [SimpleNamespace(id="span")]

        self.modules.update(
            {
                "tracer.models.trace": module(
                    "trace",
                    Trace=SimpleNamespace(objects=traces, DoesNotExist=LookupError),
                ),
                "tracer.services.clickhouse.v2": module(
                    "reader",
                    get_reader=lambda: nullcontext(SimpleNamespace(list_by_trace=read)),
                ),
            }
        )
        self.install()
        execute = definitions(
            "ai_tools/tools/tracing/explore_trace.py",
            {"execute"},
            {
                "ExploreTraceInput": object,
                "ToolContext": object,
                "ToolResult": ns["ToolResult"],
                "section": lambda *a: "",
            },
            owner="ExploreTraceTool",
        )["execute"]
        tool = SimpleNamespace(
            _sort_children=lambda n: None,
            _build_outline=lambda *a: "",
            _build_execution_summary=lambda *a: "",
            _run_chauffeur=lambda *a, **kw: effects.append("provider") or {},
        )
        with self.assertRaises(self.Aborted):
            import runpy

            from tracer.services.analysis_result_completion import CompletionWitness

            self.guard.completion_witness = CompletionWitness(4)
            params = runpy.run_path(
                str(ROOT / "tracer/tests/analysis_payload_fixture.py")
            )["parameters"](
                SimpleNamespace(trace_id="trace"), self.guard, "explore_trace_legacy"
            )
            execute(tool, params, self.context)
        self.assertEqual(effects, [])

    def test_interactive_tool_run_keeps_existing_dispatch(self):
        ns = self.tools()
        effects = []
        tool = type(
            "Interactive",
            (ns["BaseTool"],),
            {
                "name": "evaluate_with_agent",
                "execute": lambda *a: (
                    effects.append("interactive") or ns["ToolResult"](content="ok")
                ),
                "input_model": SimpleNamespace(model_validate=lambda p: p),
            },
        )()
        self.context.background_analysis = None
        self.assertFalse(tool.run({}, self.context).is_error)
        self.assertEqual(effects, ["interactive"])

    def test_static_background_capability_contract(self):
        path = ROOT / "ai_tools/background.py"
        self.assertTrue(path.exists(), "missing single background dispatch policy")
        tree = ast.parse(path.read_text())
        assignment = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "BACKGROUND_TOOL_ALLOWLIST"
                for t in n.targets
            )
        )
        self.assertEqual(ast.literal_eval(assignment.value), ALLOWED)
        guard = ast.parse(
            (ROOT / "tracer/services/falcon_analysis_guard.py").read_text()
        )
        abort = next(
            n for n in guard.body if getattr(n, "name", None) == "AnalysisAborted"
        )
        self.assertEqual([ast.unparse(b) for b in abort.bases], ["BaseException"])
        # A new override must not silently bypass the single policy. Include all
        # registered native classes, generated adapters and async MCP wrappers.
        sources = list((ROOT / "ai_tools").rglob("*.py")) + [
            ROOT / "ee/falcon_ai/mcp_tools.py"
        ]
        for source in sources:
            if "tests" in source.parts:
                continue
            parsed = ast.parse(source.read_text())
            for cls in (n for n in parsed.body if isinstance(n, ast.ClassDef)):
                for method in cls.body:
                    if isinstance(
                        method, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and method.name in ("run", "async_execute"):
                        calls = [n for n in ast.walk(method) if isinstance(n, ast.Call)]
                        self.assertTrue(
                            any(
                                ast.unparse(n.func)
                                in {"background_tool_denial", "run_background_tool"}
                                for n in calls
                            ),
                            str(source),
                        )
                        if any(
                            ast.unparse(n.func) == "run_background_tool" for n in calls
                        ):
                            delegated = ast.parse(
                                (
                                    ROOT
                                    / "ee/agenthub/traceerroragent/background_payload_tools.py"
                                ).read_text()
                            )
                            calls += [
                                n
                                for n in ast.walk(delegated)
                                if isinstance(n, ast.Call)
                            ]
                        self.assertTrue(
                            any(
                                ast.unparse(n.func) == "background_tool_denial"
                                for n in calls
                            ),
                            str(source),
                        )
        agent = ast.parse((ROOT / "ee/falcon_ai/agent.py").read_text())
        for name in ("_execute_tool", "_run_tool"):
            method = next(
                n for n in ast.walk(agent) if getattr(n, "name", None) == name
            )
            gate = next(
                (
                    n
                    for n in ast.walk(method)
                    if isinstance(n, ast.Call)
                    and ast.unparse(n.func)
                    in {"background_tool_denial", "run_background_tool"}
                ),
                None,
            )
            self.assertIsNotNone(gate, name)
            dispatches = [
                n
                for n in ast.walk(method)
                if isinstance(n, ast.Call)
                and ast.unparse(n.func) in ("tool.run", "tool.async_execute")
            ]
            self.assertTrue(all(gate.lineno < n.lineno for n in dispatches), name)
        run = next(
            n
            for n in ast.walk(agent)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run"
        )
        background_branch = next(
            (
                n
                for n in run.body
                if isinstance(n, ast.If)
                and any(
                    isinstance(c, ast.Call)
                    and ast.unparse(c.func) == "background_tools"
                    for stmt in n.body
                    for c in ast.walk(stmt)
                )
            ),
            None,
        )
        self.assertIsNotNone(
            background_branch,
            "background registration must use allowlist, not mode/skill tools",
        )
        for name, identity in ALLOWED.items():
            module_name, class_name = identity.rsplit(".", 1)
            parsed = ast.parse(
                (ROOT / (module_name.replace(".", "/") + ".py")).read_text()
            )
            cls = next(n for n in parsed.body if getattr(n, "name", None) == class_name)
            for call in (n for n in ast.walk(cls) if isinstance(n, ast.Call)):
                if isinstance(call.func, ast.Attribute) and call.func.attr in (
                    "delay",
                    "apply_async",
                ):
                    # Scoring's legacy interactive dispatch must stay guard-is-None.
                    self.assertEqual(name, "submit_trace_scores")
                    enclosing = [
                        n
                        for n in ast.walk(cls)
                        if isinstance(n, ast.If)
                        and ast.unparse(n.test) == "guard is None"
                        and call in list(ast.walk(n))
                    ]
                    self.assertTrue(enclosing, "background tool can dispatch a task")


if __name__ == "__main__":
    unittest.main()
