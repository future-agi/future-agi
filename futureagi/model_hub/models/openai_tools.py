import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator
from django.db import models

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


def validate_config(value):
    # Required structure for the config JSON field
    required_keys = {"parameters"}
    parameter_keys = {"type", "properties", "required"}

    # Check top-level keys
    if not all(key in value for key in required_keys):
        raise ValidationError(
            "Config must contain 'name', 'description', and 'parameters' keys."
        )

    # Validate 'parameters' structure
    parameters = value.get("parameters", {})
    if not isinstance(parameters, dict) or not all(
        key in parameters for key in parameter_keys
    ):
        raise ValidationError(
            "The 'parameters' key must contain 'type', 'properties', and 'required' keys."
        )

    # Check if 'parameters' has the correct types
    if parameters.get("type") != "object" or not isinstance(
        parameters.get("properties", {}), dict
    ):
        raise ValidationError(
            "Invalid 'parameters' structure: 'type' must be 'object', and 'properties' must be a dictionary."
        )
    if not isinstance(parameters.get("required", []), list):
        raise ValidationError("'required' must be a list in 'parameters'.")


class Tools(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=255, unique=True, validators=[MinLengthValidator(1)]
    )
    description = models.TextField(max_length=255, validators=[MinLengthValidator(1)])
    config = models.JSONField()
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="tools_org",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="tools",
        null=True,
        blank=True,
    )
    config_type = models.CharField(
        max_length=50, choices=[("json", "JSON"), ("yaml", "YAML")], default="json"
    )

    def __str__(self):
        return self.name
