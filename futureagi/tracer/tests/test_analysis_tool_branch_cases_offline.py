"""Remaining classified fallback branches and explicit foreground controls."""

import json
import runpy
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = Path(__file__).parent
f = runpy.run_path(str(HERE / "test_analysis_tool_fallbacks_offline.py"))
p, module, select_tool, Result = (
    f[n] for n in ("p", "module", "select_tool", "Result")
)


class BranchCases(p["ToolCalls"]):
    def submission(self, name, case, *, foreground=False):
        def prepare(tool, ns, query, span, modules):
            params = {
                "analysis_id": "missing" if case == "missing-id" else None,
                "confidence": 0.9,
                "impact": "HIGH",
                "recommended_priority": "HIGH",
                "urgency_to_fix": "HIGH",
                "category": "fact",
                "description": "fact",
                "recommendation": "fact",
                "location_spans": ["span"],
                "root_causes": ["fact"],
                "evidence_snippets": ["answer"],
                "immediate_fix": None,
                "trace_impact": None,
                "insights": "fact",
            }
            for field in (
                "overall",
                "factual_grounding",
                "privacy_and_safety",
                "instruction_adherence",
                "optimal_plan_execution",
            ):
                params[field + "_score"] = 4.0
                params[field + "_reason"] = "fact"
            changes = {
                "confidence": ("confidence", 0.1),
                "impact": ("impact", "INVALID"),
                "priority": ("recommended_priority", "INVALID"),
                "empty-evidence": ("evidence_snippets", []),
                "empty-reason": ("factual_grounding_reason", ""),
                "empty-description": ("description", ""),
                "empty-insights": ("insights", ""),
                "invalid-score": ("overall_score", float("nan")),
            }
            if case in changes:
                key, value = changes[case]
                params[key] = value
            selected, params = select_tool(name, params, ns)
            missing = type("Missing", (Exception,), {})
            model, details = Mock(), Mock()
            model.DoesNotExist = missing
            model.drafts.get.side_effect = missing()
            model.drafts.filter.return_value.first.return_value = None
            model.drafts.filter.return_value.order_by.return_value.first.return_value = None
            analysis = SimpleNamespace(
                id="new",
                total_errors=0,
                overall_score=None,
                high_impact_errors=0,
                medium_impact_errors=0,
                low_impact_errors=0,
                recommended_priority="LOW",
                save=Mock(),
            )
            model.drafts.create.return_value = analysis
            details.drafts.filter.return_value.count.return_value = 0
            project = Mock()
            project.objects.filter.return_value.first.return_value = (
                None if case == "trace-missing" else SimpleNamespace(id="project")
            )
            query.get.return_value.project = SimpleNamespace(id="project")
            if case == "trace-missing":
                query.get.side_effect = modules[
                    "tracer.models.trace"
                ].Trace.DoesNotExist()
            modules["django.db"] = module(
                "db", transaction=SimpleNamespace(atomic=nullcontext)
            )
            binding = modules["tracer.services.analysis_result_binding"]
            binding.ResultOwnershipError = RuntimeError
            binding.legacy_attempt = nullcontext
            if case == "generation-missing":
                binding.current_attempt = lambda: None
            replacements = {
                "tracer.models.trace_error_analysis": module(
                    "analysis", TraceErrorAnalysis=model, TraceErrorDetail=details
                ),
                "tracer.models.project": module("project", Project=project),
                "tracer.queries": module("queries", deep_analysis_state=Mock()),
                "tracer.services.clickhouse.v2": module(
                    "reader",
                    get_reader=lambda: nullcontext(
                        SimpleNamespace(
                            root_ids_by_trace_ids=lambda _: {
                                "trace": ("root", "project")
                            }
                        )
                    ),
                ),
            }
            execute = selected._execute_owned if foreground else selected.execute

            def actual(params, context):
                guard = context.background_analysis
                if foreground:
                    context.background_analysis = None
                try:
                    with patch.dict("sys.modules", replacements):
                        return execute(params, context)
                finally:
                    context.background_analysis = guard

            selected.execute = actual
            self.draft_model, self.draft_details = model, details
            return selected, params

        return prepare

    def test_actual_submission_non_success_branches_abort_without_writes(self):
        cases = {
            "submit_trace_finding": (
                "confidence",
                "impact",
                "trace-missing",
                "generation-missing",
                "empty-evidence",
                "empty-description",
            ),
            "submit_trace_scores": (
                "priority",
                "trace-missing",
                "generation-missing",
                "empty-reason",
                "empty-insights",
                "invalid-score",
            ),
        }
        for name, modes in cases.items():
            for case in modes:
                with self.subTest(tool=name, case=case):
                    witness = self.probe(
                        "success",
                        prepare=self.submission(name, case),
                        success=False,
                        sends_expected=0,
                    )
                    self.assertTrue(witness.failed)
                    self.draft_model.drafts.create.assert_not_called()
                    self.draft_details.drafts.create.assert_not_called()

    def test_implicit_new_analysis_is_valid_not_failed_identity_fallback(self):
        for name in ("submit_trace_finding", "submit_trace_scores"):
            with self.subTest(tool=name):
                self.probe(
                    "success",
                    prepare=self.submission(name, "new"),
                    success=True,
                    sends_expected=0,
                )
                self.draft_model.drafts.create.assert_called_once()
                self.draft_model.drafts.get.assert_not_called()

    def test_foreground_explicit_missing_id_still_creates_replacement(self):
        for name in ("submit_trace_finding", "submit_trace_scores"):
            with self.subTest(tool=name):
                self.probe(
                    "success",
                    prepare=self.submission(name, "missing-id", foreground=True),
                    success=True,
                    sends_expected=0,
                )
                self.draft_model.drafts.create.assert_called_once()
                self.draft_model.drafts.get.assert_called_once()

    def test_complete_fence_is_formatting_but_truncated_fence_is_fallback(self):
        report = json.dumps(f["REPORT"])
        self.probe("success", report="```json\n" + report + "\n```", success=True)
        self.probe("success", report="```json\n" + report, success=False)

    def test_partial_span_coverage_aborts(self):
        def prepare(tool, ns, query, span, modules):
            second = SimpleNamespace(**dict(vars(span), id="second"))
            modules["tracer.services.clickhouse.v2"].get_reader = lambda: nullcontext(
                SimpleNamespace(list_by_trace=lambda _, *, project_id: [span, second])
            )
            return tool, SimpleNamespace(trace_id="trace")

        self.probe("success", prepare=prepare, success=False)

    def test_exploration_missing_trace_is_a_failed_tool_not_empty_success(self):
        def prepare(tool, ns, query, span, modules):
            ns["ToolResult"] = Result
            query.get.side_effect = modules["tracer.models.trace"].Trace.DoesNotExist()
            return tool, SimpleNamespace(trace_id="trace")

        self.probe("success", prepare=prepare, success=False, sends_expected=0)

    def test_foreground_empty_trace_and_partial_span_keep_original_results(self):
        for case in ("empty-trace", "partial-span"):

            def prepare(tool, ns, query, span, modules, case=case):
                if case == "empty-trace":
                    modules["tracer.services.clickhouse.v2"].get_reader = lambda: (
                        nullcontext(
                            SimpleNamespace(list_by_trace=lambda _, *, project_id: [])
                        )
                    )
                    params = SimpleNamespace(trace_id="trace")
                else:
                    span.input = "x" * 4000
                    span.status = span.model = span.parent_span_id = None
                    span.latency_ms = span.total_tokens = 0
                    span.metadata, span.span_events = {}, []
                    tool, params = select_tool(
                        "read_trace_span", {"span_id": "span", "exact": False}, ns
                    )
                execute = tool.execute

                def foreground(params, context):
                    guard = context.background_analysis
                    context.background_analysis = None
                    try:
                        return execute(params, context)
                    finally:
                        context.background_analysis = guard

                tool.execute = foreground
                return tool, params

            with self.subTest(case=case):
                self.probe("success", prepare=prepare, success=True, sends_expected=0)
                self.assertIn(
                    "no spans" if case == "empty-trace" else "chars omitted",
                    self.tool_results[0].content,
                )


if __name__ == "__main__":
    unittest.main()
