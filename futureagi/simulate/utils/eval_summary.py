import numpy as np

from simulate.models import CallExecution, SimulateEvalConfig


# Maps EvalTemplate.output_type_normalized to the runtime output_type string
# the KPI aggregation pipeline keys off. Errored evals carry this so the SQL
# still emits a zero row instead of dropping the metric.
def derive_kpi_output_type(eval_template):
    mapping = {
        "pass_fail": "Pass/Fail",
        "percentage": "score",
        "deterministic": "choices",
    }
    return mapping.get(
        getattr(eval_template, "output_type_normalized", None), "score"
    )


def _get_eval_config_for_agent_version(agent_version):
    return SimulateEvalConfig.objects.filter(
        run_test__agent_definition=agent_version.agent_definition,
        run_test__organization=agent_version.organization,
    ).select_related("eval_template")


def _get_eval_configs_with_template(run_test):
    """Get eval configs with related eval template"""
    return SimulateEvalConfig.objects.filter(run_test=run_test).select_related(
        "eval_template"
    )


def _get_completed_call_executions_for_agent_version(agent_version):
    """Get completed call executions with eval outputs"""
    return CallExecution.objects.filter(
        agent_version=agent_version, status="completed", eval_outputs__isnull=False
    ).exclude(eval_outputs={})


def _get_completed_call_executions(run_test, execution_id):
    """Get completed call executions with eval outputs"""
    base_query = {
        "test_execution__run_test": run_test,
        "status": "completed",
        "eval_outputs__isnull": False,
    }

    if execution_id is not None:
        base_query["test_execution"] = execution_id

    return CallExecution.objects.filter(**base_query).exclude(eval_outputs={})


def _build_template_statistics(eval_configs, call_executions):
    """Build statistics grouped by template"""
    template_stats = {}

    for eval_config in eval_configs:
        template = eval_config.eval_template
        template_id = str(template.id)

        if template_id not in template_stats:
            template_stats[template_id] = {"template": template, "configs": []}

        config_outputs = _extract_config_outputs(eval_config, call_executions)
        config_stats = _calculate_config_stats(eval_config, config_outputs)
        template_stats[template_id]["configs"].append(config_stats)

    return template_stats


def _extract_config_outputs(eval_config, call_executions):
    """Extract outputs for a specific eval config from call executions"""
    config_outputs = []
    eval_config_id = str(eval_config.id)

    for call_execution in call_executions:
        eval_data = call_execution.eval_outputs.get(eval_config_id)
        if eval_data:
            output = eval_data.get("output")
            output_type = eval_data.get("output_type")
            reason = eval_data.get("reason", "")

            if output is not None and output_type is not None:
                config_outputs.append(
                    {
                        "output": output,
                        "output_type": output_type,
                        "reason": reason,
                        "call_execution_id": str(call_execution.id),
                    }
                )

    return config_outputs


def _calculate_final_template_summaries(template_stats):
    """Calculate final template summaries from statistics"""
    final_data = []

    for _template_id, stats_data in template_stats.items():
        template = stats_data["template"]
        template_summary = _calculate_template_summary(template, stats_data["configs"])
        final_data.append(template_summary)

    return final_data


def _calculate_config_stats(eval_config, outputs):
    """Calculate statistics for a single evaluation config"""
    total_calls = len(outputs)
    if total_calls == 0:
        return {
            "name": eval_config.name,
            "id": str(eval_config.id),
            "total_cells": 0,
            "output": {},
        }

    output_type = outputs[0].get("output_type") if outputs else None

    if output_type == "Pass/Fail":
        return _calculate_pass_fail_stats(eval_config, outputs)
    elif output_type == "score":
        return _calculate_score_stats(eval_config, outputs)
    elif output_type == "choices":
        return _calculate_choices_stats(eval_config, outputs)
    else:
        raise ValueError(f"Invalid output type: {output_type}")


def _calculate_pass_fail_stats(eval_config, outputs):
    """Calculate Pass/Fail statistics"""
    total_calls = len(outputs)
    pass_count = 0
    fail_count = 0

    for output in outputs:
        value = output.get("output")
        if isinstance(value, str):
            if value.lower() in ["passed", "Passed"]:
                pass_count += 1
            elif value.lower() in ["failed", "Failed"]:
                fail_count += 1
        elif isinstance(value, bool):
            if value:
                pass_count += 1
            else:
                fail_count += 1

    pass_rate = round((pass_count / total_calls) * 100, 2) if total_calls > 0 else 0
    fail_rate = round((fail_count / total_calls) * 100, 2) if total_calls > 0 else 0

    return {
        "name": eval_config.name,
        "id": str(eval_config.id),
        "total_cells": total_calls,
        "output": {
            "pass": pass_rate,
            "fail": fail_rate,
            "pass_count": pass_count,
            "fail_count": fail_count,
        },
    }


def _calculate_score_stats(eval_config, outputs):
    """Calculate score statistics with percentiles"""
    total_calls = len(outputs)
    valid_scores = []

    for output in outputs:
        value = output.get("output")
        if isinstance(value, int | float) and value is not None:
            score = float(value)
            valid_scores.append(score * 100)  # convert to percentage

    if not valid_scores:
        return {
            "name": eval_config.name,
            "id": str(eval_config.id),
            "total_cells": total_calls,
            "output": {},
        }

    percentiles = _calculate_percentiles(valid_scores)
    avg_score = _calculate_avg_score(valid_scores)

    return {
        "name": eval_config.name,
        "id": str(eval_config.id),
        "total_cells": total_calls,
        "output": percentiles,
        "avg_score": avg_score,
    }


def _calculate_avg_score(valid_scores):
    """Calculate average score"""
    return round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else 0


def _calculate_choices_stats(eval_config, outputs):
    """Calculate choice distribution statistics"""
    total_calls = len(outputs)
    choice_counts = {}

    for output in outputs:
        value = output.get("output")
        if value is not None:
            choice = str(value)
            choice_counts[choice] = choice_counts.get(choice, 0) + 1

    choice_percentages = {}
    for choice, count in choice_counts.items():
        choice_percentages[choice] = (
            round((count / total_calls) * 100, 2) if total_calls > 0 else 0
        )

    return {
        "name": eval_config.name,
        "id": str(eval_config.id),
        "total_cells": total_calls,
        "output": choice_percentages,
    }


def _calculate_percentiles(values):
    """Calculate percentile distribution using numpy"""
    if not values:
        return {}

    return {
        "p5": round(np.percentile(values, 5), 2),
        "p10": round(np.percentile(values, 10), 2),
        "p20": round(np.percentile(values, 20), 2),
        "p30": round(np.percentile(values, 30), 2),
        "p40": round(np.percentile(values, 40), 2),
        "p50": round(np.percentile(values, 50), 2),
        "p60": round(np.percentile(values, 60), 2),
        "p70": round(np.percentile(values, 70), 2),
        "p80": round(np.percentile(values, 80), 2),
        "p90": round(np.percentile(values, 90), 2),
        "p95": round(np.percentile(values, 95), 2),
        "p100": round(np.percentile(values, 100), 2),
    }


def _calculate_template_summary(template, config_stats):
    """Calculate overall template summary across all configs"""
    output_type = template.config.get("output", "unknown")
    total_calls = sum(config.get("total_cells", 0) for config in config_stats)

    result = {
        "name": template.name,
        "id": str(template.id),
        "output_type": output_type,
        "result": config_stats,
    }

    if output_type == "Pass/Fail":
        total_pass_count = sum(
            config.get("output", {}).get("pass_count", 0) for config in config_stats
        )
        total_pass_rate = (
            round((total_pass_count / total_calls) * 100, 2) if total_calls > 0 else 0
        )
        result["total_pass_rate"] = total_pass_rate

    elif output_type == "score":
        total_weighted_sum = 0
        total_cells = 0

        for config in config_stats:
            config_avg = config.get("avg_score", 0)
            config_cells = config.get("total_cells", 0)

            total_weighted_sum += config_avg * config_cells
            total_cells += config_cells

        total_avg = round(total_weighted_sum / total_cells, 2) if total_cells > 0 else 0
        result["total_avg"] = total_avg

    elif output_type == "choices":
        all_choices = set()
        config_output = config_stats[0].get("output", {})
        all_choices.update(config_output.keys())

        total_choices_avg = {}
        for choice in all_choices:
            total_weighted_sum = 0
            total_cells = 0

            for config in config_stats:
                config_cells = config.get("total_cells", 0)
                config_output = config.get("output", {})
                choice_percentage = config_output.get(choice, 0)

                choice_count = (choice_percentage / 100) * config_cells
                total_weighted_sum += choice_count
                total_cells += config_cells

            total_choices_avg[choice] = (
                round((total_weighted_sum / total_cells) * 100, 2)
                if total_cells > 0
                else 0
            )

        result["total_choices_avg"] = total_choices_avg

    return result
