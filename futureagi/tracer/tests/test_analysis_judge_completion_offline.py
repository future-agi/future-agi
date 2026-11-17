"""Real Judge/run, summarize, service and retry worker; synthetic DB/provider only."""

import contextlib
import gc
import json
import runpy
import sys
import threading
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
pub = runpy.run_path(
    str(Path(__file__).with_name("test_analysis_publication_followups_offline.py"))
)
entries = runpy.run_path(
    str(Path(__file__).with_name("test_background_analysis_entries_offline.py"))
)
definition, module = pub["definition"], pub["module"]
MODEL_PAYLOAD_MUTATOR = None


class JudgeCompletion(pub["PublishedLearning"]):
    def execute(self, mode, legacy=False):
        generation = None if legacy else self.begin()
        self.identity = None if legacy else (self.project, self.trace, generation)
        ns = {
            "Any": object,
            "logger": Mock(),
            "json": json,
            "get_judge_system_prompt": lambda: "offline",
            "build_judge_user_prompt": lambda **k: "offline",
            "JUDGE_TOOLS": [],
        }
        Judge = type("Judge", (), {})
        for name in (
            "run",
            "_execute_tool",
            "_compute_fallback_scores",
            "_compute_fallback_summary",
            "_tool_submit_finding",
            "_tool_submit_scores",
            "_tool_submit_summary",
        ):
            setattr(
                Judge,
                name,
                definition(
                    "ee/agenthub/traceerroragent/judge.py", name, ns, "JudgeAgent"
                ),
            )
        judge = Judge()
        judge.errors, judge.scores, judge.summary = [], {}, {}
        judge.max_turns, judge._investigation_complete, judge.token_usage = (
            (3 if mode == "limit" else 4),
            False,
            {},
        )
        judge._error_counter, judge._counter_lock = 0, threading.Lock()
        calls = []

        def response(reason, tools=None):
            message = SimpleNamespace(
                tool_calls=tools,
                model_dump=lambda: {"role": "assistant", "tool_calls": []},
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason=reason, message=message)]
            )

        def completion(**kwargs):
            calls.append(kwargs)
            if mode == "exception":
                raise RuntimeError("synthetic terminal provider failure")
            if mode == "fallback":
                return response("stop")
            if len(calls) == 1:
                report = runpy.run_path(
                    str(ROOT / "tracer/tests/analysis_payload_fixture.py")
                )["healthy_report"]()
                finding = {
                    k: v
                    for k, v in report["errors"][0].items()
                    if k not in {"error_id", "cluster_id"}
                }
                payloads = [
                    ("submit_finding", finding),
                    ("submit_scores", report["scores"]),
                    ("submit_summary", report["summary"]),
                ]
                if MODEL_PAYLOAD_MUTATOR is not None:
                    MODEL_PAYLOAD_MUTATOR(payloads)
                return response(
                    "tool_calls",
                    [
                        SimpleNamespace(
                            id=name,
                            function=SimpleNamespace(
                                name=name, arguments=json.dumps(data)
                            ),
                        )
                        for name, data in payloads
                    ],
                )
            if mode == "empty":
                return SimpleNamespace(choices=[])
            return response("stop" if mode in ("success", "limit") else mode)

        for name in (
            "read_span",
            "read_span_exact",
            "get_children",
            "get_spans_by_type",
            "search_spans",
        ):
            setattr(judge, "_tool_" + name, lambda **k: {})
        judge._compact_old_tool_results = lambda *a: None
        judge.llm = SimpleNamespace(_get_completion_with_tools=completion)

        def chauffeur_completion(**kwargs):
            chauffeur_client.last_finish_reason = "stop"
            return '{"sub_flows": [{"name": "offline", "spans": ["span"], "summary": "Answered query", "flags": []}], "trace_overview": "Answered query"}'

        chauffeur_client = SimpleNamespace(
            _get_completion_content=chauffeur_completion, model_name="offline"
        )
        chauffeur = SimpleNamespace(
            llm=chauffeur_client,
            trace_execution_summary="offline",
            _parse_response=json.loads,
            get_token_usage=lambda: {},
        )
        chauffeur.run = MethodType(
            definition(
                "ee/agenthub/traceerroragent/chauffeur.py",
                "run",
                {"logger": Mock(), "CHAUFFEUR_SYSTEM_PROMPT": "offline"},
                "ChauffeurAgent",
            ),
            chauffeur,
        )

        def agent_fixture(agent, **kwargs):
            # This suite isolates high-level orchestration. The provider-attempt
            # suite replaces these doubles with actual constructors/LLM methods.
            witness = kwargs.get("completion_witness")
            agent.completion_witness = witness
            if witness is not None:
                from tracer.services.analysis_result_completion import RecordingLLM

                agent.llm = RecordingLLM(agent.llm, witness)
            return agent

        summarize = definition(
            "ee/agenthub/traceerroragent/traceerror.py",
            "_summarize_logic_v2",
            {
                "logger": Mock(),
                "json": json,
                "ChauffeurAgent": lambda **k: agent_fixture(chauffeur, **k),
                "JudgeAgent": lambda **k: agent_fixture(judge, **k),
            },
            "TraceErrorAnalysisAgent",
        )
        trace_agent = SimpleNamespace(
            trace_id=self.trace,
            spans=[],
            span_map={"span": SimpleNamespace(id="span")},
            create_trace_execution_summary=lambda: "",
            _build_trace_outline=lambda: "",
            get_memory_context=lambda: {},
            _build_parent_map=lambda: {},
            enable_memory=False,
            save_to_db=True,
            analysis_service=self.service,
        )
        trace_agent._summarize_logic = MethodType(summarize, trace_agent)
        trace_agent.project_name = "offline"
        tracer = SimpleNamespace(
            start_as_current_span=lambda *a: contextlib.nullcontext(Mock())
        )
        provider = SimpleNamespace(get_tracer=lambda *a: tracer)
        trace_agent.summarize = MethodType(
            definition(
                "ee/agenthub/traceerroragent/traceerror.py",
                "_summarize_owned",
                {
                    "logger": Mock(),
                    "json": json,
                    "register_project": lambda **kw: provider,
                    "get_user_id_from_trace": lambda *a: None,
                    "get_user_attributes": lambda *a: None,
                    "toggle_instrumentation": Mock(),
                    "Status": lambda s: s,
                    "StatusCode": SimpleNamespace(OK="ok"),
                },
                "TraceErrorAnalysisAgent",
            ),
            trace_agent,
        )
        self.service.save_analysis_result = MethodType(
            definition(
                pub["SERVICE"], "save_analysis_result", {}, "TraceErrorAnalysisService"
            ),
            self.service,
        )
        project = SimpleNamespace(
            id=self.project, organization=SimpleNamespace(id="org")
        )
        projects = Mock()
        projects.select_related.return_value.filter.return_value.first.return_value = (
            project
        )
        statuses = SimpleNamespace(
            SUCCESS=SimpleNamespace(value="success"),
            ERROR=SimpleNamespace(value="error"),
        )
        cache = SimpleNamespace(set_failed=Mock(), clear=Mock())
        effects = Mock()
        mods = {
            "traceai_litellm": module(
                "instrument",
                LiteLLMInstrumentor=lambda: SimpleNamespace(uninstrument=lambda: None),
            ),
            "ee.agenthub.traceerroragent.traceerror": module(
                "traceerror", TraceErrorAnalysisAgent=lambda **k: trace_agent
            ),
            "tfc.constants.api_calls": module(
                "constants",
                APICallStatusChoices=statuses,
                APICallTypeChoices=SimpleNamespace(
                    TRACE_ERROR_ANALYSIS=SimpleNamespace(value="analysis")
                ),
            ),
            "tracer.models.project": module(
                "project", Project=SimpleNamespace(objects=projects)
            ),
            "tracer.queries": module("queries", deep_analysis_state=cache),
            "tracer.queries.error_analysis": module(
                "db",
                TraceErrorAnalysisDB=lambda: SimpleNamespace(
                    ingest_trace_error_embeddings=effects
                ),
            ),
            "tracer.queries.helpers": module(
                "helpers", get_default_workspace_for_project=lambda p: None
            ),
            "tracer.services.clickhouse.v2": module(
                "ch",
                get_reader=lambda: contextlib.nullcontext(
                    SimpleNamespace(
                        root_ids_by_trace_ids=lambda ids: {
                            self.trace: ("root", self.project)
                        }
                    )
                ),
            ),
            "ee.usage.utils.usage_entries": None,
            "ee.usage.services.metering": None,
            "ee.usage.schemas.event_types": None,
            "ee.usage.schemas.events": None,
            "ee.usage.services.config": None,
            "ee.usage.services.emitter": None,
            "ee.usage.utils.event_properties": None,
        }
        witness = ROOT / "tracer/services/analysis_result_completion.py"
        if witness.exists():
            mods["tracer.services.analysis_result_completion"] = module(
                "completion", **runpy.run_path(str(witness))
            )
        binding = sys.modules["tracer.services.analysis_result_binding"]
        binding.legacy_attempt = lambda *a: contextlib.nullcontext()
        analyze = definition(
            "tracer/tasks/error_analysis.py",
            "_analyze_single_trace_in_scope",
            {
                "settings": SimpleNamespace(DEBUG=False),
                "gc": gc,
                "logger": Mock(),
                "close_old_connections": lambda: None,
            },
        )
        worker, analyze_mock, _ = entries["EntryTests"].retry_worker(self)
        analyze_mock.side_effect = lambda trace_id, task_id, **kw: analyze(
            trace_id, task_id, **kw
        )
        with patch.dict("sys.modules", mods):
            if legacy:
                outcome = analyze(self.trace, None, ingest_embeddings=False)
            else:
                outcome = worker(
                    self.trace, True, project_id=self.project, generation=generation
                )
        effects.assert_not_called()
        return outcome, calls, cache

    def assert_aborted(self, mode):
        outcome, calls, cache = self.execute(mode)
        self.assertTrue(calls)
        self.assertFalse(outcome["success"], outcome)
        self.assertEqual(self.state.get_state(self.trace).status, "aborted")
        self.assertIsNone(self.state.get_state(self.trace).result_analysis_id)
        self.assertEqual(self.parents, [], "must refuse before fallback persistence")
        self.assertEqual(self.patterns, [])
        cache.clear.assert_not_called()

    def test_swallowed_judge_provider_exception_aborts_before_save(self):
        self.assert_aborted("exception")

    def test_judge_requires_normal_terminal_after_summary_tool(self):
        self.assert_aborted("max_tokens")

    def test_judge_empty_response_aborts(self):
        self.assert_aborted("empty")

    def test_judge_limit_aborts_even_with_terminal_stop(self):
        self.assert_aborted("limit")

    def test_judge_fallback_after_stop_aborts(self):
        self.assert_aborted("fallback")

    def test_successful_judge_still_completes_and_learns(self):
        outcome, calls, cache = self.execute("success")
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(self.state.get_state(self.trace).status, "completed")
        self.assertEqual(self.parents[0].overall_score, 4)
        self.assertEqual(len(self.patterns), 1)
        cache.clear.assert_called_once()

    def test_true_legacy_judge_provider_failure_retains_fallback(self):
        outcome, calls, cache = self.execute("exception", legacy=True)
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(self.state.get_state(self.trace))
        self.assertEqual(self.parents[0].overall_score, 5.0)
        cache.clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
