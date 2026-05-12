from typing import Any

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section, truncate
from ai_tools.registry import register_tool


class CreatePromptVersionInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. If omitted, candidates are returned.",
    )
    prompt_config: list[dict] | dict | None = Field(
        default=None,
        description=(
            "Prompt configuration: list of config objects, each with 'messages' "
            "(list of {role, content}), 'model' (string), and optional 'configuration' "
            "(temperature, max_tokens, etc.)"
        ),
    )
    model: str | list[str] | None = Field(
        default=None,
        description="Model to use for this prompt version (e.g., 'gpt-4o', 'claude-sonnet-4-20250514')",
    )
    commit_message: str | None = Field(
        default=None, description="Commit message for this version"
    )
    set_default: bool = Field(
        default=False, description="Set this version as the default"
    )
    metadata: dict | None = Field(
        default=None, description="Optional metadata to attach to this version"
    )
    variable_values: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Predefined values for template variables. Maps variable names to lists of "
            "allowed values. Example: {'name': ['Alice', 'Bob']}"
        ),
    )


@register_tool
class CreatePromptVersionTool(BaseTool):
    name = "create_prompt_version"
    description = (
        "Creates a new version of a prompt template with updated prompt config. "
        "Auto-generates the next version number (v1, v2, etc.). "
        "Optionally set as default and add a commit message."
    )
    category = "prompts"
    input_model = CreatePromptVersionInput

    def execute(
        self, params: CreatePromptVersionInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.resolvers import resolve_prompt_template
        from ai_tools.tools.prompts._utils import candidate_prompt_templates_result
        from model_hub.services.prompt_service import (
            ServiceError,
            create_prompt_version,
        )

        if not params.template_id:
            return candidate_prompt_templates_result(context)

        # Resolve template by name or UUID
        template_obj, err = resolve_prompt_template(
            params.template_id, context.organization, context.workspace
        )
        if err:
            return candidate_prompt_templates_result(
                context,
                "Prompt Template Not Found",
                f"{err} Use one of these template IDs or exact names.",
            )

        if not params.prompt_config:
            return ToolResult(
                content=section(
                    "Prompt Version Requires Configuration",
                    (
                        "Provide `prompt_config` with at least one message config "
                        "before creating a prompt version."
                    ),
                ),
                data={
                    "requires_prompt_config": True,
                    "template_id": str(template_obj.id),
                },
            )

        model_name = _normalize_model(params.model)

        # Auto-wrap flat message arrays into proper config format.
        prompt_config = params.prompt_config
        if isinstance(prompt_config, dict):
            prompt_config = [prompt_config]
        if prompt_config and isinstance(prompt_config, list) and len(prompt_config) > 0:
            first = prompt_config[0]
            if isinstance(first, dict) and "role" in first and "messages" not in first:
                prompt_config = [
                    {
                        "messages": prompt_config,
                        "placeholders": [],
                        "configuration": {
                            "model": model_name,
                            "temperature": 0.7,
                            "max_tokens": 1000,
                            "top_p": 1,
                            "presence_penalty": 0,
                            "frequency_penalty": 0,
                            "response_format": "text",
                        },
                    }
                ]

        # Normalize message content: convert plain strings to [{text, type}] format
        if prompt_config and isinstance(prompt_config, list):
            for cfg in prompt_config:
                # Ensure model is inside configuration, not at top level
                if "model" in cfg and "configuration" in cfg:
                    cfg["configuration"].setdefault("model", cfg.pop("model"))
                elif "model" in cfg:
                    cfg["configuration"] = {"model": cfg.pop("model")}
                # Set model from params if not present in configuration
                if "configuration" in cfg:
                    cfg["configuration"].setdefault("model", model_name)
                else:
                    cfg["configuration"] = {"model": model_name}
                # Ensure placeholders key exists
                cfg.setdefault("placeholders", [])
                for msg in cfg.get("messages", []):
                    if isinstance(msg.get("content"), str):
                        msg["content"] = [{"text": msg["content"], "type": "text"}]

        result = create_prompt_version(
            template_id=str(template_obj.id),
            organization=context.organization,
            prompt_config=prompt_config,
            commit_message=params.commit_message,
            set_default=params.set_default,
            metadata=params.metadata,
            variable_values=_normalize_variable_values(params.variable_values),
        )

        if isinstance(result, ServiceError):
            error_code = result.code
            if error_code == "NOT_FOUND":
                return ToolResult.not_found("Prompt Template", str(template_obj.id))
            return ToolResult.error(result.message, error_code=error_code)

        template = result["template"]
        version = result["version"]
        variable_names = result["variable_names"]

        info = key_value_block(
            [
                ("Template", template.name),
                ("Version", version.template_version),
                ("Version ID", f"`{version.id}`"),
                ("Is Default", "Yes" if version.is_default else "No"),
                ("Status", "Committed" if not version.is_draft else "Draft"),
                ("Commit Message", version.commit_message or "—"),
                (
                    "Variables",
                    (
                        ", ".join(f"`{v}`" for v in variable_names)
                        if variable_names
                        else "—"
                    ),
                ),
            ]
        )

        content = section("Prompt Version Created", info)

        # Show preview
        if prompt_config and isinstance(prompt_config, list):
            cfg = prompt_config[0]
            config_obj = cfg.get("configuration", {})
            model_name = config_obj.get("model", cfg.get("model", "—"))
            messages = cfg.get("messages", [])
            content += f"\n\n### Config Preview\n\n**Model:** {model_name}\n\n"
            for msg in messages[:3]:
                role = msg.get("role", "?")
                text = msg.get("content", "")
                if isinstance(text, list):
                    parts = [
                        p.get("text", "")
                        for p in text
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    text = " ".join(parts)
                content += f"- **{role}**: {truncate(str(text), 150)}\n"
            if len(messages) > 3:
                content += f"- _...{len(messages) - 3} more messages_\n"

        if version.is_draft:
            content += "\n\n_This version is a draft. Use `commit_prompt_version` to commit it._"

        return ToolResult(
            content=content,
            data={
                "template_id": result["template_id"],
                "version_id": result["version_id"],
                "version": result["version_number"],
                "is_default": result["is_default"],
                "is_draft": result["is_draft"],
                "variable_names": variable_names,
            },
        )


def _normalize_model(model: str | list[str] | None) -> str:
    if isinstance(model, list):
        for item in model:
            if item:
                return str(item)
    if model:
        return str(model)
    import os

    return os.environ.get("FALCON_AI_MODEL") or "gpt-4o"


def _normalize_variable_values(
    values: dict[str, Any] | None,
) -> dict[str, list[str]] | None:
    if not values:
        return values
    normalized: dict[str, list[str]] = {}
    for key, value in values.items():
        if value is None:
            normalized[key] = []
        elif isinstance(value, list):
            normalized[key] = [str(item) for item in value]
        else:
            normalized[key] = [str(value)]
    return normalized
