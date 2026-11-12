"""Actual model mixin/attempt context against fake lifecycle storage; no DB."""

import runpy
import unittest
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar, copy_context
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

support = runpy.run_path(
    str(Path(__file__).with_name("test_background_analysis_lifecycle_offline.py"))
)
readers = runpy.run_path(
    str(Path(__file__).with_name("test_analysis_bound_readers_offline.py"))
)


class WriterFences(support["LifecycleTests"]):
    def setUp(self):
        super().setUp()
        ns = {
            "BaseModelManager": readers["BaseManager"],
            "AllObjectsManager": readers["BaseManager"],
            "NoWorkspaceFilterManager": readers["BaseManager"],
            "models": SimpleNamespace(QuerySet=object),
            "transaction": SimpleNamespace(atomic=nullcontext),
            "_attempt": ContextVar("writer_test", default=None),
            "UUID": UUID,
        }
        support["load"]("tracer/services/analysis_result_binding.py", ns)
        for name in ("attempt_scope", "locked_attempt", "legacy_attempt"):
            ns[name] = contextmanager(ns[name])
        self.binding = SimpleNamespace(**ns)
        p = patch.dict(
            "sys.modules",
            {
                "tracer.models.background_trace_analysis_state": support["module"](
                    "state",
                    BackgroundTraceAnalysisState=SimpleNamespace(objects=self.store),
                ),
            },
        )
        p.start()
        self.addCleanup(p.stop)
        writes = self.writes = []

        class Persist:
            def save(self, *args, **kwargs):
                writes.append(str(self.pk))
                self._state.adding = False

        self.Model = type(
            "TraceErrorAnalysis", (self.binding.AttemptResultMixin, Persist), {}
        )

    def record(self):
        row = self.Model()
        row.project_id, row.trace_id = self.project, self.trace
        row.pk, row._state = uuid4(), SimpleNamespace(adding=True)
        return row

    def test_model_saves_reserve_generation_id_and_reject_stale_continuation(self):
        generation = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, generation))
        record = self.record()
        with self.binding.attempt_scope(self.project, self.trace, generation):
            record.save()
            late = copy_context()
        self.assertEqual(str(record.pk), generation)
        self.assertTrue(
            self.state.abort(self.project, self.trace, generation, "analysis_failed")
        )
        new = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, new))
        self.assertTrue(self.state.complete(self.project, self.trace, new))
        with self.assertRaises(self.binding.ResultOwnershipError):
            late.run(record.save)
        self.assertEqual(self.writes, [generation])

    def test_no_generation_and_completed_generation_cannot_mutate(self):
        legacy = self.record()
        legacy.save()
        self.assertEqual(self.writes, [str(legacy.pk)])
        self.writes.clear()
        generation = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, generation))
        record = self.record()
        with self.binding.attempt_scope(self.project, self.trace, generation):
            record.save()
        self.assertTrue(self.state.complete(self.project, self.trace, generation))
        with self.binding.attempt_scope(self.project, self.trace, generation):
            with self.assertRaises(self.binding.ResultOwnershipError):
                record.save()
        with (
            self.assertRaises(self.binding.ResultOwnershipError),
            self.binding.legacy_attempt(self.project, self.trace),
        ):
            self.fail("legacy writer admitted over completed receipt")
        with self.assertRaises(self.binding.ResultOwnershipError):
            record.delete()
        self.assertEqual(self.writes, [generation])

    def test_cross_identity_and_bulk_mutations_fail_closed(self):
        generation = self.begin()
        self.assertTrue(self.state.admit(self.project, self.trace, generation))
        with self.binding.attempt_scope(self.project, self.trace, generation):
            record = self.record()
            record.trace_id = str(uuid4())
            with self.assertRaises(self.binding.ResultOwnershipError):
                record.save()
        with self.binding.attempt_scope(self.project, self.trace, generation):
            for method, args in (
                ("update", ()),
                ("delete", ()),
                ("bulk_create", ([],)),
                ("bulk_update", ([], [])),
            ):
                with self.assertRaises(self.binding.ResultOwnershipError):
                    getattr(self.binding.ResultQuerySet(), method)(*args)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
