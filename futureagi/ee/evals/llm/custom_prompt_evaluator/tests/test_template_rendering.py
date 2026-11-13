"""Template rendering in the EE custom prompt evaluator.

Covers both rendering sites in ``_evaluate`` (the rule prompt and additional
messages) through the real constructor. Only the external client and media
helpers are replaced; the Jinja environment and rendering code are real.
"""

import json
from unittest.mock import Mock

import pytest
from jinja2.sandbox import unsafe

from agentic_eval.core.llm.llm import LLM
from ee.evals.llm.custom_prompt_evaluator import evaluator as ee_module


class SyntheticInput:
    _hidden = "SYNTHETIC-HIDDEN-VALUE"

    def __init__(self):
        self.calls = 0

    def __str__(self):
        return "public input"

    @unsafe
    def read_hidden(self):
        self.calls += 1
        return self._hidden


@pytest.fixture
def make_evaluator(monkeypatch):
    monkeypatch.setattr(LLM, "_init_client", lambda self, api_key: None)
    monkeypatch.setattr(
        ee_module, "detect_and_build_media_blocks", lambda **kw: ([], {})
    )

    def make(rule_prompt="Evaluate {{ value }}", **kwargs):
        evaluator = ee_module.CustomPromptEvaluator(
            rule_prompt=rule_prompt,
            model="gpt-4o-mini",
            provider="openai",
            api_key="synthetic-not-a-real-key",
            **kwargs,
        )
        evaluator.call_llm = Mock(
            return_value=json.dumps({"result": "Pass", "explanation": "synthetic"})
        )
        return evaluator

    return make


def rendered_message(evaluator, site, context):
    result = evaluator._evaluate(required_keys=list(context), **context)
    assert result["data"] == {"result": "Pass"}
    evaluator.call_llm.assert_called_once()
    messages = evaluator.call_llm.call_args.kwargs["prompt"]
    return messages[-1]["content"] if site == "rule" else messages[1]["content"]


def for_site(make_evaluator, site, template, **kwargs):
    if site == "rule":
        return make_evaluator(rule_prompt=template, **kwargs)
    return make_evaluator(
        rule_prompt="Evaluate the input",
        messages=[{"role": "assistant", "content": template}],
        **kwargs,
    )


@pytest.mark.parametrize("site", ["rule", "message"])
@pytest.mark.parametrize(
    ("template", "context", "expected"),
    [
        ("Hello {{ name }}", {"name": "Reader"}, "Hello Reader"),
        ("{{ row.name | upper }}", {"row": {"name": "reader"}}, "READER"),
        ("{% if ok %}yes{% else %}no{% endif %}", {"ok": True}, "yes"),
        ("{% for item in items %}{{ item }};{% endfor %}", {"items": [1, 2]}, "1;2;"),
        ("{{ name.upper() }}", {"name": "reader"}, "READER"),
        ("Hello {{ missing }}", {}, "Hello {{missing}}"),
        ("{{ record['_name'] }}", {"record": {"_name": "public"}}, "public"),
    ],
)
def test_supported_templates(make_evaluator, site, template, context, expected):
    evaluator = for_site(make_evaluator, site, template)
    output = rendered_message(evaluator, site, context)
    if site == "message":
        assert output == expected
    else:
        assert output.startswith(expected + "\n\n")


@pytest.mark.parametrize("site", ["rule", "message"])
def test_json_array_loops_in_jinja_mode(make_evaluator, site):
    evaluator = for_site(
        make_evaluator,
        site,
        "{% for item in items %}{{ item.name }};{% endfor %}",
        template_format="jinja",
    )
    output = rendered_message(
        evaluator, site, {"items": '[{"name":"one"},{"name":"two"}]'}
    )
    assert output.startswith("one;two;")


@pytest.mark.parametrize("site", ["rule", "message"])
@pytest.mark.parametrize(
    ("template", "literal"),
    [
        ("{{ obj._hidden }}", "{{_hidden}}"),
        ("{{ obj | attr('_hidden') }}", "{{_hidden}}"),
        ("{{ obj.__class__ }}", "{{__class__}}"),
    ],
)
def test_underscore_attributes_render_as_undefined_placeholder(
    make_evaluator, site, template, literal
):
    obj = SyntheticInput()
    evaluator = for_site(make_evaluator, site, template)
    output = rendered_message(evaluator, site, {"obj": obj})
    assert output.startswith(literal)
    assert SyntheticInput._hidden not in output
    assert "SyntheticInput" not in output
    assert obj.calls == 0


@pytest.mark.parametrize("site", ["rule", "message"])
@pytest.mark.parametrize(
    "template", ["{{ obj.__class__.__name__ }}", "{{ obj.read_hidden() }}"]
)
def test_unsupported_lookups_reject_rule_prompt_and_keep_message_literal(
    make_evaluator, site, template
):
    obj = SyntheticInput()
    evaluator = for_site(make_evaluator, site, template)
    if site == "rule":
        with pytest.raises(
            ValueError, match="^Error rendering rule prompt template:"
        ) as error:
            evaluator._evaluate(required_keys=["obj"], obj=obj)
        assert SyntheticInput._hidden not in str(error.value)
        evaluator.call_llm.assert_not_called()
    else:
        assert rendered_message(evaluator, site, {"obj": obj}) == template
    assert obj.calls == 0


@pytest.mark.parametrize("site", ["rule", "message"])
def test_invalid_template_raises_for_rule_and_keeps_message_literal(
    make_evaluator, site
):
    template = "{% if value %}unfinished"
    evaluator = for_site(make_evaluator, site, template)
    if site == "rule":
        with pytest.raises(ValueError, match="^Error rendering rule prompt template:"):
            evaluator._evaluate(required_keys=["value"], value="yes")
        evaluator.call_llm.assert_not_called()
    else:
        assert rendered_message(evaluator, site, {"value": "yes"}) == template


def test_rule_prompt_supports_spaced_and_flat_dotted_keys(make_evaluator):
    evaluator = make_evaluator("{{ TTS Testing }} / {{ json_col.field }}")
    output = rendered_message(
        evaluator, "rule", {"TTS Testing": "clear", "json_col.field": "value"}
    )
    assert output.startswith("clear / value\n\n")


def test_evaluator_reuse_does_not_mutate_rule_prompt(make_evaluator):
    evaluator = make_evaluator("Hello {{ name }}")
    assert rendered_message(evaluator, "rule", {"name": "first"}).startswith(
        "Hello first\n\n"
    )
    evaluator.call_llm.reset_mock()
    assert rendered_message(evaluator, "rule", {"name": "second"}).startswith(
        "Hello second\n\n"
    )
    assert evaluator.rule_prompt == "Hello {{ name }}"


def test_runtime_registry_selects_agentic_not_ee_implementation():
    from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
        CustomPromptEvaluator as RuntimeEvaluator,
    )
    from evaluations.engine.registry import get_eval_class

    assert get_eval_class("CustomPromptEvaluator") is RuntimeEvaluator
    assert (
        get_eval_class("CustomPromptEvaluator") is not ee_module.CustomPromptEvaluator
    )
