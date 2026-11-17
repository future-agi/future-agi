"""
SDK async evaluations - on-demand execution via Temporal workflows.

This module handles async evaluation requests from the SDK. When an async
evaluation is requested, it creates an Evaluation object and immediately
starts a Temporal workflow to process it (replacing the old polling-based
Celery approach).
"""

import structlog

from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.utils.api_errors import EVALUATION_COULD_NOT_START
from tfc.middleware.workspace_context import get_current_organization

logger = structlog.get_logger(__name__)


def _handle_async_eval(
    eval_template,
    inputs,
    model,
    user,
    workspace,
    span_id,
    custom_eval_name,
    trace_eval,
    is_batched,
    eval_config=None,
    error_localizer_enabled: bool = False,
    start_workflow=None,
):
    try:
        if is_batched:
            result = _handle_batched_async_eval(
                eval_template,
                inputs,
                model,
                user,
                workspace,
                span_id,
                custom_eval_name,
                trace_eval,
                eval_config,
                error_localizer_enabled,
                start_workflow,
            )
        else:
            result = _handle_single_async_eval(
                eval_template,
                inputs,
                model,
                user,
                workspace,
                span_id,
                custom_eval_name,
                trace_eval,
                eval_config,
                error_localizer_enabled,
                start_workflow,
            )
            result = [result]

        return [{"evaluations": result}]

    except Exception as e:
        raise e


def _handle_batched_async_eval(
    eval_template,
    inputs,
    model,
    user,
    workspace,
    span_id,
    custom_eval_name,
    trace_eval,
    eval_config,
    error_localizer_enabled: bool = False,
    start_workflow=None,
):
    results = []
    if not inputs or not isinstance(list(inputs.values())[0], list):
        raise ValueError("Inputs must be a list")

    keys = list(inputs.keys())
    num_items = len(inputs[keys[0]])

    for i in range(num_items):
        input_item = {key: inputs[key][i] for key in keys}
        result = _handle_single_async_eval(
            eval_template,
            input_item,
            model,
            user,
            workspace,
            span_id,
            custom_eval_name,
            trace_eval,
            eval_config,
            error_localizer_enabled,
            start_workflow,
        )
        results.append(result)

    return results


def _handle_single_async_eval(
    eval_template,
    inputs,
    model,
    user,
    workspace,
    span_id,
    custom_eval_name,
    trace_eval,
    eval_config,
    error_localizer_enabled: bool = False,
    start_workflow=None,
):
    if trace_eval:
        trace_data = {"span_id": span_id, "custom_eval_name": custom_eval_name}
    else:
        trace_data = None

    _org = get_current_organization() or user.organization
    evaluation = Evaluation.objects.create(
        user=user,
        organization=_org,
        workspace=workspace,
        eval_template=eval_template,
        model_name=model,
        input_data=inputs,
        eval_config=eval_config or {},
        trace_data=trace_data,
        error_localizer_enabled=error_localizer_enabled,
    )

    # A caller that creates evaluations inside its own transaction passes
    # start_workflow and starts each workflow once that transaction is committed:
    # the worker reads the evaluation in its own database connection.
    if start_workflow is not None:
        start_workflow(str(evaluation.id))
    else:
        start_evaluation(evaluation.id)

    return {
        "name": eval_template.name,
        "output_type": eval_template.config.get("output", "score"),
        "eval_id": str(evaluation.id),
    }


def start_evaluation(evaluation_id):
    """Start the evaluation's Temporal workflow. When the start fails, the
    evaluation ends failed with the sentence a person reads instead of waiting
    with no reason; the API call that created it does not fail."""
    try:
        from tfc.temporal.evaluations import start_evaluation_workflow

        start_evaluation_workflow(evaluation_id=str(evaluation_id))
        logger.info(f"Started evaluation workflow for {evaluation_id}")
    except Exception as e:
        logger.exception(
            f"Failed to start evaluation workflow for {evaluation_id}: {e}"
        )
        Evaluation.no_workspace_objects.filter(
            pk=evaluation_id, status=StatusChoices.PENDING
        ).update(
            status=StatusChoices.FAILED,
            reason=EVALUATION_COULD_NOT_START,
            error_message=EVALUATION_COULD_NOT_START,
        )


# =============================================================================
# Legacy Celery task removed - replaced by Temporal on-demand workflows
# =============================================================================
# The _run_async_evals Celery task that polled for PENDING evaluations every 30s
# has been replaced by Temporal workflows that execute immediately when an
# evaluation is requested. See: tfc/temporal/evaluations/
#
# The evaluation logic is now in: tfc/temporal/evaluations/activities.py
# =============================================================================
