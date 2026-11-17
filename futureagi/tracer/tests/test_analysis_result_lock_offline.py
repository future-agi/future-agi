"""Socket-blocked lock-retention probe; PostgreSQL execution is separate."""

import runpy
import threading
import time
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

support = runpy.run_path(
    str(Path(__file__).with_name("test_analysis_legacy_snapshot_offline.py"))
)
definition, module = support["definition"], support["module"]


class LockRetentionProbe(unittest.TestCase):
    def test_timeout_owner_aborts_while_result_mutation_is_still_stalled(self):
        mutex = threading.RLock()
        local = threading.local()
        acquired = threading.Event()
        release_write = threading.Event()
        abort_waiting = threading.Event()
        finished = threading.Event()
        row = SimpleNamespace(
            project_id="p",
            trace_id="t",
            generation="g",
            status="retry_pending",
            result_analysis_id=None,
            reason_code="",
        )

        @contextmanager
        def atomic():
            local.depth = getattr(local, "depth", 0) + 1
            try:
                yield
            finally:
                local.depth -= 1
                if not local.depth and getattr(local, "held", False):
                    local.held = False
                    mutex.release()

        class Query:
            def __init__(self, filters=None, locking=False):
                self.filters = filters or {}
                self.locking = locking

            def select_for_update(self):
                return Query(self.filters, True)

            def filter(self, **kwargs):
                return Query({**self.filters, **kwargs}, self.locking)

            def match(self):
                return all(
                    str(getattr(row, k)) == str(v) for k, v in self.filters.items()
                )

            def first(self):
                if self.locking and not getattr(local, "held", False):
                    mutex.acquire()
                    local.held = True
                return SimpleNamespace(**vars(row)) if self.match() else None

            def update(self, **kwargs):
                if kwargs.get("status") == "aborted":
                    abort_waiting.set()
                with mutex:
                    if not self.match():
                        return 0
                    vars(row).update(kwargs)
                    return 1

        store = Query()
        state_ns = {
            "BackgroundTraceAnalysisState": SimpleNamespace(objects=store),
            "timezone": SimpleNamespace(now=lambda: 0),
        }
        for name in ("transition", "admit", "abort"):
            definition("tracer/queries/background_analysis_state.py", name, state_ns)
        state = SimpleNamespace(**state_ns)
        binding_ns = {
            "current_attempt": lambda: ("p", "t", "g"),
            "transaction": SimpleNamespace(atomic=atomic),
        }

        class OwnershipError(RuntimeError):
            pass

        binding_ns["ResultOwnershipError"] = OwnershipError
        binding_ns["locked_attempt"] = contextmanager(
            definition(
                "tracer/services/analysis_result_binding.py",
                "locked_attempt",
                binding_ns,
            )
        )
        definition(
            "tracer/services/analysis_result_binding.py",
            "AttemptResultMixin",
            binding_ns,
        )

        class Persist:
            def save(self, *a, **k):
                acquired.set()
                if not release_write.wait(3):
                    raise AssertionError("probe write was not released")

        Model = type(
            "TraceErrorDetail", (binding_ns["AttemptResultMixin"], Persist), {}
        )
        record = Model()
        record.analysis_id = "g"

        class Aborted(BaseException):
            def __init__(self, code):
                self.code = code

        guard_ns = {"time": time, "Event": threading.Event, "AnalysisAborted": Aborted}
        Guard = definition(
            "tracer/services/falcon_analysis_guard.py", "AnalysisGuard", guard_ns
        )
        guard = Guard("p", "u", "o", "w", 0.15)

        def check():
            if guard.stopped.is_set():
                raise Aborted(guard.code)
            if not guard.remaining:
                guard.abort("analysis_deadline_exceeded")

        guard.check = check

        def in_scope(*args):
            try:
                guard.run_sync(record.save)
                return {"success": True}
            except Aborted as exc:
                return {"success": False, "code": exc.code}

        worker_ns = {
            "background_analysis_state": state,
            "_falcon_analyze_trace_in_scope": in_scope,
            "attempt_scope": lambda *a: nullcontext(),
        }
        worker = definition(
            "tracer/tasks/error_analysis.py", "_falcon_analyze_single_trace", worker_ns
        )
        outcome = []

        def invoke():
            try:
                outcome.append(worker("t", "p", generation="g"))
            except BaseException as exc:
                outcome.append(exc)
            finally:
                finished.set()

        with patch.dict(
            "sys.modules",
            {
                "tracer.services.background_transport": module(
                    "transport",
                    **runpy.run_path(
                        str(
                            Path(__file__).resolve().parents[2]
                            / "tracer/services/background_transport.py"
                        )
                    ),
                ),
                "tracer.services.analysis_result_lock": module(
                    "lock", result_lock=lambda *a: atomic()
                ),
                "tfc.middleware.workspace_context": module(
                    "context", workspace_context=lambda *a: nullcontext()
                ),
                "tracer.models.background_trace_analysis_state": module(
                    "state", BackgroundTraceAnalysisState=SimpleNamespace(objects=store)
                ),
            },
        ):
            owner = threading.Thread(target=invoke)
            owner.start()
            try:
                self.assertTrue(acquired.wait(1))
                self.assertTrue(abort_waiting.wait(1))
                self.assertTrue(guard.stopped.is_set())
                self.assertTrue(
                    finished.wait(0.25), "abort waited behind result mutation"
                )
                self.assertEqual(row.status, "aborted")
            finally:
                release_write.set()
                owner.join(2)
        self.assertTrue(finished.is_set())
        self.assertEqual(row.status, "aborted")
        self.assertFalse(outcome[0]["success"])


class LockSql(unittest.TestCase):
    def test_candidate_lock_has_timeout_and_never_locks_lifecycle(self):
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        ns = {
            "transaction": SimpleNamespace(atomic=nullcontext),
            "connection": SimpleNamespace(cursor=lambda: cursor),
        }
        lock = contextmanager(
            definition("tracer/services/analysis_result_lock.py", "result_lock", ns)
        )
        with lock("generation"):
            statements = [call.args for call in cursor.execute.call_args_list]
            self.assertEqual(statements[0], ("SET LOCAL lock_timeout = '250ms'",))
            self.assertIn("pg_advisory_xact_lock", statements[1][0])
            self.assertEqual(statements[1][1], ["trace-analysis-result:generation"])
            self.assertFalse(any("FOR UPDATE" in args[0] for args in statements))


if __name__ == "__main__":
    unittest.main(verbosity=2)
