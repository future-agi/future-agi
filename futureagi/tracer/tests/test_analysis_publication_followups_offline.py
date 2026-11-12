"""r3b5 socket-blocked probes of actual definitions; not Django/DB evidence."""

import ast
import asyncio
import runpy
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
legacy = runpy.run_path(
    str(ROOT / "tracer/tests/test_analysis_legacy_snapshot_offline.py")
)
boundaries = runpy.run_path(
    str(ROOT / "tracer/tests/test_falcon_background_boundaries_offline.py")
)
definition, module = legacy["definition"], legacy["module"]
support, readers = legacy["support"], legacy["readers"]
stream_failure_runner = runpy.run_path(
    str(Path(__file__).with_name("analysis_stream_fixture.py"))
)["stream_failure_runner"]
SERVICE = "ee/agenthub/traceerroragent/analysis_service.py"


class PublishedLearning(unittest.TestCase):
    begin = support["LifecycleTests"].begin

    def setUp(self):
        support["LifecycleTests"].setUp(self)
        self.identity = None
        self.patterns = []
        self.children = []
        self.parents = []
        self.pattern_statuses = []

        def pattern(**kwargs):
            state = self.state.get_state(self.trace)
            self.pattern_statuses.append(state.status if state else "legacy")
            if self.patterns:
                return self.patterns[0], False
            row = SimpleNamespace(
                description=kwargs["pattern_description"],
                **kwargs["defaults"],
                save=Mock(),
            )
            self.patterns.append(row)
            return row, True

        def create(**kwargs):
            row = SimpleNamespace(
                pk=self.identity[2] if self.identity else "legacy",
                id=self.identity[2] if self.identity else "legacy",
                project_id=self.project,
                deleted=False,
                **kwargs,
            )
            self.parents.append(row)
            return row

        details = SimpleNamespace(
            create=lambda **kw: self.children.append(
                SimpleNamespace(analysis_id=kw["analysis"].pk, deleted=False, **kw)
            )
        )
        results = SimpleNamespace(
            drafts=SimpleNamespace(create=create),
            _base_manager=readers["Rows"](self.parents),
        )
        ns = {
            "transaction": SimpleNamespace(atomic=nullcontext),
            "Project": SimpleNamespace(
                objects=SimpleNamespace(get=lambda **kw: object())
            ),
            "TraceErrorAnalysis": results,
            "TraceErrorDetail": SimpleNamespace(drafts=details),
            "AnalysisErrorGroup": SimpleNamespace(drafts=Mock()),
            "ErrorPattern": SimpleNamespace(
                objects=SimpleNamespace(get_or_create=pattern)
            ),
            "logger": Mock(),
        }
        cls = type("Service", (), {})
        tree = ast.parse((ROOT / SERVICE).read_text())
        body = next(n.body for n in tree.body if isinstance(n, ast.ClassDef))
        for node in body:
            if getattr(node, "name", None) in (
                "__init__",
                "_save_owned_result",
                "_update_error_patterns",
                "_learn_published_result",
            ):
                setattr(
                    cls,
                    node.name,
                    definition(SERVICE, node.name, ns, "TraceErrorAnalysisService"),
                )
        self.service = cls(self.project)
        snapshot = support["load"](
            "tracer/services/analysis_result_snapshot.py",
            {
                "transaction": SimpleNamespace(atomic=nullcontext),
            },
        )
        p = patch.dict(
            "sys.modules",
            {
                "ee.agenthub.traceerroragent.analysis_service": module(
                    "service", TraceErrorAnalysisService=cls
                ),
                "tracer.services.analysis_result_binding": module(
                    "binding", current_attempt=lambda: self.identity
                ),
                "tracer.services.analysis_result_snapshot": module(
                    "snapshot", **vars(snapshot)
                ),
                "tracer.models.trace_error_analysis": module(
                    "models",
                    TraceErrorAnalysis=results,
                    TraceErrorDetail=SimpleNamespace(
                        _base_manager=readers["Rows"](self.children)
                    ),
                    AnalysisErrorGroup=SimpleNamespace(
                        _base_manager=readers["Rows"]([])
                    ),
                ),
            },
        )
        p.start()
        self.addCleanup(p.stop)
        completion = module(
            "completion",
            **runpy.run_path(
                str(ROOT / "tracer/services/analysis_result_completion.py")
            ),
        )
        helper = runpy.run_path(str(ROOT / "tracer/tests/analysis_payload_fixture.py"))
        payload_patch = patch.dict(
            "sys.modules",
            helper["modules"](
                {"tracer.services.analysis_result_completion": completion}
            ),
        )
        payload_patch.start()
        self.addCleanup(payload_patch.stop)

    def save_judge(self, generation=None):
        self.identity = (self.project, self.trace, generation) if generation else None
        helper = runpy.run_path(str(ROOT / "tracer/tests/analysis_payload_fixture.py"))
        result = (
            helper["sealed_report"](self.identity)
            if generation
            else helper["healthy_report"]()
        )
        return self.service._save_owned_result(self.trace, result)

    def test_background_judge_learns_once_only_after_publication(self):
        generation = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, generation))
        self.save_judge(generation)
        self.children[0].description = "persisted final finding"
        self.assertEqual(self.patterns, [])
        self.assertTrue(self.state.complete(self.project, self.trace, generation))
        self.assertEqual(len(self.patterns), 1, "published Judge stopped learning")
        self.assertEqual(self.patterns[0].description, "persisted final finding")
        self.assertEqual(self.pattern_statuses, ["completed"])
        self.assertFalse(self.state.complete(self.project, self.trace, generation))
        self.assertEqual(self.patterns[0].frequency, 1)
        new = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, new))
        self.save_judge(new)
        self.children[-1].description = "persisted final finding"
        self.assertTrue(self.state.complete(self.project, self.trace, new))
        self.assertFalse(self.state.complete(self.project, self.trace, new))
        self.assertEqual(self.patterns[0].frequency, 2)

    def test_pattern_failure_propagates_only_for_atomic_publication(self):
        update = self.service._update_error_patterns
        patterns = update.__globals__["ErrorPattern"].objects
        with patch.object(
            patterns, "get_or_create", side_effect=RuntimeError("synthetic")
        ):
            with self.assertRaises(RuntimeError):
                update(
                    [
                        SimpleNamespace(
                            category="test",
                            description="finding",
                            recommendation="fix",
                            immediate_fix="retry",
                            root_causes=["cause"],
                        )
                    ],
                    strict=True,
                )
            update([{"category": "test"}])  # Legacy remains best-effort.

    def test_aborted_and_superseded_drafts_never_teach(self):
        old = self.begin()
        self.state.admit(self.project, self.trace, old)
        self.save_judge(old)
        self.state.abort(self.project, self.trace, old, "analysis_failed")
        self.assertFalse(self.state.complete(self.project, self.trace, old))
        new = self.begin()
        self.state.admit(self.project, self.trace, new)
        self.assertFalse(self.state.complete(self.project, self.trace, old))
        self.assertEqual(self.patterns, [])
        self.save_judge(new)
        self.assertTrue(self.state.complete(self.project, self.trace, new))
        self.assertEqual(len(self.patterns), 1)
        self.assertEqual(self.patterns[0].frequency, 1)

    def test_true_legacy_judge_learns_immediately(self):
        self.save_judge()
        self.assertEqual(self.pattern_statuses, ["legacy"])
        self.assertEqual(self.patterns[0].frequency, 1)
        self.assertIsNone(self.state.get_state(self.trace))


class EpisodicSnapshots(readers["BoundReaders"]):
    def test_recent_and_similar_reports_pin_children_before_retry(self):
        for method_name, report_name in (
            ("get_recent_trace_error_analyses", "get_recent_traces"),
            ("get_analyses_for_similarity", "get_similar_traces"),
        ):
            with self.subTest(report=report_name):
                receipt = self.receipts.rows[0]
                receipt.status, receipt.result_analysis_id = "completed", "G1"
                for row in self.analyses.rows:
                    row.analysis_date = datetime.now()
                    row.overall_score, row.memory_enhanced = 3, True
                    (
                        row.high_impact_errors,
                        row.medium_impact_errors,
                        row.low_impact_errors,
                    ) = 0, 1, 0
                    row.trace.input, row.trace.output, row.trace.project_version = (
                        "hello trace",
                        "answer",
                        None,
                    )
                for detail in self.details.rows:
                    for name in (
                        "error_id",
                        "cluster_id",
                        "category",
                        "impact",
                        "urgency_to_fix",
                        "recommendation",
                        "immediate_fix",
                    ):
                        setattr(detail, name, detail.id)
                    detail.root_causes = [detail.id]
                # Discard only the date predicate; keep the real visibility manager.
                original = self.analyses.get_queryset
                query = SimpleNamespace(
                    filter=lambda original=original, **kw: original().filter(
                        **{k: v for k, v in kw.items() if k != "analysis_date__gte"}
                    ),
                    none=lambda: [],
                )
                ns = {
                    "TraceErrorAnalysis": SimpleNamespace(objects=query),
                    "logger": Mock(),
                }
                select = definition(
                    "tracer/queries/error_analysis.py",
                    method_name,
                    ns,
                    "TraceErrorAnalysisDB",
                )
                self.install_snapshot()
                selected_parents = []

                def select_then_retry(
                    select=select,
                    receipt=receipt,
                    selected_parents=selected_parents,
                    **kw,
                ):
                    parents = list(select(None, **kw))
                    receipt.status, receipt.result_analysis_id = "running", None
                    selected_parents.extend(parents)
                    return parents

                self.good.error_details = SimpleNamespace(
                    all=lambda: self.details.filter(analysis_id="G1")
                )
                db = SimpleNamespace(**{method_name: select_then_retry})
                db.get_error_details_for_analysis = MethodType(
                    definition(
                        "tracer/queries/error_analysis.py",
                        "get_error_details_for_analysis",
                        {"logger": Mock()},
                        "TraceErrorAnalysisDB",
                    ),
                    db,
                )
                db.get_error_annotations_for_trace = lambda *a: SimpleNamespace(
                    values=lambda: []
                )
                db.get_error_spans_for_trace = lambda *a: SimpleNamespace(
                    count=lambda: 0
                )
                report = definition(
                    "ee/agenthub/traceerroragent/memory.py",
                    report_name,
                    {"datetime": datetime, "timedelta": timedelta},
                    "EpisodicMemory",
                )
                memory = SimpleNamespace(db=db, memory_window=30, project_id="p")
                result = report(
                    memory,
                    **(
                        {"current_trace_input": "hello trace"}
                        if "similar" in report_name
                        else {}
                    ),
                )
                self.assertEqual(
                    [d["error_id"] for d in result[0]["error_details"]], ["new"]
                )
                parent = selected_parents[0]
                self.assertEqual(
                    [
                        d.id
                        for d in parent._prefetched_objects_cache["error_details"].all()
                    ],
                    ["new"],
                )
                self.assertEqual(
                    list(
                        parent._prefetched_objects_cache["analysis_error_groups"].all()
                    ),
                    [],
                )


class StreamFailures(boundaries["BoundaryTests"]):
    def test_background_stream_error_aborts_before_any_finalizer(self):
        submitted = []
        self.agent_runner, background = stream_failure_runner(self.context, submitted)
        self.modules["ai_tools.background"] = background
        self.finalizer_read = Mock()
        result, effects, marker = self.worker(None)
        self.assertEqual(submitted, ["unscored finding"])
        self.assertFalse(result["success"], result)
        self.assertEqual(result["outcome"], "aborted")
        marker.abort.assert_called_once_with(
            "project", "trace", "generation", "analysis_failed"
        )
        marker.complete.assert_not_called()
        self.finalizer_read.assert_not_called()
        self.assertEqual(effects, [])

    def test_interactive_stream_error_retains_ordinary_result_and_error_event(self):
        submitted, events = [], []
        runner, background = stream_failure_runner(SimpleNamespace(), submitted)

        async def callback(event):
            events.append(event)

        with patch.dict("sys.modules", {"ai_tools.background": background}):
            result = asyncio.run(
                runner(
                    user_message="analyze", history_messages=[], send_callback=callback
                )
            )
        self.assertEqual(submitted, ["unscored finding"])
        self.assertEqual([e["type"] for e in events].count("error"), 1)
        self.assertEqual(result["tool_calls"][0]["status"], "completed")
        self.assertNotIn("outcome", result)
        self.assertNotIn("success", result)


if __name__ == "__main__":
    unittest.main()
