"""Row_type-aware annotation scoping for the eval-task runner.

Each test creates Score rows at a specific source scope (span / trace /
session), runs ``process_eval_task`` with an ANNOTATION-flavoured filter, and
asserts which entities were selected. Guards the prod incident where a
voiceCalls task with an annotator filter selected 0 spans because the
annotator's scores were trace-scoped (task f8481965-cd74-44b1-9b6d-f4bb66ec2218)
while the runner matched span-scoped scores only.

Pure-Postgres: the runner never reads ClickHouse, so spans/traces/sessions are
created via the ORM directly and roll back with the ``db`` fixture.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from django.db import transaction

from model_hub.models.choices import (
    AnnotationTypeChoices,
    QueueItemSourceType,
    ScoreSource,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import EvalLogger, EvalTargetType, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def scope_project(integration_setup, db):
    """Fresh project so corpus spans never enter the candidate set."""
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    return Project.objects.create(
        name=f"annotation_scope_{uuid.uuid4().hex[:8]}",
        organization=integration_setup.organization,
        workspace=integration_setup.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )


@pytest.fixture
def annotation_label(scope_project):
    return AnnotationsLabels.objects.create(
        name=f"scope_label_{uuid.uuid4().hex[:6]}",
        type=AnnotationTypeChoices.NUMERIC.value,
        organization=scope_project.organization,
        workspace=scope_project.workspace,
        project=scope_project,
        settings={"min": 0, "max": 1, "step_size": 0.1, "display_type": "slider"},
    )


def _make_call(project, session=None):
    """One voice-call shaped unit: a trace with a root conversation span."""
    trace = Trace.objects.create(
        id=uuid.uuid4(),
        project=project,
        session=session,
        name=f"call_{uuid.uuid4().hex[:8]}",
    )
    span = ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=trace.name,
        observation_type="conversation",
        status="OK",
        parent_span_id=None,
        start_time=_NOW,
        end_time=_NOW + timedelta(seconds=30),
    )
    return trace, span


def _make_score(label, project, *, annotator=None, value=0.7, **source_kwargs):
    source_type = next(
        st
        for st, fk in (
            (QueueItemSourceType.OBSERVATION_SPAN.value, "observation_span_id"),
            (QueueItemSourceType.TRACE.value, "trace_id"),
            (QueueItemSourceType.TRACE_SESSION.value, "trace_session_id"),
        )
        if fk in source_kwargs
    )
    return Score.objects.create(
        source_type=source_type,
        label=label,
        value={"value": value},
        annotator=annotator,
        organization=project.organization,
        workspace=project.workspace,
        score_source=ScoreSource.HUMAN.value,
        **source_kwargs,
    )


def _annotator_filter(user):
    # camelCase prod shape (task f8481965's filter) — exercises key
    # normalization alongside the scope fix.
    return {
        "columnId": "annotator",
        "filterConfig": {
            "colType": "ANNOTATION",
            "filterOp": "equals",
            "filterType": "text",
            "filterValue": str(user.id),
        },
    }


def _has_annotation_filter(value=True):
    return {
        "column_id": "has_annotation",
        "col_type": "has_annotation",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _label_value_filter(label, threshold=0.4):
    return {
        "column_id": str(label.id),
        "col_type": "ANNOTATION",
        "filter_config": {
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": threshold,
        },
    }


def _run_task(project, row_type, filter_items, eval_config):
    from tracer.utils.eval_tasks import process_eval_task

    task = EvalTask.objects.create(
        project=project,
        filters={"project_id": str(project.id), "filters": filter_items},
        row_type=row_type,
        run_type=RunType.HISTORICAL,
        sampling_rate=100.0,
        spans_limit=10_000,
        status=EvalTaskStatus.PENDING,
    )
    task.evals.add(eval_config)
    with transaction.atomic():
        process_eval_task._original_func(str(task.id))
    return task


def _logger_qs(task):
    return EvalLogger.objects.filter(eval_task_id=str(task.id))


class TestVoiceCallsScope:
    def test_trace_scoped_annotation_matches(
        self,
        scope_project,
        annotation_label,
        user,
        custom_eval_config_factory,
        stub_run_eval,
        inline_temporal,
        stub_cost_log,
    ):
        # The f8481965 incident shape: annotation lives on the TRACE,
        # the task filters voiceCalls by annotator.
        _, span_a = _make_call(scope_project)
        trace_a = span_a.trace
        _make_call(scope_project)  # unannotated control
        _make_score(
            annotation_label, scope_project, annotator=user, trace_id=trace_a.id
        )

        task = _run_task(
            scope_project,
            RowType.VOICE_CALLS,
            [_annotator_filter(user)],
            custom_eval_config_factory(project=scope_project),
        )

        loggers = _logger_qs(task)
        assert loggers.count() == 1
        assert loggers.first().observation_span_id == span_a.id

    def test_span_scoped_annotation_still_matches(
        self,
        scope_project,
        annotation_label,
        user,
        custom_eval_config_factory,
        stub_run_eval,
        inline_temporal,
        stub_cost_log,
    ):
        # The other arm of the OR bridge: span-scoped scores keep matching.
        _, span_a = _make_call(scope_project)
        _make_call(scope_project)
        _make_score(
            annotation_label,
            scope_project,
            annotator=user,
            observation_span_id=span_a.id,
        )

        task = _run_task(
            scope_project,
            RowType.VOICE_CALLS,
            [_annotator_filter(user)],
            custom_eval_config_factory(project=scope_project),
        )

        loggers = _logger_qs(task)
        assert loggers.count() == 1
        assert loggers.first().observation_span_id == span_a.id

    def test_per_label_value_filter_sees_trace_scoped_score(
        self,
        scope_project,
        annotation_label,
        user,
        custom_eval_config_factory,
        stub_run_eval,
        inline_temporal,
        stub_cost_log,
    ):
        # Per-label value filters resolve through build_annotation_subqueries,
        # which must use the same trace bridge as the Exists-based filters.
        _, span_a = _make_call(scope_project)
        _make_call(scope_project)
        _make_score(
            annotation_label,
            scope_project,
            annotator=user,
            value=0.8,
            trace_id=span_a.trace.id,
        )

        task = _run_task(
            scope_project,
            RowType.VOICE_CALLS,
            [_label_value_filter(annotation_label, threshold=0.4)],
            custom_eval_config_factory(project=scope_project),
        )

        loggers = _logger_qs(task)
        assert loggers.count() == 1
        assert loggers.first().observation_span_id == span_a.id

    def test_has_annotation_sees_trace_scoped_score(
        self,
        scope_project,
        annotation_label,
        user,
        custom_eval_config_factory,
        stub_run_eval,
        inline_temporal,
        stub_cost_log,
    ):
        _, span_a = _make_call(scope_project)
        _make_call(scope_project)
        _make_score(
            annotation_label, scope_project, annotator=user, trace_id=span_a.trace.id
        )

        task = _run_task(
            scope_project,
            RowType.VOICE_CALLS,
            [_has_annotation_filter(True)],
            custom_eval_config_factory(project=scope_project),
        )

        loggers = _logger_qs(task)
        assert loggers.count() == 1
        assert loggers.first().observation_span_id == span_a.id


class TestSpansScope:
    def test_trace_scoped_annotation_does_not_match(
        self,
        scope_project,
        annotation_label,
        user,
        custom_eval_config_factory,
        stub_run_eval,
        inline_temporal,
        stub_cost_log,
    ):
        # Span tasks mirror the spans grid: trace-scoped scores are invisible.
        _, span_a = _make_call(scope_project)
        _make_score(
            annotation_label, scope_project, annotator=user, trace_id=span_a.trace.id
        )

        task = _run_task(
            scope_project,
            RowType.SPANS,
            [_annotator_filter(user)],
            custom_eval_config_factory(project=scope_project),
        )

        assert _logger_qs(task).count() == 0


class TestTracesScope:
    def test_trace_scoped_annotation_matches(
        self,
        scope_project,
        annotation_label,
        user,
        custom_eval_config_factory,
        stub_run_eval,
        inline_temporal,
        stub_cost_log,
    ):
        _, span_a = _make_call(scope_project)
        trace_a = span_a.trace
        _make_call(scope_project)
        _make_score(
            annotation_label, scope_project, annotator=user, trace_id=trace_a.id
        )

        task = _run_task(
            scope_project,
            RowType.TRACES,
            [_annotator_filter(user)],
            custom_eval_config_factory(project=scope_project),
        )

        loggers = _logger_qs(task)
        assert loggers.count() == 1
        sample = loggers.first()
        assert sample.target_type == EvalTargetType.TRACE
        assert sample.trace_id == trace_a.id


class TestSessionsScope:
    def test_session_scoped_matches_and_span_scoped_does_not(
        self,
        scope_project,
        annotation_label,
        user,
        custom_eval_config_factory,
        stub_run_eval,
        inline_temporal,
        stub_cost_log,
    ):
        # Session 1 carries a session-scoped score; session 2 only has a
        # span-scoped score. Sessions tasks mirror the sessions grid:
        # only session-scoped scores count.
        sess_1 = TraceSession.objects.create(
            id=uuid.uuid4(), project=scope_project, name="sess_1"
        )
        sess_2 = TraceSession.objects.create(
            id=uuid.uuid4(), project=scope_project, name="sess_2"
        )
        _make_call(scope_project, session=sess_1)
        _, span_2 = _make_call(scope_project, session=sess_2)
        _make_score(
            annotation_label, scope_project, annotator=user, trace_session_id=sess_1.id
        )
        _make_score(
            annotation_label,
            scope_project,
            annotator=user,
            observation_span_id=span_2.id,
        )

        task = _run_task(
            scope_project,
            RowType.SESSIONS,
            [_annotator_filter(user)],
            custom_eval_config_factory(project=scope_project),
        )

        loggers = _logger_qs(task)
        assert loggers.count() == 1
        sample = loggers.first()
        assert sample.target_type == EvalTargetType.SESSION
        assert sample.trace_session_id == sess_1.id
