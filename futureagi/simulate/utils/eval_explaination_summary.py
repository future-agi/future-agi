import concurrent.futures
import structlog
from typing import Any

from tfc.ee_stub import _ee_stub

logger = structlog.get_logger(__name__)

_EVAL_AGENT_TIMEOUT_SECONDS = 120

try:
    from ee.agenthub.explanation_agent.exp_agent import (
        ExplanationAgent,
    )
except ImportError:
    ExplanationAgent = _ee_stub("ExplanationAgent")
from simulate.models import CallExecution, SimulateEvalConfig


def _get_cluster_dict_by_eval(run_test, execution_id):
    """Get evaluation explanation summary for a run test"""
    eval_configs = _get_eval_configs(run_test)
    call_executions = _get_filtered_call_executions(run_test, execution_id)

    eval_data = _collect_eval_data_by_call_execution(eval_configs, call_executions)
    cluster_dict_by_eval = _generate_cluster_dict_by_eval(eval_data)

    return cluster_dict_by_eval


def _generate_cluster_dict_by_eval(eval_reasons_by_config):
    """
    Generate cluster dictionary using ExplanationAgent's new format.

    Returns:
        Dict[str, List]: Dictionary mapping eval name / config to a single list of clusters
    """
    if not eval_reasons_by_config:
        return {}

    explanation_agent = ExplanationAgent()
    cluster_dict_by_eval = {}
    per_config: dict[str, Any] = {}

    for call_exec_id, evals_for_call in eval_reasons_by_config.items():
        for config_name, data in evals_for_call.items():
            eval_template_name = data["eval_template_name"]
            eval_template_criteria = data["eval_template_criteria"]
            eval_config_id = data["eval_config_id"]
            eval_template_id = data["eval_template_id"]

            if config_name not in per_config:
                per_config[config_name] = {
                    "eval_template_name": eval_template_name,
                    "eval_template_criteria": eval_template_criteria,
                    "eval_config_id": eval_config_id,
                    "eval_template_id": eval_template_id,
                    "config_name": config_name,
                    "explanations": [],
                }

            per_config[config_name]["explanations"].append(
                {
                    "id": call_exec_id,
                    "call_execution_id": call_exec_id,
                    "text": data.get("eval_reason", ""),
                    "value": data.get("eval_value"),
                }
            )

    for config_name, config_data in per_config.items():
        explanations = config_data["explanations"]
        eval_name = config_data["eval_template_name"]
        eval_criteria = config_data["eval_template_criteria"]
        eval_values = [e.get("value") for e in explanations]

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    explanation_agent.evaluate,
                    explanation=explanations,
                    eval_name=eval_name,
                    eval_criteria=eval_criteria,
                    eval_values=eval_values,
                )
                result = future.result(timeout=_EVAL_AGENT_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "eval_explanation_agent_timeout",
                config_name=config_name,
                eval_name=eval_name,
                timeout_seconds=_EVAL_AGENT_TIMEOUT_SECONDS,
            )
            continue
        except Exception:
            logger.exception(
                "eval_explanation_agent_error",
                config_name=config_name,
                eval_name=eval_name,
            )
            continue

        if not result:
            continue

        if isinstance(result, list):
            clusters = result
        else:
            clusters = (result.get("failure_clusters", []) or []) + (
                result.get("success_clusters", []) or []
            )

        eval_config_id = config_data["eval_config_id"]
        eval_template_id = config_data["eval_template_id"]

        for cluster in clusters:
            cluster["eval_config_id"] = eval_config_id
            cluster["eval_template_id"] = eval_template_id
            cluster["eval_name"] = eval_name

        cluster_dict_by_eval[config_name] = clusters

    return cluster_dict_by_eval


def _get_eval_configs(run_test):
    """Get all active eval configs for a run test"""
    return SimulateEvalConfig.objects.filter(
        run_test=run_test, deleted=False
    ).select_related("eval_template")


def _get_filtered_call_executions(run_test, execution_id):
    """Get completed call executions with eval outputs"""
    base_query = {
        "test_execution__run_test": run_test,
        "status": "completed",
        "eval_outputs__isnull": False,
    }

    if execution_id is not None:
        base_query["test_execution"] = execution_id

    return CallExecution.objects.filter(**base_query).exclude(eval_outputs={})


def _collect_eval_data_by_call_execution(eval_configs, call_executions):
    """Collect evaluation reasons grouped by call execution id"""
    eval_data_by_call_execution: dict[Any, Any] = {}

    for eval_config in eval_configs:
        # Skip if eval_template is missing
        if not eval_config.eval_template:
            continue

        eval_config_id = str(eval_config.id)
        eval_template = eval_config.eval_template
        eval_template_config = eval_template.config or {}

        eval_template_output = (
            eval_template_config.get("output")
            if isinstance(eval_template_config, dict)
            else None
        )
        eval_template_eval_type_id = (
            eval_template_config.get("eval_type_id")
            if isinstance(eval_template_config, dict)
            else None
        )
        reverse_output = (
            bool(eval_template_config.get("reverse_output", False))
            if isinstance(eval_template_config, dict)
            else False
        )

        # Optional score hints for "score" evals (when available).
        failure_threshold = None
        score_range_hint = None

        if isinstance(eval_template_config, dict) and eval_template_output == "score":
            cfg_block = eval_template_config.get("config")
            if isinstance(cfg_block, dict):
                raw_failure_threshold = cfg_block.get("failure_threshold")
                if isinstance(raw_failure_threshold, (int, float)):
                    failure_threshold = float(raw_failure_threshold)

            if eval_template_eval_type_id == "DeterministicEvaluator":
                # DeterministicEvaluator defaults to 0.0..1.0 scoring when no choices are
                # provided; if choices exist and are numeric, prefer that range.
                raw_choices = getattr(eval_template, "choices", None)
                if isinstance(raw_choices, list) and raw_choices:
                    numeric_choices = []
                    for c in raw_choices:
                        try:
                            numeric_choices.append(float(c))
                        except (TypeError, ValueError):
                            numeric_choices = []
                            break
                    if numeric_choices:
                        score_range_hint = {
                            "min": min(numeric_choices),
                            "max": max(numeric_choices),
                        }
                if score_range_hint is None:
                    score_range_hint = {"min": 0.0, "max": 1.0}

                # If no explicit failure_threshold is provided, default to a mid-point
                # threshold for the inferred score range.
                if failure_threshold is None:
                    try:
                        range_min = float(score_range_hint.get("min"))  # type: ignore[union-attr]
                        range_max = float(score_range_hint.get("max"))  # type: ignore[union-attr]
                    except (TypeError, ValueError, AttributeError):
                        range_min = 0.0
                        range_max = 1.0
                    failure_threshold = (range_min + range_max) / 2.0

        for call_execution in call_executions:
            if not call_execution.eval_outputs:
                continue

            eval_data = call_execution.eval_outputs.get(eval_config_id)
            if eval_data:
                call_exec_id = str(call_execution.id)

                # Initialize the call_execution entry if it doesn't exist
                if call_exec_id not in eval_data_by_call_execution:
                    eval_data_by_call_execution[call_exec_id] = {}

                if eval_data.get("error"):
                    continue

                # Add this eval config's data to the call execution
                eval_data_by_call_execution[call_exec_id][eval_config.name] = {
                    "eval_reason": eval_data.get("reason", ""),
                    "eval_value": eval_data.get("output"),
                    "eval_template_id": str(eval_template.id),
                    "eval_template_name": str(eval_template.name),
                    "eval_template_criteria": str(eval_template.criteria),
                    "eval_config_id": str(eval_config.id),
                    "config_name": eval_config.name,
                    "eval_template_output": eval_template_output,
                    "eval_template_eval_type_id": eval_template_eval_type_id,
                    "eval_template_reverse_output": reverse_output,
                    "eval_template_failure_threshold": failure_threshold,
                    "eval_template_score_range_hint": score_range_hint,
                }

    return eval_data_by_call_execution
