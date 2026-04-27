import json

import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_evals import *  # noqa: F403

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from model_hub.tasks.user_evaluation import trigger_error_localization_for_standalone
from sdk.utils.helpers import _get_api_call_type
from tracer.utils.inline_evals import trigger_inline_eval
try:
    from ee.usage.models.usage import APICallStatusChoices
except ImportError:
    APICallStatusChoices = None
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request
except ImportError:
    log_and_deduct_cost_for_api_request = None


class StandaloneEvaluationError(Exception):
    """Custom exception for errors during standalone evaluation."""

    pass


def _log_and_deduct_cost_for_standalone_eval(
    user,
    eval_template,
    is_futureagi_eval: bool,
    run_params,
    model=None,
    kb_id=None,
    workspace=None,
):
    log_config = {
        "reference_id": str(user.id),
        "is_futureagi_eval": is_futureagi_eval,
        "mappings": run_params,
        "required_keys": list(run_params.keys()),
        "source": "standalone_v2",
        "error_localizer": False,
    }

    api_call_type = _get_api_call_type(model)

    try:
        from ee.usage.services.metering import check_usage
    except ImportError:
        check_usage = None

    usage_check = check_usage(str(user.organization.id), api_call_type)
    if not usage_check.allowed:
        raise ValueError(usage_check.reason or "Usage limit exceeded")

    if model:
        log_config.update({"model": str(model)})
    if kb_id:
        log_config.update({"kb_id": str(kb_id)})

    api_call_log_row = log_and_deduct_cost_for_api_request(
        organization=user.organization,
        api_call_type=api_call_type,
        source="standalone_v2",
        source_id=eval_template.id,
        config=log_config,
        workspace=workspace,
    )

    if not api_call_log_row:
        raise ValueError("API call not allowed : Error validating the api call.")

    if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
        raise ValueError(f"API call not allowed: {api_call_log_row.status}")

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
                org_id=str(user.organization.id),
                event_type=api_call_type,
                properties={
                    "source": "standalone_v2",
                    "source_id": str(eval_template.id),
                },
            )
        )
    except Exception:
        pass  # Metering failure must not break the action

    return api_call_log_row


def _run_evaluation(run_params, eval_model, eval_instance, runner):
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


def _run_eval(eval_template, inputs, model, user, workspace, eval_config=None):
    logger.info(
        f"Standalone Eval | Start: user: {user.id}, template: {eval_template.name}"
    )
    from evaluations.constants import FUTUREAGI_EVAL_TYPES
    from evaluations.engine import EvalRequest, run_eval

    eval_type_id = eval_template.config.get("eval_type_id")
    if not eval_type_id:
        raise ValueError(
            f"eval_type_id not found in EvalTemplate config for {eval_template.name}"
        )

    futureagi_eval = eval_type_id in FUTUREAGI_EVAL_TYPES

    # --- Ground Truth Injection (caller-side, before engine call) ---
    gt_inputs = dict(inputs) if inputs else {}
    try:
        from model_hub.utils.ground_truth_retrieval import (
            format_few_shot_examples,
            get_ground_truth_few_shot_examples,
            load_ground_truth_config,
        )

        gt_config = load_ground_truth_config(eval_template)
        if gt_config:
            from model_hub.models.evals_metric import EvalGroundTruth

            gt_id = gt_config.get("ground_truth_id")
            gt_obj = EvalGroundTruth.objects.filter(id=gt_id, deleted=False).first()

            if eval_type_id == "CustomPromptEvaluator" and gt_obj:
                gt_examples = get_ground_truth_few_shot_examples(
                    gt_config, inputs or {}
                )
                if gt_examples:
                    injection_format = gt_config.get("injection_format", "structured")
                    formatted = format_few_shot_examples(
                        gt_examples, gt_obj.role_mapping, injection_format
                    )
                    gt_inputs["ground_truth_few_shot"] = formatted

            elif eval_type_id == "AgentEvaluator" and gt_obj:
                gt_inputs["ground_truth_config"] = {
                    "ground_truth_id": str(gt_id),
                    "embedding_status": gt_obj.embedding_status,
                }
    except Exception as e:
        logger.warning(
            f"Standalone Eval | Ground truth injection failed (non-fatal): {e}"
        )

    # --- Cost tracking (caller-side, before engine call) ---
    api_call_log_row = _log_and_deduct_cost_for_standalone_eval(
        user,
        eval_template,
        futureagi_eval,
        gt_inputs,
        model=model,
        workspace=workspace,
    )

    # --- Run eval via unified engine ---
    try:
        result = run_eval(
            EvalRequest(
                eval_template=eval_template,
                inputs=gt_inputs,
                model=model,
                runtime_config=eval_config,
                organization_id=str(user.organization.id),
                workspace_id=str(workspace.id) if workspace else None,
            )
        )
    except Exception as e:
        logger.exception(f"Standalone Eval | Failed: user: {user.id}, error: {e}")

        api_call_log_row.status = APICallStatusChoices.ERROR.value
        config_dict = json.loads(api_call_log_row.config)
        config_dict.update(
            {
                "output": {"output": None, "reason": str(e)},
            }
        )
        api_call_log_row.config = json.dumps(config_dict)
        api_call_log_row.save()

        raise StandaloneEvaluationError(e)  # noqa: B904

    # --- Update cost log with result (caller-side) ---
    eval_result = {
        "data": result.data,
        "failure": result.failure,
        "reason": result.reason,
        "runtime": result.runtime,
        "model": result.model_used,
        "metrics": result.metrics,
        "metadata": result.metadata,
        "output": result.output_type,
        "value": result.value,
    }

    config_dict = json.loads(api_call_log_row.config)
    config_dict.update(
        {
            "input": eval_result.get("data", {}),
            "output": {
                "output": eval_result.get("value", ""),
                "reason": eval_result.get("reason", ""),
            },
        }
    )
    api_call_log_row.config = json.dumps(config_dict)
    api_call_log_row.status = APICallStatusChoices.SUCCESS.value
    api_call_log_row.save()

    logger.info(f"Standalone Eval | Completed: user: {user.id}")

    return eval_result


def _format_standalone_eval_result(eval_template, eval_result, eval_id):
    result = {
        "name": eval_result.get("name", eval_template.name),
        "reason": eval_result.get("reason", ""),
        "runtime": eval_result.get("runtime", 0),
        "output": eval_result.get("value", ""),
        "output_type": eval_result.get("output", ""),
        "eval_id": eval_id,
    }

    return result


def _create_evaluation(
    eval_template,
    inputs,
    user,
    eval_config,
    result,
    error_localizer_enabled,
    trace_eval,
    custom_eval_name,
    span_id,
):
    """
    Create an evaluation object in the database to store the result of the evaluation.
    This result can be retrieved later for analysis.
    """
    status = (
        StatusChoices.COMPLETED if not result.get("failure") else StatusChoices.FAILED
    )

    evaluation = Evaluation.objects.create(
        user=user,
        organization=user.organization,
        eval_template=eval_template,
        input_data=inputs,
        eval_config=eval_config,
        data=result.get("data"),
        reason=result.get("reason"),
        runtime=result.get("runtime"),
        model=result.get("model"),
        metrics=result.get("metrics"),
        metadata=result.get("metadata"),
        output_type=result.get("output"),
        value=result.get("value"),
        status=status,
        error_localizer_enabled=error_localizer_enabled,
        trace_data=(
            {"span_id": span_id, "custom_eval_name": custom_eval_name}
            if trace_eval
            else None
        ),
    )

    if error_localizer_enabled:
        error_localize = trigger_error_localization_for_standalone(evaluation)
        evaluation.error_localizer = error_localize
        evaluation.save()

    return evaluation


def _run_standalone_eval(
    eval_template,
    inputs,
    model,
    user,
    workspace,
    eval_config,
    error_localizer_enabled,
    trace_eval,
    custom_eval_name,
    span_id,
):
    result = _run_eval(eval_template, inputs, model, user, workspace, eval_config)
    evaluation = _create_evaluation(
        eval_template,
        inputs,
        user,
        eval_config,
        result,
        error_localizer_enabled,
        trace_eval,
        custom_eval_name,
        span_id,
    )
    formatted_result = _format_standalone_eval_result(
        eval_template, result, evaluation.id
    )
    trigger_inline_eval(evaluation)

    return formatted_result


def _run_batched_standalone_eval(
    eval_template,
    inputs,
    model,
    user,
    workspace,
    eval_config,
    error_localizer_enabled,
    trace_eval,
    custom_eval_name,
    span_id,
):
    results = []
    if not inputs or not isinstance(list(inputs.values())[0], list):
        raise ValueError("Inputs must be a list")

    keys = list(inputs.keys())
    num_items = len(inputs[keys[0]])

    for i in range(num_items):
        input_item = {key: inputs[key][i] for key in keys}
        result = _run_standalone_eval(
            eval_template,
            input_item,
            model,
            user,
            workspace,
            eval_config,
            error_localizer_enabled,
            trace_eval,
            custom_eval_name,
            span_id,
        )
        results.append(result)

    return results


def _run_protect(
    inputs, config, protect_flash, eval_id, user, sdk_uuid, workspace=None
):
    try:
        from evaluations.engine import EvalRequest, run_eval

        eval_template = EvalTemplate.no_workspace_objects.get(eval_id=eval_id)

        # Prepare inputs: fill missing required keys with the "input" text
        protect_inputs = dict(inputs) if inputs else {}
        template_required_keys = eval_template.config.get("required_keys") or []
        input_text = inputs.get("input", "")
        for rk in template_required_keys:
            if rk not in protect_inputs:
                protect_inputs[rk] = input_text
        protect_inputs["call_type"] = "protect_flash" if protect_flash else "protect"

        api_call_log_row = _log_and_deduct_cost_for_standalone_eval(
            user, eval_template, True, protect_inputs, workspace=workspace
        )

        try:
            result = run_eval(
                EvalRequest(
                    eval_template=eval_template,
                    inputs=protect_inputs,
                    config_overrides=config or {},
                    model="protect_flash" if protect_flash else None,
                    organization_id=str(user.organization.id),
                    workspace_id=str(workspace.id) if workspace else None,
                )
            )
        except Exception as e:
            logger.exception(f"Protect Eval | Failed: user: {user.id}, error: {e}")

            api_call_log_row.status = APICallStatusChoices.ERROR.value
            config_dict = json.loads(api_call_log_row.config)
            config_dict.update(
                {
                    "output": {"output": None, "reason": str(e)},
                }
            )
            api_call_log_row.config = json.dumps(config_dict)
            api_call_log_row.save()

            raise StandaloneEvaluationError(e)  # noqa: B904

        formatted = {
            "name": eval_template.name,
            "reason": result.reason or "",
            "runtime": result.runtime or 0,
            "output": result.value or "",
            "output_type": result.output_type or "",
            "eval_id": sdk_uuid,
        }

        config_dict = json.loads(api_call_log_row.config)
        config_dict.update(
            {
                "input": result.data or {},
                "output": {
                    "output": formatted["output"],
                    "reason": formatted["reason"],
                },
            }
        )
        api_call_log_row.config = json.dumps(config_dict)
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save()

        logger.info(f"Protect Eval | Completed: user: {user.id}")
        return formatted
    except Exception as e:
        logger.exception(f"Protect Eval | Failed: user: {user.id}, error: {e}")
        raise e
