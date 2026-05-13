from agent_playground.templates._registry import TemplateDefinition, register_template

EVALUATION_TEMPLATE: TemplateDefinition = {
    "name": "evaluation",
    "display_name": "Evaluation",
    "description": "Run one or more eval templates against upstream node data.",
    "icon": None,
    "categories": ["eval", "quality", "guardrail"],
    "input_definition": [
        {"key": "input", "data_schema": {}},
        {"key": "reference", "data_schema": {}},
        {"key": "context", "data_schema": {}},
    ],
    "output_definition": [
        {"key": "evaluation_result", "data_schema": {"type": "object"}},
        {"key": "passthrough", "data_schema": {}},
        {"key": "fallback", "data_schema": {}},
    ],
    "input_mode": "strict",
    "output_mode": "strict",
    "config_schema": {
        "type": "object",
        "properties": {
            "evaluators": {"type": "array", "minItems": 1},
            "threshold": {"type": "number", "minimum": 0, "maximum": 1},
            "fail_action": {
                "type": "string",
                "enum": ["continue", "stop", "route_fallback"],
            },
        },
        "required": ["evaluators"],
        "additionalProperties": True,
    },
}

register_template(EVALUATION_TEMPLATE)
