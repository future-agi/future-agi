"""Provider-free text transforms using the normal graph input and output ports.

Registering the runner does not provision templates or expose a catalog option.
"""

from typing import Any

from jsonschema import Draft7Validator

from agent_playground.services.engine.node_runner import BaseNodeRunner, register_runner

TEMPLATE_NAME = "deterministic_text"
CONFIG_SCHEMA = {
    "type": "object",
    "required": ["input_key"],
    "additionalProperties": False,
    "properties": {
        "input_key": {"type": "string", "minLength": 1, "maxLength": 255},
        "transform": {"enum": ["identity", "upper", "lower"]},
        "prefix": {"type": "string", "maxLength": 4096},
        "suffix": {"type": "string", "maxLength": 4096},
    },
}
TEMPLATE_DEFAULTS = {
    "display_name": "Deterministic text",
    "description": "Pure text transform without a model provider.",
    "categories": ["text"],
    "input_mode": "dynamic",
    "output_mode": "dynamic",
    "input_definition": [],
    "output_definition": [],
    "config_schema": CONFIG_SCHEMA,
}
_VALIDATOR = Draft7Validator(CONFIG_SCHEMA)
MAX_INPUT_LENGTH = 65536


def validate_config(config: Any) -> None:
    """Validate without reflecting potentially sensitive configuration values."""
    if not _VALIDATOR.is_valid(config):
        raise ValueError("Invalid deterministic_text config")


class DeterministicTextRunner(BaseNodeRunner):
    """Return prefix + transform(inputs[input_key]) + suffix as response."""

    def run(
        self,
        config: dict[str, Any],
        inputs: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        validate_config(config)
        value = inputs.get(config["input_key"])
        if not isinstance(value, str):
            raise ValueError("deterministic_text requires a string input")
        if len(value) > MAX_INPUT_LENGTH:
            raise ValueError("deterministic_text input exceeds 65536 characters")
        transform = config.get("transform", "identity")
        if transform == "upper":
            value = value.upper()
        elif transform == "lower":
            value = value.lower()
        return {"response": config.get("prefix", "") + value + config.get("suffix", "")}


register_runner(TEMPLATE_NAME, DeterministicTextRunner())
