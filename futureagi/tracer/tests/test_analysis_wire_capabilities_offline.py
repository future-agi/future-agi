"""Fail-closed provenance and typed consumer closure; no provider/ORM access."""

import ast
import asyncio
import json
import runpy
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).parent
ROOT = HERE.parents[1]


class WireCapabilities(unittest.TestCase):
    def setUp(self):
        completion = ModuleType("tracer.services.analysis_result_completion")
        vars(completion).update(
            runpy.run_path(str(ROOT / "tracer/services/analysis_result_completion.py"))
        )
        modules = runpy.run_path(str(HERE / "analysis_payload_fixture.py"))["modules"](
            {"tracer.services.analysis_result_completion": completion}
        )
        isolation = patch.dict("sys.modules", modules)
        isolation.start()
        self.addCleanup(isolation.stop)
        self.witness = completion.CompletionWitness(5)
        self.wire = modules["ee.agenthub.traceerroragent.background_payload_wire"]
        self.tool = SimpleNamespace(name="read_trace_span")
        self.args = {
            "trace_id": "00000000-0000-0000-0000-000000000001",
            "span_id": "span",
        }

    def register(self, call_id="one"):
        self.wire.validate_falcon_wire(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": call_id,
                                    "function": {
                                        "name": self.tool.name,
                                        "arguments": json.dumps(self.args),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            "openai",
            self.witness,
        )

    def test_plain_dictionary_without_adapter_provenance_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.wire.consume_tool_payload(self.tool, self.args, self.witness)
        self.assertTrue(self.witness.failed)

    def test_registry_alone_without_dispatch_binding_is_not_a_capability(self):
        self.register()
        with self.assertRaises(RuntimeError):
            self.wire.consume_tool_payload(self.tool, self.args, self.witness)
        self.assertTrue(self.witness.failed)

    def test_bound_capability_can_only_be_consumed_once(self):
        self.register()

        async def execute(tool, args):
            self.wire.consume_tool_payload(tool, args, self.witness)
            self.wire.consume_tool_payload(tool, args, self.witness)

        agent = SimpleNamespace(_execute_tool=execute)
        self.wire.install_falcon_dispatch(agent, self.witness)
        with self.assertRaises(RuntimeError):
            asyncio.run(agent._execute_tool(self.tool, self.args))
        self.assertTrue(self.witness.failed)

    def test_fresh_generation_cannot_use_another_generations_registry(self):
        self.register()
        other = type(self.witness)(5)

        async def execute(tool, args):
            self.wire.consume_tool_payload(tool, args, other)

        agent = SimpleNamespace(_execute_tool=execute)
        self.wire.install_falcon_dispatch(agent, self.witness)
        with self.assertRaises(RuntimeError):
            asyncio.run(agent._execute_tool(self.tool, self.args))
        self.assertTrue(other.failed)
        self.assertTrue(self.witness.failed)

    def test_production_dispatch_propagates_capability_into_real_sync_thread(self):
        import threading
        from contextlib import nullcontext
        from types import MethodType
        from unittest.mock import Mock

        from asgiref.sync import sync_to_async

        self.register()
        tree = ast.parse((ROOT / "ee/falcon_ai/agent.py").read_text())
        method = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_execute_tool"
        )
        # database_sync_to_async inherits this actual asgiref bridge; only its
        # Django connection-close hooks are outside this offline fixture.
        method.decorator_list = []
        ns = {
            "authenticated_tool_scope": lambda context: nullcontext(),
            "logger": Mock(),
        }
        exec(
            compile(
                ast.Module(body=[method], type_ignores=[]), "actual-dispatch", "exec"
            ),
            ns,
        )
        context = SimpleNamespace(interactive_falcon_socket=False)
        context.background_analysis = SimpleNamespace(
            check=lambda: context, completion_witness=self.witness
        )
        seen = []
        main_thread = threading.get_ident()

        def run(args, ctx):
            seen.append(threading.get_ident())
            return self.wire.consume_tool_payload(
                self.tool, args, ctx.background_analysis
            )

        self.tool.run = run
        agent = SimpleNamespace(tool_context=context)
        agent._execute_tool = sync_to_async(
            MethodType(ns["_execute_tool"], agent), thread_sensitive=True
        )
        self.wire.install_falcon_dispatch(agent, self.witness)
        with patch.dict(
            "sys.modules",
            {
                "accounts.models.user": SimpleNamespace(User=object),
                "ee.falcon_ai.mcp_tools": SimpleNamespace(
                    MCPToolWrapper=type("MCP", (), {})
                ),
                "ai_tools.background": SimpleNamespace(
                    background_tool_denial=lambda *a: None
                ),
            },
        ):
            payload = asyncio.run(agent._execute_tool(self.tool, self.args))
        self.assertEqual(payload["span_id"], "span")
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], main_thread)
        self.assertTrue(self.witness.wire_calls["one"].consumed)
        self.assertFalse(self.witness.failed)

    def test_background_advertisement_requires_evidence_without_changing_foreground(
        self,
    ):
        original = {
            "type": "function",
            "function": {
                "name": "submit_trace_finding",
                "description": "finding",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "urgency_to_fix": {"type": "string", "default": "HIGH"}
                    },
                },
            },
        }
        before = json.dumps(original, sort_keys=True)
        outgoing = self.wire.background_tool_definitions([original], self.witness)
        schema = outgoing[0]["function"]["parameters"]
        for field in ("urgency_to_fix", "immediate_fix", "trace_impact"):
            self.assertIn(field, schema["required"])
            self.assertNotIn("default", schema["properties"][field])
        self.assertEqual(json.dumps(original, sort_keys=True), before)
        schema["required"].clear()
        self.assertTrue(self.wire.TOOL_SCHEMAS["submit_trace_finding"]["required"])

    def test_every_background_tool_schema_covers_actual_input_fields(self):
        inputs = runpy.run_path(str(HERE / "test_analysis_payloads_offline.py"))
        allowlist = ast.parse((ROOT / "ai_tools/background.py").read_text())
        names = next(
            ast.literal_eval(n.value)
            for n in allowlist.body
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "BACKGROUND_TOOL_ALLOWLIST"
                for t in n.targets
            )
        )
        self.assertEqual(set(names), set(self.wire.TOOL_SCHEMAS))
        for name in inputs["MODELS"]:
            canonical = "explore_trace_legacy" if name == "explore_trace" else name
            actual = inputs["model_for"](name).model_json_schema()
            schema = self.wire.TOOL_SCHEMAS[canonical]
            self.assertEqual(set(actual["properties"]), set(schema["properties"]))
            self.assertLessEqual(set(actual["required"]), set(schema["required"]))
            self.assertLessEqual(
                set(schema["properties"]) - set(schema["required"]),
                {"exact", "analysis_id"},
            )

    def test_every_consumed_error_and_summary_attribute_is_required(self):
        from ee.agenthub.traceerroragent.background_payload_schemas import (
            ERROR,
            SCORES,
            SUMMARY,
        )

        source = ast.parse(
            (
                ROOT / "ee/agenthub/traceerroragent/background_payload_consumers.py"
            ).read_text()
        )
        for node in ast.walk(source):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in {"error", "e"}:
                    self.assertIn(node.attr, ERROR["required"])
                elif node.value.id == "summary":
                    self.assertIn(node.attr, SUMMARY["required"])
                elif node.value.id == "scores":
                    self.assertIn(node.attr, SCORES["required"])
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
            ):
                # Only explicitly classified host/operational metadata defaults.
                self.assertIn(
                    (ast.unparse(node.func.value), node.args[0].value),
                    {
                        ("data", "memory_context"),
                        ("data", "grouped_errors"),
                        ("memory", "episodic_memory_used"),
                    },
                )


if __name__ == "__main__":
    unittest.main()
