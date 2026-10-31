import re
import uuid

import structlog
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator
from django.db import models
from django.db.models import Q

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


logger = structlog.get_logger(__name__)

OPENAI_TOOL_TYPE = "function"
OPENAI_TOOL_BODY_KEY = "function"
OPENAI_TOOL_NAME_MAX_LENGTH = 64
OPENAI_TOOL_NAME_DISALLOWED = re.compile(r"[^a-zA-Z0-9_-]+")


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


def openai_function_name(name):
    """Map a stored tool name onto the identifier charset OpenAI accepts."""
    cleaned = OPENAI_TOOL_NAME_DISALLOWED.sub("_", (name or "").strip())
    return cleaned[:OPENAI_TOOL_NAME_MAX_LENGTH].strip("_") or "tool"


def openai_tool_envelope(name, description, config):
    """Wrap a stored tool schema in the OpenAI tools envelope litellm expects."""
    if not isinstance(config, dict):
        logger.warning(
            "openai_tool_envelope_unsupported_config", config_type=type(config).__name__
        )
        return config
    body = config.get(OPENAI_TOOL_BODY_KEY)
    if config.get("type") == OPENAI_TOOL_TYPE and isinstance(body, dict):
        return {
            **config,
            OPENAI_TOOL_BODY_KEY: {
                **body,
                "name": openai_function_name(body.get("name") or name),
                "description": body.get("description") or description or "",
            },
        }
    if "parameters" in config:
        parameters = config.get("parameters")
        if isinstance(parameters, dict):
            if not parameters:
                parameters = {"type": "object", "properties": {}}
            return {
                "type": OPENAI_TOOL_TYPE,
                OPENAI_TOOL_BODY_KEY: {
                    "name": openai_function_name(name),
                    "description": description or "",
                    "parameters": parameters,
                },
            }
        # Malformed parameters schema (e.g. non-dict like string or int)
        return config

    if isinstance(config.get("properties"), dict) or config.get("type") == "object":
        return {
            "type": OPENAI_TOOL_TYPE,
            OPENAI_TOOL_BODY_KEY: {
                "name": openai_function_name(name),
                "description": description or "",
                "parameters": config,
            },
        }

    # Pass malformed schema through as written so it still fails loudly
    return config


def ensure_openai_tool_envelope(tool, name=None, description=None):
    """Wrap any tool representation into the OpenAI function envelope.

    Handles:
    - Model instance of Tools: calls tool.as_openai_tool()
    - Dict already in the function envelope: preserves body, fills name/description if missing
    - Dict with top-level 'parameters': wraps in function envelope
    - Dict with top-level 'properties' or type 'object': wraps in function envelope
    - Dict with nested 'config': wraps via openai_tool_envelope
    - Malformed schemas and non-dicts: passed through as written to fail loudly
    """
    if hasattr(tool, "as_openai_tool") and callable(tool.as_openai_tool):
        return tool.as_openai_tool()
    if not isinstance(tool, dict):
        return tool

    body = tool.get(OPENAI_TOOL_BODY_KEY)
    if tool.get("type") == OPENAI_TOOL_TYPE and isinstance(body, dict):
        return {
            **tool,
            OPENAI_TOOL_BODY_KEY: {
                **body,
                "name": openai_function_name(
                    body.get("name") or name or tool.get("name")
                ),
                "description": body.get("description")
                or description
                or tool.get("description")
                or "",
            },
        }

    tool_name = tool.get("name") or name
    tool_desc = tool.get("description") or description

    if "config" in tool and isinstance(tool["config"], dict):
        return openai_tool_envelope(tool_name, tool_desc, tool["config"])

    if "parameters" in tool or "properties" in tool or tool.get("type") == "object":
        return openai_tool_envelope(tool_name, tool_desc, tool)

    # Malformed schema: pass through as written so it still fails loudly
    return tool


class Tools(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, validators=[MinLengthValidator(1)])
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

    def as_openai_tool(self):
        return openai_tool_envelope(self.name, self.description, self.config)

    def __str__(self):
        return self.name

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace", "name"],
                condition=Q(deleted=False),
                name="unique_active_tool_name_workspace",
            )
        ]
