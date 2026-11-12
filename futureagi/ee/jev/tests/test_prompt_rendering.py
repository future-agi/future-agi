"""W10-JEV-USAGE-PRICING-L03: spaced input names must not reparse input text."""

from unittest.mock import Mock

import pytest
from jinja2 import Environment, StrictUndefined, UndefinedError

from ee.evals.llm.custom_prompt_evaluator.rendering import render_prompt
from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator

pytestmark = pytest.mark.requires_ee


@pytest.mark.parametrize("template_format", ["mustache", "jinja"])
@pytest.mark.parametrize(
    "value",
    [
        "The saved template is {{customer_id}}.",
        "Example expression: {{ 2 + 2 }}.",
        "Example block: {% if customer_id %}yes{% endif %}.",
        "Example comment: {# keep this text #}.",
    ],
)
def test_spaced_input_template_notation_is_literal(template_format, value):
    context = {"Customer Text": value}
    rendered, _ = render_prompt(
        "Judge {{Customer Text}}",
        context,
        template_format,
        Environment(undefined=StrictUndefined),
    )
    assert rendered == "Judge " + value
    assert context == {"Customer Text": value}


def test_repeated_spaced_references_do_not_expand_other_input_values():
    context = {
        "Customer Text": "{{Other Text}}",
        "Other Text": "literal",
        "result.text": "dotted",
        "input": "ordinary",
    }
    rendered, _ = render_prompt(
        "{{Customer Text}} | {{ Customer Text }} | {{\tCustomer Text\n}} | "
        "{{Other Text}} | {{result.text}} | {{input}}",
        context,
        "mustache",
        Environment(undefined=StrictUndefined),
    )
    assert rendered == (
        "{{Other Text}} | {{Other Text}} | {{Other Text}} | literal | dotted | ordinary"
    )
    assert context["Customer Text"] == "{{Other Text}}"
    assert context["result.text"] == "dotted"


@pytest.mark.parametrize("value", ['{ "text": "hello" }', "[ 1, 2 ]", 42, None])
def test_spaced_values_keep_string_formatting_in_jinja_mode(value):
    rendered, _ = render_prompt(
        "{{Customer Text}} / {% for item in items %}{{item.text}}{% endfor %}",
        {"Customer Text": value, "items": '[{"text": "loop"}]'},
        "jinja",
        Environment(undefined=StrictUndefined),
    )
    assert rendered == str(value) + " / loop"


def test_spaced_alias_does_not_shadow_context_or_template_names():
    rendered, _ = render_prompt(
        "{% set _fa_spaced_var_0_ = 'local' %}"
        "{{Customer Text}} | {{_fa_spaced_var_0}} | {{_fa_spaced_var_0_}}",
        {"Customer Text": "literal", "_fa_spaced_var_0": "existing"},
        "mustache",
        Environment(undefined=StrictUndefined),
    )
    assert rendered == "literal | existing | local"


def test_spaced_alias_does_not_satisfy_an_undefined_template_variable():
    with pytest.raises(UndefinedError):
        render_prompt(
            "{{Customer Text}} {{_fa_spaced_var_0}}",
            {"Customer Text": "literal"},
            "mustache",
            Environment(undefined=StrictUndefined),
        )


@pytest.mark.parametrize("variable_name", ["input", "Customer Text"])
@pytest.mark.parametrize("template_format", ["mustache", "jinja"])
def test_jev_sends_input_template_notation_unchanged(variable_name, template_format):
    value = "The saved template is {{customer_id}}."
    client = Mock()
    client.evaluate.return_value = {
        "model": "jev-1.13.0",
        "answers": {"q1": {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 412, "output_tokens": 6},
    }
    client.gateway_request_id = "prompt-rendering-regression"
    instance = JevEvaluator(
        model="jev-latest",
        rule_prompt="Judge {{" + variable_name + "}}",
        required_keys=[variable_name],
        template_format=template_format,
        client=client,
    )

    result = instance._evaluate(**{variable_name: value})

    client.evaluate.assert_called_once()
    sent = client.evaluate.call_args.args
    assert sent[1] == value
    assert "Judge " + value in sent[2]["q1"]["instructions"]
    assert result["failure"] is False
