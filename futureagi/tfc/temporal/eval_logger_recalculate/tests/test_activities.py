"""Tests for the eval_logger_recalculate dispatch helper (pure-mock).

The DB-touching ``_soft_delete_siblings_sync`` tests live under
``tracer/tests/test_eval_logger_recalculate_soft_delete.py`` since they
depend on tracer-side fixtures.
"""

import pytest

from tfc.temporal.eval_logger_recalculate.activities import _dispatch_rerun_sync
from tfc.temporal.eval_logger_recalculate.types import DispatchRerunInput


@pytest.mark.integration
class TestDispatchRerunSync:
    def _input(self, target_type, **ids):
        return DispatchRerunInput(
            target_type=target_type,
            custom_eval_config_id="cfg",
            eval_task_id="task",
            feedback_id=None,
            **ids,
        )

    def test_dispatches_span_runner(self, db, mocker):
        span_mock = mocker.patch(
            "tracer.utils.eval.evaluate_observation_span_observe", return_value=True
        )
        result = _dispatch_rerun_sync(
            self._input("span", observation_span_id="span-1")
        )
        assert result.status == "completed"
        span_mock.assert_called_once_with("span-1", "cfg", "task", None)

    def test_dispatches_trace_runner(self, db, mocker):
        trace_mock = mocker.patch(
            "tracer.utils.eval.evaluate_trace_observe", return_value=True
        )
        result = _dispatch_rerun_sync(self._input("trace", trace_id="trace-1"))
        assert result.status == "completed"
        trace_mock.assert_called_once_with("trace-1", "cfg", "task", None)

    def test_dispatches_session_runner(self, db, mocker):
        session_mock = mocker.patch(
            "tracer.utils.eval.evaluate_trace_session_observe", return_value=True
        )
        result = _dispatch_rerun_sync(
            self._input("session", trace_session_id="session-1")
        )
        assert result.status == "completed"
        session_mock.assert_called_once_with("session-1", "cfg", "task", None)

    def test_marks_failed_when_runner_returns_falsy(self, db, mocker):
        mocker.patch(
            "tracer.utils.eval.evaluate_observation_span_observe", return_value=None
        )
        result = _dispatch_rerun_sync(
            self._input("span", observation_span_id="span-1")
        )
        assert result.status == "failed"

    def test_unknown_target_type_returns_failed(self, db):
        result = _dispatch_rerun_sync(self._input("bogus"))
        assert result.status == "failed"
        assert "Unhandled target_type" in (result.error or "")
