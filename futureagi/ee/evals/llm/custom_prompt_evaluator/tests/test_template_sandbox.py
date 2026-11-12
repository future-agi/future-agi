"""This evaluator's rule prompt and editor messages are tenant-written.

They render in the immutable, fail-closed prompt sandbox, as in the ordinary
custom prompt evaluator, so a template cannot walk Python attributes or call
methods on its values. Ordinary templates and unmapped placeholders render as
before.
"""

from unittest.mock import patch

import pytest
from jinja2.exceptions import SecurityError

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from ee.evals.llm.custom_prompt_evaluator.evaluator import CustomPromptEvaluator
from ee.evals.llm.custom_prompt_evaluator.rendering import render_prompt

pytestmark = pytest.mark.requires_ee

ATTRIBUTE_WALKS = [
    "{{ ''.__class__.__mro__ }}",
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{{ input.__class__.__mro__ }}",
    "{{ '' | attr('__class__') | attr('__mro__') }}",
    "{{ cycler.__init__.__globals__ }}",
]


def environment():
    """The evaluator's own environment, built without any client setup."""
    with patch.object(LLM, "__init__", lambda self, **kwargs: None), patch.object(
        ModelConfigs, "is_turing", return_value=False
    ):
        return CustomPromptEvaluator(rule_prompt="Judge {{input}}", model="test-model").env


class Probe:
    """A value whose private method records every call."""

    def __init__(self):
        self.calls = []

    def _run(self):
        self.calls.append("_run")
        return "ran"


@pytest.mark.parametrize("payload", ATTRIBUTE_WALKS)
def test_rule_prompt_attribute_walk_is_refused(payload):
    with pytest.raises(SecurityError):
        render_prompt(payload, {"input": "text"}, "mustache", environment())


@pytest.mark.parametrize("payload", ATTRIBUTE_WALKS)
def test_editor_message_attribute_walk_is_refused(payload):
    with pytest.raises(SecurityError):
        environment().from_string(payload).render(input="text")


def test_methods_on_values_are_not_called():
    env = environment()
    probe, row = Probe(), {"answer": "yes"}
    with pytest.raises(SecurityError):
        env.from_string("{{ input._run() }}").render(input=probe)
    with pytest.raises(SecurityError):
        env.from_string("{{ input.update(answer='no') }}").render(input=row)
    assert probe.calls == []
    assert row == {"answer": "yes"}


def test_ordinary_template_and_unmapped_placeholder_render_as_before():
    rendered, _ = render_prompt(
        "Judge {{ name | upper }}:"
        "{% for item in input %} {{ loop.index }}={{ item.text | trim }}{% endfor %}"
        " {{unmapped}}",
        {"name": "bob", "input": '[{"text": " a "}, {"text": "b"}]'},
        "jinja",
        environment(),
    )
    assert rendered == "Judge BOB: 1=a 2=b {{unmapped}}"
