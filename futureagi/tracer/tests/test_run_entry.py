"""Tests for run_entry — executes one claimed entry and records its terminal
status, reusing the per-target_type eval core. Engine + cost are stubbed."""

import uuid

import pytest

from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace_session import TraceSession
from tracer.services.eval_tasks.run_entry import run_entry
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
