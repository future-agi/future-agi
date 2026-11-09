"""Ruling 464: actual tool/AgentLoop/worker; malformed or partial is not success."""

import copy
import json
import runpy
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = Path(__file__).parent
p = runpy.run_path(str(HERE / "test_analysis_tool_calls_offline.py"))
ROOT, f, module = p["ROOT"], p["f"], p["b"]["boundaries"]["module"]
REPORT = {
    "sub_flows": [
        {"name": "Answer", "spans": ["span"], "summary": "Answered query", "flags": []}
    ],
    "trace_overview": "An answer was generated.",
}


class Result:
    def __init__(self, content="", data=None, is_error=False, **kw):
        self.content, self.data, self.is_error = content, data, is_error

    @classmethod
    def error(cls, *args, **kw):
        return cls(content="tool failed", is_error=True)

    not_found = validation_error = error


def select_tool(name, params, ns):
    classes = {
        "read_trace_span": "ReadTraceSpanTool",
        "submit_trace_finding": "SubmitTraceFindingTool",
        "submit_trace_scores": "SubmitTraceScoresTool",
    }
    cls = classes[name]
    ns = dict(ns, **{cls.replace("Tool", "Input"): object})
    local = dict(
        ns,
        __name__=f"ai_tools.tools.tracing.{name}",
        ToolResult=Result,
        key_value_block=lambda *a: "info",
        format_number=str,
        format_datetime=lambda *a: "time",
        LARGE_SPAN_THRESHOLD=3000,
    )
    f["compile_definitions"](
        ROOT / f"ai_tools/tools/tracing/{name}.py",
        local,
        names={cls, "_format_content"} if name == "read_trace_span" else {cls},
    )
    return local[cls](), SimpleNamespace(trace_id="trace", **params)


class Fallbacks(p["ToolCalls"]):
    def reject(self, report):
        witness = self.probe("success", report=json.dumps(report), success=False)
        self.assertTrue(witness.failed)
        self.assertTrue(witness.fallback_used)

    def test_both_report_dialects_require_nonempty_arrays(self):
        for key in ("sub_flows", "span_groups"):
            for value in (None, False, True, [], {}, "report", 1):
                with self.subTest(key=key, value=value):
                    self.reject({key: value, "trace_overview": "unfinished"})

    def test_subflow_members_have_explicit_required_schema(self):
        for value in (None, False, "flow", {}, {"name": "Flow"}):
            with self.subTest(member=value):
                self.reject(dict(REPORT, sub_flows=[value]))
        for field in ("name", "summary", "spans", "flags"):
            for value in (None, False, {}, 7, "" if field != "flags" else [False]):
                report = copy.deepcopy(REPORT)
                report["sub_flows"][0][field] = value
                with self.subTest(field=field, value=value):
                    self.reject(report)
        for value in (None, False, "", "   ", [], {}):
            with self.subTest(overview=value):
                self.reject(dict(REPORT, trace_overview=value))
        for spans in ([], [""], [False], ["not-in-trace"]):
            report = copy.deepcopy(REPORT)
            report["sub_flows"][0]["spans"] = spans
            with self.subTest(spans=spans):
                self.reject(report)

    def test_legacy_groups_require_signal_and_spans(self):
        for group in (
            None,
            False,
            "group",
            {},
            {"signal": "x"},
            {"signal": "", "spans": ["span"]},
            {"signal": "x", "spans": []},
            {"signal": "x", "spans": [None]},
        ):
            with self.subTest(group=group):
                self.reject({"span_groups": [group]})

    def test_valid_modern_and_legacy_reports_complete(self):
        for report in (
            REPORT,
            {"span_groups": [{"signal": "Answered query", "spans": ["span"]}]},
        ):
            with self.subTest(report=report):
                self.probe("success", report=json.dumps(report), success=True)
                self.assertNotIn("unavailable", self.tool_results[0].content)
                self.assertIn("Answered query", self.tool_results[0].content)

    def test_parse_recovery_and_unknown_payload_do_not_hide_fallback(self):
        for text in (
            'prefix {"span_groups":[{"signal":"ok","spans":["span"]}]} suffix',
            "{}",
            "[]",
            "null",
            "false",
            '{"other":1}',
            "[1]",
            "not json",
        ):
            with self.subTest(text=text):
                self.probe("success", report=text, success=False)

    def test_unavailable_branch_is_defensive_even_if_helper_returns_bad_report(self):
        def prepare(tool, ns, query, span, modules):
            tool._run_chauffeur = lambda *a, **k: {
                "sub_flows": [],
                "trace_overview": "",
            }
            return tool, SimpleNamespace(trace_id="trace")

        witness = self.probe(
            "success", prepare=prepare, success=False, sends_expected=0
        )
        self.assertTrue(witness.failed)
        self.assertTrue(witness.fallback_used)

    def test_empty_trace_and_partial_tree_refuse_before_provider(self):
        for mode in (
            "empty",
            "cycle",
            "orphan",
            "truncated-input",
            "truncated-output",
            "unserializable",
        ):

            def prepare(tool, ns, query, span, modules, mode=mode):
                spans = [span]
                if mode == "empty":
                    spans = []
                elif mode == "cycle":
                    span.parent_span_id = "span"
                elif mode == "orphan":
                    span.parent_span_id = "missing"
                elif mode == "truncated-input":
                    span.input = "x" * 2001
                elif mode == "truncated-output":
                    span.output = "x" * 2001
                else:
                    span.input = {"not-json": object()}
                modules["tracer.services.clickhouse.v2"].get_reader = lambda: (
                    nullcontext(SimpleNamespace(list_by_trace=lambda _: spans))
                )
                return tool, SimpleNamespace(trace_id="trace")

            with self.subTest(mode=mode):
                self.probe("success", prepare=prepare, success=False, sends_expected=0)

    def test_tool_errors_empty_results_and_exceptions_are_sticky(self):
        for mode in ("error", "empty", "none", "exception"):

            def prepare(tool, ns, query, span, modules, mode=mode):
                def execute(*a):
                    if mode == "exception":
                        raise RuntimeError("synthetic tool error")
                    return {
                        "error": Result.error(),
                        "empty": Result(),
                        "none": None,
                    }.get(mode)

                tool.execute = execute
                return tool, SimpleNamespace(trace_id="trace")

            with self.subTest(mode=mode):
                witness = self.probe(
                    "success", prepare=prepare, success=False, sends_expected=0
                )
                self.assertTrue(witness.failed)

    def test_read_span_not_found_empty_and_truncation_branches(self):
        for mode in (
            "trace-missing",
            "span-missing",
            "empty",
            "input",
            "output",
            "metadata",
            "events",
            "exact",
        ):

            def prepare(tool, ns, query, span, modules, mode=mode):
                ns["ToolResult"] = Result
                if mode == "trace-missing":
                    query.get.side_effect = modules[
                        "tracer.models.trace"
                    ].Trace.DoesNotExist()
                span.status = span.model = span.parent_span_id = None
                span.latency_ms = span.total_tokens = 0
                span.metadata, span.span_events = {}, []
                if mode == "empty":
                    span.input = span.output = ""
                if mode in ("input", "output", "exact"):
                    setattr(
                        span,
                        "input" if mode == "exact" else mode,
                        "x" * (50001 if mode == "exact" else 3001),
                    )
                if mode == "metadata":
                    span.metadata = {"data": "x" * 1001}
                if mode == "events":
                    span.span_events = ["x" * 1001]
                return select_tool(
                    "read_trace_span",
                    {
                        "span_id": "missing" if mode == "span-missing" else "span",
                        "exact": mode == "exact",
                    },
                    ns,
                )

            with self.subTest(mode=mode):
                self.probe("success", prepare=prepare, success=False, sends_expected=0)

    def test_failed_explicit_analysis_id_never_selects_or_creates_replacement(self):
        for name in ("submit_trace_finding", "submit_trace_scores"):

            def prepare(tool, ns, query, span, modules, name=name):
                model = Mock()
                missing = type("MissingAnalysis", (Exception,), {})
                model.DoesNotExist = missing
                model.drafts.get.side_effect = missing()
                modules["tracer.models.trace_error_analysis"] = module(
                    "analysis", TraceErrorAnalysis=model, TraceErrorDetail=Mock()
                )
                modules["tracer.models.project"] = module("project", Project=Mock())
                modules["django.db"] = module(
                    "db", transaction=SimpleNamespace(atomic=nullcontext)
                )
                modules["tracer.queries"] = module(
                    "queries", deep_analysis_state=Mock()
                )
                modules["tracer.services.clickhouse.v2"].get_reader = lambda: (
                    nullcontext(
                        SimpleNamespace(
                            root_ids_by_trace_ids=lambda _: {
                                "trace": ("root", "project")
                            }
                        )
                    )
                )
                binding = modules["tracer.services.analysis_result_binding"]
                binding.ResultOwnershipError = RuntimeError
                binding.legacy_attempt = nullcontext
                params = {
                    "analysis_id": "missing",
                    "confidence": 0.9,
                    "impact": "HIGH",
                    "recommended_priority": "HIGH",
                }
                selected, params = select_tool(name, params, ns)
                # Execute actual implementation; allow later stages at base so an
                # unnoticed replacement is observable instead of a fixture error.
                params.category = params.description = params.recommendation = "fact"
                params.urgency_to_fix = "HIGH"
                params.location_spans = params.evidence_snippets = (
                    params.root_causes
                ) = ["span"]
                params.immediate_fix = params.trace_impact = None
                for field in (
                    "overall_score",
                    "factual_grounding_score",
                    "privacy_and_safety_score",
                    "instruction_adherence_score",
                    "optimal_plan_execution_score",
                ):
                    setattr(params, field, 4)
                    setattr(params, field.replace("score", "reason"), "fact")
                params.insights = "fact"
                model.drafts.filter.return_value.first.return_value = None
                model.drafts.filter.return_value.order_by.return_value.first.return_value = None
                model.drafts.create.return_value = SimpleNamespace(
                    id="replacement",
                    total_errors=0,
                    high_impact_errors=0,
                    medium_impact_errors=0,
                    low_impact_errors=0,
                    recommended_priority="LOW",
                    save=Mock(),
                )
                modules[
                    "tracer.models.trace_error_analysis"
                ].TraceErrorDetail.drafts.filter.return_value.count.return_value = 0
                query.get.return_value.project = SimpleNamespace(id="project")
                self.replacement_model = model
                analysis_module = modules["tracer.models.trace_error_analysis"]
                execute = selected.execute

                def actual(params, context):
                    with patch.dict(
                        "sys.modules",
                        {"tracer.models.trace_error_analysis": analysis_module},
                    ):
                        return execute(params, context)

                selected.execute = actual
                return selected, params

            with self.subTest(tool=name):
                self.probe("success", prepare=prepare, success=False, sends_expected=0)
                self.replacement_model.drafts.get.assert_called_once()
                self.replacement_model.drafts.create.assert_not_called()
                self.replacement_model.drafts.filter.assert_not_called()

    def test_foreground_malformed_reports_retain_previous_fallback(self):
        run = p["b"]["boundaries"]["definitions"](
            "ai_tools/tools/tracing/explore_trace.py",
            {"_run_chauffeur"},
            {
                "json": json,
                "os": SimpleNamespace(environ={}),
                "strip_code_fence": lambda x: x,
            },
            owner="ExploreTraceTool",
        )["_run_chauffeur"]
        for value in (None, False, [], {}):
            with (
                self.subTest(value=value),
                patch.dict(
                    "sys.modules",
                    {
                        "litellm": module(
                            "litellm",
                            completion=lambda value=value, **k: f["response"](
                                json.dumps({"sub_flows": value}), "stop"
                            ),
                        ),
                        "structlog": module("structlog", get_logger=lambda *a: Mock()),
                    },
                ),
            ):
                self.assertEqual(
                    run(None, "trace"), {"sub_flows": value, "trace_overview": ""}
                )


if __name__ == "__main__":
    unittest.main()
