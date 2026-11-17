"""Every consumed model assessment is required before persistence or learning."""

import copy
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
s = runpy.run_path(str(HERE / "test_analysis_shared_payloads_offline.py"))


class RequiredEvidence(s["SharedPayloads"]):
    def test_missing_judge_evidence_aborts_actual_worker_and_consumer(self):
        fields = (
            (0, "category"),
            (0, "location_spans"),
            (0, "evidence_snippets"),
            (0, "description"),
            (0, "impact"),
            (0, "root_causes"),
            (0, "recommendation"),
            (0, "confidence"),
            (0, "urgency_to_fix"),
            (0, "immediate_fix"),
            (0, "trace_impact"),
            (2, "overall_score"),
            (2, "error_count"),
            (2, "insights"),
            (2, "recommended_priority"),
        )
        scope = s["n"]["p"]["j"]["JudgeCompletion"].execute.__globals__
        for index, field in fields:
            with self.subTest(index=index, field=field):
                self.setUp()

                def mutate(payloads, index=index, field=field):
                    payloads[index][1].pop(field, None)

                with patch.dict(scope, {"MODEL_PAYLOAD_MUTATOR": mutate}):
                    (result, _, cache, _, witnesses, _), _, _ = self.native()
                self.assertFalse(result["success"], (field, result))
                self.assertTrue(witnesses[0].failed)
                self.assertEqual(self.parents, [])
                self.assertEqual(self.children, [])
                self.assertEqual(self.patterns, [])
                cache.clear.assert_not_called()

    def test_missing_every_dimension_score_and_reason_aborts(self):
        scope = s["n"]["p"]["j"]["JudgeCompletion"].execute.__globals__
        for dimension in (
            "factual_grounding",
            "privacy_and_safety",
            "instruction_adherence",
            "optimal_plan_execution",
        ):
            for field in ("score", "reason"):
                with self.subTest(dimension=dimension, field=field):

                    def mutate(payloads, dimension=dimension, field=field):
                        payloads[1][1][dimension].pop(field)

                    with patch.dict(scope, {"MODEL_PAYLOAD_MUTATOR": mutate}):
                        (result, _, cache, _, witnesses, _), _, _ = self.native()
                    self.assertFalse(result["success"])
                    self.assertTrue(witnesses[0].failed)
                    self.assertEqual(self.parents, [])
                    self.assertEqual(self.patterns, [])
                    cache.clear.assert_not_called()

    def test_duplicate_native_judge_input_and_chauffeur_report_abort_then_recover(self):
        import json

        for kind in ("judge", "chauffeur"):
            with self.subTest(kind=kind):
                self.setUp()

                def mutate(raw, kind=kind):
                    raw = copy.deepcopy(raw)
                    if kind == "chauffeur":
                        for block in raw["content"]:
                            if block["type"] == "text" and block["text"].startswith(
                                '{"sub_flows"'
                            ):
                                block["text"] = (
                                    '{"sub_flows":false,' + block["text"][1:]
                                )
                    wire = json.dumps(raw)
                    if kind == "judge":
                        wire = wire.replace(
                            '"confidence": 0.9', '"confidence": true, "confidence": 0.9'
                        )
                    return wire

                (result, _, cache, _, witnesses, _), _, _ = self.native(
                    wire_mutator=mutate
                )
                self.assertFalse(result["success"])
                self.assertTrue(witnesses[0].failed)
                self.assertEqual(self.parents, [])
                self.assertEqual(self.children, [])
                self.assertEqual(self.patterns, [])
                cache.clear.assert_not_called()
                generation = self.state.get_state(self.trace).generation
                (recovered, _, _, _, _, _), _, _ = self.native()
                self.assertTrue(recovered["success"])
                self.assertNotEqual(
                    self.state.get_state(self.trace).generation, generation
                )
                self.assertEqual(len(self.patterns), 1)

    def test_missing_each_chauffeur_evidence_field_aborts(self):
        for field in ("name", "spans", "summary", "flags", "trace_overview"):
            with self.subTest(field=field):
                report = copy.deepcopy(s["REPORT"])
                (report if field == "trace_overview" else report["sub_flows"][0]).pop(
                    field
                )
                self.shared(report)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        RequiredEvidence(name)
        for name in vars(RequiredEvidence)
        if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main()
