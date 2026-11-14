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
    client.gateway_request_id = "01JAGATEWAYREQUEST00000000"
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


def test_r06_r13_result_metadata_actual_alias_and_factual_reason(caplog):
    instance, _ = evaluator(actual="jev-future", model="jev-latest")
    result = instance._evaluate(input="PRIVATE-CANARY")
    assert result["model"] == "jev-future"
    # Built from the typed answer alone; TypeSafe returns no rationale.
    assert result["reason"] == "Jev probability 0.80 vs threshold 0.50 → Pass"
    meta = json.loads(result["metadata"])
    assert "explanation" not in meta
    assert "reasoning_available" not in meta["jev"]
    # The call can be found in the gateway's logs.
    assert meta["jev"]["gateway_request_id"] == "01JAGATEWAYREQUEST00000000"
    assert meta["jev"]["requested_model"] == "jev-latest"
    assert meta["usage"] == {
        "prompt_tokens": 412,
        "completion_tokens": 6,
        "total_tokens": 418,
    }
    # Org-key runs: TypeSafe bills the org, so no provider cost is charged.
    assert instance.cost["total_cost"] == 0
    assert "PRIVATE-CANARY" not in caplog.text


def test_r06_pinned_mismatch_is_error():
    instance, client = evaluator(actual="jev-future", model="jev-1.13.0")
    with pytest.raises(JevResponseError, match="JEV_MODEL_MISMATCH"):
        instance._evaluate(input="text")
    client.invalid_response.assert_called_once_with(
        "invalid_answer", "JEV_MODEL_MISMATCH"
    )


def test_an_unusable_answer_is_reported_with_the_gateway_call():
    instance, client = evaluator(answer={"type": "noul", "noul": 1.5})
    with pytest.raises(JevResponseError) as raised:
        instance._evaluate(input="text")
    client.invalid_response.assert_called_once_with("invalid_answer", raised.value.code)


def test_a_usable_answer_reports_nothing():
    instance, client = evaluator()
    instance._evaluate(input="text")
    client.invalid_response.assert_not_called()


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


def test_r17_gateway_run_keeps_both_keys_out_of_results(monkeypatch, settings):
    """End to end on the real client: the org's key reaches only the gateway
    header, and neither it nor the internal key reaches the result."""
    import httpx

    monkeypatch.setenv("AGENTCC_GATEWAY_INTERNAL_URL", "http://agentcc-gateway.test")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-key-CANARY")
    settings.JEV_TRANSPORT = "gateway"
    instance = JevEvaluator(
        model="jev-latest",
        rule_prompt="Judge {{input}}",
        required_keys=["input"],
        api_key="org-key-CANARY",
    )
    ok = {
        "model": "jev-1.13.0",
        "answers": {"q1": {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 412, "output_tokens": 6},
    }
    with patch("httpx.post", return_value=httpx.Response(200, json=ok)) as post:
        result = instance.run(input="text").eval_results[0]
    assert post.call_args.kwargs["json"]["model"] == "typesafe/jev-latest"
    assert post.call_args.kwargs["headers"]["x-agentcc-provider-key"] == (
        "org-key-CANARY"
    )
    assert result["data"]["result"] == "Pass"
    assert json.loads(result["metadata"])["jev"]["actual_model"] == "jev-1.13.0"
    recorded = json.dumps(
        [result, instance.last_jev, instance.client.attempts], default=repr
    )
    assert "CANARY" not in recorded


@pytest.mark.parametrize(
    "answer,config,reason",
    [
        (
            {"type": "noul", "noul": 0.98},
            {},
            "Jev probability 0.98 vs threshold 0.50 → Pass",
        ),
        (
            {"type": "noul", "noul": 0.3},
            {"pass_threshold": 0.25},
            "Jev probability 0.30 vs threshold 0.25 → Pass",
        ),
        (
            {"type": "noul", "noul": 0.98},
            {"reverse_output": True},
            "Jev probability 0.98 vs threshold 0.50 → Fail (reversed)",
        ),
        (
            {
                "type": "choice",
                "choice": "Resolved",
                "probabilities": {"Resolved": 1.0, "Escalated": 0.0},
                "confidence": 0.9,
            },
            {
                "output_type": "choices",
                "choice_scores": {"Resolved": 1, "Escalated": 0},
            },
            'Jev chose "Resolved" (probability 1.00)',
        ),
        (
            {
                "type": "score",
                "score": 3,
                "legend": {"0": "Poor", "1": "Fair", "2": "Good", "3": "Excellent"},
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.1, "3": 0.9},
                "confidence": 0.9,
            },
            {
                "output_type": "score",
                "jev_mapping": {
                    "score": {"levels": ["Poor", "Fair", "Good", "Excellent"]}
                },
            },
            'Jev scored level 3 of 4 "Excellent" (normalized 1.00)',
        ),
        (
            {
                "type": "score",
                "score": 1.05,
                "legend": {"0": "bad", "1": "ok", "2": "good"},
                "probabilities": {"0": 0.2, "1": 0.55, "2": 0.25},
                "confidence": 0.55,
            },
            {
                "output_type": "score",
                "jev_mapping": {"score": {"levels": ["bad", "ok", "good"]}},
            },
            "Jev scored level 1.05 of 3 (normalized 0.53)",
        ),
    ],
)
def test_reason_restates_the_typed_answer(answer, config, reason):
    instance, _ = evaluator(answer, **config)
    result = instance.run(input="text").eval_results[0]
    assert result["reason"] == reason


# One rule for every media type: a value that is itself a media link is
# rejected; a link inside prose is text.
_MEDIA_LINKS = [
    "https://example.invalid/a/photo.png",
    "https://example.invalid/a/photo.jpg",
    "https://example.invalid/a/photo.jpeg",
    "https://example.invalid/a/anim.gif",
    "https://example.invalid/a/photo.webp",
    "https://example.invalid/a/icon.svg",
    "https://example.invalid/a/scan.bmp",
    "https://example.invalid/a/scan.tiff",
    "https://example.invalid/files/invoice.pdf",
    "https://example.invalid/a/call.mp3",
    "https://example.invalid/a/call.wav",
    "https://example.invalid/a/call.ogg",
    "https://example.invalid/a/call.m4a",
    "https://example.invalid/a/call.flac",
    "https://example.invalid/a/clip.mp4",
    "https://example.invalid/a/clip.webm",
]


@pytest.mark.parametrize(
    "value",
    [
        *_MEDIA_LINKS,
        "  https://example.invalid/files/INVOICE.PDF\n",
        "https://example.invalid/a/photo.png?token=fake#page=2",
        "data:image/png;base64,ZmFrZQ==",
        "DATA:application/pdf;base64,ZmFrZQ==",
        {"attachment": "https://example.invalid/a/clip.mp4"},
        ["text", "https://example.invalid/a/call.wav"],
    ],
)
def test_a_value_that_is_a_media_link_is_rejected(value):
    instance, client = evaluator()
    with pytest.raises(JevMappingError, match="JEV_INPUT_UNSUPPORTED"):
        instance._evaluate(input=value)
    client.evaluate.assert_not_called()


@pytest.mark.parametrize("link", _MEDIA_LINKS)
@pytest.mark.parametrize(
    "prose",
    [
        "Customer: my file is at {link} and it is wrong. Agent: fixed it.",
        "Customer: here is the file {link}\nAgent: Thanks, fixed.",
        "Customer: see {link}?x=1 please",
        "Customer: the file is {link}",
    ],
)
def test_a_media_link_inside_prose_is_text(prose, link):
    instance, client = evaluator()
    value = prose.format(link=link)
    instance._evaluate(input={"transcript": value})
    assert client.evaluate.call_args.args[1] == {"transcript": value}
