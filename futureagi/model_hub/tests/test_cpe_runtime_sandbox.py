"""RED-first runtime security regressions, with harmless traversal canaries only.

On 4952674379 the mutable sandbox falls back after unsafe traversal and sends
it to the judge; spaced-name values are compiled as Jinja source. No real
shell, file, provider or subprocess is reachable from these canary payloads.
"""

import copy
import os

import pytest
from jinja2.sandbox import ImmutableSandboxedEnvironment

from model_hub.tests import cpe_runtime_fixtures

make_judge = cpe_runtime_fixtures.make_judge
refuse_network = cpe_runtime_fixtures.refuse_network
render_body = cpe_runtime_fixtures.render_body


class RuntimeSandboxCanary:
    calls = []

    @classmethod
    def fire(cls):
        cls.calls.append("fired")
        return "CANARY-FIRED"


@pytest.fixture
def canaries(monkeypatch):
    calls = []

    def canary(*args, **kwargs):
        calls.append("fired")
        return "CANARY-FIRED"

    monkeypatch.setattr(os, "fi_template_canary", canary, raising=False)
    monkeypatch.setattr(RuntimeSandboxCanary, "calls", [])
    yield calls
    assert calls == []
    assert RuntimeSandboxCanary.calls == []


TRAVERSALS = [
    "{{ ''.__class__ }}",
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{% for c in ''.__class__.__mro__[1].__subclasses__() %}"
    "{% if c.__name__ == 'RuntimeSandboxCanary' %}{{ c.fire() }}{% endif %}"
    "{% endfor %}",
    "{{ answer.__class__.__init__.__globals__ }}",
    "{{ cycler.__init__.__globals__.os.fi_template_canary() }}",
    "{{ joiner.__init__.__globals__.os.fi_template_canary() }}",
    "{{ namespace.__init__.__globals__.os.fi_template_canary() }}",
    "{{ lipsum.__globals__.os.fi_template_canary() }}",
    "{{ (cycler | attr('__init__') | attr('__globals__'))['os'].fi_template_canary() }}",
    "{{ '{0.__class__.__mro__}'.format(answer) }}",
    "{% set unused = notes.append('injected') %}{{ notes | join(',') }}",
    "{% set unused = record.update({'injected': true}) %}{{ record }}",
]


@pytest.mark.parametrize("site", ["rule", "system", "user", "assistant"])
@pytest.mark.parametrize("template", TRAVERSALS)
def test_unsafe_templates_fail_before_judge(make_judge, canaries, site, template):
    options = {}
    rule = "Judge {{answer}}"
    if site == "rule":
        rule = template
    elif site == "system":
        options["system_prompt"] = template
    else:
        options["messages"] = [{"role": site, "content": template}]
    judge = make_judge(rule, **options)
    values = {"answer": "hello", "notes": ["a", "b"], "record": {"ok": True}}
    before = copy.deepcopy(values)

    with pytest.raises(ValueError, match="unsafe"):
        judge._evaluate(required_keys=list(values), **values)

    judge.call_llm.assert_not_called()
    assert values == before


def test_environment_is_immutable(make_judge):
    assert isinstance(make_judge("Judge.").env, ImmutableSandboxedEnvironment)


PAYLOADS = [
    "{{ 7 * 7 }}",
    "{% if true %}INJECTED{% endif %}",
    "before{# removed if compiled #}after",
    "{% if",
    "{{ other }}",
    "{{ cycler.__init__.__globals__.os.fi_template_canary() }}",
    # Huge if evaluated, but Jinja's safe_range rejects it immediately on RED.
    "{% for n in range(1000000000) %}BOMB{% endfor %}",
]


@pytest.mark.parametrize("value", PAYLOADS)
@pytest.mark.parametrize("name", ["User Input", "answer", "trace.note", "spans.0.text"])
@pytest.mark.parametrize("spelling", ["{{%s}}", "{{ %s }}"])
def test_values_are_literal_at_every_render_site(
    make_judge, render_body, canaries, value, name, spelling
):
    template = "Data: " + (spelling % name) + " | {{other}}"
    judge = make_judge(
        template,
        system_prompt=template,
        messages=[
            {"role": "user", "content": template},
            {"role": "assistant", "content": template},
        ],
    )
    expected = "Data: " + value + " | ordinary"
    assert render_body(judge, {name: value, "other": "ordinary"}) == expected
    messages = judge.call_llm.call_args.kwargs["prompt"]
    assert [m["content"] for m in messages[:-1]] == [expected] * 3


def test_fallback_spaced_value_is_not_compiled(make_judge, canaries):
    judge = make_judge("unused")
    payload = "{{ 7 * 7 }}{% for i in range(1000000000) %}X{% endfor %}"
    assert (
        judge._render_template("Trace: {{ Trace Text }}", {}, {"Trace Text": payload})
        == "Trace: " + payload
    )


def test_syntax_fallback_does_not_substitute_inside_values(make_judge):
    judge = make_judge("unused")
    assert (
        judge._render_template(
            "{% invalid %}{{answer}} {{other}}",
            {"answer": "{{other}}", "other": "plain"},
        )
        == "{% invalid %}{{other}} plain"
    )


def test_row_context_is_data_not_template_source(make_judge):
    payload = PAYLOADS[-1] + "{{ 7 * 7 }}{# comment #}"
    judge = make_judge("Judge this row.")
    judge._evaluate(required_keys=[], row_context={"note": payload})
    text = judge.call_llm.call_args.kwargs["prompt"][-1]["content"]
    assert payload in text


@pytest.mark.parametrize("site", ["rule", "system", "user", "assistant"])
def test_output_limit_refuses_expanding_templates(make_judge, site):
    template = "{% for n in range(10001) %}{{piece}}{% endfor %}"
    options = {}
    rule = "Judge {{piece}}"
    if site == "rule":
        rule = template
    elif site == "system":
        options["system_prompt"] = template
    else:
        options["messages"] = [{"role": site, "content": template}]
    judge = make_judge(rule, **options)
    with pytest.raises(ValueError, match="rendered prompt exceeds 1000000 characters"):
        judge._evaluate(required_keys=["piece"], piece="x" * 100)
    judge.call_llm.assert_not_called()


def test_output_limit_boundary_and_literal_fallback(make_judge):
    judge = make_judge("unused")
    assert (
        judge._render_template("{{piece}}", {"piece": "x" * 1_000_000})
        == "x" * 1_000_000
    )
    with pytest.raises(ValueError, match="rendered prompt exceeds"):
        judge._render_template("{% invalid %}{{piece}}", {"piece": "x" * 1_000_000})
