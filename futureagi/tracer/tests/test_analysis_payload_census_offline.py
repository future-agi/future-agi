"""Closed original-payload parse/coerce graph, including shared agent reports."""

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
INVENTORY = Path(__file__).with_name("analysis_payload_sites.json")
FILES = (
    "ai_tools/base.py",
    "ee/falcon_ai/agent.py",
    "ai_tools/tools/tracing/explore_trace.py",
    "ai_tools/tools/tracing/read_trace_span.py",
    "ai_tools/tools/tracing/submit_trace_finding.py",
    "ai_tools/tools/tracing/submit_trace_scores.py",
    "ee/agenthub/traceerroragent/chauffeur.py",
    "ee/agenthub/traceerroragent/judge.py",
    "ee/agenthub/traceerroragent/traceerror.py",
    "ee/agenthub/traceerroragent/analysis_service.py",
    "ee/agenthub/traceerroragent/voice_compass.py",
    "ee/agenthub/traceerroragent/deprecated.py",
    "ee/agenthub/traceerroragent/background_tool_validation.py",
    "ee/agenthub/traceerroragent/background_tools.py",
    "ee/agenthub/traceerroragent/background_llm.py",
    "ee/agenthub/traceerroragent/background_provider.py",
    "ee/agenthub/traceerroragent/background_falcon.py",
    "ee/agenthub/traceerroragent/prompts_v2.py",
    "ai_tools/background.py",
    "tracer/services/analysis_result_completion.py",
    "tracer/tasks/error_analysis.py",
    "ee/falcon_ai/llm_client.py",
    "agentic_eval/core/llm/llm.py",
    "agentic_eval/core/utils/json_utils.py",
    "ee/agenthub/traceerroragent/background_gateway.py",
    "ee/licensing/managed_ai.py",
    "ee/licensing/activation_client.py",
    "ee/agenthub/traceerroragent/background_wire.py",
)


def sites(path):
    tree = ast.parse(path.read_text())
    found = []

    def visit(node, scope="module"):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope += "." + node.name
        # Close ALL call/import edges, not just names containing 'parse'. An
        # innocent-looking new helper cannot introduce a hidden coercion/send.
        if isinstance(
            node, (ast.Call, ast.Import, ast.ImportFrom, ast.IfExp, ast.BoolOp)
        ):
            found.append({"scope": scope, "expression": ast.unparse(node)})
        if isinstance(node, ast.If):
            found.append({"scope": scope, "expression": "if " + ast.unparse(node.test)})
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree)
    return found


class PayloadCensus(unittest.TestCase):
    def test_every_parse_coerce_and_default_site_has_a_closed_classification(self):
        self.assertTrue(
            INVENTORY.exists(), "ruling 485 original-payload boundary census missing"
        )
        inventory = json.loads(INVENTORY.read_text())
        owned = sorted(
            str(p.relative_to(ROOT))
            for p in (ROOT / "ee/agenthub/traceerroragent").glob(
                "background*payload*.py"
            )
        )
        self.assertTrue(owned, "one shared type-preserving validator is required")
        expected = sorted(set(FILES) | set(owned))
        self.assertEqual(sorted(inventory), expected)
        for file in expected:
            rows = inventory[file]
            self.assertEqual(
                [{k: row[k] for k in ("scope", "expression")} for row in rows],
                sites(ROOT / file),
                file,
            )
            for row in rows:
                self.assertIn(
                    row["classification"],
                    {
                        "validated-original",
                        "ERROR_BODY_NOT_CONSUMED",
                        "foreground-only",
                        "non-model-data",
                        "validated-consumer",
                    },
                )
                self.assertTrue(row["boundary"], row)
                self.assertIn(
                    row["order_proof"],
                    {
                        "GatewayOrder.test_strict_decode_precedes_every_gateway_response_observer",
                        "InstalledBedrock.test_error_body_is_not_consumed_or_published",
                        "InstalledBedrock.test_healthy_success_body_is_strict_and_completes",
                        "SDKWire.test_sdk_error_body_never_parsed_or_retried",
                        "GatewayControls.test_managed_error_content_never_reaches_evidence_decoder",
                        "NativeHTTPErrors.test_native_http_error_content_never_reaches_evidence_decoder",
                        "WirePayloads.test_valid_original_wire_all_tools_complete",
                        "WireOrder.test_every_legacy_evidence_default_is_dominated_by_a_background_exit",
                        "WireOrder.test_shared_original_report_parsers_exit_before_legacy_recovery",
                        "WireCapabilities.test_every_consumed_error_and_summary_attribute_is_required",
                        "WireOrder.test_inactive_legacy_default_paths_are_not_generation_call_edges",
                        "PayloadCensus.test_every_parse_coerce_and_default_site_has_a_closed_classification",
                    },
                )

    def test_admission_rows_have_response_free_boundary_proof(self):
        inventory = json.loads(INVENTORY.read_text())
        rows = [
            row
            for values in inventory.values()
            for row in values
            if row["expression"] in {"before_send()", "before_send(timeout)"}
        ]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["classification"], "non-model-data")
            self.assertEqual(
                row["admission_proof"],
                "RevocationWitness.test_admission_consumes_no_response_body",
            )

    def test_every_allowed_tool_intercepts_run_before_base_cleanup(self):
        for file in FILES[2:6]:
            tree = ast.parse((ROOT / file).read_text())
            cls = next(
                n
                for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name.endswith("Tool")
            )
            run = next(
                (
                    n
                    for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "run"
                ),
                None,
            )
            self.assertIsNotNone(run, file)
            self.assertIn("run_background_tool", ast.unparse(run))


if __name__ == "__main__":
    unittest.main()
