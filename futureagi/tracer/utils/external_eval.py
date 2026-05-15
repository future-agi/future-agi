import structlog
from langfuse import Langfuse

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_evals import *  # noqa: F403
from evaluations.constants import FUTUREAGI_EVAL_TYPES
from model_hub.views.eval_runner import EvaluationRunner
from sdk.utils.helpers import _get_api_call_type
from tfc.temporal import temporal_activity
from tracer.models.external_eval_config import (
    ExternalEvalConfig,
    PlatformChoices,
    StatusChoices,
)
try:
    from ee.usage.models.usage import APICallStatusChoices
except ImportError:
    APICallStatusChoices = None
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request
except ImportError:
    from futureagi._ee_stubs import log_and_deduct_cost_for_api_request


def _run_external_platform_evaluation(
    run_params, eval_model, eval_instance, external_eval_config, runner
):
    """
    This is a simplified version of _run_evaluation from tracer/utils/eval.py
    It runs the evaluation and returns the result.
    """
    result = eval_instance.run(**run_params)
    output_type = eval_model.config.get("output", "score")

    response = {
        "data": result.eval_results[0].get("data"),
        "failure": result.eval_results[0].get("failure"),
        "reason": result.eval_results[0].get("reason"),
        "runtime": result.eval_results[0].get("runtime"),
        "model": result.eval_results[0].get("model"),
        "metrics": result.eval_results[0].get("metrics"),
        "metadata": result.eval_results[0].get("metadata"),
        "output": output_type,
    }

    value = runner.format_output(result_data=response, eval_template=eval_model)
    response["value"] = value

    return response


def _log_and_deduct_cost_for_external_eval(
    config: ExternalEvalConfig, is_futureagi_eval: bool
):
    log_config = config.config if config.config else {}
    log_config["is_futureagi_eval"] = is_futureagi_eval

    api_call_type = _get_api_call_type(config.model)

    # Pre-check: enforce free tier limits
    try:
        from ee.usage.services.metering import check_usage
    except ImportError:
        check_usage = None

    usage_check = check_usage(str(config.organization.id), api_call_type)
    if not usage_check.allowed:
        raise ValueError(usage_check.reason or "Usage limit exceeded")

    api_call_log_row = log_and_deduct_cost_for_api_request(
        organization=config.organization,
        api_call_type=api_call_type,
        source="tracer",
        source_id=config.id,
        config=log_config,
        workspace=config.workspace,
    )
    if not api_call_log_row:
        raise ValueError("API call not allowed : Error validating the api call.")

    if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
        raise ValueError("API call not allowed : ", api_call_log_row.status)

    # Dual-write: emit usage event for new billing system
    try:
        try:
            from ee.usage.schemas.events import UsageEvent
        except ImportError:
            UsageEvent = None
        try:
            from ee.usage.services.emitter import emit
        except ImportError:
            emit = None

        emit(
            UsageEvent(
                org_id=str(config.organization.id),
                event_type=api_call_type,
                properties={
                    "source": "tracer",
                    "source_id": str(config.id),
                },
            )
        )
    except Exception:
        pass  # Metering failure must not break the action

    return api_call_log_row


def _execute_composite_on_external_platform(config: ExternalEvalConfig):
    """Run a composite eval against an external platform config.

    Mirrors `_execute_evaluation_on_external_platform` but uses the
    shared composite helper instead of instantiating an EvaluationRunner
    per child. Persists the aggregate + per-child result on the config
    and emits the same downstream notification.
    """
    from model_hub.models.evals_metric import CompositeEvalChild
    from model_hub.utils.composite_execution import execute_composite_children_sync

    parent = config.eval_template
    child_links = list(
        CompositeEvalChild.objects.filter(parent=parent, deleted=False)
        .select_related("child", "pinned_version")
        .order_by("order")
    )
    if not child_links:
        raise ValueError(
            f"Composite {parent.id} has no children — cannot run externally."
        )

    _log_and_deduct_cost_for_external_eval(config, futureagi_eval=False)

    outcome = execute_composite_children_sync(
        parent=parent,
        child_links=child_links,
        mapping=config.mapping or {},
        config=config.config or {},
        org=config.organization,
        workspace=config.workspace,
        model=config.model,
        source="external_composite",
    )

    value = (
        outcome.aggregate_score
        if parent.aggregation_enabled
        else (outcome.summary or "")
    )
    reason = outcome.summary or ""

    config.eval_results = {
        "value": value,
        "reason": reason,
        "aggregate_pass": outcome.aggregate_pass,
        "composite": True,
        "children": [cr.model_dump() for cr in outcome.child_results],
    }
    config.save()

    _send_eval_result(config, value, reason)


def _execute_evaluation_on_external_platform(config: ExternalEvalConfig):
    logger.info(
        f"Executing evaluation for config: {config.id} on platform {config.platform}"
    )

    eval_template = config.eval_template

    # Composite templates don't have an eval_type_id — fan them out via
    # the shared helper and persist the aggregate result on the config.
    if eval_template.template_type == "composite":
        _execute_composite_on_external_platform(config)
        return

    eval_type_id = eval_template.config.get("eval_type_id")

    if not eval_type_id:
        raise ValueError(
            f"eval_type_id not found in EvalTemplate config for {eval_template.name}"
        )

    futureagi_eval = True if eval_type_id in FUTUREAGI_EVAL_TYPES else False
    _log_and_deduct_cost_for_external_eval(config, futureagi_eval)

    runner = EvaluationRunner(
        eval_type_id,
        format_output=True,
        futureagi_eval=futureagi_eval,
        source="external",
        source_id=str(config.id),
        organization_id=config.organization.id,
        workspace_id=config.workspace.id if config.workspace else None,
    )
    runner.eval_template = eval_template
    from evaluations.engine.registry import get_eval_class

    eval_class = get_eval_class(eval_type_id)

    eval_instance = runner._create_eval_instance(
        config=config.config or {},
        eval_class=eval_class,
        model=config.model,
        runtime_config=config.config,
    )

    param_keys = []
    param_values = []

    if config.mapping is not None:
        for key, value in config.mapping.items():
            param_keys.append(key)
            param_values.append(value)

    run_params = runner.map_fields(required_field=param_keys, mapping=param_values)
    eval_result = _run_external_platform_evaluation(
        run_params=run_params,
        eval_model=eval_template,
        eval_instance=eval_instance,
        external_eval_config=config,
        runner=runner,
    )

    config.eval_results = eval_result
    config.save()

    value = eval_result.get("value")
    reason = eval_result.get("reason")

    _send_eval_result(config, value, reason)


@temporal_activity(
    max_retries=0,
    time_limit=3600,
    queue="tasks_s",
)
def run_external_eval_config(config_id):
    """
    Main entry point for running an external evaluation.
    This function is called by the Celery task.
    """
    try:
        config = ExternalEvalConfig.objects.get(id=config_id)
    except ExternalEvalConfig.DoesNotExist:
        logger.error(f"ExternalEvalConfig with id {config_id} not found.")
        return

    logger.info(f"Processing external eval config: {config.id}")
    try:
        _execute_evaluation_on_external_platform(config)

        config.status = StatusChoices.COMPLETED
        config.save()
        logger.info(f"Successfully processed external eval config: {config.id}")

    except Exception as e:
        logger.exception(f"Failed to process external eval config {config.id}: {e}")
        config.status = StatusChoices.FAILED
        config.error_message = str(e)
        config.save()


def _send_eval_result(config: ExternalEvalConfig, value: str, reason: str):
    if config.platform == PlatformChoices.LANGFUSE:
        _send_langfuse_eval_result(config, value, reason)
    else:
        raise NotImplementedError(
            f"Platform '{config.platform}' is not supported for external evaluations."
        )


def _send_langfuse_eval_result(config: ExternalEvalConfig, value, reason: str):
    langfuse = Langfuse(
        secret_key=config.credentials.get("langfuse_secret_key"),
        public_key=config.credentials.get("langfuse_public_key"),
        host=config.credentials.get("langfuse_host"),
    )

    langfuse.create_score(
        trace_id=str(config.credentials.get("trace_id")),
        observation_id=str(config.credentials.get("span_id")),
        name=config.name,
        value=_process_results_langfuse(value),
        comment=reason,
    )

    config.logs.append(
        {"type": "langfuse", "message": f"Langfuse Eval Result Sent: {value} {reason}"}
    )
    config.save()


def _process_results_langfuse(value):
    if isinstance(value, list):
        return ", ".join(value)
    elif isinstance(value, str):
        if value == "Passed":
            return 1
        elif value == "Failed":
            return 0
    elif isinstance(value, int | float):
        return float(value)
    else:
        raise ValueError(f"Invalid value type: {type(value)}")
