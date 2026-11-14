"""Template rendering for Jev instructions (rule prompt and included messages)."""

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
        "usage": {"input_tokens": 12, "output_tokens": 1},
    }
    client.gateway_request_id = None
    config.setdefault("model", "jev-latest")
    config.setdefault("rule_prompt", "Judge {{input}}")
    config.setdefault("required_keys", ["input"])
    return JevEvaluator(client=client, **config), client


def sent_instructions(client):
    return client.evaluate.call_args.args[2]["q1"]["instructions"]


@pytest.mark.parametrize(
    ("rule_prompt", "template_format", "value", "expected"),
    [
        ("Judge {{ input | upper }}", "mustache", "text", "Judge TEXT"),
        ("Judge {{ input.upper() }}", "mustache", "text", "Judge TEXT"),
        ("{% if input %}yes{% else %}no{% endif %}", "mustache", "text", "yes"),
        (
            "{% for key, val in input.items() %}{{ key }}={{ val }};{% endfor %}",
            "jinja",
            '{"a": 1, "b": 2}',
            "a=1;b=2;",
        ),
        ("{{ input['_name'] }}", "jinja", '{"_name": "kept"}', "kept"),
    ],
)
def test_supported_template_features_still_render(
    rule_prompt, template_format, value, expected
):
    instance, client = evaluator(
        rule_prompt=rule_prompt, template_format=template_format
    )
    instance._evaluate(input=value)
    instructions = sent_instructions(client)
    rendered = instructions if isinstance(instructions, str) else instructions[0]
    assert rendered == expected


@pytest.mark.parametrize(
    "rule_prompt",
    [
        "Judge {{ input.__class__ }}",
        "Judge {{ input | attr('__class__') }}",
        "Judge {{ input.__class__.__mro__ }}",
        "Judge {{ cycler.__init__.__globals__ }}",
    ],
)
def test_underscore_attribute_in_rule_prompt_is_rejected_before_client(rule_prompt):
    instance, client = evaluator(rule_prompt=rule_prompt)
    with pytest.raises(JevMappingError, match="JEV_MAPPING_INVALID") as error:
        instance._evaluate(input="text")
    assert "class" not in str(error.value)
    client.evaluate.assert_not_called()


def test_underscore_attribute_in_included_message_is_rejected_before_client():
    instance, client = evaluator(
        rule_prompt="Judge {{input}}",
        messages=[{"role": "user", "content": "Read {{ input.__class__ }}"}],
        jev_mapping={"include_messages": True},
    )
    with pytest.raises(JevMappingError, match="JEV_MAPPING_INVALID"):
        instance._evaluate(input="text")
    client.evaluate.assert_not_called()
