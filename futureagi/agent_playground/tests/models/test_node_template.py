"""
Tests for the NodeTemplate model.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from agent_playground.models import NodeTemplate
from agent_playground.models.choices import PortMode


@pytest.mark.unit
class TestNodeTemplateCreation:
    """Tests for NodeTemplate model creation."""

    def test_node_template_creation_success(self, db):
        """Basic creation with valid data."""
        template = NodeTemplate.no_workspace_objects.create(
            name="my_template",
            display_name="My Template",
            description="A test template",
            categories=["testing"],
            input_definition=[{"key": "input1", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "output1", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={"type": "object"},
        )
        assert template.id is not None
        assert template.name == "my_template"


@pytest.mark.unit
class TestNodeTemplateUniqueConstraint:
    """Tests for NodeTemplate unique constraints."""

    def test_node_template_unique_name(self, db, node_template):
        """name field unique constraint."""
        with pytest.raises(IntegrityError):
            NodeTemplate.no_workspace_objects.create(
                name=node_template.name,  # Duplicate
                display_name="Another Template",
                description="Another description",
                categories=["other"],
                input_definition=[],
                output_definition=[],
                input_mode=PortMode.DYNAMIC,
                output_mode=PortMode.DYNAMIC,
                config_schema={"type": "object"},
            )


@pytest.mark.unit
class TestNodeTemplateFieldValidation:
    """Tests for NodeTemplate field validation."""

    def test_node_template_categories_must_be_list(self, db):
        """clean() validates categories is list."""
        template = NodeTemplate(
            name="invalid_categories",
            display_name="Invalid Categories",
            description="Test",
            categories="not_a_list",  # Invalid
            input_definition=[],
            output_definition=[],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={"type": "object"},
        )
        with pytest.raises(ValidationError, match="categories must be a list"):
            template.clean()

    def test_node_template_input_definition_must_be_list(self, db):
        """clean() validates input_definition is list."""
        template = NodeTemplate(
            name="invalid_input",
            display_name="Invalid Input",
            description="Test",
            categories=["test"],
            input_definition="not_a_list",  # Invalid
            output_definition=[],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={"type": "object"},
        )
        with pytest.raises(ValidationError, match="input_definition must be a list"):
            template.clean()

    def test_node_template_output_definition_must_be_list(self, db):
        """clean() validates output_definition is list."""
        template = NodeTemplate(
            name="invalid_output",
            display_name="Invalid Output",
            description="Test",
            categories=["test"],
            input_definition=[],
            output_definition="not_a_list",  # Invalid
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={"type": "object"},
        )
        with pytest.raises(ValidationError, match="output_definition must be a list"):
            template.clean()


@pytest.mark.unit
class TestNodeTemplateDynamicMode:
    """Tests for DYNAMIC mode port validation."""

    def test_node_template_dynamic_input_empty_definition(self, db):
        """DYNAMIC input mode requires empty input_definition."""
        template = NodeTemplate(
            name="dynamic_with_input",
            display_name="Dynamic With Input",
            description="Test",
            categories=["test"],
            input_definition=[{"key": "should_be_empty", "data_schema": {}}],  # Invalid
            output_definition=[],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={"type": "object"},
        )
        with pytest.raises(
            ValidationError,
            match="Dynamic input mode should have empty input_definition",
        ):
            template.clean()

    def test_node_template_dynamic_output_empty_definition(self, db):
        """DYNAMIC output mode requires empty output_definition."""
        template = NodeTemplate(
            name="dynamic_with_output",
            display_name="Dynamic With Output",
            description="Test",
            categories=["test"],
            input_definition=[],
            output_definition=[
                {"key": "should_be_empty", "data_schema": {}}
            ],  # Invalid
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={"type": "object"},
        )
        with pytest.raises(
            ValidationError,
            match="Dynamic output mode should have empty output_definition",
        ):
            template.clean()


@pytest.mark.unit
class TestNodeTemplateStrictExtensibleMode:
    """Tests for STRICT and EXTENSIBLE mode port validation."""

    def test_node_template_strict_mode_with_definitions(self, db):
        """STRICT mode allows definitions."""
        template = NodeTemplate.no_workspace_objects.create(
            name="strict_template",
            display_name="Strict Template",
            description="Test",
            categories=["test"],
            input_definition=[{"key": "input1", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "output1", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={"type": "object"},
        )
        template.clean()  # Should not raise
        assert len(template.input_definition) == 1
        assert len(template.output_definition) == 1

    def test_node_template_extensible_mode_with_definitions(self, db):
        """EXTENSIBLE mode allows definitions."""
        template = NodeTemplate.no_workspace_objects.create(
            name="extensible_template",
            display_name="Extensible Template",
            description="Test",
            categories=["test"],
            input_definition=[{"key": "input1", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "output1", "data_schema": {"type": "string"}}],
            input_mode=PortMode.EXTENSIBLE,
            output_mode=PortMode.EXTENSIBLE,
            config_schema={"type": "object"},
        )
        template.clean()  # Should not raise
        assert template.input_mode == PortMode.EXTENSIBLE


@pytest.mark.unit
class TestNodeTemplatePortDefinitionFormat:
    """Tests for port definition format validation."""

    def test_node_template_port_definition_requires_key(self, db):
        """Each port definition needs 'key' field."""
        template = NodeTemplate(
            name="missing_key",
            display_name="Missing Key",
            description="Test",
            categories=["test"],
            input_definition=[{"data_schema": {"type": "string"}}],  # Missing 'key'
            output_definition=[],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.DYNAMIC,
            config_schema={"type": "object"},
        )
        with pytest.raises(
            ValidationError, match="Each .* entry must have 'key' and 'data_schema'"
        ):
            template.clean()

    def test_node_template_port_definition_requires_data_schema(self, db):
        """Each port definition needs 'data_schema' field."""
        template = NodeTemplate(
            name="missing_schema",
            display_name="Missing Schema",
            description="Test",
            categories=["test"],
            input_definition=[{"key": "input1"}],  # Missing 'data_schema'
            output_definition=[],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.DYNAMIC,
            config_schema={"type": "object"},
        )
        with pytest.raises(
            ValidationError, match="Each .* entry must have 'key' and 'data_schema'"
        ):
            template.clean()


@pytest.mark.unit
class TestNodeTemplateDuplicateAndReservedKeys:
    """Tests for duplicate definition keys and reserved key validation."""

    def test_node_template_duplicate_definition_keys_fails(self, db):
        """input_definition with two entries having the same key should fail."""
        template = NodeTemplate(
            name="dup_keys",
            display_name="Dup Keys",
            description="Test",
            categories=["test"],
            input_definition=[
                {"key": "prompt", "data_schema": {"type": "string"}},
                {"key": "prompt", "data_schema": {"type": "string"}},
            ],
            output_definition=[{"key": "output1", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={"type": "object"},
        )
        with pytest.raises(
            ValidationError, match="Duplicate key 'prompt' in input_definition"
        ):
            template.clean()

    def test_node_template_reserved_custom_key_fails(self, db):
        """input_definition with key='custom' should fail."""
        template = NodeTemplate(
            name="reserved_key",
            display_name="Reserved Key",
            description="Test",
            categories=["test"],
            input_definition=[
                {"key": "custom", "data_schema": {"type": "string"}},
            ],
            output_definition=[{"key": "output1", "data_schema": {"type": "string"}}],
            input_mode=PortMode.EXTENSIBLE,
            output_mode=PortMode.STRICT,
            config_schema={"type": "object"},
        )
        with pytest.raises(
            ValidationError,
            match="'custom' is a reserved port key and cannot be used in input_definition",
        ):
            template.clean()


@pytest.mark.unit
class TestNodeTemplateConfigSchema:
    """Tests for config_schema validation."""

    def test_node_template_valid_config_schema(self, db):
        """Valid JSON Schema passes."""
        template = NodeTemplate.no_workspace_objects.create(
            name="valid_schema",
            display_name="Valid Schema",
            description="Test",
            categories=["test"],
            input_definition=[{"key": "input1", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "output1", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["name"],
            },
        )
        template.clean()  # Should not raise
        assert template.config_schema["type"] == "object"

    def test_node_template_invalid_config_schema(self, db):
        """Invalid JSON Schema raises error."""
        template = NodeTemplate(
            name="invalid_schema",
            display_name="Invalid Schema",
            description="Test",
            categories=["test"],
            input_definition=[],
            output_definition=[],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={
                "type": "invalid_type",  # Invalid type
            },
        )
        with pytest.raises(ValidationError, match="Invalid config_schema"):
            template.clean()
