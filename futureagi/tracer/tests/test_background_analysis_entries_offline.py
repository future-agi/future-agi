"""Entry and legacy-retry probes; stdlib/socket-blocked, no Django/DB setup."""

import ast
import runpy
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

support = runpy.run_path(
    str(Path(__file__).with_name("test_background_analysis_lifecycle_offline.py"))
)
ROOT = support["ROOT"]


class EntryTests(support["LifecycleTests"]):
    def retry_worker(self, result=None):
        tree = ast.parse((ROOT / "tracer/tasks/error_analysis.py").read_text())
        worker = next(
            n
            for n in tree.body
            if getattr(n, "name", None) == "run_deep_analysis_on_demand"
        )
        worker.decorator_list = []
        query = Mock()
        analyze = Mock(return_value=result or {"success": True, "trace_id": self.trace})
        ns = {
            "background_analysis_state": self.state,
            "attempt_scope": lambda *a: nullcontext(),
            "TraceErrorAnalysis": SimpleNamespace(objects=query),
            "analyze_single_trace": analyze,
            "close_old_connections": lambda: None,
        }
        exec(
            compile(
                ast.Module(body=[worker], type_ignores=[]), "retry-worker.py", "exec"
            ),
            ns,
        )
        return ns[worker.name], analyze, query

    def test_actual_retry_worker_fences_deletion_admission_and_completion(self):
        old = self.abort()
        new = self.begin()
        worker, analyze, query = self.retry_worker()
        outcome = worker(self.trace, True, project_id=self.project, generation=old)
        self.assertFalse(outcome["success"])
        analyze.assert_not_called()
        query.filter.assert_not_called()
        self.assertEqual(self.state.get_state(self.trace).status, "retry_pending")

        def run(*args, **kwargs):
            self.assertEqual(self.state.get_state(self.trace).status, "running")
            self.assertEqual(self.poll.status(self.trace, has_analysis=True), "running")
            return {"success": True}

        analyze.side_effect = run
        outcome = worker(self.trace, True, project_id=self.project, generation=new)
        self.assertTrue(outcome["success"])
        query.filter.assert_not_called()
        self.assertEqual(self.poll.status(self.trace, has_analysis=True), "done")
        worker(self.trace, True, project_id=self.project, generation=new)
        analyze.assert_called_once()
        query.filter.return_value.delete.assert_not_called()

    def test_retry_worker_failure_stays_aborted_with_partial_findings(self):
        self.abort()
        new = self.begin()
        worker, analyze, query = self.retry_worker({"success": False})
        outcome = worker(self.trace, True, project_id=self.project, generation=new)
        self.assertFalse(outcome["success"])
        self.assertEqual(self.state.get_state(self.trace).status, "aborted")
        self.assertEqual(self.poll.status(self.trace, has_analysis=True), "failed")

    def test_retry_worker_exception_is_terminal_and_stale_completion_fails(self):
        new = self.begin()
        worker, analyze, query = self.retry_worker()
        analyze.side_effect = RuntimeError("synthetic failure")
        with self.assertRaises(RuntimeError):
            worker(self.trace, True, project_id=self.project, generation=new)
        self.assertEqual(
            self.state.get_state(self.trace).reason_code, "analysis_failed"
        )
        self.assertFalse(self.state.complete(self.project, self.trace, new))

    def test_legacy_worker_delegates_admission_to_judge_without_deletion(self):
        worker, analyze, query = self.retry_worker()
        self.assertTrue(worker(self.trace, True)["success"])
        analyze.assert_called_once_with(self.trace, None, ingest_embeddings=False)
        query.filter.assert_not_called()
        self.assertEqual(self.store.rows, [])

    def test_dispatch_failure_does_not_abort_a_running_new_owner(self):
        dispatch = support["load"](
            "tracer/services/background_analysis_dispatch.py",
            {"background_analysis_state": self.state},
        )

        def queue_then_error(args, kwargs, **options):
            generation = kwargs["generations"][self.trace]
            self.assertTrue(self.state.admit(self.project, self.trace, generation))
            raise RuntimeError("lost dispatch acknowledgment")

        task = dispatch.durable_background_dispatch(
            SimpleNamespace(apply_async=queue_then_error)
        )
        with self.assertRaises(RuntimeError):
            task.apply_async(args=(self.project, [self.trace]), kwargs={})
        self.assertEqual(self.state.get_state(self.trace).status, "running")

    def test_feed_queue_failure_retains_durable_abort_and_fresh_retry(self):
        old = self.abort()
        feed, analyses, queued = self.feed()
        queued.side_effect = RuntimeError("queue unavailable")
        with self.assertRaises(RuntimeError):
            feed.dispatch_deep_analysis("cluster", self.trace, [self.project])
        row = self.state.get_state(self.trace)
        self.assertNotEqual(str(row.generation), old)
        self.assertEqual(row.status, "aborted")
        self.assertEqual(row.reason_code, "analysis_dispatch_failed")
        self.assertEqual(
            feed.get_deep_analysis("cluster", self.trace, [self.project]).status,
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
