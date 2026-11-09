"""Actual shared parser, real ModelConfigs and generation worker; no providers."""

import json
import re
import runpy
import unittest
from pathlib import Path
from types import MethodType
from unittest.mock import Mock, patch

HERE = Path(__file__).parent
n = runpy.run_path(str(HERE / "test_analysis_real_configs_offline.py"))
f = n["f"]
ROOT = n["ROOT"]
REPORT = {
    "sub_flows": [
        {"name": "Answer", "spans": ["span"], "summary": "Answered query", "flags": []}
    ],
    "trace_overview": "An answer was generated.",
}


class SharedPayloads(n["RealConfigs"]):
    def shared(self, report, *, success=False, legacy=False):
        old_response = f["response"]
        old_definition = n["p"]["original_definition"]
        methods = f["compile_definitions"](
            ROOT / "ee/agenthub/traceerroragent/chauffeur.py",
            {"json": json, "re": re, "logger": Mock()},
            "ChauffeurAgent",
            {"_parse_response", "_validate_sub_flows"},
        )

        def response(content="complete", reason="stop"):
            if isinstance(content, str) and content.startswith('{"sub_flows"'):
                content = json.dumps(report)
            return old_response(content, reason)

        def definition(path, name, ns, parent=None):
            method = old_definition(path, name, ns, parent)
            if name == "run" and parent == "ChauffeurAgent":

                def run(agent, *a, **k):
                    agent._parse_response = MethodType(
                        methods["_parse_response"], agent
                    )
                    agent._validate_sub_flows = MethodType(
                        methods["_validate_sub_flows"], agent
                    )
                    return method(agent, *a, **k)

                return run
            return method

        with (
            patch.dict(f, {"response": response}),
            patch.dict(n["p"], {"original_definition": definition}),
        ):
            if legacy:
                result, _, cache, _, witnesses, _ = self.replay(legacy=True)
            else:
                (result, _, cache, _, witnesses, _), _, _ = self.native()
        self.assertEqual(result["success"], success, result)
        if legacy:
            self.assertIsNone(self.state.get_state(self.trace))
        elif success:
            self.assertEqual(self.state.get_state(self.trace).status, "completed")
            self.assertTrue(witnesses[0].satisfied)
            self.assertEqual(len(self.parents), 1)
            self.assertEqual(len(self.patterns), 1)
            cache.clear.assert_called_once()
        else:
            self.assertEqual(self.state.get_state(self.trace).status, "aborted")
            self.assertTrue(witnesses[0].failed or witnesses[0].fallback_used)
            self.assertFalse(witnesses[0].satisfied)
            self.assertEqual(self.parents, [])
            self.assertEqual(self.patterns, [])
            cache.clear.assert_not_called()

    def test_malformed_and_defaulted_shared_reports_abort(self):
        for report in (
            {"span_groups": [{}]},
            {"sub_flows": "unavailable", "trace_overview": ""},
            {"sub_flows": [{}], "trace_overview": ""},
            {"sub_flows": False},
            {"sub_flows": None},
            {"sub_flows": []},
            {"span_groups": [{"signal": "flow", "spans": '["span"]'}]},
            {
                "sub_flows": [
                    {"name": "flow", "spans": ["span"], "summary": True, "flags": []}
                ],
                "trace_overview": "overview",
            },
        ):
            with self.subTest(report=report):
                self.shared(report)

    def test_judge_original_arguments_abort_before_publication_and_learning(self):
        cases = [
            (0, "confidence", True),
            (0, "confidence", "0.9"),
            (0, "location_spans", '["span"]'),
            (0, "evidence_snippets", False),
            (0, "root_causes", []),
            (2, "overall_score", True),
            (2, "overall_score", "4"),
            (2, "error_count", True),
            (2, "error_count", "1"),
            (2, "insights", ""),
            (2, "recommended_priority", "UNKNOWN"),
        ]
        for dimension in (
            "factual_grounding",
            "privacy_and_safety",
            "instruction_adherence",
            "optimal_plan_execution",
        ):
            for bad in (True, "4", None, {}, {"score": 4}):
                cases.append(
                    (
                        1,
                        dimension,
                        bad
                        if isinstance(bad, dict)
                        else {"score": bad, "reason": "evidence"},
                    )
                )
        for index, field, value in cases:
            with self.subTest(index=index, field=field, value=value):

                def mutate(payloads, index=index, field=field, value=value):
                    payloads[index][1][field] = value

                scope = n["p"]["j"]["JudgeCompletion"].execute.__globals__
                with patch.dict(scope, {"MODEL_PAYLOAD_MUTATOR": mutate}):
                    (result, _, cache, _, witnesses, _), requests, _ = self.native()
                self.assertFalse(result["success"], result)
                self.assertTrue(witnesses[0].failed)
                self.assertEqual(self.state.get_state(self.trace).status, "aborted")
                self.assertEqual(self.parents, [])
                self.assertEqual(self.patterns, [])
                cache.clear.assert_not_called()
                self.assertEqual(len(requests), 2)

    def test_valid_shared_report_completes(self):
        self.shared(REPORT, success=True)

    def test_valid_legacy_report_completes_without_empty_defaults(self):
        self.shared(
            {"span_groups": [{"signal": "Answered the query", "spans": ["span"]}]},
            success=True,
        )

    def test_invalid_report_then_distinct_generation_recovers(self):
        self.shared({"span_groups": [{}]})
        generation = self.state.get_state(self.trace).generation
        self.shared(REPORT, success=True)
        self.assertNotEqual(self.state.get_state(self.trace).generation, generation)

    def test_foreground_defaulted_report_remains_compatible(self):
        self.shared({"span_groups": [{}]}, success=True, legacy=True)


if __name__ == "__main__":
    unittest.main()
