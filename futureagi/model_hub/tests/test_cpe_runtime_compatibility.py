"""Byte-level compatibility derived from the base (4952674379), not the fix.

The seeder loads function/agent/specialty YAMLs; function code is not Jinja.
All seeded prompt fields contain only identifier print tags and literal text.
Base reasoning: no spaced/dotted prepass applies, no blocks/filters execute,
text autoescape is off, and Jinja drops one final newline. Therefore replacing
each identifier with its benign string and dropping that newline is an
independent expected-output oracle. Shape assertions fail if the corpus grows
beyond that proof, rather than silently blessing new template constructs.

Customer-style expectations below are hand-derived from the base prepass:
spaced tags get str(value); dotted paths are nested (numeric segments are list
indices); JSON arrays/objects are decoded only at the top level in Jinja mode.
"""

import copy
import re
from pathlib import Path

import pytest

from model_hub.tests import cpe_runtime_fixtures

make_judge = cpe_runtime_fixtures.make_judge
refuse_network = cpe_runtime_fixtures.refuse_network
render_body = cpe_runtime_fixtures.render_body

SEED_ROOT = Path(__file__).resolve().parents[1] / "system_evals"
SEED_PATHS = [
    path.relative_to(SEED_ROOT).as_posix()
    for track in ("function", "agent", "specialty")
    for path in sorted((SEED_ROOT / track).glob("*.yaml"))
]
SIMPLE_NAME = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}")


@pytest.fixture(scope="module")
def seeded_definitions():
    from model_hub.management.commands.seed_system_evals import load_yaml_evals

    definitions = load_yaml_evals()
    by_source = {item["_source_file"]: item for item in definitions}
    assert len(definitions) == len(by_source) == len(SEED_PATHS) == 174
    assert set(by_source) == set(SEED_PATHS)
    return by_source


def _base_expected(template, values):
    assert "{%" not in template and "{#" not in template
    stripped = SIMPLE_NAME.sub("", template)
    assert "{{" not in stripped and "}}" not in stripped
    # Normalise newlines exactly as Jinja's lexer, then drop one final LF.
    source = template.replace("\r\n", "\n").replace("\r", "\n")
    return SIMPLE_NAME.sub(lambda m: values[m.group(1)], source).removesuffix("\n")


@pytest.mark.parametrize("source", SEED_PATHS)
def test_every_seeded_eval_prompt_is_byte_identical(
    source, seeded_definitions, make_judge, render_body
):
    definition = seeded_definitions[source]
    config = definition.get("config", {})
    if definition["_track"] == "function":
        # The seeder dispatches these 89 definitions to code, never Jinja;
        # in particular is_email's {{2,}} is a Python regex, not a print tag.
        assert config.get("eval_type_id") != "CustomPromptEvaluator"
        assert not config.get("rule_prompt") and not config.get("system_prompt")
        assert not config.get("messages")
        return

    # Matches _yaml_to_template_fields: explicit rule_prompt wins, otherwise
    # criteria becomes the rule. Include criteria itself and every editor turn.
    rule = config.get("rule_prompt", definition.get("criteria", ""))
    if not rule:
        # The legacy hidden deterministic_evals definition is a configurable
        # blank rubric, not one of the 84 concrete seeded judge prompts.
        assert definition["name"] == "deterministic_evals"
        assert definition["visible_ui"] is False
        assert config["config"]["rule_prompt"]["default"] == ""
        judge = make_judge("")
        assert render_body(judge, {}) == ""
        assert judge._render_template("", {}) == ""
        return
    prompts = [definition.get("criteria", ""), rule]
    if config.get("system_prompt"):
        prompts.append(config["system_prompt"])
    prompts.extend(
        msg["content"] for msg in config.get("messages", []) if msg.get("content")
    )
    for template in prompts:
        names = set(SIMPLE_NAME.findall(template))
        values = {name: "value-" + name + " <é&>" for name in names}
        expected = _base_expected(template, values)
        judge = make_judge(
            template,
            system_prompt=template,
            messages=[
                {"role": "user", "content": template},
                {"role": "assistant", "content": template},
            ],
            template_format=config.get("template_format", "mustache"),
        )
        assert render_body(judge, values).encode("utf-8") == expected.encode("utf-8")
        messages = judge.call_llm.call_args.kwargs["prompt"][:-1]
        assert [m["content"].encode("utf-8") for m in messages] == [
            expected.encode("utf-8")
        ] * 3


def test_existing_customer_style_spaced_and_dotted_template(make_judge, render_body):
    # TTS Testing / obj.field are existing test_custom_prompt_render_template
    # fixtures. Base str.replace + nest_dotted_value yields exactly this text.
    template = "Rate {{TTS Testing}} / {{ TTS Testing }} for {{speaker}}: {{obj.field}}"
    values = {"TTS Testing": "hello <world>&", "speaker": "Ana", "obj.field": "good"}
    expected = "Rate hello <world>& / hello <world>& for Ana: good"
    judge = make_judge(
        template,
        system_prompt=template,
        messages=[{"role": "assistant", "content": template}],
    )
    assert render_body(judge, values) == expected
    assert [m["content"] for m in judge.call_llm.call_args.kwargs["prompt"][:-1]] == [
        expected
    ] * 2
    # A reused evaluator must not retain earlier row values or aliases.
    assert (
        render_body(judge, {**values, "TTS Testing": "next"})
        == "Rate next / next for Ana: good"
    )
    assert judge.rule_prompt == template


def test_ordinary_variables_loops_and_filters(make_judge, render_body):
    judge = make_judge(
        "Q: {{question}} | A: {{ answer }} | U: {{ answer | upper }}\n"
        "{% for item in items %}[{{loop.index}}]{{item.name|upper}};{% endfor %}"
        " tags={{tags|join(', ')}} {% if score|int > 3 %}high{% else %}low{% endif %}",
        template_format="jinja",
    )
    assert (
        render_body(
            judge,
            {
                "question": "2+2?",
                "answer": "four",
                "items": '[{"name":"a"},{"name":"b"}]',
                "tags": '["x","y"]',
                "score": "4",
            },
        )
        == "Q: 2+2? | A: four | U: FOUR\n[1]A;[2]B; tags=x, y high"
    )


def test_dotted_numeric_and_auto_roots_keep_base_mapping(make_judge, render_body):
    template = (
        "{{spans.0.kind}}/{{spans.1.kind}} {{trace.note}} "
        "{% for span in spans %}[{{span.kind}}]{% endfor %} "
        "{% if trace.note == 'ok' %}yes{% endif %}"
    )
    judge = make_judge(template, system_prompt=template)
    values = {"spans.0.kind": "A", "spans.1.kind": "B", "trace.note": "ok"}
    assert render_body(judge, values) == "A/B ok [A][B] yes"
    assert (
        judge.call_llm.call_args.kwargs["prompt"][0]["content"] == "A/B ok [A][B] yes"
    )


def test_spaced_and_dotted_json_stay_text(make_judge, render_body):
    judge = make_judge(
        "{{Raw Payload}} / {{meta.tags}} / {% for t in tags %}{{t}}{% endfor %}",
        template_format="jinja",
    )
    assert (
        render_body(
            judge,
            {
                "Raw Payload": '{"a": 1}',
                "meta.tags": '["a", "b"]',
                "tags": '["x", "y"]',
            },
        )
        == '{"a": 1} / ["a", "b"] / xy'
    )


def test_unmapped_and_syntax_fallback_remain_literal(make_judge):
    judge = make_judge("unused")
    assert (
        judge._render_template("Hi {{missing}} {{name}}", {"name": "Ana"})
        == "Hi {{missing}} Ana"
    )
    assert (
        judge._render_template("Hi {{ Missing Name }} {{name}}", {"name": "Ana"})
        == "Hi {{Missing Name}} Ana"
    )
    assert (
        judge._render_template(
            '{% if {{input}} == "yes" %}Y{% endif %}', {"input": "yes"}
        )
        == '{% if yes == "yes" %}Y{% endif %}'
    )


@pytest.mark.parametrize(
    "template,expected",
    [
        (
            "{% raw %}{{User Input}}{% endraw %} / {{User Input}}",
            "{{User Input}} / safe",
        ),
        ("{# {{User Input}} #}{{User Input}}", "safe"),
        ("{{ '{{User Input}}' }} / {{User Input}}", "{{User Input}} / safe"),
        (
            "{% set x = '{{User Input}}' %}{{x}} / {{User Input}}",
            "{{User Input}} / safe",
        ),
        (
            "{{ {'key': '{{User Input}}'}['key'] }} / {{User Input}}",
            "{{User Input}} / safe",
        ),
        (
            "{% raw -%}{{User Input}}{% endraw %} / {{User Input}}",
            "{{User Input}} / safe",
        ),
    ],
)
def test_aliasing_changes_only_real_print_tags(
    make_judge, render_body, template, expected
):
    # Intended correction: base replaced inside raw blocks and quoted strings.
    judge = make_judge(template)
    assert render_body(judge, {"User Input": "safe"}) == expected


@pytest.mark.parametrize(
    "template,values,expected",
    [
        (
            "{{item.name}}|{% for item in items %}{{item.name}};{% endfor %}",
            {"item.name": "outer", "items": [{"name": "a"}, {"name": "b"}]},
            "outer|a;b;",
        ),
        (
            "{{obj.field}}{% set obj = {'field': 'local'} %}|{{obj.field}}",
            {"obj.field": "outer"},
            "outer|local",
        ),
        (
            "{{obj.field}}|{% macro show(obj) %}{{obj.field}}{% endmacro %}"
            "{{show({'field': 'local'})}}",
            {"obj.field": "outer"},
            "outer|local",
        ),
    ],
    ids=["loop", "set", "macro"],
)
def test_dotted_names_keep_lexical_rebinding(
    make_judge, render_body, template, values, expected
):
    # Base nests the mapping, but leaves the expressions for Jinja to resolve.
    judge = make_judge(template, template_format="jinja")
    assert render_body(judge, values) == expected


@pytest.mark.parametrize("site", ["system", "user", "assistant"])
def test_missing_nested_turn_keeps_original_and_calls_judge(
    make_judge, render_body, site
):
    template = "Policy {{name}} / {{missing.field}}"
    options = (
        {"system_prompt": template}
        if site == "system"
        else {"messages": [{"role": site, "content": template}]}
    )
    judge = make_judge("Judge {{answer}}", **options)
    assert render_body(judge, {"answer": "ok", "name": "Ana"}) == "Judge ok"
    judge.call_llm.assert_called_once()
    messages = judge.call_llm.call_args.kwargs["prompt"]
    # Not a partial render ('Policy Ana ...'): base preserved the WHOLE turn.
    assert messages[0 if site == "system" else 1] == {
        "role": site,
        "content": template,
    }


def test_empty_jinja_rule_shapes_json_for_later_turns(make_judge, render_body):
    loop = "{% for n in xs %}{{n}}{% endfor %}"
    judge = make_judge(
        "",
        template_format="jinja",
        system_prompt=loop,
        messages=[{"role": "assistant", "content": loop}],
    )
    assert render_body(judge, {"xs": "[1,2]"}) == ""
    assert [m["content"] for m in judge.call_llm.call_args.kwargs["prompt"][:-1]] == [
        "12",
        "12",
    ]


@pytest.mark.parametrize(
    "template,value,expected",
    [
        ("Rate {{User Input}}", "a\r\nb\r\n", "Rate a\nb"),
        ("Rate {{User Input}}\n", "a\r\nb\r\n", "Rate a\nb\n"),
        ("A\r{{User Input}}", "\nB\r\n", "A\nB"),
        ("A\n\n{{User Input}}", "", "A\n"),
        ("{{User Input}}{%- if true %}!{% endif %}", "hello \n", "hello!"),
        ("{% if true -%}{{User Input}}{% endif %}", " \nhello \n", "hello \n"),
        ("{{User Input}}{#- comment #}!", "hello \n", "hello!"),
        ("{# comment -#}{{User Input}}!", " \nhello", "hello!"),
        ("{{User Input}}{{- marker}}", "hello \n", "hello:"),
        ("{{marker -}}{{User Input}}!", " \nhello", ":hello!"),
        (
            "{{User Input}}|{{marker}}{{User Input}}{%- if true %}!{% endif %}",
            "hello \n",
            "hello \n|:hello!",
        ),
        (
            "{{User Input}}{{Blank Input}}{%- if true %}!{% endif %}",
            "hello \n",
            "hello!",
        ),
    ],
    ids=[
        "crlf",
        "authored-final-lf",
        "split-crlf",
        "empty-value",
        "left-trim",
        "right-trim",
        "comment-left",
        "comment-right",
        "print-left",
        "print-right",
        "different-occurrences",
        "adjacent-whitespace",
    ],
)
@pytest.mark.parametrize("site", ["helper", "rule", "system", "user", "assistant"])
def test_spaced_values_keep_base_text_formatting(
    make_judge, render_body, template, value, expected, site
):
    # These are inert data strings. Base inserted them before newline/trim lexing.
    values = {"User Input": value, "Blank Input": " \t", "marker": ":"}
    options = {}
    rule = "Judge {{marker}}"
    if site == "rule":
        rule = template
    elif site == "system":
        options["system_prompt"] = template
    elif site != "helper":
        options["messages"] = [{"role": site, "content": template}]
    judge = make_judge(rule, **options)
    if site == "helper":
        assert judge._render_template(template, values) == expected
        return
    assert render_body(judge, values) == (expected if site == "rule" else "Judge :")
    if site != "rule":
        messages = judge.call_llm.call_args.kwargs["prompt"]
        assert messages[0 if site == "system" else 1]["content"] == expected


def test_alias_collision_and_nested_input_ownership(make_judge, render_body):
    values = {
        "User Input": "safe",
        "fi_prompt_var_0": "author",
        "obj": {"keep": 1},
        "obj.field": "value",
    }
    original = copy.deepcopy(values)
    judge = make_judge("{{fi_prompt_var_0}} {{User Input}} {{obj.field}} {{obj.keep}}")
    assert render_body(judge, values) == "author safe value 1"
    assert values == original
