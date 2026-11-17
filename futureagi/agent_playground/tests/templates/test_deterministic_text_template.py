"""The provider-free template must use the normal registry and seed path."""

import jsonschema
import pytest
from django.core.management import call_command

from agent_playground.models.node_template import NodeTemplate
from agent_playground.templates import get_all_templates
from agent_playground.templates.llm_prompt import LLM_PROMPT_TEMPLATE

pytestmark = pytest.mark.unit


def test_registry_loads_deterministic_text_and_preserves_llm_prompt():
    templates = get_all_templates()
    assert "deterministic_text" in templates
    assert templates["llm_prompt"] is LLM_PROMPT_TEMPLATE
    assert templates["llm_prompt"]["config_schema"] == {}
    assert templates["llm_prompt"]["output_mode"] == "strict"
    assert templates["llm_prompt"]["output_definition"] == [
        {"key": "response", "data_schema": {}, "schema_source": "prompt_version"}
    ]


def test_definition_matches_runner_defaults():
    template = get_all_templates()["deterministic_text"]
    from agent_playground.services.engine.runners.deterministic_text import (
        CONFIG_SCHEMA,
        TEMPLATE_DEFAULTS,
        TEMPLATE_NAME,
    )

    assert template == {"name": TEMPLATE_NAME, "icon": None, **TEMPLATE_DEFAULTS}
    assert template["input_mode"] == template["output_mode"] == "dynamic"
    assert template["input_definition"] == template["output_definition"] == []
    assert template["config_schema"] == CONFIG_SCHEMA
    jsonschema.Draft7Validator.check_schema(template["config_schema"])


@pytest.mark.parametrize(
    "config,value,expected",
    [
        ({"input_key": "query"}, " Hello ", " Hello "),
        (
            {"input_key": "region", "transform": "upper", "prefix": "native:"},
            "catalog-west",
            "native:CATALOG-WEST",
        ),
        (
            {"input_key": "query", "transform": "lower", "suffix": "]"},
            "HELLO",
            "hello]",
        ),
        (
            {
                "input_key": "q" * 255,
                "transform": "identity",
                "prefix": "[" * 4096,
                "suffix": "]" * 4096,
            },
            "text",
            "[" * 4096 + "text" + "]" * 4096,
        ),
    ],
    ids=["minimal", "upper-native-graph", "lower", "schema-boundaries"],
)
def test_template_config_accepts_runner_inputs(config, value, expected):
    schema = get_all_templates()["deterministic_text"]["config_schema"]
    from agent_playground.services.engine.runners.deterministic_text import (
        DeterministicTextRunner,
        validate_config,
    )

    jsonschema.Draft7Validator(schema).validate(config)
    validate_config(config)
    assert DeterministicTextRunner().run(
        config, {config["input_key"]: value}, {}
    ) == {"response": expected}


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"input_key": ""},
        {"input_key": 7},
        {"input_key": "q" * 256},
        {"input_key": "q", "transform": "title"},
        {"input_key": "q", "url": "unused"},
        {"input_key": "q", "prefix": 7},
        {"input_key": "q", "prefix": "x" * 4097},
        {"input_key": "q", "suffix": "x" * 4097},
    ],
    ids=[
        "not-object",
        "missing-input-key",
        "empty-input-key",
        "non-string-input-key",
        "long-input-key",
        "unknown-transform",
        "extra-property",
        "non-string-prefix",
        "long-prefix",
        "long-suffix",
    ],
)
def test_template_config_rejects_runner_invalid_inputs(config):
    schema = get_all_templates()["deterministic_text"]["config_schema"]
    from agent_playground.services.engine.runners.deterministic_text import (
        validate_config,
    )

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(config)
    with pytest.raises(ValueError, match="^Invalid deterministic_text config$"):
        validate_config(config)


def test_command_fresh_seed_is_valid_and_idempotent(db):
    assert not NodeTemplate.no_workspace_objects.exists()
    call_command("seed_node_templates")
    template = NodeTemplate.no_workspace_objects.get(name="deterministic_text")
    llm = NodeTemplate.no_workspace_objects.get(name="llm_prompt")
    for row in (template, llm):
        row.clean()
        for field, value in get_all_templates()[row.name].items():
            assert getattr(row, field) == value
    original_ids = (template.pk, llm.pk)

    call_command("seed_node_templates")
    for name, original_id in zip(("deterministic_text", "llm_prompt"), original_ids):
        rows = NodeTemplate.no_workspace_objects.filter(name=name)
        assert rows.count() == 1
        assert rows.get().pk == original_id


def test_post_migrate_seeds_deterministic_text_idempotently(db):
    from agent_playground.apps import _seed_after_migrate

    assert not NodeTemplate.no_workspace_objects.exists()
    _seed_after_migrate(sender=None)
    template = NodeTemplate.no_workspace_objects.get(name="deterministic_text")
    template.clean()
    _seed_after_migrate(sender=None)
    rows = NodeTemplate.no_workspace_objects.filter(name="deterministic_text")
    assert rows.count() == 1
    assert rows.get().pk == template.pk
    assert NodeTemplate.no_workspace_objects.filter(name="llm_prompt").count() == 1
