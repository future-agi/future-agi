"""
Temporal activities for SDK evaluations (on-demand execution).

These activities process individual Evaluation objects immediately when
triggered, rather than being polled by a scheduled job.
"""

import structlog
from django.db import close_old_connections
from temporalio import activity

from tfc.telemetry import otel_sync_to_async
from tfc.temporal.evaluations.types import (
    RunSingleEvaluationInput,
    RunSingleEvaluationOutput,
)

logger = structlog.get_logger(__name__)

# =============================================================================
# Synchronous Helper Functions
# =============================================================================


def _run_single_evaluation_sync(evaluation_id: str) -> dict:
    """
    Synchronous implementation of running a single Evaluation.

    This is the core logic from _run_and_update_evaluation in async_evaluations.py,
    refactored to work as a Temporal activity.
    """
    close_old_connections()

    from model_hub.models.evaluation import Evaluation, StatusChoices
    from sdk.utils.async_evaluations import mark_evaluation_failed

    evaluation = None
    try:
        from model_hub.tasks.user_evaluation import (
            trigger_error_localization_for_standalone,
        )
        from sdk.utils.evaluations import _run_eval
        from tracer.utils.inline_evals import trigger_inline_eval

        evaluation = Evaluation.objects.select_related(
            "user", "eval_template", "workspace", "organization"
        ).get(id=evaluation_id)

        # Mark as processing
        evaluation.status = StatusChoices.PROCESSING
        evaluation.save(update_fields=["status"])

        # Run the actual evaluation
        eval_result = _run_eval(
            eval_template=evaluation.eval_template,
            inputs=evaluation.input_data,
            model=evaluation.model_name,
            user=evaluation.user,
            workspace=evaluation.workspace,
            eval_config=evaluation.eval_config,
        )

        # Update evaluation with results
        evaluation.data = eval_result.get("data")
        evaluation.reason = eval_result.get("reason")
        evaluation.runtime = eval_result.get("runtime")
        evaluation.model = eval_result.get("model")
        evaluation.metrics = eval_result.get("metrics")
        evaluation.metadata = eval_result.get("metadata")
        evaluation.output_type = eval_result.get("output")
        evaluation.value = eval_result.get("value")
        evaluation.status = StatusChoices.COMPLETED

        # NB: the engine's `failure` flag is a verdict indicator
        # (e.g. "this eval returned a Fail choice"), not an execution
        # failure. A run that produced a valid value/reason is always
        # COMPLETED from the API status perspective — the verdict is
        # carried in `value`/`output_type`. Real execution errors are
        # caught by the except branch below.

        # Trigger error localization if enabled
        if evaluation.error_localizer_enabled:
            error_localize = trigger_error_localization_for_standalone(evaluation)
            evaluation.error_localizer = error_localize

        evaluation.save()

        # Isolated: a trace-integration failure must not downgrade a completed run.
        try:
            trigger_inline_eval(evaluation)
        except Exception:
            logger.exception(
                "evaluation_inline_eval_failed", evaluation_id=evaluation_id
            )

        return {
            "evaluation_id": str(evaluation.id),
            "status": evaluation.status,
        }

    except Exception as e:
        if evaluation is None:
            mark_evaluation_failed(evaluation_id, str(e))
        else:
            evaluation.status = StatusChoices.FAILED
            evaluation.error_message = str(e)

        return {
            "evaluation_id": evaluation_id,
            "status": StatusChoices.FAILED,
            "error": str(e),
        }

    finally:
        if evaluation is not None:
            try:
                evaluation.save()
            except Exception:
                logger.exception(
                    "evaluation_status_save_failed", evaluation_id=evaluation_id
                )
                if evaluation.status == StatusChoices.FAILED:
                    mark_evaluation_failed(evaluation_id, evaluation.error_message)
        close_old_connections()


# =============================================================================
# Activities
# =============================================================================


@activity.defn
async def run_single_evaluation_activity(
    input: RunSingleEvaluationInput,
) -> RunSingleEvaluationOutput:
    """
    Process a single Evaluation object.

    This activity is triggered immediately when an async evaluation is requested,
    rather than waiting for a polling job to pick it up.
    Uses Heartbeater for automatic heartbeats during long-running LLM calls.
    """
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(f"Running evaluation {input.evaluation_id}")

    try:
        async with Heartbeater():
            # Use otel_sync_to_async to propagate OTel context to the sync thread.
            # LLM spans will appear as children of the activity span.
            result = await otel_sync_to_async(
                _run_single_evaluation_sync, thread_sensitive=False
            )(input.evaluation_id)

        activity.logger.info(
            f"Evaluation {input.evaluation_id} completed with status: {result['status']}"
        )

        return RunSingleEvaluationOutput(
            evaluation_id=result["evaluation_id"],
            status=result["status"],
            error=result.get("error"),
        )

    except Exception as e:
        activity.logger.exception(
            f"Error running evaluation {input.evaluation_id}: {e}"
        )

        from model_hub.models.evaluation import StatusChoices

        return RunSingleEvaluationOutput(
            evaluation_id=input.evaluation_id,
            status=StatusChoices.FAILED,
            error=str(e),
        )


__all__ = [
    "run_single_evaluation_activity",
]
