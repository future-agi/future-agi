import json
from types import SimpleNamespace

from tfc.constants.api_calls import APICallStatusChoices
from tfc.utils.functions import calculate_eval_average


def _log(output):
    return {
        "status": APICallStatusChoices.SUCCESS.value,
        "config": json.dumps({"output": {"output": output}}),
    }


def test_calculate_eval_average_weights_multi_choice_results():
    template = SimpleNamespace(
        config={
            "output": "choices",
            "choices_map": {"Excellent": "pass", "Okay": "neutral", "Poor": "fail"},
        },
        multi_choice=True,
    )

    # [Excellent, Poor] averages to 50%; [Poor] contributes 0%.
    assert calculate_eval_average(template, [_log(["Excellent", "Poor"]), _log(["Poor"])]) == 25


def test_calculate_eval_average_preserves_single_choice_scoring():
    template = SimpleNamespace(
        config={"output": "choices", "choices_map": {"Excellent": "pass", "Poor": "fail"}},
        multi_choice=False,
    )

    assert calculate_eval_average(template, [_log(["Excellent"]), _log(["Poor"])]) == 50
