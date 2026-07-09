import json

import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_evals import *  # noqa: F403

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from model_hub.tasks.user_evaluation import trigger_error_localization_for_standalone
from sdk.utils.helpers import _get_api_call_type
from tfc.billing.boundary import get_billing, token_usage_properties
from tfc.constants.api_calls import APICallStatusChoices
from tracer.utils.inline_evals import trigger_inline_eval


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

    billing = get_billing()
    usage_check = billing.check_usage(str(user.organization.id), api_call_type)
    if not usage_check.allowed:
        raise ValueError(usage_check.reason or "Usage limit exceeded")

    if model:
        log_config.update({"model": str(model)})
    if kb_id:
        log_config.update({"kb_id": str(kb_id)})

    api_call_log_row = billing.log_and_deduct(
        organization=user.organization,
        api_call_type=api_call_type,
        source="standalone_v2",
        source_id=eval_template.id,
        config=log_config,
        workspace=workspace,
    )

    if billing.deduct_denied(api_call_log_row):
        if api_call_log_row is None:
            raise ValueError("API call not allowed")
        raise ValueError(f"API call not allowed: {api_call_log_row.status}")

    # NOTE: No pre-eval UsageEvent emission. The cost isn't known until the
    # eval finishes, and UsageEvent.amount defaults to 1 — emitting here
    # would charge a flat 1 credit on every call (and double-bill on success
    # alongside the post-eval cost-based emit in `_run_eval`). The UI path
    # in `model_hub/views/utils/evals.py` follows the same pattern: only the
    # post-eval cost-based event is emitted to the new billing system.
    return api_call_log_row


def _run_evaluation(run_params, eval_model, eval_instance, runner):
    """
    This is a simplified version of _run_evaluation from tracer/utils/eval.py
    It runs the evaluation and returns the result.
    """
    # Apply the shared empty-input rules so the SDK path is consistent
    # with dataset/playground/tracing. For custom evals the validator
    # also fills any missing required_keys with "" so the engine's
    # "Missing required key" check passes; the eval still runs with a
    # partial_input warning attached to the response.
    from model_hub.utils.eval_input_validation import validate_eval_inputs

    partial_input_warning, run_params = validate_eval_inputs(
        eval_model, run_params
    )

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
    if partial_input_warning:
        response["warnings"] = [partial_input_warning]

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
    # The SDK wraps user eval_config in {"params": {...}} (see
    # ConfigureEvaluationsSerializer / normalize_eval_runtime_config), so look
    # in both the top level (legacy/internal callers) and inside ``params``
    # (SDK callers).
    _params = (eval_config or {}).get("params") or {}
    kb_id = (
        (eval_config or {}).get("kb_id")
        or (eval_config or {}).get("knowledge_base_id")
        or _params.get("kb_id")
        or _params.get("knowledge_base_id")
    )

    # Build the runtime_config that is forwarded to the engine. Downstream
    # consumers (``create_eval_instance``) look at top-level keys like
    # ``run_config`` for AgentEvaluator overrides, but the SDK wraps user
    # input in ``params``. Lift any non-schema keys from ``params`` to the
    # top level so multi-KB (``knowledge_bases``) and other runtime overrides
    # work over the SDK as well.
    if isinstance(_params, dict) and _params:
        _engine_runtime_config = {
            **(eval_config or {}),
            **{k: v for k, v in _params.items() if k not in ("params",)},
        }
    else:
        _engine_runtime_config = eval_config

    futureagi_eval = eval_type_id in FUTUREAGI_EVAL_TYPES

    # --- Shared empty-input validation (must happen before cost
    # deduction so an invalid request never charges). For custom evals
    # the validator backfills missing required_keys with "" and may
    # return a partial_input warning. For system evals it keeps the
    # historical per-key strict behaviour.
    from model_hub.utils.eval_input_validation import validate_eval_inputs

    partial_input_warning, inputs = validate_eval_inputs(
        eval_template, dict(inputs) if inputs else {}, mapped_keys=(inputs or {}).keys()
    )

    gt_inputs = dict(inputs) if inputs else {}
    from model_hub.services.ground_truth_service import GroundTruthService

    user_org = getattr(user, "organization", None)
    GroundTruthService.inject_context(
        gt_inputs,
        eval_template,
        organization_id=user_org.id if user_org else None,
        workspace_id=workspace.id if workspace else None,
    )

    # --- Cost tracking (caller-side, before engine call) ---
    api_call_log_row = _log_and_deduct_cost_for_standalone_eval(
        user,
        eval_template,
        futureagi_eval,
        gt_inputs,
        model=model,
        kb_id=kb_id,
        workspace=workspace,
    )

    # --- Run eval via unified engine ---
    try:
        result = run_eval(
            EvalRequest(
                eval_template=eval_template,
                inputs=gt_inputs,
                model=model,
                kb_id=kb_id,
                runtime_config=_engine_runtime_config,
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

    output_payload = {
        "output": eval_result.get("value", ""),
        "reason": eval_result.get("reason", ""),
    }
    if partial_input_warning:
        output_payload["warnings"] = [partial_input_warning]
        eval_result["warnings"] = [partial_input_warning]

    if api_call_log_row is not None:
        config_dict = json.loads(api_call_log_row.config)
        config_dict.update(
            {
                "input": eval_result.get("data", {}),
                "output": output_payload,
            }
        )
        api_call_log_row.config = json.dumps(config_dict)
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save()

    # Dual-write: emit cost-based usage event for new billing system.
    # The pre-eval emit in _log_and_deduct_cost_for_standalone_eval has no
    # amount (cost is unknown until the eval runs), so without this post-eval
    # emit SDK/API-key evals never produce a billable UsageEvent. (TH-3402)
    try:
        billing = get_billing()
        eval_cost = result.cost or {}
        token_usage = result.token_usage or {}
        llm_cost = eval_cost.get("total_cost", 0)

        per_run_fee = 0
        try:
            per_run_fee = billing.eval_per_run_fee()
        except Exception:
            pass
        actual_cost = llm_cost + per_run_fee
        credits = billing.ai_credits(actual_cost)

        api_call_type = _get_api_call_type(model)

        billing.record_usage(
            str(user.organization.id),
            api_call_type,
            amount=credits,
            source="standalone_v2",
            source_id=str(eval_template.id),
            raw_cost_usd=str(actual_cost),
            log_id=str(api_call_log_row.log_id) if api_call_log_row else None,
            **token_usage_properties(token_usage),
        )

    except Exception:
        pass  # Metering failure must not break the action

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
            user, eval_template, True, protect_inputs,
            model="protect_flash" if protect_flash else "protect",
            workspace=workspace,
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

            if api_call_log_row is not None:
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

        if api_call_log_row is not None:
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

        # Emit usage event with actual cost after eval completion
        try:
            token_usage = (result.metadata or {}).get("token_usage", {})
            from agentic_eval.core_evals.fi_utils.token_count_helper import (
                calculate_total_cost,
            )

            # Resolve model alias for pricing lookup
            if protect_flash:
                protect_model = "protect_flash"
            else:
                try:
                    from ee.protect.helper import ProtectHelper

                    protect_model = ProtectHelper.resolve_alias(
                        eval_template.name, is_flash=False
                    )
                except ImportError:
                    protect_model = f"protect_{eval_template.name}"
            cost_info = calculate_total_cost(protect_model, token_usage)
            llm_cost = cost_info.get("total_cost", 0)

            per_run_fee = 0
            try:
                per_run_fee = get_billing().eval_per_run_fee()
            except Exception:
                pass
            actual_cost = llm_cost + per_run_fee

            billing_protect = get_billing()
            credits = billing_protect.ai_credits(actual_cost)

            billing_protect.record_usage(
                str(user.organization.id),
                _get_api_call_type(
                    "protect_flash" if protect_flash else "protect"
                ),
                amount=credits,
                source="standalone_v2",
                source_id=str(eval_template.id),
                raw_cost_usd=str(actual_cost),
                **token_usage_properties(token_usage),
            )

        except Exception:
            pass

        logger.info(f"Protect Eval | Completed: user: {user.id}")
        return formatted
    except Exception as e:
        logger.exception(f"Protect Eval | Failed: user: {user.id}, error: {e}")
        raise e
