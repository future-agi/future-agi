from types import SimpleNamespace

from model_hub.services.experiment_config_resolver import resolve_prompt_config
from model_hub.views.run_prompt import render_template


def _experiment_prompt_config(*, snapshot=None, configuration=None):
    prompt_version = (
        SimpleNamespace(prompt_config_snapshot=snapshot)
        if snapshot is not None
        else None
    )
    return SimpleNamespace(
        name="prompt-gpt-4o",
        prompt_version=prompt_version,
        configuration=configuration or {},
        model="gpt-4o",
        model_display_name=None,
        model_config={},
        model_params={},
        output_format="string",
        voice_input_column_id=None,
        messages=[],
        get_messages=lambda: [],
    )


def test_resolve_prompt_config_preserves_saved_template_format():
    config = resolve_prompt_config(
        _experiment_prompt_config(
            snapshot={
                "messages": [],
                "configuration": {"template_format": "jinja"},
            },
            configuration={"template_format": "mustache"},
        )
    )

    assert config["configuration"]["template_format"] == "jinja"


def test_resolve_prompt_config_defaults_missing_template_format_to_mustache():
    config = resolve_prompt_config(_experiment_prompt_config())

    assert config["configuration"]["template_format"] == "mustache"


def test_render_template_defaults_to_mustache():
    rendered = render_template("{{#items}}{{.}}{{/items}}", {"items": ["a", "b"]})

    assert rendered == "ab"


def test_render_template_preserves_explicit_jinja_format():
    rendered = render_template(
        "{% if enabled %}enabled{% endif %}",
        {"enabled": True},
        template_format="jinja2",
    )

    assert rendered == "enabled"
