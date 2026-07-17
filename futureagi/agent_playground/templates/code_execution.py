from agent_playground.templates._registry import TemplateDefinition, register_template

CODE_EXECUTION_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "ok",
        "value",
        "stdout",
        "stderr",
        "exit_code",
        "duration_ms",
        "error",
        "metadata",
    ],
    "properties": {
        "ok": {"type": "boolean"},
        "value": {},
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
        "exit_code": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "duration_ms": {"type": "integer", "minimum": 0},
        "error": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "metadata": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "language",
                "runner",
                "timed_out",
                "memory_mb",
                "stdout_truncated",
                "stderr_truncated",
                "output_limit_bytes",
            ],
            "properties": {
                "language": {
                    "type": "string",
                    "enum": ["python", "javascript", "typescript"],
                },
                "runner": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "timed_out": {"type": "boolean"},
                "memory_mb": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "stdout_truncated": {"type": "boolean"},
                "stderr_truncated": {"type": "boolean"},
                "output_limit_bytes": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                },
            },
        },
    },
}

CODE_EXECUTION_TEMPLATE: TemplateDefinition = {
    "name": "code_execution",
    "display_name": "Code Execution",
    "description": (
        "Run Python, JavaScript, or TypeScript in an isolated Docker sandbox. "
        "The node receives a JSON object as inputs and emits a structured result."
    ),
    "icon": None,
    "categories": ["code", "transform", "execution"],
    "input_definition": [
        {
            "key": "inputs",
            "display_name": "inputs",
            "data_schema": {"type": "object"},
            "required": False,
        }
    ],
    "output_definition": [
        {
            "key": "result",
            "display_name": "result",
            "data_schema": CODE_EXECUTION_RESULT_SCHEMA,
            "required": True,
        }
    ],
    "input_mode": "strict",
    "output_mode": "strict",
    "config_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["language", "code"],
        "properties": {
            "language": {
                "type": "string",
                "enum": ["python", "javascript", "typescript"],
            },
            "code": {"type": "string", "minLength": 1, "maxLength": 20000},
            "timeout_ms": {
                "type": "integer",
                "minimum": 100,
                "maximum": 30000,
                "default": 5000,
            },
            "memory_mb": {
                "type": "integer",
                "minimum": 32,
                "maximum": 512,
                "default": 128,
            },
        },
    },
}

register_template(CODE_EXECUTION_TEMPLATE)
