"""Additional socket-blocked R3a entry-point and allowlisted-effect controls."""

import ast
import asyncio
import json
import runpy
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

support = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_background_capabilities_offline.py"))
)
ROOT, definitions, module = (support[n] for n in ("ROOT", "definitions", "module"))


class CapabilityFollowups(unittest.TestCase):
    setUp = support["CapabilityTests"].setUp
    install = support["CapabilityTests"].install
    tools = support["CapabilityTests"].tools

    def test_allowlisted_implementations_run_but_name_spoofs_do_not(self):
        ns = self.tools()
        policy = self.modules["ai_tools.background"]
        effects, registered = [], {}
        for name, identity in support["ALLOWED"].items():
            module_name, class_name = identity.rsplit(".", 1)
            tool = type(
                class_name,
                (ns["BaseTool"],),
                {
                    "__module__": module_name,
                    "name": name,
                    "input_model": SimpleNamespace(model_validate=lambda p: p),
                    "execute": lambda self, *a: (
                        effects.append(self.name) or ns["ToolResult"](content="ok")
                    ),
                },
            )()
            registered[name] = tool
            self.assertFalse(tool.run({}, self.context).is_error)
            spoof = type(
                class_name,
                (ns["BaseTool"],),
                {
                    "__module__": "ee.falcon_ai.mcp_tools",
                    "name": name,
                    "input_model": SimpleNamespace(
                        model_validate=Mock(
                            side_effect=AssertionError("validated denied tool")
                        )
                    ),
                    "execute": Mock(side_effect=AssertionError("executed name spoof")),
                },
            )()
            self.assertEqual(
                spoof.run({}, self.context).error_code, "background_tool_not_allowed"
            )
        self.assertEqual(effects, list(support["ALLOWED"]))
        # Even a registry containing other tools yields precisely four entries.
        registered["evaluate_with_agent"] = object()
        selected = policy.background_tools(
            SimpleNamespace(get=registered.get), self.context
        )
        self.assertEqual([tool.name for tool in selected], list(support["ALLOWED"]))

    def test_generated_override_and_direct_mcp_async_refuse_before_io(self):
        ns = self.tools()
        effects = []
        generated = definitions(
            "ai_tools/generated.py",
            {"run", "_invoke"},
            {
                "ToolContext": object,
                "ToolResult": ns["ToolResult"],
                "_ensure_urlconf_loaded_once": lambda: effects.append("urlconf"),
            },
            owner="GeneratedAPITool",
        )
        adapter = SimpleNamespace(
            name="evaluate_with_agent", _coerce_params=Mock(), _invoke=Mock()
        )
        result = generated["run"](adapter, {}, self.context)
        self.assertEqual(result.error_code, "background_tool_not_allowed")
        adapter._coerce_params.assert_not_called()
        adapter._invoke.assert_not_called()
        result = generated["_invoke"](adapter, {}, self.context)
        self.assertEqual(result.error_code, "background_tool_not_allowed")
        self.assertEqual(effects, [])
        self.modules.update(
            {
                "channels.db": module(
                    "channels_db", database_sync_to_async=lambda f: f
                ),
                "ee.falcon_ai.mcp_proxy": module(
                    "proxy",
                    MCPConnectorProxy=Mock(
                        side_effect=AssertionError("created connector proxy")
                    ),
                ),
            }
        )
        self.install()
        mcp = definitions(
            "ee/falcon_ai/mcp_tools.py",
            {"execute", "async_execute"},
            {
                "ToolContext": object,
                "ToolResult": ns["ToolResult"],
                "PydanticBaseModel": object,
                "connector_for_execution": Mock(
                    side_effect=AssertionError("looked up connector")
                ),
            },
            owner="MCPToolWrapper",
        )
        connector = SimpleNamespace(name="mcp_oauth_send", _call_mcp_sync=Mock())
        self.assertEqual(
            mcp["execute"](connector, {}, self.context).error_code,
            "background_tool_not_allowed",
        )
        self.assertEqual(
            asyncio.run(mcp["async_execute"](connector, {}, self.context)).error_code,
            "background_tool_not_allowed",
        )
        connector._call_mcp_sync.assert_not_called()

    def test_actual_child_dispatch_tool_is_unreachable_in_background(self):
        ns = self.tools()
        effects = []
        project = SimpleNamespace(id="project", name="project")
        # Load the real admission switch without Django startup or host settings.
        # ON matches the product default for this foreground dispatch control.
        with patch.dict(
            "sys.modules",
            {
                "django.conf": module(
                    "django.conf",
                    settings=SimpleNamespace(FALCON_BACKGROUND_ANALYSIS_ENABLED=True),
                )
            },
        ):
            switch = runpy.run_path(str(ROOT / "tfc/capabilities/background_analysis.py"))
        self.modules["tfc.capabilities.background_analysis"] = module(
            "tfc.capabilities.background_analysis", **switch
        )

        def read(*a, **kw):
            effects.append("read")
            self.active = False
            return {"trace": ("root", "project")}

        self.modules.update(
            {
                "structlog": module("structlog", get_logger=lambda *a: Mock()),
                "rest_framework.exceptions": module(
                    "exceptions", PermissionDenied=support["support"]["Denied"]
                ),
                "tracer.models.trace_error_analysis": module(
                    "analysis", TraceErrorAnalysis=Mock()
                ),
                "tracer.queries": module(
                    "queries",
                    deep_analysis_state=SimpleNamespace(
                        set_running=lambda *a: effects.append("marker")
                    ),
                ),
                "tracer.services.clickhouse.v2": module(
                    "reader",
                    get_reader=lambda: nullcontext(
                        SimpleNamespace(root_ids_by_trace_ids=read)
                    ),
                ),
                "tracer.services.falcon_analysis_scope": module(
                    "scope",
                    resolve_analysis_scope=lambda *a, **kw: (project, self.context),
                ),
                "tracer.tasks.error_analysis": module(
                    "tasks",
                    analyze_traces_on_demand=SimpleNamespace(
                        delay=lambda *a, **kw: effects.append("dispatch")
                    ),
                ),
            }
        )
        self.install()
        execute = definitions(
            "ai_tools/tools/tracing/analyze_project_traces.py",
            {"execute"},
            {
                "AnalyzeProjectTracesInput": object,
                "ToolContext": object,
                "ToolResult": ns["ToolResult"],
                "key_value_block": lambda *a: "",
                "section": lambda *a: "",
            },
            owner="AnalyzeProjectTracesTool",
        )["execute"]
        params = SimpleNamespace(
            project_id="project", trace_ids=["trace"], max_traces=1
        )
        tool = type(
            "AnalyzeProjectTracesTool",
            (ns["BaseTool"],),
            {
                "__module__": "ai_tools.tools.tracing.analyze_project_traces",
                "name": "analyze_project_traces",
                "execute": execute,
                "input_model": SimpleNamespace(model_validate=lambda p: params),
            },
        )()
        self.assertEqual(
            tool.run({}, self.context).error_code, "background_tool_not_allowed"
        )
        self.assertEqual(effects, [])
        # Interactive control executes the same real implementation unchanged.
        self.context.background_analysis = None
        self.assertFalse(tool.run({}, self.context).is_error)
        self.assertEqual(effects, ["read", "marker", "dispatch"])

    def test_read_tools_recheck_pg_before_clickhouse(self):
        ns = self.tools()
        calls = []

        def pg_read(*a, **kw):
            self.active = False
            return SimpleNamespace(id="trace")

        traces = Mock()
        traces.get.side_effect = traces.select_related.return_value.get.side_effect = (
            pg_read
        )
        self.modules.update(
            {
                "tracer.models.trace": module(
                    "trace",
                    Trace=SimpleNamespace(objects=traces, DoesNotExist=LookupError),
                ),
                "tracer.services.clickhouse.v2": module(
                    "reader", get_reader=lambda: calls.append("clickhouse")
                ),
            }
        )
        self.install()
        for filename, cls, input_name in (
            ("explore_trace", "ExploreTraceTool", "ExploreTraceInput"),
            ("read_trace_span", "ReadTraceSpanTool", "ReadTraceSpanInput"),
        ):
            with self.subTest(tool=filename):
                self.active = True
                self.guard = self.Guard("p", "u", "o", "w", 2)
                self.context.background_analysis = self.guard
                execute = definitions(
                    f"ai_tools/tools/tracing/{filename}.py",
                    {"execute"},
                    {
                        input_name: object,
                        "ToolContext": object,
                        "ToolResult": ns["ToolResult"],
                    },
                    owner=cls,
                )["execute"]
                with self.assertRaises(self.Aborted):
                    from tracer.services.analysis_result_completion import (
                        CompletionWitness,
                    )

                    self.guard.completion_witness = CompletionWitness(4)
                    params = runpy.run_path(
                        str(ROOT / "tracer/tests/analysis_payload_fixture.py")
                    )["parameters"](
                        SimpleNamespace(trace_id="trace"),
                        self.guard,
                        "explore_trace_legacy"
                        if filename == "explore_trace"
                        else filename,
                    )
                    execute(None, params, self.context)
        self.assertEqual(calls, [])

    def test_chauffeur_bounded_one_call_and_terminal_abort_not_fallback(self):
        self._chauffeur_control(managed=False)

    def test_chauffeur_managed_falcon_ai_control(self):
        self._chauffeur_control(managed=True)

    def _chauffeur_control(self, *, managed):
        self.tools()
        calls = []

        def provider(*args, **kwargs):
            payload = args[0] if args else None
            # Both original call shapes remain executable, without rewriting
            # recorded arguments to make the other transport's assertion pass.
            calls.append(kwargs if payload is None else payload)
            self.active = False
            if payload is not None:
                return {
                    "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]
                }
            return {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "{}"}],
            }

        fixture = runpy.run_path(str(ROOT / "tracer/tests/analysis_llm_fixture.py"))
        base, adapter, completion = fixture["clients"](lambda *a: None)
        base._requires_managed_transport = lambda _, model: (
            managed and model == "falcon_ai"
        )
        adapter["direct_completion"] = provider
        if managed:
            import httpx

            gateway = runpy.run_path(
                str(ROOT / "tracer/tests/analysis_gateway_fixture.py")
            )

            def post(url, **kwargs):
                return httpx.Response(
                    200,
                    json=provider(kwargs["json"]),
                    request=httpx.Request("POST", url),
                )

            self.modules.update(gateway["managed_modules"](post))
        self.guard.completion_witness = completion["CompletionWitness"](4)
        helper = runpy.run_path(
            str(ROOT / "ee/agenthub/traceerroragent/background_tools.py")
        )
        self.modules.update(
            {
                "litellm": module(
                    "litellm",
                    completion=Mock(side_effect=AssertionError("unwitnessed call")),
                ),
                "structlog": module("structlog", get_logger=lambda *a: Mock()),
                "ee.agenthub.traceerroragent.background_tools": module(
                    "helper", **helper
                ),
                "ee.agenthub.traceerroragent.background_llm": module(
                    "adapter", **adapter
                ),
                "tracer.services.analysis_result_completion": module(
                    "completion", **completion
                ),
                "agentic_eval.core.utils.model_config": module(
                    "config",
                    ModelConfigs=SimpleNamespace(
                        HAIKU_4_5_BEDROCK_ARN=SimpleNamespace(
                            provider="openai" if managed else "aws_bedrock_anthropic",
                            model_name="falcon_ai" if managed else "offline",
                        )
                    ),
                ),
            }
        )
        self.install()
        run = definitions(
            "ai_tools/tools/tracing/explore_trace.py",
            {"_run_chauffeur"},
            {
                "json": json,
                "os": SimpleNamespace(environ={}),
                "strip_code_fence": lambda raw: raw,
            },
            owner="ExploreTraceTool",
        )["_run_chauffeur"]
        with self.assertRaises(self.Aborted):
            run(None, "synthetic", guard=self.guard)
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.guard.completion_witness.failed)
        self.assertEqual(len(self.guard.completion_witness.calls), 1)
        if managed:
            self.assertEqual(calls[0]["model"], "falcon_ai")
            # Timeout and zero-retry transport are independently tested at the
            # managed HTTP boundary, not just kwargs to an opaque SDK.
        else:
            self.assertGreater(calls[0]["timeout"], 0)
            self.assertLessEqual(calls[0]["timeout"], 2)
        with self.assertRaises(self.Aborted):
            run(None, "synthetic", guard=self.guard)
        self.assertEqual(len(calls), 1)

    def test_static_dispatch_gates_dominate_effects_and_registration(self):
        entries = (
            ("ai_tools/base.py", "BaseTool", "run", "self.execute"),
            ("ai_tools/generated.py", "GeneratedAPITool", "run", "self._invoke"),
            (
                "ai_tools/generated.py",
                "GeneratedAPITool",
                "_invoke",
                "_ensure_urlconf_loaded_once",
            ),
            (
                "ee/falcon_ai/mcp_tools.py",
                "MCPToolWrapper",
                "execute",
                "self._call_mcp_sync",
            ),
            (
                "ee/falcon_ai/mcp_tools.py",
                "MCPToolWrapper",
                "async_execute",
                "database_sync_to_async",
            ),
        )
        for path, owner, name, effect in entries:
            tree = ast.parse((ROOT / path).read_text())
            cls = next(n for n in tree.body if getattr(n, "name", None) == owner)
            method = next(n for n in cls.body if getattr(n, "name", None) == name)
            gate = next(
                n
                for n in ast.walk(method)
                if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "background_tool_denial"
            )
            refusal = next(
                n
                for n in method.body
                if isinstance(n, ast.If) and ast.unparse(n.test) == "denial is not None"
            )
            self.assertEqual(ast.unparse(refusal.body[0]), "return denial")
            calls = [
                n
                for n in ast.walk(method)
                if isinstance(n, ast.Call) and ast.unparse(n.func) == effect
            ]
            self.assertTrue(calls, path)
            self.assertTrue(
                all(gate.lineno < refusal.lineno < n.lineno for n in calls), path
            )
        agent = ast.parse((ROOT / "ee/falcon_ai/agent.py").read_text())
        # Any newly added direct dispatch path requires explicit review/tests.
        for method in (
            n
            for n in ast.walk(agent)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            for statement in method.body:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                nested_functions = [
                    n
                    for n in ast.walk(statement)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                nested_nodes = {
                    id(child) for fn in nested_functions for child in ast.walk(fn)
                }
                for call in (
                    n
                    for n in ast.walk(statement)
                    if isinstance(n, ast.Call) and id(n) not in nested_nodes
                ):
                    if ast.unparse(call.func) in (
                        "tool.run",
                        "tool.async_execute",
                        "tool.execute",
                    ):
                        self.assertIn(method.name, ("_run_tool", "_execute_tool"))
        tree = ast.parse((ROOT / "ai_tools/background.py").read_text())
        registration = next(
            n for n in tree.body if getattr(n, "name", None) == "background_tools"
        )
        loops = [n for n in ast.walk(registration) if isinstance(n, ast.For)]
        self.assertEqual(len(loops), 1)
        self.assertEqual(ast.unparse(loops[0].iter), "BACKGROUND_TOOL_ALLOWLIST")
        appends = [
            n
            for n in ast.walk(registration)
            if isinstance(n, ast.Call) and ast.unparse(n.func) == "tools.append"
        ]
        self.assertEqual(len(appends), 1)
        self.assertIn(appends[0], list(ast.walk(loops[0])))


if __name__ == "__main__":
    unittest.main()
