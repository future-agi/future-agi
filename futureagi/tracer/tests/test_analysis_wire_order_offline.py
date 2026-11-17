"""Ordered boundaries, not a label on an already lossy decoder."""

import ast
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
AGENTS = ROOT / "ee/agenthub/traceerroragent"


class WireOrder(unittest.TestCase):
    def test_raw_adapter_checks_before_normalization_and_agentloop(self):
        source = (AGENTS / "background_falcon.py").read_text()
        self.assertIn("validate_falcon_wire", source)
        self.assertLess(
            source.index("validate_falcon_wire(raw"),
            source.index("normalize_response(raw"),
        )
        worker = (ROOT / "tracer/tasks/error_analysis.py").read_text()
        self.assertIn("install_falcon_dispatch(agent, witness)", worker)
        self.assertLess(
            worker.index("install_falcon_dispatch(agent, witness)"),
            worker.index(
                "agent.run(", worker.index("install_falcon_dispatch(agent, witness)")
            ),
        )

    def test_dispatch_consumes_provenance_not_a_new_validation_of_decoded_dict(self):
        source = (AGENTS / "background_payload_tools.py").read_text()
        tree = ast.parse(source)
        run = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "run_background_tool"
        )
        body = ast.unparse(run)
        self.assertIn("consume_tool_payload", body)
        self.assertNotIn("validate_payload(raw_params", body)

    def test_every_legacy_evidence_default_is_dominated_by_a_background_exit(self):
        service = ast.parse((AGENTS / "analysis_service.py").read_text())
        save = next(
            n
            for n in ast.walk(service)
            if isinstance(n, ast.FunctionDef) and n.name == "_save_owned_result"
        )
        exit_node = next(
            n
            for n in ast.walk(save)
            if isinstance(n, ast.If)
            and ast.unparse(n.test) == "current_attempt() is not None"
        )
        self.assertTrue(
            any(
                isinstance(n, ast.Return) and "save_publication" in ast.unparse(n)
                for n in exit_node.body
            )
        )
        defaults = [
            n
            for n in ast.walk(save)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get"
            and n.args
        ]
        self.assertTrue(defaults)
        self.assertTrue(all(n.lineno > exit_node.end_lineno for n in defaults))
        for filename, name, flag in (
            ("analysis_service.py", "_update_error_patterns", "strict"),
            ("traceerror.py", "save_error_patterns_to_memory", "background"),
        ):
            tree = ast.parse((AGENTS / filename).read_text())
            method = next(
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name
            )
            parents = {
                child: parent
                for parent in ast.walk(method)
                for child in ast.iter_child_nodes(parent)
            }
            for node in ast.walk(method):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and ast.unparse(node.func.value) == "error"
                    and node.func.attr == "get"
                ):
                    continue
                parent = parents[node]
                self.assertIsInstance(parent, ast.IfExp, (filename, ast.unparse(node)))
                self.assertEqual(ast.unparse(parent.test), flag)
                self.assertIs(parent.orelse, node)
                self.assertNotIn(".get(", ast.unparse(parent.body))
        judge = ast.parse((AGENTS / "judge.py").read_text())
        dispatch = next(
            n
            for n in ast.walk(judge)
            if isinstance(n, ast.FunctionDef) and n.name == "_execute_tool"
        )
        first = dispatch.body[1]  # docstring then background gate
        self.assertIsInstance(first, ast.If)
        self.assertIn("completion_witness", ast.unparse(first.test))
        self.assertIn("return execute_judge_tool", ast.unparse(first))

    def test_shared_original_report_parsers_exit_before_legacy_recovery(self):
        tree = ast.parse((AGENTS / "chauffeur.py").read_text())
        parse = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_parse_response"
        )
        gate = parse.body[1]
        self.assertIsInstance(gate, ast.If)
        self.assertIn("decode=True", ast.unparse(gate))
        self.assertIn("return chauffeur_report", ast.unparse(gate))
        judge = (AGENTS / "judge.py").read_text()
        self.assertLess(
            judge.index("tool_args = judge_arguments("),
            judge.index("tool_args = json.loads("),
        )
        provider = (AGENTS / "background_provider.py").read_text()
        self.assertIn('original_json(stream.read().decode("utf-8"))', provider)
        self.assertIn('original_json(response.content.decode("utf-8"))', provider)
        self.assertNotIn("return response.json()", provider)

    def test_inactive_legacy_default_paths_are_not_generation_call_edges(self):
        # The two other cited urgency defaults are not in either active worker's
        # call graph; the frozen payload census still inventories their sites.
        for filename in ("voice_compass.py", "deprecated.py"):
            self.assertIn('get("urgency_to_fix",', (AGENTS / filename).read_text())
        for path in (AGENTS / "traceerror.py", ROOT / "tracer/tasks/error_analysis.py"):
            source = path.read_text()
            self.assertNotIn("VoiceCompassAgent", source)
            self.assertNotIn("from ee.agenthub.traceerroragent.deprecated", source)

    def test_consumed_finding_fields_are_required(self):
        schema = runpy.run_path(str(AGENTS / "background_payload_schemas.py"))
        for name in ("FINDING", "ERROR"):
            self.assertTrue(
                set(schema["FINDING_FIELDS"]) <= set(schema[name]["required"]), name
            )

    def test_original_decoder_refuses_nonobject_and_nonfinite_at_the_boundary(self):
        # Load the real decoder in isolation, without importing Django.
        tree = ast.parse((AGENTS / "background_payloads.py").read_text())
        selected = [
            n
            for n in tree.body
            if isinstance(n, (ast.Import, ast.ImportFrom))
            and not (isinstance(n, ast.ImportFrom) and n.module.startswith("tracer"))
            or isinstance(n, ast.FunctionDef)
            and n.name
            in {"original_json", "_pairs", "_invalid_constant", "_finite_float"}
        ]
        ns = {}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "decoder", "exec"), ns)
        for raw in (
            "[]",
            "null",
            "true",
            '"text"',
            '{"x":NaN}',
            '{"x":1e999}',
            '{"x":Infinity}',
            '{"x":-Infinity}',
            '{"x":1,"x":2}',
            "{}{}",
        ):
            with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
                ns["original_json"](raw)


if __name__ == "__main__":
    unittest.main()
