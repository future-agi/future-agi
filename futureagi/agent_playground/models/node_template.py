import uuid

import jsonschema
from django.core.exceptions import ValidationError
from django.db import models

from agent_playground.models.choices import PortMode
from tfc.utils.base_model import BaseModel


class NodeTemplate(BaseModel):
    """
    Registry of available node types with their port definitions and configuration.

    Port Modes:
    - "strict": Only template-defined ports allowed. No customization.
    - "extensible": Template defines required ports. Users can add additional custom ports.
    - "dynamic": Template defines no ports. Users define all ports based on their use case.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=100)
    display_name = models.CharField(max_length=255)
    description = models.TextField(max_length=1000)
    icon = models.URLField(max_length=1000, null=True, blank=True)
    categories = models.JSONField(default=list)

    # Port definitions
    input_definition = models.JSONField(default=list)
    output_definition = models.JSONField(default=list)
    input_mode = models.CharField(
        max_length=20,
        choices=PortMode.choices,
    )
    output_mode = models.CharField(
        max_length=20,
        choices=PortMode.choices,
    )

    config_schema = models.JSONField(
        default=dict, help_text="JSON Schema for Node.config validation"
    )

    class Meta:
        db_table = "agent_playground_node_template"
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(deleted=False),
                name="unique_node_template_name",
            )
        ]
        indexes = [
            models.Index(fields=["categories"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.name})"

    def _validate_field_is_list(self, field_name: str) -> None:
        """Validate the field value is a list."""
        value = getattr(self, field_name)
        if not isinstance(value, list):
            raise ValidationError(f"{field_name} must be a list")

    def _validate_dynamic_mode_empty_definitions(self) -> None:
        """Validate dynamic mode has empty port definitions."""
        if self.input_mode == PortMode.DYNAMIC and self.input_definition:
            raise ValidationError(
                "Dynamic input mode should have empty input_definition"
            )
        if self.output_mode == PortMode.DYNAMIC and self.output_definition:
            raise ValidationError(
                "Dynamic output mode should have empty output_definition"
            )

    def _validate_port_definition_format(self, field_name: str) -> None:
        """Validate each port definition has 'key' and 'data_schema'."""
        port_definitions = getattr(self, field_name)
        for port_spec in port_definitions:
            if "key" not in port_spec or "data_schema" not in port_spec:
                raise ValidationError(
                    f"Each {field_name} entry must have 'key' and 'data_schema'"
                )

    def _validate_config_schema(self) -> None:
        """Validate the config schema is a valid JSON Schema."""
        try:
            jsonschema.Draft7Validator.check_schema(self.config_schema)
        except jsonschema.SchemaError as e:
            raise ValidationError(f"Invalid config_schema: {e.message}")

    def _validate_no_duplicate_definition_keys(self, field_name: str) -> None:
        """Validate no duplicate keys within a port definition list."""
        port_definitions = getattr(self, field_name)
        seen_keys: set[str] = set()
        for port_spec in port_definitions:
            key = port_spec.get("key")
            if key is not None and key in seen_keys:
                raise ValidationError(f"Duplicate key '{key}' in {field_name}")
            seen_keys.add(key)

    def _validate_no_reserved_keys(self, field_name: str) -> None:
        """Validate that template definitions do not use the reserved 'custom' key."""
        port_definitions = getattr(self, field_name)
        for port_spec in port_definitions:
            if port_spec.get("key") == "custom":
                raise ValidationError(
                    f"'custom' is a reserved port key and cannot be used in {field_name}"
                )

    def clean(self):
        """
        Validate the node template configuration.
        """
        super().clean()

        self._validate_field_is_list("categories")
        self._validate_field_is_list("input_definition")
        self._validate_field_is_list("output_definition")
        self._validate_dynamic_mode_empty_definitions()
        self._validate_port_definition_format("input_definition")
        self._validate_port_definition_format("output_definition")
        self._validate_no_duplicate_definition_keys("input_definition")
        self._validate_no_duplicate_definition_keys("output_definition")
        self._validate_no_reserved_keys("input_definition")
        self._validate_no_reserved_keys("output_definition")
        self._validate_config_schema()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
