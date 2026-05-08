import json
import time
import traceback
import uuid

import structlog
from django.db import close_old_connections

from accounts.models.organization import Organization

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_evals import *  # noqa: F403
from evaluations.constants import FUTUREAGI_EVAL_TYPES
from model_hub.models.choices import ModelChoices
from model_hub.models.develop_dataset import Column
from model_hub.models.evals_metric import EvalTemplate
from model_hub.views.eval_runner import (
    EvaluationRunner,
    _extract_column_id_and_path,
    process_mapping,
)
from sdk.utils.helpers import _get_api_call_type
from tfc.middleware.workspace_context import get_current_organization
from tfc.temporal import temporal_activity
from tfc.utils.error_codes import get_specific_error_message
try:
    from ee.usage.models.usage import APICallStatusChoices
except ImportError:
    from tfc.oss_stubs.usage import APICallStatusChoices
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request
except ImportError:
    from tfc.oss_stubs.usage import log_and_deduct_cost_for_api_request


def run_eval_func(
    config,
    mappings,
    template,
    org,
    model=ModelChoices.TURING_LARGE.value,
    *args,
    **kwargs,
):
    api_call_log_row = None
    try:
        # Block agent-type evals in OSS mode — AgentEvaluator requires ee/
        if getattr(template, "eval_type", "") == "agent":
            from tfc.ee_loader import _is_oss_mode
            if _is_oss_mode():
                raise ValueError(
                    "Agent evaluations are not available on OSS. "
                    "Use LLM-as-a-Judge or Code evaluations instead."
                )

        # call_type = config["mapping"].get("call_type",None) ## doubt
        call_type = config.get("call_type", None)
        workspace = kwargs.get("workspace", None)
        input_data_types = kwargs.get("input_data_types", {})
        error_localizer = kwargs.get("error_localizer", False)
        kb_id = kwargs.get("kb_id", None)
        # Auto-context payloads — forwarded untouched to the evaluator's
        # run() so `{{row.X}}` / `{{span.X}}` / `{{trace.X}}` /
        # `{{session.X}}` resolve at render time.
        row_context = kwargs.get("row_context")
        span_context = kwargs.get("span_context")
        trace_context = kwargs.get("trace_context")
        session_context = kwargs.get("session_context")
        call_context = kwargs.get("call_context")
        protect = False
        is_only_eval = True
        if call_type:
            if call_type == "protect":
                protect = True
                is_only_eval = False

        # Run the evaluation and get the result
        data_config = config.get("config") if config else {}
        if not isinstance(data_config, dict):
            data_config = {}
        eval_id = kwargs.get("eval_id", None) or template.config.get("eval_type_id")

        # Code evals: if the template is explicitly a code eval AND has code,
        # ensure eval_id is set to CustomCodeEval (guards against frontend saving
        # the wrong eval_type_id). Don't override for other eval types even if
        # they happen to have a stale "code" key in their config.
        if (
            template.config.get("code")
            and template.config.get("eval_type_id") == "CustomCodeEval"
            and eval_id != "CustomCodeEval"
        ):
            eval_id = "CustomCodeEval"

        # get eval class from eval_id (use provided eval_id if available, otherwise from template)
        from evaluations.engine.registry import get_eval_class

        eval_class = globals().get(eval_id)
        if not eval_class:
            try:
                eval_class = get_eval_class(eval_id)
            except Exception:
                pass
        futureagi_eval = True if eval_id in FUTUREAGI_EVAL_TYPES else False
        source = kwargs.get("source", "eval_playground")
        runner = EvaluationRunner(
            eval_id,
            is_only_eval=is_only_eval,
            format_output=True,
            futureagi_eval=futureagi_eval,
            source=source,
            source_id=str(template.id),
            protect=protect,
            organization_id=org.id,
            workspace_id=workspace.id if workspace else None,
        )

        source_config = {
            "reference_id": str(template.id),
            "is_futureagi_eval": futureagi_eval,
        }
        source_config.update(config)
        if input_data_types:
            source_config.update({"input_data_types": input_data_types})
        source_config.update({"mappings": mappings, "source": "eval_playground"})
        # api_call_log_row = log_and_deduct_cost_for_api_request(organization=org,api_call_type=APICallTypeChoices.DATASET_EVALUATION.value, config=source_config, source='eval_playground',source_id=template.id, workspace=workspace)

        # if not api_call_log_row:
        #     raise ValueError("API call not allowed : Error validating the api call.")

        # if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
        #     return get_error_message("INSUFFICIENT_CREDITS") if api_call_log_row.status == 'insufficient_credits' else get_error_message("RATE_LIMIT_REACHED")

        # runner.load_user_eval_metric()
        runner.eval_template = template
        if "mapping" in data_config:
            data_config.pop("mapping")

        eval_instance = runner._create_eval_instance(
            config=data_config,
            eval_class=eval_class,
            model=model,
            kb_id=kb_id,
            runtime_config=config,
        )

        if not mappings:
            for key, value in config.items():
                # Exclude "mapping" to avoid a self-reference cycle: the view
                # passes `mappings` as a reference to `config["mapping"]`
                # when no top-level mapping is provided; copying that key
                # back into itself would create mappings["mapping"] == mappings.
                # The cycle then blows up json.dumps in the usage logger.
                if key not in [
                    "model",
                    "choices",
                    "multi_choice",
                    "params",
                    "config",
                    "mapping",
                ]:
                    mappings[key] = value
                    input_data_types[key] = "text"

        updated_mapping = mappings.copy()
        if mappings is not None:
            param_keys = []
            param_values = []
            for key, value in mappings.items():
                param_keys.append(key)
                if key == "call_type":
                    param_values.append(value)
                    continue
                if isinstance(value, str) and (
                    "https://" in value or "http://" in value
                ):
                    param_values.append(value)
                    updated_mapping[key] = param_values[-1]
                    continue
                if isinstance(value, list):
                    data_list = []
                    for item in value:
                        try:
                            data_list.append(item)
                        except Exception:
                            data_list.append(None)
                    param_values.append(data_list)
                else:
                    try:
                        param_values.append(value)
                    except Exception as e:
                        logger.error(f"e***** {e}")
                        param_values.append(None)

            updated_mapping = runner.map_fields(
                required_field=param_keys,
                mapping=param_values,
                eval_template=template,
                config=config.get("config") if kwargs.get("test", False) else {},
                bypass=kwargs.get("test", False),
            )

        source_config = {
            "reference_id": str(template.id),
            "is_futureagi_eval": futureagi_eval,
            "mappings": mappings,
            "required_keys": list(mappings.keys()),
            "source": source,
            "error_localizer": error_localizer,
        }
        if kb_id:
            source_config.update({"kb_id": str(kb_id)})
        if model:
            source_config.update({"model": str(model)})
        if config is not None:
            source_config.update(config)
        if input_data_types:
            source_config.update({"input_data_types": input_data_types})

        try:
            from ee.usage.schemas.event_types import BillingEventType
        except ImportError:
            BillingEventType = None

        _is_code_eval = getattr(template, "eval_type", "") == "code"
        api_call_type = (
            BillingEventType.CODE_EVALUATOR.value
            if _is_code_eval
            else _get_api_call_type(model)
        )

        # Pre-check: enforce free tier limits before running eval
        try:
            from ee.usage.exceptions import UsageLimitExceeded
        except ImportError:
            UsageLimitExceeded = None
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        if check_usage is not None:
            usage_check = check_usage(str(org.id), api_call_type)
            if not usage_check.allowed:
                raise UsageLimitExceeded(usage_check)

        if log_and_deduct_cost_for_api_request is not None:
            api_call_log_row = log_and_deduct_cost_for_api_request(
                organization=org,
                api_call_type=api_call_type,
                config=source_config,
                source=source,
                source_id=template.id,
                workspace=workspace,
            )

            if not api_call_log_row:
                raise ValueError("API call not allowed : Error validating the api call.")

            if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
                raise ValueError("API call not allowed : ", api_call_log_row.status)
        else:
            api_call_log_row = None

        start_time = time.time()
        # Layer in auto-context kwargs alongside the mapped variables. Any
        # unset context is simply absent from the kwargs dict and the
        # evaluator will substitute "(... data not provided)" placeholders
        # for references it can't resolve.
        _run_kwargs = dict(updated_mapping)
        if row_context is not None:
            _run_kwargs["row_context"] = row_context
        if span_context is not None:
            _run_kwargs["span_context"] = span_context
        if trace_context is not None:
            _run_kwargs["trace_context"] = trace_context
        if session_context is not None:
            _run_kwargs["session_context"] = session_context
        if call_context is not None:
            _run_kwargs["call_context"] = call_context

        # For code evals, inject static user-defined params as kwargs so
        # the user's evaluate() function can receive them via **kwargs.
        if _is_code_eval:
            code_eval_params = config.get("params", {})
            if isinstance(code_eval_params, dict):
                _run_kwargs.update(code_eval_params)

        # Inject ground truth config if enabled on the template
        gt_config_in_template = (
            template.config.get("ground_truth") if template.config else None
        )
        if gt_config_in_template and gt_config_in_template.get("enabled"):
            from model_hub.utils.ground_truth_retrieval import load_ground_truth_config

            gt_config = load_ground_truth_config(template)
            if gt_config:
                # Enrich with embedding_status from the GT model
                try:
                    from model_hub.models.evals_metric import EvalGroundTruth

                    gt_obj = EvalGroundTruth.objects.filter(
                        id=gt_config["ground_truth_id"], deleted=False
                    ).first()
                    if gt_obj:
                        gt_config["embedding_status"] = gt_obj.embedding_status
                except Exception:
                    pass
                _run_kwargs["ground_truth_config"] = gt_config

        # Preprocess inputs for code evals that need external data (e.g. CLIP embeddings)
        if _is_code_eval:
            from evaluations.engine.preprocessing import preprocess_inputs

            _run_kwargs = preprocess_inputs(template.name, _run_kwargs)

        eval_result = eval_instance.run(**_run_kwargs)
        end_time = time.time()

        response = {
            "data": eval_result.eval_results[0].get("data"),
            "failure": eval_result.eval_results[0].get("failure"),
            "reason": eval_result.eval_results[0].get("reason"),
            "runtime": eval_result.eval_results[0].get("runtime"),
            "model": eval_result.eval_results[0].get("model"),
            "metrics": eval_result.eval_results[0].get("metrics"),
            "metadata": eval_result.eval_results[0].get("metadata"),
            "start_time": start_time,
            "end_time": end_time,
            "duration": end_time - start_time,
            "output": (
                template.config.get("output", "score")
                if not kwargs.get("test", False)
                else config.get("output", "choices")
            ),
        }
        # logger.info(f"response*******: {response}")

        metadata = response.get("metadata")
        # Format the result based on output type

        value = runner.format_output(result_data=response, eval_template=template)
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception as e:
                logger.exception(f"Error in parsing metadata: {str(e)}")
                metadata = {}

        if api_call_log_row is None:
            return response
        config_dict = json.loads(api_call_log_row.config)
        config_dict.update(
            {
                "output": {"output": value, "reason": response["reason"]},
                "input": response["data"],
            }
        )
        api_call_log_row.input_token_count = (
            metadata.get("usage", {}).get("prompt_tokens") or 0 if metadata else 0
        )
        api_call_log_row.config = json.dumps(config_dict)
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save()

        # Dual-write: emit usage event for new billing system (cost-based)
        try:
            try:
                from ee.usage.schemas.events import UsageEvent
            except ImportError:
                UsageEvent = None
            try:
                from ee.usage.services.config import BillingConfig
            except ImportError:
                BillingConfig = None
            try:
                from ee.usage.services.emitter import emit
            except ImportError:
                emit = None

            billing_config = BillingConfig.get()
            eval_cost = getattr(eval_instance, "cost", {})
            llm_cost = eval_cost.get("total_cost", 0)
            per_run_fee = billing_config.get_eval_per_run_fee()
            actual_cost = llm_cost + per_run_fee

            # Fallback cost for comparison logging
            _fallback_cost = 0
            try:
                from agentic_eval.core_evals.fi_utils.token_count_helper import (
                    calculate_total_cost,
                )

                _token_usage = getattr(eval_instance, "token_usage", {})
                _fallback = calculate_total_cost(model or "unknown", _token_usage)
                _fallback_cost = _fallback.get("total_cost", 0)
            except Exception:
                pass

            logger.info(
                "eval_cost_breakdown",
                template=template.name,
                model=model,
                llm_cost=llm_cost,
                per_run_fee=per_run_fee,
                actual_cost=actual_cost,
                fallback_calculated_cost=_fallback_cost,
                token_usage=getattr(eval_instance, "token_usage", {}),
            )

            credits = billing_config.calculate_ai_credits(actual_cost)

            emit(
                UsageEvent(
                    org_id=str(org.id),
                    event_type=api_call_type,
                    amount=credits,
                    properties={
                        "source": source,
                        "source_id": str(template.id),
                        "raw_cost_usd": str(actual_cost),
                    },
                )
            )
        except Exception:
            pass  # Metering failure must not break the action

        output = {}
        output["output"] = value
        output["reason"] = response.get("reason")
        output["model"] = response.get("model")
        output["metadata"] = response.get("metadata")
        output["output_type"] = template.config.get("output")
        output["log_id"] = str(api_call_log_row.log_id)

        if error_localizer:
            from model_hub.tasks.user_evaluation import (
                trigger_error_localization_for_playground,
            )

            logger.info(
                f"sending to error localizer: {api_call_log_row.log_id}, {value}, {param_values}, {response.get('reason')}"
            )
            trigger_error_localization_for_playground(
                eval_template=template,
                log=api_call_log_row,
                value=value,
                mapping=mappings,
                eval_explanation=response.get("reason"),
            )

        return output

    except Exception as e:
        try:
            if api_call_log_row:
                api_call_log_row.status = APICallStatusChoices.ERROR.value
                current_config = json.loads(api_call_log_row.config)
                current_config.update(
                    {
                        "output": {"output": None, "reason": str(e)},
                        "mappings": mappings,
                        "required_keys": list(mappings.keys()),
                    }
                )
                api_call_log_row.config = json.dumps(current_config)
                api_call_log_row.save()
        except Exception as exc:
            logger.exception(f"Error updating api call log row status: {str(exc)}")
            pass

        logger.exception(f"Error running evaluation: {str(e)}")
        raise e


def process_eval_for_single_row(
    runner,
    user,
    row,
    mappings,
    data_config,
    run_prompt_column,
    eval_class,
    eval_template,
    futureagi_eval,
    source,
    dataset_id,
    model=ModelChoices.TURING_LARGE.value,
):
    try:
        close_old_connections()
        runner.eval_template = eval_template
        eval_instance = runner._create_eval_instance(
            config=data_config, eval_class=eval_class, model=model
        )

        # Extract base column IDs from mappings (handle JSON paths like uuid.field)
        mapping_uids = []
        for value in mappings.values():
            if isinstance(value, uuid.UUID):
                mapping_uids.append(value)
            elif isinstance(value, str) and value:
                base_col_id, _ = _extract_column_id_and_path(value)
                if base_col_id:
                    mapping_uids.append(base_col_id)

        cols = Column.objects.filter(id__in=mapping_uids).values("id", "data_type")
        col_map = {str(col["id"]): col["data_type"] for col in cols}
        required_field, mapping = process_mapping(
            mappings, row, run_prompt_column=run_prompt_column, runner=runner
        )

        api_call_log_row = runner._handle_api_call(
            row,
            mappings,
            config={
                "preview": True,
                "dataset_id": str(dataset_id),
                "row_id": str(row.id),
                "required_keys": required_field,
            },
            eval_template=eval_template,
            org=get_current_organization() or user.organization,
            preview=True,
            req_map={"required_field": required_field, "mapping": mapping},
        )

        eval_result = eval_instance.run(
            **runner.map_fields(required_field, mapping, eval_template)
        )

        response = {
            "data": eval_result.eval_results[0].get("data"),
            "failure": eval_result.eval_results[0].get("failure"),
            "reason": eval_result.eval_results[0].get("reason"),
            "runtime": eval_result.eval_results[0].get("runtime"),
            "model": eval_result.eval_results[0].get("model"),
            "metrics": eval_result.eval_results[0].get("metrics"),
            "metadata": eval_result.eval_results[0].get("metadata"),
            "output": eval_template.config.get("output"),
        }

        # Format the result based on output type

        value = runner.format_output(response)

        config_dict = json.loads(api_call_log_row.config)

        if (
            eval_template
            and eval_template.config.get("eval_type_id") == "DeterministicEvaluator"
        ):
            metadata_json = eval_result.eval_results[0].get("metadata", "{}")
            json.loads(metadata_json)
            config_dict.update(
                {"output": {"output": value, "reason": response["reason"]}}
            )

        else:
            config_dict.update(
                {"output": {"output": value, "reason": response["reason"]}}
            )

        input_types = {}
        for key, mapping_value in mappings.items():
            # Extract base column ID to look up data type
            if isinstance(mapping_value, uuid.UUID):
                base_col_id = str(mapping_value)
            elif isinstance(mapping_value, str) and mapping_value:
                base_col_id, _ = _extract_column_id_and_path(mapping_value)
            else:
                base_col_id = None
            data_type = col_map.get(str(base_col_id)) if base_col_id else None
            input_types[key] = data_type if data_type in ["image", "audio"] else "text"
        config_dict.update({"input_data_types": input_types})
        api_call_log_row.config = json.dumps(config_dict)
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save()

        output = {}
        for index, key in enumerate(required_field):
            output[key] = mapping[index]
        output["output"] = value
        output["reason"] = response.get("reason")
        # output["model"] = response.get("model")
        output["metadata"] = response.get("metadata")
        output["output_type"] = eval_template.config.get("output")
        output["runtime"] = response.get("runtime")

        # if source == DatasetSourceChoices.SDK.value:
        #     response["output_type"] = eval_template.config.get("output")
        #     response["eval_output"] = value
        #     return response
        # else:
        return output
    except Exception as e:
        try:
            api_call_log_row.status = APICallStatusChoices.ERROR.value
            current_config = json.loads(api_call_log_row.config)
            current_config.update({"output": {"output": None, "reason": str(e)}})
            api_call_log_row.config = json.dumps(current_config)
            api_call_log_row.save()
        except Exception:
            pass
        traceback.print_exc()
        response = {"reason": get_specific_error_message(e), "output_type": "reason"}
        return response
    finally:
        close_old_connections()


@temporal_activity(time_limit=3600, queue="tasks_s")
def run_eval_func_task(
    mappings,
    template_id,
    org_id,
    model=ModelChoices.TURING_LARGE.value,
    kb_id=None,
    log_id=None,
    workspace_id=None,
    input_data_types=None,
):
    try:
        try:
            template = EvalTemplate.no_workspace_objects.get(id=template_id)
        except EvalTemplate.DoesNotExist:
            logger.exception(f"Evaluation template not found for {template_id}")
            return

        try:
            org = Organization.objects.get(id=org_id)
        except Organization.DoesNotExist:
            logger.exception(f"Organization not found for {org_id}")
            return

        # Re-hydrate workspace from ID (callers pass a string ID for Temporal serialization)
        workspace = None
        if workspace_id:
            from accounts.models.workspace import Workspace

            workspace = Workspace.objects.filter(id=workspace_id).first()

        run_eval_func(
            {},
            mappings,
            template,
            org,
            model=model,
            kb_id=kb_id,
            workspace=workspace,
            input_data_types=input_data_types,
        )

        logger.info(f"Evaluation for feedback completed for {log_id}")
    except Exception as e:
        logger.exception(f"Error running evaluation for feedback: {str(e)}")
