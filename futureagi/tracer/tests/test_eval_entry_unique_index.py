"""Per-target_type live-entry uniqueness: at most one live entry per
(task, row, eval), keyed on the row's identity column for its target_type
(span->observation_span, trace->trace, session->trace_session)."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

# Import model_hub.tasks before the tracer models to break a tracer.utils.eval
# import cycle.
import model_hub.tasks  # noqa: F401
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace_session import TraceSession

# Rejected either by full_clean (ValidationError) or the DB index (IntegrityError).
_REJECTION_ERRORS = (ValidationError, IntegrityError)

_SPAN_INDEX = "eval_logger_live_span_uniq"
_TRACE_INDEX = "eval_logger_live_trace_uniq"
_SESSION_INDEX = "eval_logger_live_session_uniq"


def _task_id(task_id):
    return None if task_id is None else str(task_id)


def _span_entry(task_id, span, config, **overrides):
    return EvalLogger.objects.create(
        target_type=EvalTargetType.SPAN,
        observation_span=span,
        trace=span.trace,
        custom_eval_config=config,
        eval_task_id=_task_id(task_id),
        output_bool=True,
        **overrides,
    )


def _trace_entry(task_id, root_span, config, **overrides):
    # Trace target is anchored to the trace's root span (existing CHECK shape).
    return EvalLogger.objects.create(
        target_type=EvalTargetType.TRACE,
        observation_span=root_span,
        trace=root_span.trace,
        custom_eval_config=config,
        eval_task_id=_task_id(task_id),
        output_bool=True,
        **overrides,
    )


def _session_entry(task_id, session, config, **overrides):
    return EvalLogger.objects.create(
        target_type=EvalTargetType.SESSION,
        trace_session=session,
        custom_eval_config=config,
        eval_task_id=_task_id(task_id),
        output_bool=True,
        **overrides,
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestSpanUniqueIndex:
    def test_rejects_second_live_span(
        self, observation_span, custom_eval_config, eval_task
    ):
        _span_entry(eval_task.id, observation_span, custom_eval_config)
        with pytest.raises(_REJECTION_ERRORS):
            with transaction.atomic():
                _span_entry(eval_task.id, observation_span, custom_eval_config)

    def test_db_index_rejects_dup_via_bulk_create(
        self, observation_span, custom_eval_config, eval_task
    ):
        _span_entry(eval_task.id, observation_span, custom_eval_config)
        dup = EvalLogger(
            target_type=EvalTargetType.SPAN,
            observation_span=observation_span,
            trace=observation_span.trace,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(eval_task.id),
            output_bool=True,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                EvalLogger.objects.bulk_create([dup])

    def test_allows_after_soft_delete(
        self, observation_span, custom_eval_config, eval_task
    ):
        first = _span_entry(eval_task.id, observation_span, custom_eval_config)
        first.delete()
        second = _span_entry(eval_task.id, observation_span, custom_eval_config)
        assert second.pk != first.pk

    def test_different_tasks_do_not_collide(
        self, observation_span, custom_eval_config, eval_task
    ):
        _span_entry(eval_task.id, observation_span, custom_eval_config)
        other = _span_entry(
            "00000000-0000-0000-0000-0000000000aa", observation_span, custom_eval_config
        )
        assert other.eval_task_id != str(eval_task.id)

    def test_null_task_rows_exempt(self, observation_span, custom_eval_config):
        # Inline evals (null task) are not work-items and may repeat.
        _span_entry(None, observation_span, custom_eval_config)
        _span_entry(None, observation_span, custom_eval_config)
        assert (
            EvalLogger.objects.filter(
                observation_span=observation_span, eval_task_id__isnull=True
            ).count()
            == 2
        )


@pytest.mark.integration
@pytest.mark.django_db
class TestTraceUniqueIndex:
    def test_rejects_second_live_trace(
        self, observation_span, custom_eval_config, eval_task
    ):
        _trace_entry(eval_task.id, observation_span, custom_eval_config)
        with pytest.raises(_REJECTION_ERRORS):
            with transaction.atomic():
                _trace_entry(eval_task.id, observation_span, custom_eval_config)

    def test_allows_after_soft_delete(
        self, observation_span, custom_eval_config, eval_task
    ):
        first = _trace_entry(eval_task.id, observation_span, custom_eval_config)
        first.delete()
        second = _trace_entry(eval_task.id, observation_span, custom_eval_config)
        assert second.pk != first.pk


@pytest.mark.integration
@pytest.mark.django_db
class TestSessionUniqueIndex:
    def test_rejects_second_live_session(self, project, custom_eval_config, eval_task):
        session = TraceSession.objects.create(project=project, name="s")
        _session_entry(eval_task.id, session, custom_eval_config)
        with pytest.raises(_REJECTION_ERRORS):
            with transaction.atomic():
                _session_entry(eval_task.id, session, custom_eval_config)

    def test_allows_after_soft_delete(self, project, custom_eval_config, eval_task):
        session = TraceSession.objects.create(project=project, name="s")
        first = _session_entry(eval_task.id, session, custom_eval_config)
        first.delete()
        second = _session_entry(eval_task.id, session, custom_eval_config)
        assert second.pk != first.pk


@pytest.mark.integration
@pytest.mark.django_db
class TestCrossTargetTypeAndIndexes:
    def test_span_and_trace_on_same_root_span_coexist(
        self, observation_span, custom_eval_config, eval_task
    ):
        # A span row and a trace row sharing the same observation_span (the
        # root span) live in different partial indexes (scoped by target_type),
        # so they must not collide.
        _span_entry(eval_task.id, observation_span, custom_eval_config)
        _trace_entry(eval_task.id, observation_span, custom_eval_config)
        assert (
            EvalLogger.objects.filter(
                eval_task_id=str(eval_task.id), observation_span=observation_span
            ).count()
            == 2
        )

    def test_all_three_indexes_exist(self):
        with connection.cursor() as cursor:
            names = set(
                connection.introspection.get_constraints(
                    cursor, EvalLogger._meta.db_table
                ).keys()
            )
        assert {_SPAN_INDEX, _TRACE_INDEX, _SESSION_INDEX} <= names
