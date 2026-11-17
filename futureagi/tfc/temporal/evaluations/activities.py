"""
Temporal activities for SDK evaluations (on-demand execution).

These activities process individual Evaluation objects immediately when
triggered, rather than being polled by a scheduled job.
"""

from django.db import Error as DatabaseError
from django.db import close_old_connections
from temporalio import activity

from tfc.telemetry import otel_sync_to_async
from tfc.temporal.evaluations.types import (
    RunSingleEvaluationInput,
    RunSingleEvaluationOutput,
)


class EvaluationReadError(Exception):
    """The worker could not read the evaluation for a reason other than its
    absence; the activity raises it so the workflow retries."""


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

    evaluation = None
    eval_result = None
    try:
        from model_hub.models.evaluation import Evaluation, StatusChoices
        from model_hub.tasks.user_evaluation import (
            trigger_error_localization_for_standalone,
        )
        from sdk.utils.evaluations import _run_eval
        from tracer.utils.inline_evals import trigger_inline_eval

        evaluation = Evaluation.objects.select_related(
            "user", "eval_template", "workspace", "organization"
        ).get(id=evaluation_id)

        # The persisted evaluation is the tenant authority after dispatch. Key
        # authentication only binds user.organization in memory; the worker's
        # reloaded user may still have a different legacy organization.
        if (
            evaluation.workspace is not None
            and evaluation.workspace.organization_id != evaluation.organization_id
        ):
            raise ValueError("Evaluation workspace does not belong to its organization")
        # _run_eval uses this binding for quota, ground truth, provider context
        # and metering. Do not save the user: their legacy organization is unchanged.
        evaluation.user.organization = evaluation.organization

        # Mark as processing
        evaluation.status = StatusChoices.PROCESSING
        evaluation.save(update_fields=["status"])

        # Run the actual evaluation. Billed under the evaluation, so a retry
        # of this activity emits the usage event the first attempt emitted.
        # The id goes as it is: an evaluation without one is billed under a
        # fresh id, never under another evaluation's.
        eval_result = _run_eval(
            eval_template=evaluation.eval_template,
            inputs=evaluation.input_data,
            model=evaluation.model_name,
            user=evaluation.user,
            workspace=evaluation.workspace,
            eval_config=evaluation.eval_config,
            evaluation_id=evaluation.id,
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

        # Trigger inline eval (for trace integration)
        trigger_inline_eval(evaluation)

        return {
            "evaluation_id": str(evaluation.id),
            "status": evaluation.status,
        }

    except Exception as e:
        if evaluation is None:
            if isinstance(e, DatabaseError):
                # The database could not answer (for example, it was unreachable).
                # Nothing has run or been charged yet, so the workflow retries.
                raise EvaluationReadError(type(e).__name__) from e
            # The evaluation does not exist (for example, it was deleted after
            # its workflow was started), or its id cannot name one: there is no
            # row to mark, and reading it again cannot succeed.
            return {
                "evaluation_id": evaluation_id,
                "status": "MISSING",
                "error": str(e),
            }

        from sdk.utils.api_errors import evaluation_failure_reason
        from tfc.ee_gates import jev_error_message

        # Mark as failed on any error (matches original behavior)
        evaluation.status = StatusChoices.FAILED
        evaluation.error_message = jev_error_message(e) or str(e)
        # A run that returned keeps the evaluator's own reason.
        if eval_result is None:
            evaluation.reason = evaluation_failure_reason(e)
        # Don't return here - let finally block save and return

        return {
            "evaluation_id": evaluation_id,
            "status": "FAILED",
            "error": str(e),
        }

    finally:
        # Always save (matches original finally block behavior)
        try:
            evaluation.save()
        except Exception:
            pass  # evaluation might not be defined if initial get() failed
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

    except EvaluationReadError:
        # Raised so the workflow's retry policy runs the activity again.
        raise

    except Exception as e:
        activity.logger.exception(
            f"Error running evaluation {input.evaluation_id}: {e}"
        )

        return RunSingleEvaluationOutput(
            evaluation_id=input.evaluation_id,
            status="FAILED",
            error=str(e),
        )


__all__ = [
    "run_single_evaluation_activity",
]
