"""Schema tests for the additive work-item columns and status index."""

import pytest
from django.db import connection

# Import model_hub.tasks before the tracer models to break a tracer.utils.eval
# import cycle.
import model_hub.tasks  # noqa: F401
from tracer.models.eval_task import EvalTaskLogger
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
)

_TASK_STATUS_INDEX = "eval_logger_task_status_idx"


def _make_span_entry(task_id, span, config, **overrides):
    kwargs = {
        "target_type": EvalTargetType.SPAN,
        "observation_span": span,
        "trace": span.trace,
        "custom_eval_config": config,
        "eval_task_id": str(task_id),
        "output_bool": True,
    }
    kwargs.update(overrides)
    return EvalLogger.objects.create(**kwargs)


@pytest.mark.integration
@pytest.mark.django_db
class TestEntryColumnDefaults:
    def test_status_defaults_to_completed(
        self, observation_span, custom_eval_config, eval_task
    ):
        row = _make_span_entry(eval_task.id, observation_span, custom_eval_config)
        assert row.status == EvalEntryStatus.COMPLETED
        assert row.config_hash is None

    def test_status_accepts_every_entry_state(
        self, observation_span, custom_eval_config, eval_task
    ):
        assert set(EvalEntryStatus.values) == {
            "pending",
            "running",
            "completed",
            "errored",
            "skipped",
        }
        row = _make_span_entry(
            eval_task.id,
            observation_span,
            custom_eval_config,
            status=EvalEntryStatus.PENDING,
        )
        row.refresh_from_db()
        assert row.status == "pending"

    def test_config_hash_persists(
        self, observation_span, custom_eval_config, eval_task
    ):
        digest = "a" * 64
        row = _make_span_entry(
            eval_task.id, observation_span, custom_eval_config, config_hash=digest
        )
        row.refresh_from_db()
        assert row.config_hash == digest


@pytest.mark.integration
@pytest.mark.django_db
class TestEntryStatusIndex:
    def test_task_status_index_exists(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, EvalLogger._meta.db_table
            )
        assert _TASK_STATUS_INDEX in set(constraints.keys())


@pytest.mark.integration
@pytest.mark.django_db
class TestEvalTaskLoggerContinuousCursor:
    def test_continuous_cursor_defaults_null(self, eval_task):
        logger = EvalTaskLogger.objects.create(eval_task=eval_task)
        logger.refresh_from_db()
        assert logger.continuous_cursor is None

    def test_legacy_cursor_columns_still_present(self, eval_task):
        logger = EvalTaskLogger.objects.create(eval_task=eval_task)
        assert hasattr(logger, "offset")
        assert hasattr(logger, "spanids_processed")
