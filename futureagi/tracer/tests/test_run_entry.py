"""Tests for run_entry — executes one claimed entry and records its terminal
status, reusing the per-target_type eval core. Engine + cost are stubbed."""

import uuid
from unittest.mock import patch

import pytest

from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace_session import TraceSession
from tracer.services.eval_tasks.run_entry import _reseed_eval_clustering, run_entry
from tracer.tests._ch_seed import seed_ch_span, seed_ch_trace_sessions

_TERMINAL = {
    EvalEntryStatus.COMPLETED,
    EvalEntryStatus.ERRORED,
    EvalEntryStatus.SKIPPED,
}


def _span_entry(task, span, config):
    return EvalLogger.objects.create(
        target_type=EvalTargetType.SPAN,
        observation_span=span,
        trace=span.trace,
        custom_eval_config=config,
        eval_task_id=str(task.id),
        status=EvalEntryStatus.RUNNING,
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestRunEntrySpan:
    def test_completed_on_success(
        self,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        entry = _span_entry(eval_task, observation_span, custom_eval_config)
        assert run_entry(entry) == EvalEntryStatus.COMPLETED
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.COMPLETED
        assert entry.config_hash and len(entry.config_hash) == 64
        assert entry.error is False

    def test_completed_dispatches_clustering(
        self,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        """Reaching COMPLETED must invoke the clustering hook — the exact seam
        the TH-5978 cutover severed. The hook's own fail/pass logic is unit-
        tested in TestReseedEvalClusteringHook; this pins the *wiring* so
        deleting the call from run_entry fails a test instead of silently
        re-orphaning the trigger."""
        entry = _span_entry(eval_task, observation_span, custom_eval_config)
        with patch(
            "tracer.services.eval_tasks.run_entry._reseed_eval_clustering"
        ) as reseed:
            assert run_entry(entry) == EvalEntryStatus.COMPLETED
        reseed.assert_called_once()
        assert reseed.call_args.args[0].id == entry.id
        assert reseed.call_args.args[1] == custom_eval_config.project_id

    def test_errored_run_does_not_dispatch_clustering(
        self,
        monkeypatch,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_cost_log,
    ):
        """A run that ERRORED produced no eval result, so the hook must not
        fire. Pins the COMPLETED-only gate against an accidental move of the
        dispatch out of the status guard."""
        entry = _span_entry(eval_task, observation_span, custom_eval_config)

        def _boom(*a, **k):
            raise RuntimeError("engine down")

        monkeypatch.setattr("evaluations.engine.run_eval", _boom, raising=False)
        monkeypatch.setattr(
            "evaluations.engine.runner.run_eval", _boom, raising=False
        )
        with patch(
            "tracer.services.eval_tasks.run_entry._reseed_eval_clustering"
        ) as reseed:
            assert run_entry(entry) == EvalEntryStatus.ERRORED
        reseed.assert_not_called()

    def test_errored_when_engine_raises(
        self,
        monkeypatch,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_cost_log,
    ):
        entry = _span_entry(eval_task, observation_span, custom_eval_config)

        def _boom(*a, **k):
            raise RuntimeError("engine down")

        monkeypatch.setattr("evaluations.engine.run_eval", _boom, raising=False)
        monkeypatch.setattr("evaluations.engine.runner.run_eval", _boom, raising=False)
        assert run_entry(entry) == EvalEntryStatus.ERRORED
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.ERRORED
        assert entry.error is True

    def test_skipped_on_missing_attribute(
        self,
        monkeypatch,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_cost_log,
    ):
        from tracer.utils.eval import EvalSkippedMissingAttribute

        entry = _span_entry(eval_task, observation_span, custom_eval_config)

        def _skip(*a, **k):
            raise EvalSkippedMissingAttribute("input", "input", observation_span.id)

        monkeypatch.setattr("tracer.utils.eval._process_mapping", _skip)
        assert run_entry(entry) == EvalEntryStatus.SKIPPED
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.SKIPPED
        assert entry.error is False
        assert "input" in (entry.skipped_reason or "")

    def test_noop_on_deleted_entry(
        self,
        observation_span,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        entry = _span_entry(eval_task, observation_span, custom_eval_config)
        entry.delete()  # soft-delete mid-run
        assert run_entry(entry) == "deleted"
        assert EvalLogger.objects.filter(id=entry.id).count() == 0  # not resurrected


@pytest.mark.integration
@pytest.mark.django_db
class TestRunEntryTraceAndSession:
    def test_trace_dispatch_reaches_terminal(
        self,
        project,
        trace,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        root = ObservationSpan.objects.create(
            id=f"root-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name="root",
            observation_type="llm",
            parent_span_id="",
            input={"prompt": "hi"},
        )
        seed_ch_span(root)
        entry = EvalLogger.objects.create(
            target_type=EvalTargetType.TRACE,
            observation_span=root,
            trace=trace,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.id),
            status=EvalEntryStatus.RUNNING,
        )
        assert run_entry(entry) in _TERMINAL
        entry.refresh_from_db()
        assert entry.status in _TERMINAL  # dispatch ran end-to-end


_APPLY_ASYNC = "tracer.tasks.eval_clustering.cluster_eval_results_task.apply_async"
_PID = "11111111-1111-1111-1111-111111111111"


@pytest.mark.unit
class TestReseedEvalClusteringHook:
    """The clustering trigger. This is the exact link the TH-5978 cutover
    orphaned; an untested trigger is what let the regression ship silently."""

    def test_dispatches_on_failure(self):
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_bool=False, eval_explanation="bad"
        )
        with patch(_APPLY_ASYNC) as m:
            _reseed_eval_clustering(entry, _PID)
        m.assert_called_once()
        assert m.call_args.kwargs["task_id"] == f"eval-cluster-{_PID}"

    def test_dispatches_on_float_below_one(self):
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_float=0.4, eval_explanation="meh"
        )
        with patch(_APPLY_ASYNC) as m:
            _reseed_eval_clustering(entry, _PID)
        m.assert_called_once()

    def test_no_dispatch_on_pass(self):
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_bool=True, eval_explanation="good"
        )
        with patch(_APPLY_ASYNC) as m:
            _reseed_eval_clustering(entry, _PID)
        m.assert_not_called()

    def test_no_dispatch_without_explanation(self):
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_bool=False, eval_explanation=""
        )
        with patch(_APPLY_ASYNC) as m:
            _reseed_eval_clustering(entry, _PID)
        m.assert_not_called()

    def test_dispatch_failure_is_swallowed(self):
        """A clustering-dispatch hiccup must never fail an eval that already
        produced a result (fail-open)."""
        entry = EvalLogger(
            target_type=EvalTargetType.SPAN, output_bool=False, eval_explanation="bad"
        )
        with patch(_APPLY_ASYNC, side_effect=RuntimeError("temporal down")):
            _reseed_eval_clustering(entry, _PID)  # must not raise

    def test_session_dispatch_reaches_terminal(
        self,
        observe_project,
        custom_eval_config,
        eval_task,
        stub_run_eval,
        stub_cost_log,
    ):
        session = TraceSession.objects.create(project=observe_project, name="sess")
        seed_ch_trace_sessions([session])  # forced CH reads the curated session.
        entry = EvalLogger.objects.create(
            target_type=EvalTargetType.SESSION,
            trace_session=session,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.id),
            status=EvalEntryStatus.RUNNING,
        )
        assert run_entry(entry) in _TERMINAL
        entry.refresh_from_db()
        assert entry.status in _TERMINAL
