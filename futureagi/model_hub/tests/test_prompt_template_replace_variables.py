"""Regression coverage for the shared playground message renderer."""

import pytest

from model_hub.views.prompt_template import replace_variables

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("template_format", [None, "mustache"])
@pytest.mark.parametrize("content_kind", ["string", "text_parts"])
@pytest.mark.parametrize(
    ("template", "expected"),
    [
        pytest.param("Reply to {{ name }}", "Reply to Ada", id="spaced"),
        pytest.param("Reply to {{name}}", "Reply to Ada", id="unspaced-control"),
        pytest.param("Reply to {{\tname  }}", "Reply to Ada", id="tabs-and-spaces"),
        pytest.param(
            "{{ name }} / {{name}} / {{ name}} / {{name }}",
            "Ada / Ada / Ada / Ada",
            id="mixed-occurrences",
        ),
    ],
)
def test_mustache_replaces_known_variables(
    template_format, content_kind, template, expected
):
    content = (
        template if content_kind == "string" else [{"type": "text", "text": template}]
    )

    result = replace_variables(
        [{"role": "user", "content": content}],
        {"name": "Ada"},
        model_name="unused-text-only-model",
        template_format=template_format,
    )

    assert result == [{"role": "user", "content": [{"type": "text", "text": expected}]}]


@pytest.mark.parametrize("variables", [{}, {"name": "Ada"}])
def test_mustache_leaves_unknown_variables_unchanged(variables):
    template = "{{ missing }} / {{missing}} / {{ name | upper }}"

    result = replace_variables(
        [{"role": "user", "content": template}],
        variables,
        model_name="unused-text-only-model",
        template_format="mustache",
    )

    assert result[0]["content"][0]["text"] == template


@pytest.mark.parametrize(
    ("name", "unknown"),
    [
        ("user.name", "userXname"),
        ("a+b", "aaab"),
        ("items[0]", "items0"),
        ("name|other", "other"),
    ],
)
def test_mustache_treats_variable_names_literally(name, unknown):
    template = "{{ " + name + " }} / {{" + name + "}} / {{ " + unknown + " }}"

    result = replace_variables(
        [{"role": "user", "content": template}],
        {name: "Ada"},
        model_name="unused-text-only-model",
        template_format="mustache",
    )

    assert result[0]["content"][0]["text"] == "Ada / Ada / {{ " + unknown + " }}"


@pytest.mark.parametrize("template", ["{{ name }}", "{{name}}"])
@pytest.mark.parametrize("value", [r"\1\g<name>\nC:\tmp", 0, False, None])
def test_mustache_preserves_literal_replacement_values(template, value):
    result = replace_variables(
        [{"role": "user", "content": template}],
        {"name": value},
        model_name="unused-text-only-model",
        template_format="mustache",
    )

    assert result[0]["content"][0]["text"] == str(value)


@pytest.mark.parametrize("template_format", ["jinja", "jinja2"])
def test_jinja_still_renders_spaced_variables_and_filters(template_format):
    result = replace_variables(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Reply to {{ name }} ({{ name | upper }})"}
                ],
            }
        ],
        {"name": "Ada"},
        model_name="unused-text-only-model",
        template_format=template_format,
    )

    assert result == [
        {"role": "user", "content": [{"type": "text", "text": "Reply to Ada (ADA)"}]}
    ]
