from django.db import migrations

CODE_EXECUTION_TEMPLATE = {
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
            "data_schema": {
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
                    "exit_code": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}]
                    },
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
                            "runner": {
                                "anyOf": [{"type": "string"}, {"type": "null"}]
                            },
                            "timed_out": {"type": "boolean"},
                            "memory_mb": {
                                "anyOf": [{"type": "integer"}, {"type": "null"}]
                            },
                            "stdout_truncated": {"type": "boolean"},
                            "stderr_truncated": {"type": "boolean"},
                            "output_limit_bytes": {
                                "anyOf": [{"type": "integer"}, {"type": "null"}]
                            },
                        },
                    },
                },
            },
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


def seed_code_execution_template(apps, schema_editor):
    NodeTemplate = apps.get_model("agent_playground", "NodeTemplate")
    defaults = {k: v for k, v in CODE_EXECUTION_TEMPLATE.items() if k != "name"}
    NodeTemplate.objects.update_or_create(
        name=CODE_EXECUTION_TEMPLATE["name"],
        defaults=defaults,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("agent_playground", "0011_alter_prompttemplatenode_prompt_template_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_code_execution_template, migrations.RunPython.noop),
    ]
