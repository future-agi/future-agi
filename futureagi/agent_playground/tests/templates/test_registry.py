"""Tests for the template registry."""

import pytest

from agent_playground.templates._registry import (
    _TEMPLATE_REGISTRY,
    TemplateDefinition,
    get_all_templates,
    register_template,
)


@pytest.mark.unit
class TestGetAllTemplates:
    """Tests for get_all_templates."""

    def test_returns_llm_prompt(self):
        templates = get_all_templates()
        assert "llm_prompt" in templates
        assert "evaluation" in templates

    def test_returns_copy(self):
        """Modifying the returned dict does not affect the registry."""
        templates = get_all_templates()
        templates.pop("llm_prompt", None)
        assert "llm_prompt" in get_all_templates()


@pytest.mark.unit
class TestRegisterTemplate:
    """Tests for register_template."""

    def test_duplicate_registration_skips_silently(self):
        """Registering the same name twice is silently ignored."""
        # llm_prompt is already registered by get_all_templates import chain
        templates_before = get_all_templates()
        original = templates_before["llm_prompt"]

        # Re-registering with a different display_name should be a no-op
        register_template(
            TemplateDefinition(
                name="llm_prompt",
                display_name="Duplicate",
                description="dup",
                icon=None,
                categories=[],
                input_definition=[],
                output_definition=[],
                input_mode="dynamic",
                output_mode="dynamic",
                config_schema={},
            )
        )

        # Original entry is unchanged
        assert get_all_templates()["llm_prompt"] == original

    def test_register_new_template(self):
        """Registering a new name succeeds."""
        test_name = "_test_registry_temp"
        try:
            register_template(
                TemplateDefinition(
                    name=test_name,
                    display_name="Temp",
                    description="temp",
                    icon=None,
                    categories=[],
                    input_definition=[],
                    output_definition=[],
                    input_mode="dynamic",
                    output_mode="dynamic",
                    config_schema={},
                )
            )
            assert test_name in _TEMPLATE_REGISTRY
        finally:
            _TEMPLATE_REGISTRY.pop(test_name, None)
