"""Typed consumers refuse unvalidated, mutated or cross-generation payloads."""

import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).parent
p = runpy.run_path(str(HERE / "test_analysis_publication_followups_offline.py"))
f = runpy.run_path(str(HERE / "analysis_payload_fixture.py"))


class PayloadCapabilities(p["PublishedLearning"]):
    def test_service_rejects_unvalidated_value_before_any_draft_or_learning(self):
        generation = self.begin()
        self.state.admit(self.project, self.trace, generation)
        self.identity = (self.project, self.trace, generation)
        with self.assertRaises(RuntimeError):
            self.service._save_owned_result(self.trace, f["healthy_report"]())
        self.assertEqual(self.parents, [])
        self.assertEqual(self.children, [])
        self.assertEqual(self.patterns, [])

    def test_cross_generation_capability_is_rejected(self):
        self.identity = (self.project, self.trace, "first")
        value = f["sealed_report"](self.identity)
        self.identity = (self.project, self.trace, "second")
        with self.assertRaises(RuntimeError):
            self.service._save_owned_result(self.trace, value)
        self.assertTrue(value._witness.failed)
        self.assertEqual(self.parents, [])
        self.assertEqual(self.patterns, [])

    def test_original_and_consumed_copies_cannot_mutate_the_seal(self):
        from ee.agenthub.traceerroragent.background_payload_reports import judge_report
        from ee.agenthub.traceerroragent.background_payloads import require_payload
        from tracer.services.analysis_result_completion import CompletionWitness

        witness = CompletionWitness(4)
        raw = f["healthy_report"]()
        payload = judge_report(raw, witness)
        raw["scores"]["factual_grounding"]["score"] = True
        copy = require_payload(payload, witness, "judge")
        copy["summary"]["overall_score"] = "4"
        self.assertEqual(
            require_payload(payload, witness, "judge")["summary"]["overall_score"], 4
        )
        self.assertIs(type(payload["scores"]["factual_grounding"]["score"]), int)
        with self.assertRaises((AttributeError, TypeError)):
            payload._json = "{}"

    def test_duplicate_json_nonfinite_and_salvaged_reports_refuse_stickily(self):
        from ee.agenthub.traceerroragent.background_payload_reports import (
            chauffeur_report,
            judge_arguments,
        )
        from tracer.services.analysis_result_completion import CompletionWitness

        cases = [
            '{"overall_score": 4, "overall_score": true}',
            '{"overall_score": NaN}',
            '{"overall_score": Infinity}',
            '{"overall_score": 4} trailing',
            '```json\n{"overall_score": 4}',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                witness = CompletionWitness(4)
                with self.assertRaises(RuntimeError):
                    judge_arguments("submit_summary", raw, witness, decode=True)
                self.assertTrue(witness.failed)
                with self.assertRaises(RuntimeError):
                    chauffeur_report(
                        {"span_groups": [{"signal": "normal", "spans": ["span"]}]},
                        witness,
                    )

    def test_raw_judge_handlers_require_a_typed_argument_capability(self):
        from ee.agenthub.traceerroragent.background_payload_judge import (
            execute_judge_tool,
        )
        from tracer.services.analysis_result_completion import CompletionWitness

        for name in ("submit_finding", "submit_scores", "submit_summary", "read_span"):
            witness = CompletionWitness(4)
            agent = SimpleNamespace(completion_witness=witness)
            with self.assertRaises(RuntimeError):
                execute_judge_tool(agent, name, {})
            self.assertTrue(witness.failed)

    def test_string_that_looks_like_json_stays_a_string(self):
        from ee.agenthub.traceerroragent.background_payload_reports import (
            judge_arguments,
        )
        from ee.agenthub.traceerroragent.background_payloads import require_payload
        from tracer.services.analysis_result_completion import CompletionWitness

        witness = CompletionWitness(4)
        raw = {"keyword": '["this is quoted source text"]'}
        value = judge_arguments("search_spans", json.dumps(raw), witness, decode=True)
        self.assertEqual(
            require_payload(value, witness, "judge-tool:search_spans"), raw
        )
        self.assertFalse(witness.failed)


c = runpy.run_path(str(HERE / "test_analysis_completion_witness_offline.py"))


class OuterReport(c["FalconCompletion"]):
    def test_invalid_outer_report_cannot_finalize_or_export(self):
        for content in (None, False, 42, [], {}, "", "   "):
            with self.subTest(content=content):
                self.guard = self.Guard("project", "user", "org", "workspace", 2)
                self.context.background_analysis = self.guard
                invoke, _, _ = self.configure("success")

                async def invalid_report(invoke=invoke, content=content, **kwargs):
                    result = await invoke(**kwargs)
                    result["content"] = content
                    return result

                invalid_report.agent = invoke.agent
                self.agent_runner = invalid_report
                result, effects, marker = self.worker(None)
                self.assertFalse(result["success"], result)
                self.assertEqual(effects, [])
                self.assertEqual(self.saved, [])
                marker.complete.assert_not_called()
                marker.abort.assert_called_once()
                self.assertTrue(invoke.agent.llm_client._witness.failed)


if __name__ == "__main__":
    unittest.main()
