"""Custom prompt templates are tenant-written.

ee's render_prompt renders them in the immutable, fail-closed prompt sandbox
that the custom prompt evaluator builds, so a template cannot walk Python
attributes or call methods on its values. Ordinary templates and unmapped
placeholders render as before.
"""

import pytest
from jinja2.exceptions import SecurityError

from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.template_sandbox import (
    build_prompt_environment,
)
from agentic_eval.core_evals.fi_utils.utils import PreserveUndefined
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
    """The custom prompt evaluator's environment (evaluator.py builds the same)."""
    return build_prompt_environment(PreserveUndefined)


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
