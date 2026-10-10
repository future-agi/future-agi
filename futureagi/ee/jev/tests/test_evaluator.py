"""R-07..R-13: typed execution and exactly-once normalization."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
from ee.jev.client import JevGatewayError
from ee.jev.mapping import JevMappingError
from ee.jev.validation import JevResponseError
from evaluations.engine.formatting import format_eval_value

pytestmark = pytest.mark.requires_ee


def evaluator(answer=None, actual="jev-1.13.0", **config):
    client = Mock()
    client.evaluate.return_value = {
        "model": actual,
        "answers": {"q1": answer or {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 412, "output_tokens": 6},
    }
    config.setdefault("model", "jev-latest")
    config.setdefault("rule_prompt", "Judge {{input}}")
    config.setdefault("required_keys", ["input"])
    return JevEvaluator(client=client, **config), client


@pytest.mark.parametrize(
    "probability,threshold,reverse,passed",
    [
        (0.8, 0.5, False, True),
        (0.8, 0.5, True, False),
        (0, 0, False, True),
        (0, 0, True, False),
        (1, 1, False, True),
        (0.999, 1, False, False),
        (0.999, 1, True, True),
    ],
)
def test_r07_threshold_edges_and_reverse_once_through_formatter(
    probability, threshold, reverse, passed
):
    instance, client = evaluator(
        {"type": "noul", "noul": probability},
        pass_threshold=threshold,
        reverse_output=reverse,
    )
    result = instance.run(input="text").eval_results[0]
    assert result["data"]["result"] == ("Pass" if passed else "Fail")
    assert result["failure"] is (not passed)
    assert result["metrics"][0]["value"] == probability
    template = SimpleNamespace(config={"reverse_output": reverse}, choice_scores=None)
    assert format_eval_value({**result, "output": "Pass/Fail"}, template) == (
        "Passed" if passed else "Failed"
    )
    assert client.evaluate.call_count == 1


@pytest.mark.parametrize("reverse,failure", [(False, True), (True, False)])
def test_r08_choice_score_and_failure(reverse, failure):
    instance, _ = evaluator(
        {
            "type": "choice",
            "choice": "Low",
            "probabilities": {"Low": 0.8, "High": 0.2},
            "confidence": 0.7,
        },
        output_type="choices",
        choice_scores={"Low": 0.2, "High": 0.9},
        reverse_output=reverse,
    )
    result = instance._evaluate(input="text")
    template = SimpleNamespace(config={}, choice_scores={"Low": 0.2, "High": 0.9})
    assert format_eval_value({**result, "output": "choices"}, template) == {
        "choice": "Low",
        "score": 0.2,
    }
    assert result["failure"] is failure
    metadata = json.loads(result["metadata"])["jev"]
    assert metadata["confidence"] == 0.7
    assert metadata["verdict"] is None


@pytest.mark.parametrize("reverse,failure", [(False, True), (True, False)])
def test_r09_reference_rubric_normalizes_once(reverse, failure):
    instance, _ = evaluator(
        {
            "type": "score",
            "score": 1.05,
            "legend": {"0": "bad", "1": "ok", "2": "good"},
            "probabilities": {"0": 0.2, "1": 0.55, "2": 0.25},
            "confidence": 0.55,
        },
        output_type="score",
        jev_mapping={"score": {"levels": ["bad", "ok", "good"]}},
        pass_threshold=0.6,
        reverse_output=reverse,
    )
    result = instance._evaluate(input="text")
    assert result["metrics"][0]["value"] == 0.525
    assert result["failure"] is failure
    assert json.loads(result["metadata"])["jev"]["normalized_score"] == 0.525


def test_r06_r13_result_metadata_actual_alias_and_no_reason(caplog):
    instance, _ = evaluator(actual="jev-future", model="jev-latest")
    result = instance._evaluate(input="PRIVATE-CANARY")
    assert result["model"] == "jev-future"
    assert result["reason"] is None
    meta = json.loads(result["metadata"])
    assert "explanation" not in meta
    assert meta["jev"]["reasoning_available"] is False
    assert meta["jev"]["requested_model"] == "jev-latest"
    assert meta["usage"] == {
        "prompt_tokens": 412,
        "completion_tokens": 6,
        "total_tokens": 418,
    }
    assert instance.cost["total_cost"] == 0
    assert "PRIVATE-CANARY" not in caplog.text


def test_r06_pinned_mismatch_is_error():
    instance, _ = evaluator(actual="jev-future", model="jev-1.13.0")
    with pytest.raises(JevResponseError, match="JEV_MODEL_MISMATCH"):
        instance._evaluate(input="text")


def test_r12_provider_error_never_score():
    instance, client = evaluator()
    client.evaluate.side_effect = JevGatewayError()
    with pytest.raises(JevGatewayError):
        instance.run(input="text")


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({}, "JEV_MISSING_VARIABLE"),
        ({"input": "https://example.invalid/photo.png"}, "JEV_INPUT_UNSUPPORTED"),
        (
            {"input": {"attachment": "https://example.invalid/file.pdf?token=fake"}},
            "JEV_INPUT_UNSUPPORTED",
        ),
        ({"input": "data:audio/mp3;base64,ZmFrZQ=="}, "JEV_INPUT_UNSUPPORTED"),
        ({"input": b"raw"}, "JEV_INPUT_UNSUPPORTED"),
        (
            {"input": "x", "input_data_types": {"input": "audio"}},
            "JEV_INPUT_UNSUPPORTED",
        ),
        (
            {"input": "x", "ground_truth_few_shot": "example"},
            "JEV_FEW_SHOT_UNSUPPORTED",
        ),
        (
            {"input": "x", "image_urls": ["https://example.invalid/file"]},
            "JEV_INPUT_UNSUPPORTED",
        ),
    ],
)
def test_r11_reject_before_client(kwargs, code):
    instance, client = evaluator()
    with patch("httpx.get") as fetch, pytest.raises(JevMappingError, match=code):
        instance._evaluate(**kwargs)
    client.evaluate.assert_not_called()
    fetch.assert_not_called()


def test_r11_undeclared_missing_variable_rejected():
    instance, client = evaluator(rule_prompt="Judge {{missing}}", required_keys=[])
    with pytest.raises(JevMappingError, match="JEV_MISSING_VARIABLE"):
        instance._evaluate()
    client.evaluate.assert_not_called()


def test_r10_c6_c8_rendered_messages_system_and_structured_state():
    instance, client = evaluator(
        rule_prompt="{% for item in input %}{{item.text}}{% endfor %}",
        template_format="jinja",
        system_prompt="Judge carefully",
        messages=[{"role": "user", "content": "Read {{input}}"}],
        jev_mapping={"include_messages": True},
    )
    instance._evaluate(input='[{"text":"Hello"}]', organization_id="not-state")
    args = client.evaluate.call_args.args
    assert args[1] == [{"text": "Hello"}]
    instructions = args[2]["q1"]["instructions"]
    assert instructions[0] == "Judge carefully"
    assert "Hello" in instructions
    assert "not-state" not in json.dumps(args)


def test_r11_render_spaced_and_dotted_keys_without_truncation():
    instance, client = evaluator(
        rule_prompt="{{ User Name }} {{row_text.answer}}",
        required_keys=["User Name", "row_text.answer"],
    )
    instance._evaluate(**{"User Name": "Alice", "row_text.answer": "x" * 16000})
    sent = client.evaluate.call_args.args
    assert sent[1]["row_text.answer"] == "x" * 16000
    assert "Alice " + "x" * 16000 in json.dumps(sent[2])


def test_r06_unknown_evaluator_model_never_client():
    client = Mock()
    with pytest.raises(JevMappingError, match="JEV_MODEL_UNKNOWN"):
        JevEvaluator(model="jev-2.0.0", rule_prompt="Judge", client=client)._evaluate()
    client.evaluate.assert_not_called()


def test_r15_template_syntax_error_is_sanitized_before_client(caplog):
    instance, client = evaluator(rule_prompt="{% PRIVATE_CANARY %}")
    with pytest.raises(JevMappingError, match="JEV_MAPPING_INVALID") as error:
        instance._evaluate(input="text")
    assert "PRIVATE_CANARY" not in str(error.value)
    assert "PRIVATE_CANARY" not in caplog.text
    client.evaluate.assert_not_called()
