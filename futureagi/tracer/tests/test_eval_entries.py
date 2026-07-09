"""Tests for the entry-store materialize/soft-delete primitives (PR 4).

materialize_pending turns an eval task's desired row set into pending
EvalLogger entries (one per (row, eval)), resolving the per-target_type FK
shape and stamping the config hash. Idempotent via the PR 3b unique indexes.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.entries import materialize_pending, soft_delete_live
from tracer.tests._ch_seed import seed_ch_spans


def _config(project, eval_template, name):
    return CustomEvalConfig.objects.create(
        name=name,
        project=project,
        eval_template=eval_template,
        config={"threshold": 0.5},
        mapping={"input": "input"},
        filters={},
    )


def _task(project, *, row_type=RowType.SPANS, evals=(), sampling_rate=100.0):
    task = EvalTask.objects.create(
        project=project,
        name="mat-task",
        filters={},
        sampling_rate=sampling_rate,
        spans_limit=1_000_000,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PENDING,
        row_type=row_type,
    )
    for cfg in evals:
        task.evals.add(cfg)
    return task


def _seed_past(spans, *, delta=timedelta(minutes=1)):
    """Seed spans stamped a moment in the past so each span's start_time is
    strictly before the desired-row query's now() upper bound — otherwise a span
    created in the same second as materialize (as these tests do) is excluded by
    ``start_time < end_date`` and never materializes."""
    ObservationSpan.objects.filter(id__in=[s.id for s in spans]).update(
        created_at=timezone.now() - delta
    )
    for s in spans:
        s.refresh_from_db()
    seed_ch_spans(spans)


def _make_spans(
    project, n, *, session=None, shared_trace=None, parent_span_id=None, prefix="s"
):
    spans = []
    for i in range(n):
        trace = shared_trace or Trace.objects.create(
            project=project, name=f"tr-{prefix}-{i}", session=session
        )
        spans.append(
            ObservationSpan.objects.create(
                id=f"{prefix}-{i}-{uuid.uuid4().hex[:8]}",
                project=project,
                trace=trace,
                name=f"sp-{prefix}-{i}",
                observation_type="llm",
                parent_span_id=parent_span_id,
            )
        )
    _seed_past(spans)
    return spans


def _live(task, **filters):
    return EvalLogger.objects.filter(eval_task_id=str(task.id), **filters)


@pytest.mark.integration
@pytest.mark.django_db
class TestMaterializeSpans:
    def test_creates_one_pending_per_span_and_eval(
        self, project, eval_template, custom_eval_config
    ):
        _make_spans(project, 4)
        e2 = _config(project, eval_template, "eval-2")
        task = _task(project, evals=[custom_eval_config, e2])
        materialize_pending(task)
        assert _live(task).count() == 4 * 2
        assert _live(task, status=EvalEntryStatus.PENDING).count() == 8

    def test_span_fk_shape_and_hash(self, project, custom_eval_config):
        [span] = _make_spans(project, 1)
        task = _task(project, evals=[custom_eval_config])
        materialize_pending(task)
        row = _live(task).get()
        assert row.target_type == EvalTargetType.SPAN
        assert row.observation_span_id == span.id
        assert row.trace_id == span.trace_id
        assert row.trace_session_id is None
        assert row.config_hash == resolved_config_hash(custom_eval_config)

    def test_idempotent(self, project, custom_eval_config):
        _make_spans(project, 5)
        task = _task(project, evals=[custom_eval_config])
        materialize_pending(task)
        materialize_pending(task)
        assert _live(task).count() == 5

    def test_no_evals_creates_nothing(self, project):
        _make_spans(project, 3)
        task = _task(project, evals=[])
        assert materialize_pending(task) == 0
        assert _live(task).count() == 0


@pytest.mark.integration
@pytest.mark.django_db
class TestMaterializeTracesAndSessions:
    def test_trace_entries_anchored_to_root_span(self, project, custom_eval_config):
        # 1 trace, 2 spans (one root with parent_span_id='').
        trace = Trace.objects.create(project=project, name="tr")
        root = ObservationSpan.objects.create(
            id=f"root-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name="root",
            observation_type="llm",
            parent_span_id="",
        )
        child = ObservationSpan.objects.create(
            id=f"child-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name="child",
            observation_type="tool",
            parent_span_id=root.id,
        )
        _seed_past([root, child])
        task = _task(project, row_type=RowType.TRACES, evals=[custom_eval_config])
        materialize_pending(task)
        row = _live(task).get()
        assert row.target_type == EvalTargetType.TRACE
        assert row.observation_span_id == root.id  # anchored to root span
        assert str(row.trace_id) == str(trace.id)

    def test_trace_without_root_span_is_skipped(self, project, custom_eval_config):
        trace = Trace.objects.create(project=project, name="rootless")
        orphan = ObservationSpan.objects.create(
            id=f"orphan-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name="orphan",
            observation_type="llm",
            parent_span_id="missing-parent",
        )
        _seed_past([orphan])
        task = _task(project, row_type=RowType.TRACES, evals=[custom_eval_config])
        materialize_pending(task)  # must not raise
        assert _live(task).count() == 0

    def test_session_entries(self, project, custom_eval_config):
        session = TraceSession.objects.create(project=project, name="sess")
        _make_spans(project, 2, session=session)
        task = _task(project, row_type=RowType.SESSIONS, evals=[custom_eval_config])
        materialize_pending(task)
        row = _live(task).first()
        assert row.target_type == EvalTargetType.SESSION
        assert str(row.trace_session_id) == str(session.id)
        assert row.observation_span_id is None
        assert row.trace_id is None


@pytest.mark.integration
@pytest.mark.django_db
class TestSoftDeleteLive:
    def test_soft_deletes_all_live_entries(self, project, custom_eval_config):
        _make_spans(project, 4)
        task = _task(project, evals=[custom_eval_config])
        materialize_pending(task)
        assert _live(task).count() == 4
        deleted = soft_delete_live(task)
        assert deleted == 4
        assert _live(task).count() == 0
        assert (
            EvalLogger.all_objects.filter(
                eval_task_id=str(task.id), deleted=True
            ).count()
            == 4
        )


class _RecordingReader:
    """Wraps a real CHSpanReader, recording the ``include_heavy`` kwarg each
    id-resolution method was called with while delegating everything else."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: dict[str, object] = {}

    def list_by_ids(self, *args, **kwargs):
        self.calls["list_by_ids"] = kwargs.get("include_heavy")
        return self._inner.list_by_ids(*args, **kwargs)

    def list_root_spans_by_trace_ids(self, *args, **kwargs):
        self.calls["list_root_spans_by_trace_ids"] = kwargs.get("include_heavy")
        return self._inner.list_root_spans_by_trace_ids(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _spy_reader(monkeypatch):
    from tracer.services.eval_tasks import entries as entries_mod

    spy = _RecordingReader(entries_mod.get_reader())
    monkeypatch.setattr(entries_mod, "get_reader", lambda: spy)
    return spy


@pytest.mark.integration
@pytest.mark.django_db
class TestMaterializeLeanRead:
    """Materialize only needs id/trace_id, so it must issue the lean read (no
    attributes_extra) — hydrating the fat columns OOMs large tasks."""

    def test_spans_materialize_requests_lean_read(
        self, project, custom_eval_config, monkeypatch
    ):
        _make_spans(project, 3)
        task = _task(project, evals=[custom_eval_config])
        spy = _spy_reader(monkeypatch)
        materialize_pending(task)
        assert spy.calls.get("list_by_ids") is False
        assert _live(task).count() == 3  # still materializes correctly

    def test_traces_materialize_requests_lean_read(
        self, project, custom_eval_config, monkeypatch
    ):
        trace = Trace.objects.create(project=project, name="tr-lean")
        root = ObservationSpan.objects.create(
            id=f"root-lean-{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name="root",
            observation_type="llm",
            parent_span_id="",
        )
        _seed_past([root])
        task = _task(project, row_type=RowType.TRACES, evals=[custom_eval_config])
        spy = _spy_reader(monkeypatch)
        materialize_pending(task)
        assert spy.calls.get("list_root_spans_by_trace_ids") is False
        assert _live(task).count() == 1  # still anchored + materialized
