"""Remaining template edge cases, with expectations derived from base 4952674379.

The legacy prepass consumed each dotted key once and inserted spaced-name values
as literal template data. These benign cases must pass on the legacy RED3 sibling
as well as the repaired renderer; they do not execute providers or derive expected
outputs from the implementation under test.
"""

import copy

import pytest

from model_hub.tests import cpe_runtime_fixtures

make_judge = cpe_runtime_fixtures.make_judge
refuse_network = cpe_runtime_fixtures.refuse_network
render_body = cpe_runtime_fixtures.render_body


@pytest.mark.parametrize(
    "template,values,expected",
    [
        (
            "{{obj.field}}|{{obj.field.child}}|{{obj.field}}",
            {"obj.field": "outer", "obj.field.child": "inner"},
            "{'child': 'inner'}|inner|{'child': 'inner'}",
        ),
        (
            "{% autoescape true %}<b>{{User Input}}</b>{% endautoescape %}",
            {"User Input": "hello & bye"},
            "<b>hello & bye</b>",
        ),
        (
            "{% autoescape true %}<b>{{User Input}}</b>|{{ordinary}}"
            "{% endautoescape %}",
            {"User Input": "hello & bye", "ordinary": "<i>A&B</i>"},
            "<b>hello & bye</b>|&lt;i&gt;A&amp;B&lt;/i&gt;",
        ),
    ],
    ids=["repeated-overlapping-dots", "autoescape-literal", "autoescape-mixed"],
)
@pytest.mark.parametrize("site", ["helper", "rule", "system", "user", "assistant"])
def test_base_literal_and_first_occurrence_semantics(
    make_judge, render_body, template, values, expected, site
):
    # At base the third dotted occurrence has already been popped. It cannot
    # overwrite the child mapping created by the second occurrence.
    # Spaced values are template data, so autoescape affects only ordinary
    # interpolations, not either the spaced value or the surrounding markup.
    original = copy.deepcopy(values)
    options = {}
    rule = template if site == "rule" else "Judge ok"
    if site == "system":
        options["system_prompt"] = template
    elif site in ("user", "assistant"):
        options["messages"] = [{"role": site, "content": template}]
    judge = make_judge(rule, **options)
    if site == "helper":
        # The helper intentionally reshapes its private top-level context.
        actual = judge._render_template(template, dict(values))
    else:
        body = render_body(judge, values)
        assert body == (expected if site == "rule" else "Judge ok")
        judge.call_llm.assert_called_once()
        if site == "rule":
            actual = body
        else:
            messages = judge.call_llm.call_args.kwargs["prompt"]
            message = messages[0 if site == "system" else 1]
            assert message["role"] == site
            actual = message["content"]
    assert actual == expected
    assert values == original
