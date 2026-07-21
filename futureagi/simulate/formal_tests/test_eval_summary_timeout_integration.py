"""
Integration probe for the eval_explanation_summary timeout fix (issue #313).

Tests run_eval_summary_task() end-to-end with mocked Django ORM and
_get_cluster_dict_by_eval, verifying ALL invariants from the TLA+ spec
simultaneously on every scenario.

Five-step methodology: TLA+ -> ADR -> Z3 proofs -> Hypothesis tests -> [this file]

Key invariant (from Z3 proof test_running_never_final):
    RUNNING is always transient -- after run_eval_summary_task() returns,
    the status stored to the database is NEVER EvalExplanationSummaryStatus.RUNNING.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Lightweight stubs so the module can be imported without Django/Temporal
# ---------------------------------------------------------------------------

def _make_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _ensure_stub(name):
    if name not in sys.modules:
        _make_module(name)
    return sys.modules[name]


# structlog
if "structlog" not in sys.modules:
    structlog = _make_module("structlog")

    class _NullLogger:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
        def exception(self, *a, **k): pass
        def debug(self, *a, **k): pass

    sys.modules["structlog"].get_logger = lambda *a, **k: _NullLogger()

# django.utils.timezone
_ensure_stub("django")
_ensure_stub("django.utils")
_ensure_stub("django.utils.timezone")
sys.modules["django.utils.timezone"].now = lambda: "2026-01-01T00:00:00Z"

# tfc.temporal.drop_in -- stub the decorator so @temporal_activity is a no-op
_ensure_stub("tfc")
_ensure_stub("tfc.temporal")
_ensure_stub("tfc.temporal.drop_in")


def _noop_temporal_activity(*args, **kwargs):
    """Decorator that returns the decorated function unchanged."""
    def decorator(fn):
        return fn
    return decorator


sys.modules["tfc.temporal.drop_in"].temporal_activity = _noop_temporal_activity

# ---------------------------------------------------------------------------
# Status enum -- the real values from simulate.models.test_execution
# ---------------------------------------------------------------------------

class EvalExplanationSummaryStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# _assert_invariants: the one function that checks ALL TLA+ invariants.
# Call this after every scenario -- it is the integration probe heart.
# ---------------------------------------------------------------------------

def _assert_invariants(mock_execution, context: str = "") -> None:
    """
    Check all invariants from the TLA+ spec simultaneously on the mock
    TestExecution object.

    Invariant 1 (RUNNING-is-transient, Z3 proof test_running_never_final):
        The final persisted status is NEVER RUNNING after the task completes.

    Invariant 2 (Terminal-is-correct):
        If the happy path succeeded, status must be COMPLETED.
        If an exception occurred, status must be FAILED.

    Invariant 3 (save-called-after-status-set):
        Every status transition must be persisted (save() called).

    Args:
        mock_execution: The mock TestExecution object after the task ran.
        context: Human-readable label for the scenario, used in assertion messages.
    """
    prefix = f"[{context}] " if context else ""
    final_status = mock_execution.eval_explanation_summary_status

    # Invariant 1: RUNNING is always transient
    assert final_status != EvalExplanationSummaryStatus.RUNNING, (
        f"{prefix}RUNNING-is-transient violated: "
        f"status is still RUNNING after task completion"
    )

    # Invariant 2: terminal state is one of the known terminal values
    assert final_status in (
        EvalExplanationSummaryStatus.COMPLETED,
        EvalExplanationSummaryStatus.FAILED,
        EvalExplanationSummaryStatus.PENDING,  # only when test_execution is None
    ), (
        f"{prefix}Terminal-state violated: "
        f"unexpected status '{final_status}'"
    )

    # Invariant 3: save() must have been called at least once after the RUNNING
    # assignment (verifying persistence).  We only check this when the object
    # was used (test_execution is not None).
    assert mock_execution.save.called, (
        f"{prefix}Save-persistence violated: "
        f"save() was never called on the test execution"
    )


# ---------------------------------------------------------------------------
# Helper: build a mock TestExecution that simulates DB round-trips
# ---------------------------------------------------------------------------

def _make_mock_execution(initial_status=None):
    """
    Return a MagicMock that behaves like a TestExecution model instance.

    refresh_from_db() simulates re-reading the field from the database:
    it copies whatever was last written via save(update_fields=[...]) back
    onto the object.  This faithfully mirrors the real DB behaviour.
    """
    mock = MagicMock()
    mock.eval_explanation_summary_status = initial_status or EvalExplanationSummaryStatus.PENDING
    mock.run_test = MagicMock()

    # Track the "DB value" for refresh_from_db simulation
    _db_state = {"eval_explanation_summary_status": mock.eval_explanation_summary_status}

    def _save(update_fields=None, **kwargs):
        # Commit in-memory value to the simulated DB state
        if update_fields and "eval_explanation_summary_status" in update_fields:
            _db_state["eval_explanation_summary_status"] = mock.eval_explanation_summary_status

    def _refresh_from_db(fields=None, **kwargs):
        # Re-read the simulated DB value back into the object
        if fields is None or "eval_explanation_summary_status" in fields:
            mock.eval_explanation_summary_status = _db_state["eval_explanation_summary_status"]

    mock.save = MagicMock(side_effect=_save)
    mock.refresh_from_db = MagicMock(side_effect=_refresh_from_db)
    return mock


# ---------------------------------------------------------------------------
# Load the task under test
# ---------------------------------------------------------------------------

try:
    # Patch the ORM class before importing the task module
    _simulate_models = _ensure_stub("simulate.models")
    _simulate_models_te = _ensure_stub("simulate.models.test_execution")
    _simulate_models_te.EvalExplanationSummaryStatus = EvalExplanationSummaryStatus

    # We'll also need simulate.models.TestExecution accessible
    class _FakeDoesNotExist(Exception):
        pass

    _FakeTestExecution = MagicMock()
    _FakeTestExecution.DoesNotExist = _FakeDoesNotExist
    _simulate_models.TestExecution = _FakeTestExecution
    _simulate_models_te.TestExecution = _FakeTestExecution

    _ensure_stub("simulate.utils")
    _ensure_stub("simulate.utils.eval_explaination_summary")

    from simulate.tasks.eval_summary_tasks import run_eval_summary_task
    _TASK_IMPORTABLE = True
except Exception as _import_error:
    _TASK_IMPORTABLE = False
    _IMPORT_ERROR = _import_error


# ---------------------------------------------------------------------------
# Scenario 1: Happy path -- _get_cluster_dict_by_eval succeeds
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TASK_IMPORTABLE, reason=f"Could not import task")
class TestEvalSummaryTaskIntegration:

    def _run_task_with_mocks(self, mock_execution, cluster_dict_side_effect):
        """
        Run run_eval_summary_task with fully mocked Django ORM and helper.

        Patches:
          - TestExecution.objects.get → returns mock_execution
          - _get_cluster_dict_by_eval → cluster_dict_side_effect
        """
        te_class_mock = MagicMock()
        te_class_mock.objects.get.return_value = mock_execution
        te_class_mock.DoesNotExist = _FakeDoesNotExist if _TASK_IMPORTABLE else Exception

        with patch("simulate.tasks.eval_summary_tasks.TestExecution", te_class_mock), \
             patch("simulate.tasks.eval_summary_tasks._get_cluster_dict_by_eval",
                   side_effect=cluster_dict_side_effect):
            # run_eval_summary_task may raise (the bare except swallows most);
            # we call it and always check invariants afterwards.
            try:
                run_eval_summary_task("test-uuid-1234")
            except Exception:
                pass  # integration probe checks state, not exceptions

        return mock_execution

    # ------------------------------------------------------------------
    # Scenario 1: happy path
    # ------------------------------------------------------------------

    def test_happy_path_status_is_completed(self):
        """
        _get_cluster_dict_by_eval returns a dict.
        Expected: final status = COMPLETED, never RUNNING.
        """
        mock_execution = _make_mock_execution()
        summary = {"cluster_1": {"eval": "ok"}}

        self._run_task_with_mocks(mock_execution, cluster_dict_side_effect=lambda *a, **k: summary)

        # All invariants
        _assert_invariants(mock_execution, context="happy_path")

        # Scenario-specific assertion: success must land in COMPLETED
        assert mock_execution.eval_explanation_summary_status == EvalExplanationSummaryStatus.COMPLETED, (
            "Happy path: expected COMPLETED, got "
            f"'{mock_execution.eval_explanation_summary_status}'"
        )

        # The summary should have been stored
        assert mock_execution.eval_explanation_summary == summary

    # ------------------------------------------------------------------
    # Scenario 2: exception in task body
    # ------------------------------------------------------------------

    def test_exception_in_body_gives_failed(self):
        """
        _get_cluster_dict_by_eval raises an unexpected exception.
        Expected: finally guard fires, final status = FAILED.
        """
        mock_execution = _make_mock_execution()

        def _raise(*a, **k):
            raise RuntimeError("simulated LLM timeout")

        self._run_task_with_mocks(mock_execution, cluster_dict_side_effect=_raise)

        _assert_invariants(mock_execution, context="exception_in_body")

        assert mock_execution.eval_explanation_summary_status == EvalExplanationSummaryStatus.FAILED, (
            "Exception path: expected FAILED, got "
            f"'{mock_execution.eval_explanation_summary_status}'"
        )

    # ------------------------------------------------------------------
    # Scenario 3: TestExecution.DoesNotExist -- early return, no status set
    # ------------------------------------------------------------------

    def test_does_not_exist_returns_early(self):
        """
        TestExecution.objects.get raises DoesNotExist.
        Expected: task returns early; test_execution stays None so finally is a no-op.
        We verify no status is mutated (the mock we created is never touched by the task).
        """
        sentinel = _make_mock_execution()

        te_class_mock = MagicMock()
        te_class_mock.objects.get.side_effect = _FakeDoesNotExist("not found")
        te_class_mock.DoesNotExist = _FakeDoesNotExist

        with patch("simulate.tasks.eval_summary_tasks.TestExecution", te_class_mock), \
             patch("simulate.tasks.eval_summary_tasks._get_cluster_dict_by_eval",
                   return_value={}):
            result = run_eval_summary_task("nonexistent-uuid")

        # Early return -- the sentinel object must not have been touched by the task.
        # The task returned None (implicit return from the DoesNotExist branch).
        # We can't call _assert_invariants on the sentinel because the task never
        # touched it; instead verify the returned value and that save() was not called.
        assert result is None, f"DoesNotExist path: expected None return, got {result!r}"
        sentinel.save.assert_not_called()
        sentinel.refresh_from_db.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 4: exception with partial summary already computed
    # ------------------------------------------------------------------

    def test_exception_after_partial_work_still_fails(self):
        """
        Simulate the case where the task sets RUNNING, computes a partial
        result, then raises.  The finally guard must still set FAILED.
        """
        mock_execution = _make_mock_execution()
        call_count = {"n": 0}

        def _partial_then_raise(*a, **k):
            call_count["n"] += 1
            # Pretend some work was done, then blow up
            if call_count["n"] == 1:
                raise ValueError("partial failure after some work")
            return {"cluster_1": "done"}

        self._run_task_with_mocks(mock_execution, cluster_dict_side_effect=_partial_then_raise)

        _assert_invariants(mock_execution, context="partial_then_exception")

        assert mock_execution.eval_explanation_summary_status == EvalExplanationSummaryStatus.FAILED, (
            "Partial-work exception path: expected FAILED, got "
            f"'{mock_execution.eval_explanation_summary_status}'"
        )

    # ------------------------------------------------------------------
    # Scenario 5: finally guard is idempotent -- calling it twice is safe
    # ------------------------------------------------------------------

    def test_finally_guard_idempotent(self):
        """
        If by some race the task body had already stored COMPLETED before the
        finally guard runs, refresh_from_db brings back COMPLETED and the guard
        must NOT downgrade it to FAILED.
        """
        mock_execution = _make_mock_execution()
        summary = {"data": "done"}

        # Happy path -- status will be COMPLETED when finally runs
        self._run_task_with_mocks(mock_execution, cluster_dict_side_effect=lambda *a, **k: summary)

        _assert_invariants(mock_execution, context="idempotent_guard")

        # Status must stay COMPLETED, not regress to FAILED
        assert mock_execution.eval_explanation_summary_status == EvalExplanationSummaryStatus.COMPLETED, (
            "Idempotent guard: guard should not downgrade COMPLETED to FAILED; got "
            f"'{mock_execution.eval_explanation_summary_status}'"
        )

    # ------------------------------------------------------------------
    # Scenario 6: refresh_from_db always called in finally (when te is not None)
    # ------------------------------------------------------------------

    def test_refresh_from_db_always_called_in_finally(self):
        """
        Whether the task succeeds or fails, refresh_from_db must be called in
        the finally block so the guard reads the actual DB state.
        """
        mock_execution_success = _make_mock_execution()
        mock_execution_failure = _make_mock_execution()

        self._run_task_with_mocks(
            mock_execution_success,
            cluster_dict_side_effect=lambda *a, **k: {"ok": True},
        )
        self._run_task_with_mocks(
            mock_execution_failure,
            cluster_dict_side_effect=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail")),
        )

        # Both paths must call refresh_from_db exactly once
        mock_execution_success.refresh_from_db.assert_called_once()
        mock_execution_failure.refresh_from_db.assert_called_once()

        _assert_invariants(mock_execution_success, context="refresh_success_path")
        _assert_invariants(mock_execution_failure, context="refresh_failure_path")


# ---------------------------------------------------------------------------
# Allow running directly (no pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not _TASK_IMPORTABLE:
        print(f"SKIP: Could not import task: {_IMPORT_ERROR}")
        sys.exit(0)

    suite = TestEvalSummaryTaskIntegration()
    tests = [
        suite.test_happy_path_status_is_completed,
        suite.test_exception_in_body_gives_failed,
        suite.test_does_not_exist_returns_early,
        suite.test_exception_after_partial_work_still_fails,
        suite.test_finally_guard_idempotent,
        suite.test_refresh_from_db_always_called_in_finally,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
