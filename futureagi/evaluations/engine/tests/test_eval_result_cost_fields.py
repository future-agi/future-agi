from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentic_eval.core_evals.fi_utils.evals_result import (
    EvalResult,
    EvalResultCost,
    EvalResultTokenUsage,
)
from evaluations.engine.formatting import extract_raw_result
from evaluations.engine.runner import EvalRequest, run_eval

RESULT_COST = {
    "total_cost": 0.03,
    "prompt_cost": 0.01,
    "completion_cost": 0.02,
}
RESULT_TOKEN_USAGE = {
    "total_tokens": 30,
    "prompt_tokens": 10,
    "completion_tokens": 20,
}
INSTANCE_COST = {
    "total_cost": 9.0,
    "prompt_cost": 4.0,
    "completion_cost": 5.0,
}
INSTANCE_TOKEN_USAGE = {
    "total_tokens": 900,
    "prompt_tokens": 400,
    "completion_tokens": 500,
}


def _template(output="score"):
    return SimpleNamespace(
        name="unit_test_eval",
        config={"eval_type_id": "UnitTestEval", "output": output},
        choice_scores=None,
        choices=[],
        multi_choice=False,
    )


def _raw_result(*, cost_payload=RESULT_COST, token_usage_payload=RESULT_TOKEN_USAGE):
    payload = {
        "data": {"result": 0.7},
        "failure": False,
        "reason": "ok",
        "runtime": 123,
        "model": "unit-model",
        "metrics": [{"id": "score", "value": 0.7}],
        "metadata": {"source": "raw-result"},
    }
    if cost_payload is not None:
        payload["cost"] = cost_payload
    if token_usage_payload is not None:
        payload["token_usage"] = token_usage_payload
    return SimpleNamespace(eval_results=[payload])


class _FakeEvalInstance:
    cost = INSTANCE_COST
    token_usage = INSTANCE_TOKEN_USAGE

    def __init__(self, raw_result):
        self.raw_result = raw_result

    def run(self, **kwargs):
        return self.raw_result


def _patch_runner(monkeypatch, raw_result):
    import evaluations.engine.runner as runner

    monkeypatch.setattr(runner, "get_eval_class", lambda eval_type_id: object)
    monkeypatch.setattr(
        runner,
        "create_eval_instance",
        lambda **kwargs: (_FakeEvalInstance(raw_result), "criteria"),
    )
    import evaluations.engine.preprocessing as preprocessing

    monkeypatch.setattr(preprocessing, "preprocess_inputs", lambda name, params: params)


def test_eval_result_cost_fields_are_optional_and_shaped():
    assert {"cost", "token_usage"}.issubset(EvalResult.__optional_keys__)
    assert EvalResultCost.__required_keys__ == {
        "total_cost",
        "prompt_cost",
        "completion_cost",
    }
    assert EvalResultCost.__optional_keys__ == {"pricing_source"}
    assert EvalResultTokenUsage.__required_keys__ == {
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
    }
    assert EvalResultTokenUsage.__optional_keys__ == {
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    }


def test_extract_raw_result_preserves_cost_token_usage_from_nested_result():
    raw = SimpleNamespace(eval_results=[[_raw_result().eval_results[0]]])

    extracted = extract_raw_result(raw, _template())

    assert extracted["metadata"] == {"source": "raw-result"}
    assert extracted["cost"] == RESULT_COST
    assert extracted["token_usage"] == RESULT_TOKEN_USAGE


def test_extract_raw_result_empty_result_has_none_cost_token_usage():
    extracted = extract_raw_result(SimpleNamespace(eval_results=[]), _template())

    assert extracted["cost"] is None
    assert extracted["token_usage"] is None


@pytest.mark.parametrize("eval_results", [[None], [[None]]])
def test_extract_raw_result_none_result_shapes_preserve_template_output(eval_results):
    extracted = extract_raw_result(
        SimpleNamespace(eval_results=eval_results),
        _template(output="reason"),
    )

    assert extracted["cost"] is None
    assert extracted["token_usage"] is None
    assert extracted["output"] == "reason"


def test_run_eval_prefers_result_cost_token_usage(monkeypatch):
    _patch_runner(monkeypatch, _raw_result())

    result = run_eval(
        EvalRequest(
            eval_template=_template(),
            inputs={"input": "value"},
            skip_params_preparation=True,
        )
    )

    assert result.value == 0.7
    assert result.cost == RESULT_COST
    assert result.token_usage == RESULT_TOKEN_USAGE


def test_run_eval_empty_response_cost_token_usage_falls_back_to_instance(monkeypatch):
    _patch_runner(
        monkeypatch,
        _raw_result(cost_payload={}, token_usage_payload={}),
    )

    result = run_eval(
        EvalRequest(
            eval_template=_template(),
            inputs={"input": "value"},
            skip_params_preparation=True,
        )
    )

    assert result.cost == INSTANCE_COST
    assert result.token_usage == INSTANCE_TOKEN_USAGE


def test_run_eval_partial_response_cost_token_usage_falls_back_to_instance(monkeypatch):
    _patch_runner(
        monkeypatch,
        _raw_result(
            cost_payload={"total_cost": 0.03, "prompt_cost": 0.01},
            token_usage_payload={"total_tokens": 30, "prompt_tokens": 10},
        ),
    )

    result = run_eval(
        EvalRequest(
            eval_template=_template(),
            inputs={"input": "value"},
            skip_params_preparation=True,
        )
    )

    assert result.cost == INSTANCE_COST
    assert result.token_usage == INSTANCE_TOKEN_USAGE


@pytest.mark.parametrize(
    ("cost_payload", "token_usage_payload"),
    [
        ({"total_cost": 0.03, "prompt_cost": 0.01}, RESULT_TOKEN_USAGE),
        (RESULT_COST, {"total_tokens": 30, "prompt_tokens": 10}),
        (None, RESULT_TOKEN_USAGE),
        (RESULT_COST, None),
    ],
)
def test_run_eval_incomplete_accounting_bundle_falls_back_to_instance(
    monkeypatch, cost_payload, token_usage_payload
):
    _patch_runner(
        monkeypatch,
        _raw_result(
            cost_payload=cost_payload,
            token_usage_payload=token_usage_payload,
        ),
    )

    result = run_eval(
        EvalRequest(
            eval_template=_template(),
            inputs={"input": "value"},
            skip_params_preparation=True,
        )
    )

    assert result.cost == INSTANCE_COST
    assert result.token_usage == INSTANCE_TOKEN_USAGE


def test_run_eval_complete_zero_response_cost_token_usage_is_accepted(monkeypatch):
    zero_cost = {
        "total_cost": 0.0,
        "prompt_cost": 0.0,
        "completion_cost": 0.0,
    }
    zero_token_usage = {
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    _patch_runner(
        monkeypatch,
        _raw_result(cost_payload=zero_cost, token_usage_payload=zero_token_usage),
    )

    result = run_eval(
        EvalRequest(
            eval_template=_template(),
            inputs={"input": "value"},
            skip_params_preparation=True,
        )
    )

    assert result.cost == zero_cost
    assert result.token_usage == zero_token_usage


def test_run_eval_falls_back_to_instance_cost_token_usage(monkeypatch):
    _patch_runner(
        monkeypatch,
        _raw_result(cost_payload=None, token_usage_payload=None),
    )

    result = run_eval(
        EvalRequest(
            eval_template=_template(),
            inputs={"input": "value"},
            skip_params_preparation=True,
        )
    )

    assert result.value == 0.7
    assert result.cost == INSTANCE_COST
    assert result.token_usage == INSTANCE_TOKEN_USAGE


@pytest.mark.parametrize(
    "cost_payload",
    [
        {"total_cost": True, "prompt_cost": 0.01, "completion_cost": 0.02},
        {"total_cost": -0.01, "prompt_cost": 0.01, "completion_cost": 0.02},
        {"total_cost": float("inf"), "prompt_cost": 0.01, "completion_cost": 0.02},
        {"total_cost": float("nan"), "prompt_cost": 0.01, "completion_cost": 0.02},
        {"total_cost": "0.03", "prompt_cost": 0.01, "completion_cost": 0.02},
    ],
)
def test_run_eval_invalid_response_cost_falls_back_to_instance(
    monkeypatch, cost_payload
):
    _patch_runner(
        monkeypatch,
        _raw_result(cost_payload=cost_payload, token_usage_payload=RESULT_TOKEN_USAGE),
    )

    result = run_eval(
        EvalRequest(
            eval_template=_template(),
            inputs={"input": "value"},
            skip_params_preparation=True,
        )
    )

    assert result.cost == INSTANCE_COST
    assert result.token_usage == INSTANCE_TOKEN_USAGE


@pytest.mark.parametrize(
    "token_usage_payload",
    [
        {"total_tokens": True, "prompt_tokens": 10, "completion_tokens": 20},
        {"total_tokens": -1, "prompt_tokens": 10, "completion_tokens": 20},
        {"total_tokens": 30.5, "prompt_tokens": 10, "completion_tokens": 20},
        {"total_tokens": "30", "prompt_tokens": 10, "completion_tokens": 20},
    ],
)
def test_run_eval_invalid_response_token_usage_falls_back_to_instance(
    monkeypatch, token_usage_payload
):
    _patch_runner(
        monkeypatch,
        _raw_result(cost_payload=RESULT_COST, token_usage_payload=token_usage_payload),
    )

    result = run_eval(
        EvalRequest(
            eval_template=_template(),
            inputs={"input": "value"},
            skip_params_preparation=True,
        )
    )

    assert result.cost == INSTANCE_COST
    assert result.token_usage == INSTANCE_TOKEN_USAGE


def test_run_eval_valid_response_cost_token_usage_are_sanitized(monkeypatch):
    cost_payload = {
        "total_cost": 0.03,
        "prompt_cost": 0.01,
        "completion_cost": 0.02,
        "pricing_source": "unit-prices",
        "unknown_cost": 100,
    }
    token_usage_payload = {
        "total_tokens": 30,
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 4,
        "invalid_optional": 5,
        "unknown_tokens": 99,
    }
    _patch_runner(
        monkeypatch,
        _raw_result(
            cost_payload=cost_payload,
            token_usage_payload=token_usage_payload,
        ),
    )

    result = run_eval(
        EvalRequest(
            eval_template=_template(),
            inputs={"input": "value"},
            skip_params_preparation=True,
        )
    )

    assert result.cost == {
        "total_cost": 0.03,
        "prompt_cost": 0.01,
        "completion_cost": 0.02,
        "pricing_source": "unit-prices",
    }
    assert result.token_usage == {
        "total_tokens": 30,
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 4,
    }
