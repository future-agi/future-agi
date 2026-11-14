"""Tool-loop diagnostics through the catalog, registry and real eval API."""

import json

import pytest

from agentic_eval.core_evals.fi_evals.eval_type import is_function_eval
from evaluations.engine.registry import get_eval_class
from model_hub.management.commands.seed_system_evals import (
    _yaml_to_template_fields,
    load_yaml_evals,
)
from model_hub.models.evals_metric import EvalTemplate


@pytest.mark.django_db
def test_previous_catalog_cache_does_not_skip_new_diagnostic():
    from django.core.cache import cache

    from model_hub.management.commands.seed_system_evals import seed_evals

    EvalTemplate.no_workspace_objects.filter(eval_id=220).delete()
    cache.set("system_evals_version", 32)
    try:
        seed_evals()
        template = EvalTemplate.no_workspace_objects.get(eval_id=220)
        assert template.name == "tool_call_loop_repetition"
        assert template.visible_ui is True
    finally:
        cache.delete("system_evals_version")


def definition():
    return next(
        e for e in load_yaml_evals() if e["name"] == "tool_call_loop_repetition"
    )


def test_diagnostic_template_loads_and_registered_evaluator_scores_gateway_calls():
    template = definition()
    assert template["eval_id"] == 220
    assert template["visible_ui"] is True
    assert template["config"]["required_keys"] == []
    assert is_function_eval(template["config"]["eval_type_id"])
    evaluator = get_eval_class(template["config"]["eval_type_id"])()
    calls = [
        {
            "function": {"name": "poll", "arguments": '{"id":"job-1"}'},
            "result": "waiting",
        }
    ] * 3
    result = evaluator._evaluate(output=json.dumps(calls))
    assert result["metrics"][0]["value"] == pytest.approx(0.2333)
    assert result["failure"] is True
    assert "exact_repeat" in result["reason"]
    assert evaluator.cost["total_cost"] == 0


def test_registered_evaluator_uses_configured_threshold_and_progress():
    evaluator = get_eval_class("ToolCallLoopRepetition")(min_repeats=4)
    calls = [{"name": "poll", "arguments": {}, "result": "waiting"}] * 3
    assert evaluator._evaluate(output=calls)["metrics"][0]["value"] == 1.0
    calls.append(calls[0])
    assert evaluator._evaluate(output=calls)["failure"] is True
    for i, call in enumerate(calls):
        calls[i] = {**call, "result": i}
    assert evaluator._evaluate(output=calls)["failure"] is False


def test_configured_loop_parameters_are_exported_without_aliasing():
    parameters = {
        "window_size": 8,
        "min_repeats": 4,
        "max_cycle_length": 2,
        "near_duplicate_threshold": 0.85,
        "also_compare_results": False,
    }
    evaluator = get_eval_class("ToolCallLoopRepetition")(**parameters)
    exported = evaluator.to_config()
    assert exported == parameters
    exported["min_repeats"] = 99
    assert evaluator.to_config() == parameters


def test_exported_parameters_round_trip_and_allow_runtime_override():
    evaluator_class = get_eval_class("ToolCallLoopRepetition")
    evaluator = evaluator_class(min_repeats=4, max_cycle_length=2)
    assert evaluator.to_config() is not None
    restored = evaluator_class(**evaluator.to_config())
    calls = [{"name": "poll", "arguments": {"id": "job-1"}, "result": "waiting"}] * 3
    assert restored._evaluate(output=calls)["failure"] is False
    assert restored._evaluate(output=calls, min_repeats=3)["failure"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("progress,min_repeats", [(False, 3), (True, 3), (False, 4)])
def test_actual_eval_api_executes_registered_diagnostic(
    auth_client, progress, min_repeats
):
    template = EvalTemplate.objects.create(**_yaml_to_template_fields(definition()))
    calls = [
        {
            "id": f"call-{i}",
            "type": "function",
            "function": {"name": "poll", "arguments": '{"id":"job-1"}'},
            "result": i if progress else "waiting",
        }
        for i in range(3)
    ]
    response = auth_client.post(
        "/model-hub/test-evaluation/",
        {
            "name": "synthetic-loop-diagnostic",
            "template_type": "Function",
            "template_id": str(template.id),
            "eval_type_id": "ToolCallLoopRepetition",
            "model": "",
            "output_type": "Pass/Fail",
            "required_keys": ["output"],
            "input_data_types": {"output": "text"},
            "config": {
                "mapping": {"output": json.dumps(calls)},
                "config": {},
                "params": {"min_repeats": min_repeats},
            },
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    rendered = json.dumps(response.data)
    healthy = progress or min_repeats > len(calls)
    assert ("No repetitive" if healthy else "exact_repeat") in rendered, response.data
    assert "Unknown evaluator" not in rendered
