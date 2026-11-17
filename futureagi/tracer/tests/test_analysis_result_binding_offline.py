"""Socket-blocked result-binding regressions; no Django/DB/provider setup."""

import ast
import runpy
import unittest
from pathlib import Path

support = runpy.run_path(
    str(Path(__file__).with_name("test_background_analysis_entries_offline.py"))
)
ROOT = support["ROOT"]


class BindingRegressions(support["EntryTests"]):
    def test_predeploy_force_cannot_delete_a_completed_generation(self):
        generation = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, generation))
        self.assertTrue(self.state.complete(self.project, self.trace, generation))
        worker, analyze, query = self.retry_worker({"success": False})
        result = worker(self.trace, True)
        query.filter.return_value.delete.assert_not_called()
        analyze.assert_not_called()
        self.assertFalse(result["success"])

    def test_completion_durably_binds_its_own_result_not_latest_trace_row(self):
        old = self.abort()
        generation = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, generation))
        self.assertTrue(self.state.complete(self.project, self.trace, generation))
        row = self.state.get_state(self.trace)
        self.assertEqual(str(getattr(row, "result_analysis_id", None)), generation)
        # A late G0 finalizer cannot change the pointer even after G1 completed.
        self.assertFalse(self.state.complete(self.project, self.trace, old))
        self.assertEqual(
            str(self.state.get_state(self.trace).result_analysis_id), generation
        )

    def test_retry_without_own_result_aborts_even_if_old_partial_exists(self):
        self.abort()
        generation = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, generation))
        self.results.filter.return_value.first.return_value = None
        self.assertFalse(self.state.complete(self.project, self.trace, generation))
        self.assertEqual(self.state.get_state(self.trace).status, "aborted")
        self.assertIsNone(self.state.get_state(self.trace).result_analysis_id)
        self.assertEqual(self.poll.status(self.trace, has_analysis=True), "failed")
        self.results.filter.assert_called_with(
            pk=generation, project_id=self.project, trace_id=self.trace, deleted=False
        )

    def test_empty_retry_finalizer_reads_only_its_draft_never_old_partial(self):
        tree = ast.parse((ROOT / "tracer/tasks/error_analysis.py").read_text())
        inner = next(
            n
            for n in tree.body
            if getattr(n, "name", None) == "_falcon_analyze_trace_in_scope"
        )
        finalizer = next(
            n
            for n in ast.walk(inner)
            if isinstance(n, ast.FunctionDef) and n.name == "finalize"
        )
        reads = [ast.unparse(n) for n in ast.walk(finalizer) if isinstance(n, ast.Call)]
        self.assertTrue(any("TraceErrorAnalysis.drafts.filter" in n for n in reads))
        self.assertFalse(any("TraceErrorAnalysis.objects.filter" in n for n in reads))

    def test_all_result_models_default_to_completed_binding_for_every_reader(self):
        # Covers feed, drawer, cluster aggregation, analyzed-set selection,
        # embedding ingestion, reports, and reverse-related detail/group reads.
        tree = ast.parse((ROOT / "tracer/models/trace_error_analysis.py").read_text())
        for name in ("TraceErrorAnalysis", "TraceErrorDetail", "AnalysisErrorGroup"):
            cls = next(
                n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name
            )
            assignments = {
                n.targets[0].id: ast.unparse(n.value)
                for n in cls.body
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            }
            self.assertEqual(
                assignments.get("objects"), "CompletedAnalysisManager()", name
            )
            self.assertIn("AttemptResultMixin", [ast.unparse(b) for b in cls.bases])


if __name__ == "__main__":
    unittest.main()
