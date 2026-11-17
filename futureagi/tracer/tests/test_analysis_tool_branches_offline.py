"""Closed semantic-branch census, complementing the provider call-graph census.

Every return, exception handler, conditional, default and slice is inventoried,
not only branches with an English 'fallback' comment. Source-coordinate-free
expressions survive formatting, but a new branch/default cannot pass silently.
Classifications are audited with executable full-tool worker probes. Optional
presentation defaults do not signify a failed analysis; partial reads, parser
recovery, unavailable summaries and invalid explicit identity do.
"""

import ast
import json
import runpy
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
GRAPH = runpy.run_path(str(HERE / "test_analysis_tool_graph_offline.py"))


def branches(source):
    rows = []

    class Visitor(ast.NodeVisitor):
        owner = "module"

        def visit_FunctionDef(self, node):
            prior = self.owner
            self.owner = prior + "." + node.name
            self.generic_visit(node)
            self.owner = prior

        def visit_If(self, node):
            condition = ast.unparse(node.test)
            rows.append((self.owner, "if " + condition))
            self.visit(node.test)
            prior = self.owner
            for arm, statements in (("then", node.body), ("else", node.orelse)):
                self.owner = prior + f".if({condition}).{arm}"
                for statement in statements:
                    self.visit(statement)
            self.owner = prior

        def visit_ExceptHandler(self, node):
            kind = ast.unparse(node.type) if node.type else "*"
            rows.append((self.owner, "except " + kind))
            prior = self.owner
            self.owner = prior + f".except({kind})"
            for statement in node.body:
                self.visit(statement)
            self.owner = prior

        def generic_visit(self, node):
            expression = None
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                expression = "do " + ast.unparse(node.value)
            elif isinstance(node, ast.Return):
                expression = "return " + (
                    ast.unparse(node.value) if node.value else "None"
                )
            elif isinstance(node, (ast.IfExp, ast.BoolOp, ast.Slice)):
                expression = ast.unparse(node)
            elif isinstance(node, ast.Call) and (
                (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and len(node.args) > 1
                )
                or (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) > 2
                )
                or (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "next"
                    and len(node.args) > 1
                )
            ):
                expression = ast.unparse(node)
            if expression is not None:
                rows.append((self.owner, expression))
            super().generic_visit(node)

    Visitor().visit(ast.parse(source))
    return Counter(rows)


def sources():
    case = GRAPH["ToolGraph"]()
    result = case.sources()
    # Follow imported presentation/parsing helpers as well as tool-local methods.
    for path, names in {
        "ai_tools/formatting.py": {
            "section",
            "key_value_block",
            "format_number",
            "format_datetime",
        },
        "agentic_eval/core/utils/json_utils.py": {"strip_code_fence"},
    }.items():
        tree = ast.parse((ROOT / path).read_text())
        selected = [node for node in tree.body if getattr(node, "name", None) in names]
        assert len(selected) == len(names)
        result[path] = ast.unparse(ast.Module(body=selected, type_ignores=[]))
    for helper in ("background_tools", "background_tool_validation"):
        path = f"ee/agenthub/traceerroragent/{helper}.py"
        result[path] = (ROOT / path).read_text()
    for path in sorted(
        (ROOT / "ee/agenthub/traceerroragent").glob("background*payload*.py")
    ):
        result[str(path.relative_to(ROOT))] = path.read_text()
    return result


class ToolBranches(unittest.TestCase):
    def test_all_allowlist_return_default_and_fallback_branches_classified(self):
        inventory = json.loads(
            (HERE / "analysis_tool_branch_inventory.json").read_text()
        )
        current = sources()
        self.assertEqual(set(current), set(inventory))
        for path, source in current.items():
            rows = inventory[path]
            self.assertTrue(
                all(
                    row[2]
                    in {
                        "presentation-or-optional-data",
                        "lookup-or-tree-control",
                        "background-refusal-or-validated-success",
                        "generation-owned-write-or-foreground-only",
                    }
                    for row in rows
                )
            )
            self.assertEqual(
                branches(source), Counter((row[0], row[1]) for row in rows), path
            )

    def test_census_refuses_new_fallbacks_even_without_failure_words(self):
        for source in sources().values():
            before = branches(source)
            self.assertNotEqual(
                before,
                branches(source + "\ndef helper(x):\n    if x:\n        return {}\n"),
            )
            self.assertNotEqual(
                before,
                branches(source + '\ndef helper(x):\n    return x or "normal"\n'),
            )

    def test_census_binds_refusal_to_its_exact_branch_not_only_call_name(self):
        original = 'def run(guard, bad):\n    if bad:\n        reject_chauffeur(guard)\n    else:\n        return "ok"\n'
        removed = original.replace("reject_chauffeur(guard)", "pass")
        moved = 'def run(guard, bad):\n    if bad:\n        pass\n    else:\n        reject_chauffeur(guard)\n        return "ok"\n'
        self.assertNotEqual(branches(original), branches(removed))
        self.assertNotEqual(branches(original), branches(moved))

    def test_semantic_refusals_dominate_known_degraded_branches(self):
        explore = (ROOT / "ai_tools/tools/tracing/explore_trace.py").read_text()
        self.assertIn("validate_chauffeur_report", explore)
        self.assertIn("validate_trace_spans", explore)
        worker = (ROOT / "tracer/tasks/error_analysis.py").read_text()
        self.assertIn("witness.observe_tool_result", worker)
        for name in ("submit_trace_finding", "submit_trace_scores"):
            tree = ast.parse((ROOT / f"ai_tools/tools/tracing/{name}.py").read_text())
            handler = next(
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.ExceptHandler)
                and ast.unparse(n.type) == "TraceErrorAnalysis.DoesNotExist"
            )
            self.assertTrue(
                any(
                    isinstance(n, ast.Call)
                    and ast.unparse(n.func) == "reject_chauffeur"
                    for n in ast.walk(handler)
                ),
                name,
            )
        span = (ROOT / "ai_tools/tools/tracing/read_trace_span.py").read_text()
        self.assertIn("validate_span_content", span)


if __name__ == "__main__":
    unittest.main()
