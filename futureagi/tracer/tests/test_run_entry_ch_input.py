"""run_entry loads eval input from ClickHouse (CH-direct world) AND lands the
result on the materialized entry: the span / trace / session row never exists in
Postgres, only in CH, yet the eval reaches COMPLETED, the materialized entry is
updated in place (no duplicate EvalLogger row), and no phantom PG span is made."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.eval_tasks.run_entry import run_entry
from tracer.tests._ch_seed import seed_ch_span, seed_ch_trace, seed_ch_trace_sessions


def _make_entry(**kwargs):
    """Create an entry the way the materializer does — bulk_create bypasses
    full_clean, so a CH-only observation_span_id (no PG row) is allowed."""
    kwargs.setdefault("status", EvalEntryStatus.RUNNING)
    entry = EvalLogger(**kwargs)
    EvalLogger.objects.bulk_create([entry])
    return entry


def _ch_only_span(project, trace, *, observation_type="llm", parent_span_id=""):
    """A span that lives ONLY in CH (never saved to PG)."""
    span = ObservationSpan(
        id=f"ch-{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        parent_span_id=parent_span_id,
        name="ch-span",
        observation_type=observation_type,
        start_time=timezone.now() - timedelta(seconds=2),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "hi"}]},
        output={"choices": [{"message": {"content": "yo"}}]},
        status="OK",
    )
    seed_ch_span(span)  # CH only — NOT ObservationSpan.objects.create
    return span


def _assert_completed(status, entry, *, task_id):
    """The eval loaded from CH and the result landed on the materialized entry:
    COMPLETED, entry updated in place, exactly one EvalLogger row for the task
    (no colliding duplicate)."""
    entry.refresh_from_db()
    assert status == EvalEntryStatus.COMPLETED, (status, entry.error_message)
    assert entry.status == EvalEntryStatus.COMPLETED
    assert not entry.error
    assert entry.config_hash  # terminal stamp written
    assert EvalLogger.objects.filter(eval_task_id=task_id).count() == 1


@pytest.mark.integration
@pytest.mark.django_db
class TestRunEntryChInput:
    def test_span_evaluates_from_ch_with_pg_empty(
        self, project, custom_eval_config, eval_task, stub_run_eval, stub_cost_log
    ):
        trace = Trace.objects.create(project=project, name="t")  # no PG span
        span = _ch_only_span(project, trace)
        entry = _make_entry(
            target_type=EvalTargetType.SPAN,
            observation_span_id=span.id,
            trace=trace,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.id),
            status=EvalEntryStatus.RUNNING,
        )
        _assert_completed(run_entry(entry), entry, task_id=str(eval_task.id))
        # The span was read from CH and never written to PG.
        assert ObservationSpan.objects.count() == 0

    def test_voicecall_rides_span_path_from_ch(
        self, project, custom_eval_config, eval_task, stub_run_eval, stub_cost_log
    ):
        trace = Trace.objects.create(project=project, name="vc")
        span = _ch_only_span(project, trace, observation_type="conversation")
        entry = _make_entry(
            target_type=EvalTargetType.SPAN,  # voiceCalls map to SPAN
            observation_span_id=span.id,
            trace=trace,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.id),
            status=EvalEntryStatus.RUNNING,
        )
        _assert_completed(run_entry(entry), entry, task_id=str(eval_task.id))

    def test_trace_evaluates_from_ch_with_pg_empty(
        self, project, custom_eval_config, eval_task, stub_run_eval, stub_cost_log
    ):
        # Trace lives only in CH (the `traces` store get_trace reads); give it an
        # input so the eval has something to map and completes.
        trace = Trace(
            id=uuid.uuid4(),
            project=project,
            name="t",
            input={"q": "hello"},
            output={"a": "world"},
        )
        seed_ch_trace(trace)
        root = _ch_only_span(project, trace, parent_span_id="")  # root span in CH
        entry = _make_entry(
            target_type=EvalTargetType.TRACE,
            observation_span_id=root.id,  # anchor (target_type_fks check constraint)
            trace_id=str(trace.id),
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.id),
            status=EvalEntryStatus.RUNNING,
        )
        _assert_completed(run_entry(entry), entry, task_id=str(eval_task.id))
        assert ObservationSpan.objects.count() == 0
        assert Trace.objects.count() == 0  # trace came from CH, not PG

    def test_session_evaluates_from_ch_with_pg_empty(
        self, observe_project, eval_template, eval_task, stub_run_eval, stub_cost_log
    ):
        session = TraceSession.objects.create(project=observe_project, name="sess")
        seed_ch_trace_sessions([session])
        # Session-valid mapping ("name" is a session field; "input" is not).
        config = CustomEvalConfig.objects.create(
            name="sess-eval",
            project=observe_project,
            eval_template=eval_template,
            config={"threshold": 0.8},
            mapping={"input": "name"},
            filters={},
        )
        entry = _make_entry(
            target_type=EvalTargetType.SESSION,
            trace_session=session,
            custom_eval_config=config,
            eval_task_id=str(eval_task.id),
            status=EvalEntryStatus.RUNNING,
        )
        _assert_completed(run_entry(entry), entry, task_id=str(eval_task.id))

    def test_ch_miss_hard_fails_to_errored(
        self, project, custom_eval_config, eval_task, stub_run_eval, stub_cost_log
    ):
        trace = Trace.objects.create(project=project, name="t")
        entry = _make_entry(
            target_type=EvalTargetType.SPAN,
            observation_span_id=f"missing-{uuid.uuid4().hex[:12]}",  # not in CH
            trace=trace,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.id),
            status=EvalEntryStatus.RUNNING,
        )
        assert run_entry(entry) == EvalEntryStatus.ERRORED
        entry.refresh_from_db()
        assert entry.error is True
        assert "ClickHouse" in (entry.error_message or "")
