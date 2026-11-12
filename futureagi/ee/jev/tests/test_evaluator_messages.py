"""W10-JEV-USAGE-PRICING-L04: preserve confirmed System scoring messages."""

from unittest.mock import Mock

import pytest

from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
from ee.jev.mapping import JevMappingError

pytestmark = pytest.mark.requires_ee


def evaluator(**config):
    client = Mock()
    client.evaluate.return_value = {
        "model": "jev-1.13.0",
        "answers": {"q1": {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 412, "output_tokens": 6},
    }
    client.gateway_request_id = None
    config.setdefault("rule_prompt", "Judge {{input}}")
    return JevEvaluator(
        model="jev-latest", required_keys=["input"], client=client, **config
    ), client


@pytest.mark.parametrize("template_format", ["mustache", "jinja"])
@pytest.mark.parametrize(
    "system_prompt,rule_prompt,primary_instructions",
    [
        ("Judge carefully", "Score {{input}}", ["Judge carefully", "Score sample"]),
        (None, "Judge carefully", ["Judge carefully"]),
        (None, "Score {{input}}", ["Score sample", "Judge carefully"]),
        ("Judge carefully", "Judge carefully", ["Judge carefully", "Judge carefully"]),
    ],
    ids=["system-field", "rule-field", "messages-only", "ui-mirrored-system"],
)
def test_w10_confirmed_system_messages_reach_jev(
    template_format, system_prompt, rule_prompt, primary_instructions
):
    instance, client = evaluator(
        system_prompt=system_prompt,
        rule_prompt=rule_prompt,
        template_format=template_format,
        messages=[
            {"role": "system", "content": "Judge carefully"},
            {"role": "system", "content": "Fail if {{input}} has no citation"},
            {"role": "user", "content": "Evaluate {{input}}"},
            {"role": "system", "content": "Require a supporting quotation"},
            # Non-System repetitions keep their existing instruction semantics.
            {"role": "assistant", "content": "Judge carefully"},
            {"role": "system", "content": ""},
        ],
        jev_mapping={"include_messages": True},
    )

    result = instance._evaluate(input="sample")

    assert result["data"]["result"] == "Pass"
    client.evaluate.assert_called_once()
    assert client.evaluate.call_args.args[1] == "sample"
    assert client.evaluate.call_args.args[2]["q1"]["instructions"] == [
        *primary_instructions,
        "Fail if sample has no citation",
        "Evaluate sample",
        "Require a supporting quotation",
        "Judge carefully",
        "The state contains the input variables: input.",
    ]


def test_w10_extra_system_variables_are_validated_before_provider_call():
    instance, client = evaluator(
        system_prompt="Judge carefully",
        messages=[
            {"role": "system", "content": "Judge carefully"},
            {"role": "system", "content": "Check {{missing}} before passing"},
        ],
        jev_mapping={"include_messages": True},
    )

    with pytest.raises(JevMappingError, match="JEV_MISSING_VARIABLE"):
        instance._evaluate(input="sample")

    client.evaluate.assert_not_called()


def test_w10_extra_messages_still_require_confirmation():
    with pytest.raises(JevMappingError, match="JEV_MESSAGES_REQUIRE_CONFIRMATION"):
        evaluator(
            system_prompt="Judge carefully",
            messages=[
                {"role": "system", "content": "Judge carefully"},
                {"role": "system", "content": "Require a citation"},
                {"role": "user", "content": "Evaluate {{input}}"},
            ],
            jev_mapping={"include_messages": False},
        )
