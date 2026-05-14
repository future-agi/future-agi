"""
Eval result formatting.

Pure function that converts raw evaluator output into the final value
based on the template's output_type.

Extracted from EvaluationRunner.format_output() (eval_runner.py:1238).
This is the pure version — no dataset side effects (column/cell creation).
"""

import structlog

logger = structlog.get_logger(__name__)


def format_eval_value(result_data, eval_template):
    """
    Convert raw eval result into the formatted output value.

    Args:
        result_data: Dict with keys: data, failure, reason, runtime, model,
                     metrics, metadata, output (output_type string)
        eval_template: EvalTemplate instance (needs config, choice_scores,
                      multi_choice, choices)

    Returns:
        The formatted value — type depends on output_type:
          - Pass/Fail → "Passed" / "Failed" (str)
          - score → float (0-1)
          - numeric → float
          - reason → str
          - choices → {"score": float, "choice": str} or raw choice data
    """
    output_type = result_data.get("output")

    # If choice_scores exist, force choices processing
    if (
        eval_template
        and eval_template.choice_scores
        and output_type not in ("Pass/Fail",)
    ):
        output_type = "choices"

    value = None

    if output_type == "Pass/Fail":
        data = result_data.get("data")
        eval_type_id = eval_template.config.get("eval_type_id", "")

        # Function evals return data as dict (input kwargs), use failure flag
        if isinstance(data, dict):
            value = "Passed" if not result_data.get("failure") else "Failed"
        elif eval_type_id == "DeterministicEvaluator":
            if not eval_template.multi_choice:
                data = data if data else []
                value = data[0] if data else None
            else:
                value = data
        else:
            value = "Passed" if not result_data.get("failure") else "Failed"

    elif output_type == "score":
        metrics = result_data.get("metrics", [])
        if not metrics:
            value = None
        else:
            # Take first metric's value
            value = metrics[0].get("value") if metrics else None

    elif output_type == "numeric":
        metrics = result_data.get("metrics", [])
        if metrics:
            value = metrics[0].get("value")
        else:
            value = None

    elif output_type == "reason":
        value = result_data.get("reason")

    elif output_type == "choices":
        choice_result = result_data.get("data")

        # Extract choice from nested objects
        if isinstance(choice_result, dict):
            choice_result = (
                choice_result.get("result")
                or choice_result.get("choice")
                or next(iter(choice_result.values()), choice_result)
            )

        # Map choice string to numeric score via choice_scores
        from model_hub.utils.scoring import apply_choice_scores

        if (
            eval_template
            and eval_template.choice_scores
            and isinstance(choice_result, str)
        ):
            mapped = apply_choice_scores(choice_result, eval_template.choice_scores)
            value = {
                "score": mapped if mapped is not None else 0.0,
                "choice": choice_result,
            }
        elif (
            eval_template
            and eval_template.choice_scores
            and isinstance(choice_result, list)
            and choice_result
        ):
            first = str(choice_result[0])
            mapped = apply_choice_scores(first, eval_template.choice_scores)
            value = {
                "score": mapped if mapped is not None else 0.0,
                "choices": choice_result,
            }
        else:
            value = choice_result

    return value


def extract_raw_result(eval_result, eval_template):
    """
    Extract the standard response dict from a raw evaluator result.

    All evaluators return an object with eval_results[0] containing the
    actual data. This normalizes it into the standard dict used everywhere.

    Args:
        eval_result: The raw return value from eval_instance.run()
        eval_template: EvalTemplate for output_type lookup

    Returns:
        Dict with keys: data, failure, reason, runtime, model, metrics,
                       metadata, output
    """
    first = eval_result.eval_results[0]
    return {
        "data": first.get("data"),
        "failure": first.get("failure"),
        "reason": first.get("reason"),
        "runtime": first.get("runtime"),
        "model": first.get("model"),
        "metrics": first.get("metrics"),
        "metadata": first.get("metadata"),
        "output": eval_template.config.get("output", "score"),
        "cost": first.get("cost"),
        "token_usage": first.get("token_usage"),
    }
