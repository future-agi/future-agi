"""Tests for tfc.utils.functions.calculate_eval_average.

For ``choices``/``reason`` eval templates the average is a weighted
pass-rate: each log's selected choice is mapped through ``choices_map``
(``pass`` -> 1, ``neutral`` -> 0.5, anything else -> 0) and averaged.

Single-choice templates were scored this way, but multi-choice templates
fell through to a branch that counted *every* log as a full success,
forcing the displayed average to 100% no matter what the model actually
selected. These tests pin the weighted behaviour for both shapes.
"""

import json
from types import SimpleNamespace

from tfc.constants.api_calls import APICallStatusChoices
from tfc.utils.functions import calculate_eval_average

SUCCESS = APICallStatusChoices.SUCCESS.value

CHOICES_MAP = {"Excellent": "pass", "Okay": "neutral", "Poor": "fail"}


def _template(*, multi_choice, output_type="choices", choices_map=CHOICES_MAP):
    return SimpleNamespace(
        config={"output": output_type, "choices_map": choices_map},
        multi_choice=multi_choice,
    )


def _log(selected):
    # api_logs entries carry the eval output as a JSON string under "config",
    # with the selected choice(s) at output.output.
    return {
        "status": SUCCESS,
        "config": json.dumps({"output": {"output": selected}}),
    }


def test_single_choice_pass_is_hundred():
    avg = calculate_eval_average(_template(multi_choice=False), [_log(["Excellent"])])
    assert avg == 100.0


def test_single_choice_fail_is_zero():
    avg = calculate_eval_average(_template(multi_choice=False), [_log(["Poor"])])
    assert avg == 0.0


def test_multi_choice_all_fail_is_not_hundred():
    # Regression: this returned 100.0 because multi-choice logs were counted
    # as blanket successes instead of being scored through choices_map.
    avg = calculate_eval_average(_template(multi_choice=True), [_log(["Poor"])])
    assert avg == 0.0


def test_multi_choice_averages_selected_choices():
    # ("Excellent"=1 + "Poor"=0) / 2 == 0.5 -> 50%.
    avg = calculate_eval_average(
        _template(multi_choice=True), [_log(["Excellent", "Poor"])]
    )
    assert avg == 50.0


def test_multi_choice_mixed_logs_average():
    # Log A: pass + neutral -> (1 + 0.5)/2 = 0.75 ; Log B: fail -> 0.0 ;
    # overall (0.75 + 0.0)/2 = 0.375 -> 37.5%.
    logs = [_log(["Excellent", "Okay"]), _log(["Poor"])]
    avg = calculate_eval_average(_template(multi_choice=True), logs)
    assert avg == 37.5
