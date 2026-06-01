"""DB-touching tests for the soft-delete activity helper.

Lives under tracer/tests so it picks up the tracer conftest's
observation_span + custom_eval_config fixtures. Logically owned by the
``tfc/temporal/eval_logger_recalculate`` package.
"""

import uuid

import pytest


@pytest.mark.integration
class TestSoftDeleteSiblingsSync:
    def test_soft_deletes_only_matching_task_and_config(
        self, db, observation_span, custom_eval_config
    ):
        from tfc.temporal.eval_logger_recalculate.activities import (
            _soft_delete_siblings_sync,
        )
        from tracer.models.observation_span import EvalLogger, EvalTargetType

        task_id = str(uuid.uuid4())
        other_task_id = str(uuid.uuid4())

        match_span = EvalLogger.objects.create(
            trace=observation_span.trace,
            observation_span=observation_span,
            custom_eval_config=custom_eval_config,
            target_type=EvalTargetType.SPAN,
            output_str="passed",
            eval_explanation="ok",
            results_explanation={"reason": "ok"},
            eval_task_id=task_id,
        )
        match_trace = EvalLogger.objects.create(
            trace=observation_span.trace,
            observation_span=observation_span,
            custom_eval_config=custom_eval_config,
            target_type=EvalTargetType.TRACE,
            output_str="passed",
            eval_explanation="ok",
            results_explanation={"reason": "ok"},
            eval_task_id=task_id,
        )
        decoy = EvalLogger.objects.create(
            trace=observation_span.trace,
            observation_span=observation_span,
            custom_eval_config=custom_eval_config,
            target_type=EvalTargetType.SPAN,
            output_str="passed",
            eval_explanation="ok",
            results_explanation={"reason": "ok"},
            eval_task_id=other_task_id,
        )

        deleted = _soft_delete_siblings_sync(task_id, str(custom_eval_config.id))

        assert deleted == 2
        match_span.refresh_from_db()
        match_trace.refresh_from_db()
        decoy.refresh_from_db()
        assert match_span.deleted is True
        assert match_trace.deleted is True
        assert decoy.deleted is False

    def test_returns_zero_when_no_matches(self, db, custom_eval_config):
        from tfc.temporal.eval_logger_recalculate.activities import (
            _soft_delete_siblings_sync,
        )

        deleted = _soft_delete_siblings_sync(
            "no-such-task", str(custom_eval_config.id)
        )
        assert deleted == 0
