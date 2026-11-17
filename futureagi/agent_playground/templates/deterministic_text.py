from agent_playground.templates._registry import TemplateDefinition, register_template

# Keep seeding independent of engine imports; tests pin parity with the runner.
DETERMINISTIC_TEXT_TEMPLATE: TemplateDefinition = {
    "name": "deterministic_text",
    "display_name": "Deterministic text",
    "description": "Pure text transform without a model provider.",
    "icon": None,
    "categories": ["text"],
    "input_definition": [],
    "output_definition": [],
    "input_mode": "dynamic",
    "output_mode": "dynamic",
    "config_schema": {
        "type": "object",
        "required": ["input_key"],
        "additionalProperties": False,
        "properties": {
            "input_key": {"type": "string", "minLength": 1, "maxLength": 255},
            "transform": {"enum": ["identity", "upper", "lower"]},
            "prefix": {"type": "string", "maxLength": 4096},
            "suffix": {"type": "string", "maxLength": 4096},
        },
    },
}

register_template(DETERMINISTIC_TEXT_TEMPLATE)
