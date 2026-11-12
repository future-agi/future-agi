"""Jev instructions are tenant-written and render inside the server process.

A plain jinja2 Environment lets a template walk Python attributes
(``''.__class__.__mro__[1].__subclasses__()``) and call methods on the values
it is given, which is code execution on the server. The Jev evaluator renders
in the immutable, fail-closed sandbox the custom prompt evaluator uses: such a
template is refused before anything reaches TypeSafe and nothing in it runs,
ordinary templates render as before, and a missing variable still fails as
StrictUndefined did.
"""

from unittest.mock import Mock

import pytest

from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
from ee.jev.mapping import JevMappingError

pytestmark = pytest.mark.requires_ee

ATTRIBUTE_WALKS = [
    "{{ ''.__class__.__mro__ }}",
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{{ input.__class__.__mro__ }}",
    "{{ '' | attr('__class__') | attr('__mro__') }}",
    "{{ cycler.__init__.__globals__ }}",
]


def evaluator(**config):
    client = Mock()
    client.evaluate.return_value = {
        "model": "jev-1.13.0",
        "answers": {"q1": {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 412, "output_tokens": 6},
    }
    client.gateway_request_id = "template-sandbox"
    config.setdefault("model", "jev-latest")
    config.setdefault("rule_prompt", "Judge {{input}}")
    config.setdefault("required_keys", ["input"])
    return JevEvaluator(client=client, **config), client


class Probe:
    """An input value whose private method records every call."""

    def __init__(self):
        self.calls = []

    def _run(self):
        self.calls.append("_run")
        return "ran"


@pytest.mark.parametrize("payload", ATTRIBUTE_WALKS)
@pytest.mark.parametrize("place", ["rule_prompt", "system_prompt", "message"])
def test_attribute_walk_is_refused_before_the_client(place, payload):
    config = {}
    if place == "message":
        config["messages"] = [{"role": "user", "content": payload}]
        config["jev_mapping"] = {"include_messages": True}
    else:
        config[place] = payload
    instance, client = evaluator(**config)
    with pytest.raises(JevMappingError, match="JEV_MAPPING_INVALID"):
        instance._evaluate(input="text")
    client.evaluate.assert_not_called()


def test_a_private_method_on_an_input_is_not_called():
    probe = Probe()
    instance, client = evaluator(rule_prompt="Judge {{ input._run() }}")
    with pytest.raises(JevMappingError, match="JEV_MAPPING_INVALID"):
        instance._evaluate(input=probe)
    assert probe.calls == []
    client.evaluate.assert_not_called()


def test_a_mutating_method_on_an_input_is_not_called():
    row = {"answer": "yes"}
    instance, client = evaluator(rule_prompt="Judge {{ input.update(answer='no') }}")
    with pytest.raises(JevMappingError, match="JEV_MAPPING_INVALID"):
        instance._evaluate(input=row)
    assert row == {"answer": "yes"}
    client.evaluate.assert_not_called()


def test_ordinary_template_renders_as_before():
    instance, client = evaluator(
        rule_prompt=(
            "Judge {{ name | upper }}:"
            "{% for item in input %} {{ loop.index }}={{ item.text | trim }}{% endfor %}"
            " ({{ input | length }} items;"
            "{% for key, value in meta.items() %} {{ key }}={{ value }}{% endfor %})"
        ),
        required_keys=["name", "input", "meta"],
        template_format="jinja",
    )
    instance._evaluate(
        name="bob",
        input='[{"text": " a "}, {"text": "b"}]',
        meta='{"source": "web"}',
    )
    client.evaluate.assert_called_once()
    instructions = client.evaluate.call_args.args[2]["q1"]["instructions"]
    assert "Judge BOB: 1=a 2=b (2 items; source=web)" in instructions


@pytest.mark.parametrize(
    "rule_prompt,required_keys",
    [
        ("Judge {{ missing }}", []),
        ("Judge {{ input.missing }}", ["input"]),
        ("{% if missing %}yes{% endif %}", []),
    ],
)
def test_missing_variable_still_fails_as_strict_undefined(rule_prompt, required_keys):
    instance, client = evaluator(rule_prompt=rule_prompt, required_keys=required_keys)
    with pytest.raises(JevMappingError, match="JEV_MISSING_VARIABLE"):
        instance._evaluate(input={"answer": "yes"})
    client.evaluate.assert_not_called()
