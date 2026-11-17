"""Closed call graph from every allowed tool, including direct/aliased sends.

ORM/CH, formatting, validation and lifecycle are reviewed non-model leaves.
All tool-local functions are visited (a superset of dynamic self dispatch).
New imports or outbound call shapes must be explicitly classified, not silently
accepted because the client constructor has a familiar name.
"""

import ast
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "ai_tools/tools/tracing"
GRAPH = runpy.run_path(str(Path(__file__).with_name("analysis_tool_graph_fixture.py")))
LEAVES = {
    "tracer.services.background_transport",
    "uuid",
    "pydantic",
    "json",
    "os",
    "re",
    "structlog",
    "ai_tools.base",
    "ai_tools.formatting",
    "ai_tools.registry",
    "agentic_eval.core.utils.json_utils",
    "django.db",
    "tracer.models.trace",
    "tracer.models.project",
    "tracer.models.trace_error_analysis",
    "tracer.services.clickhouse.v2",
    "tracer.services.analysis_result_binding",
    "tracer.queries",
    "tracer.queries.error_analysis",
    "tracer.tasks.error_analysis",
}
SEND_NAMES = {
    "completion",
    "acompletion",
    "create",
    "post",
    "get",
    "request",
    "send",
    "invoke_model",
    "invoke_model_with_response_stream",
    "generate_content",
    "_get_completion_content",
    "_get_completion_with_tools",
}
# Non-model operations with overloaded SDK-like method names. Exact expressions,
# not an unrestricted suffix/prefix allowlist. A new outbound call fails closed.
DATA_CALLS = {
    "Trace.objects.select_related('project').get",
    "Trace.objects.get",
    "TraceErrorAnalysis.drafts.get",
    "TraceErrorAnalysis.drafts.create",
    "TraceErrorDetail.drafts.create",
    "os.environ.get",
    "chauffeur_report.get",
    "node.get",
    "msg.get",
    "g.get",
    "sf.get",
    "parsed.get",
    "roots.get",
    "reader.root_ids_by_trace_ids([str(params.trace_id)]).get",
    "{'HIGH': 'high_impact_errors', 'MEDIUM': 'medium_impact_errors', 'LOW': 'low_impact_errors'}.get",
}


def census(sources):
    sends = []
    for filename, source in sources.items():
        tree = ast.parse(source)
        key = filename.rsplit(".", 1)[-1]
        if key in GRAPH["CALL_EDGES"]:
            calls = {
                ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)
            }
            if calls != GRAPH["CALL_EDGES"][key]:
                raise AssertionError(
                    f"unclassified call graph: {filename}: {calls ^ GRAPH['CALL_EDGES'][key]}"
                )
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name] = item.name
                    if item.name not in LEAVES | {"litellm"}:
                        raise AssertionError(f"unclassified graph import: {item.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module != "litellm" and not {
                    n.name for n in node.names
                } <= GRAPH["IMPORT_EDGES"].get(node.module, set()):
                    raise AssertionError(
                        f"unclassified imported callable: {node.module}"
                    )
                if node.module in {
                    "ee.agenthub.traceerroragent.background_tools",
                    "ee.agenthub.traceerroragent.background_tool_validation",
                    "ee.agenthub.traceerroragent.background_payload_tools",
                }:
                    if any(n.asname for n in node.names):
                        raise AssertionError("unclassified background helper alias")
                elif node.module not in LEAVES | {"litellm"}:
                    raise AssertionError(f"unclassified graph edge: {node.module}")
                for item in node.names:
                    aliases[item.asname or item.name] = f"{node.module}.{item.name}"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            expression = ast.unparse(node.func)
            root = expression.split(".")[0]
            resolved = aliases.get(root, root) + expression[len(root) :]
            name = expression.rsplit(".", 1)[-1]
            if resolved.startswith("litellm.") or name in SEND_NAMES:
                if expression not in DATA_CALLS:
                    sends.append((filename, resolved, node))
    return sends


def routed(tree, call):
    """The only legacy send is in the ELSE of the background routing branch."""
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.If)
            or ast.unparse(node.test) != "guard is not None"
        ):
            continue
        if not any(call in list(ast.walk(child)) for child in node.orelse):
            continue
        return any(
            isinstance(n, ast.Call) and ast.unparse(n.func) == "chauffeur_content"
            for child in node.body
            for n in ast.walk(child)
        )
    return False


class ToolGraph(unittest.TestCase):
    def sources(self):
        policy = ast.parse((ROOT / "ai_tools/background.py").read_text())
        allowlist = next(
            ast.literal_eval(n.value)
            for n in policy.body
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "BACKGROUND_TOOL_ALLOWLIST"
                for t in n.targets
            )
        )
        self.assertEqual(
            set(allowlist),
            {
                "explore_trace_legacy",
                "read_trace_span",
                "submit_trace_finding",
                "submit_trace_scores",
            },
        )
        return {
            identity.rsplit(".", 1)[0]: (
                ROOT / (identity.rsplit(".", 1)[0].replace(".", "/") + ".py")
            ).read_text()
            for identity in allowlist.values()
        }

    def test_every_allowlisted_tool_send_is_background_routed(self):
        sources = self.sources()
        sends = census(sources)
        self.assertEqual(
            [(p, name) for p, name, _ in sends],
            [("ai_tools.tools.tracing.explore_trace", "litellm.completion")],
        )
        for filename, _, call in sends:
            tree = ast.parse(sources[filename])
            actual = next(
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and n.lineno == call.lineno
                and n.col_offset == call.col_offset
            )
            self.assertTrue(
                routed(tree, actual),
                "reachable provider send bypasses generation witness",
            )
        helper = ROOT / "ee/agenthub/traceerroragent/background_tools.py"
        self.assertTrue(helper.exists(), "model helper is missing")
        source = helper.read_text()
        self.assertIn("BackgroundLLM", source)
        self.assertIn("completion_witness", source)
        self.assertIn("timeout_ms=", source)
        self.assertNotIn("litellm", source)
        helper_tree = ast.parse(source)
        calls = {
            ast.unparse(n.func)
            for n in ast.walk(helper_tree)
            if isinstance(n, ast.Call)
        }
        self.assertEqual(
            calls,
            {
                "getattr",
                "IncompleteAnalysis",
                "guard.check",
                "BackgroundLLM",
                "client._get_completion_with_tools",
            },
        )
        model_sends = [
            n
            for n in ast.walk(helper_tree)
            if isinstance(n, ast.Call)
            and ast.unparse(n.func) == "client._get_completion_with_tools"
        ]
        self.assertEqual(len(model_sends), 1)
        factory = next(
            n
            for n in ast.walk(helper_tree)
            if isinstance(n, ast.Call) and ast.unparse(n.func) == "BackgroundLLM"
        )
        self.assertIn("witness=witness", ast.unparse(factory))
        # Follow the entry's local helpers, rather than assuming execute itself
        # contains the model call. Unknown edges are rejected above, even if
        # their names contain no provider/SDK keyword.
        explore_tree = ast.parse(sources["ai_tools.tools.tracing.explore_trace"])
        methods = {
            n.name: n for n in ast.walk(explore_tree) if isinstance(n, ast.FunctionDef)
        }
        pending, reached = ["execute"], set()
        while pending:
            name = pending.pop()
            if name in reached:
                continue
            reached.add(name)
            for call in ast.walk(methods[name]):
                if isinstance(call, ast.Call):
                    target = ast.unparse(call.func).removeprefix("self.")
                    if target in methods:
                        pending.append(target)
        self.assertIn("_run_chauffeur", reached)
        self.assertIn(
            "chauffeur_content",
            {
                ast.unparse(n.func)
                for n in ast.walk(methods["_run_chauffeur"])
                if isinstance(n, ast.Call)
            },
        )
        worker = (ROOT / "tracer/tasks/error_analysis.py").read_text()
        self.assertIn("guard.completion_witness = witness", worker)

    def test_validation_helper_has_no_hidden_model_or_recovery_edges(self):
        tree = ast.parse(
            (
                ROOT / "ee/agenthub/traceerroragent/background_tool_validation.py"
            ).read_text()
        )
        self.assertEqual(
            {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)},
            {
                "type",
                "_text",
                "value.strip",
                "str",
                "min",
                "str(value).strip",
                "visited.add",
                "len",
                "_strings",
                "isinstance",
                "reject_chauffeur",
                "all",
                "getattr",
                "bool",
                "set",
                "any",
                "chauffeur_report",
                "json.dumps",
            },
        )
        self.assertEqual(
            [
                ast.unparse(n)
                for n in tree.body
                if isinstance(n, (ast.Import, ast.ImportFrom))
            ],
            [
                "import json",
                "from ee.agenthub.traceerroragent.background_tools import reject_chauffeur",
            ],
        )
        # Imported validator graph is closed independently, including all calls,
        # imports, parses, defaults and the typed publication/learning sinks.
        self.assertTrue(
            (ROOT / "tracer/tests/test_analysis_payload_census_offline.py").exists()
        )

    def test_census_rejects_new_alias_sdk_http_and_imported_helper(self):
        for snippet in (
            "import litellm as other\nother.acompletion()",
            "from litellm import completion as hidden\nhidden()",
            "client.chat.completions.create()",
            'http.post("https://api.openai.com/v1/chat/completions")',
            "client.invoke_model()",
        ):
            with self.subTest(snippet=snippet):
                sends = census({"injected-helper": snippet})
                self.assertTrue(sends)
                self.assertFalse(routed(ast.parse(snippet), sends[0][2]))
        with self.assertRaises(AssertionError):
            census(
                {"injected-helper": "from new_package import summarize\nsummarize()"}
            )
        with self.assertRaises(AssertionError):
            census(
                {
                    "injected-helper": "from tracer.tasks.error_analysis import hidden_model\nhidden_model()"
                }
            )
        for filename, source in self.sources().items():
            with self.subTest(root=filename), self.assertRaises(AssertionError):
                census(
                    {filename: source + "\ndef innocent_helper():\n    surprise()\n"}
                )


if __name__ == "__main__":
    unittest.main()
