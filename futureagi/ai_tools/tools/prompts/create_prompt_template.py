from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from pydantic import field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class CreatePromptTemplateInput(PydanticBaseModel):
    name: str = Field(
        description="Name for the prompt template",
        min_length=1,
        max_length=2000,
    )
    description: Optional[str] = Field(
        default=None, description="Description of the prompt template"
    )
    prompt_config: Optional[list[dict]] = Field(
        default=None,
        description=(
            "Prompt configuration: list of config objects, each with 'messages' (list of "
            "{role, content}), 'model' (string), and optional 'configuration' "
            "(temperature, max_tokens, etc.). "
            "Example: [{'messages': [{'role': 'system', 'content': 'You are helpful.'}, "
            "{'role': 'user', 'content': 'Hello {{name}}'}], 'model': 'gpt-4o', "
            "'configuration': {'temperature': 0.7}}]"
        ),
    )
    model: str = Field(
        default="gpt-4o",
        description="Model to use for the prompt template (e.g., 'gpt-4o', 'claude-sonnet-4-20250514')",
    )
    variable_values: Optional[dict[str, list[str]]] = Field(
        default=None,
        description=(
            "Predefined values for template variables. Maps variable names to lists of "
            "allowed values. Example: {'name': ['Alice', 'Bob'], 'tone': ['formal', 'casual']}"
        ),
    )
    folder_id: Optional[str] = Field(
        default=None,
        description="UUID of the folder to place this template in",
    )

    @field_validator("variable_values", mode="before")
    @classmethod
    def normalize_variable_values(cls, value):
        """Treat null variable values as an empty allowed-values list."""
        if not isinstance(value, dict):
            return value
        return {key: ([] if val is None else val) for key, val in value.items()}

    @field_validator("prompt_config", mode="before")
    @classmethod
    def normalize_prompt_config(cls, value):
        """Accept a single config object as shorthand for a one-item config list."""
        if isinstance(value, dict):
            return [value]
        return value


@register_tool
class CreatePromptTemplateTool(BaseTool):
    name = "create_prompt_template"
    description = (
        "Creates a new prompt template in the workspace. "
        "Optionally provide a prompt_config with messages and model settings. "
        "A default version (v1) is created automatically."
    )
    category = "prompts"
    input_model = CreatePromptTemplateInput

    def execute(
        self, params: CreatePromptTemplateInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.run_prompt import PromptTemplate, PromptVersion
        from model_hub.services.prompt_service import (
            ServiceError,
            create_prompt_template,
        )

        existing_template = (
            PromptTemplate.objects.filter(
                name=params.name,
                organization=context.organization,
                workspace=context.workspace,
                deleted=False,
            )
            .order_by("-created_at")
            .first()
        )
        if existing_template:
            existing_version = (
                PromptVersion.objects.filter(
                    original_template=existing_template,
                    deleted=False,
                )
                .order_by("-is_default", "-created_at")
                .first()
            )
            content = section(
                "Prompt Template Already Exists",
                key_value_block(
                    [
                        ("ID", f"`{existing_template.id}`"),
                        ("Name", existing_template.name),
                        ("Version ID", f"`{existing_version.id}`" if existing_version else "—"),
                    ]
                ),
            )
            return ToolResult(
                content=content,
                data={
                    "template_id": str(existing_template.id),
                    "name": existing_template.name,
                    "version_id": str(existing_version.id) if existing_version else None,
                    "already_exists": True,
                },
            )

        # Auto-wrap flat message arrays into proper config format.
        # Users often pass [{"role": "system", "content": "..."}] instead of
        # [{"messages": [...], "configuration": {"model": "..."}}].
        prompt_config = params.prompt_config
        if prompt_config and isinstance(prompt_config, list) and len(prompt_config) > 0:
            first = prompt_config[0]
            if isinstance(first, dict) and "role" in first and "messages" not in first:
                model_name = params.model
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
                # Ensure placeholders key exists
                cfg.setdefault("placeholders", [])
                for msg in cfg.get("messages", []):
                    if isinstance(msg.get("content"), str):
                        msg["content"] = [{"text": msg["content"], "type": "text"}]

        result = create_prompt_template(
            name=params.name,
            organization=context.organization,
            workspace=context.workspace,
            user=context.user,
            prompt_config=prompt_config,
            description=params.description or "",
            folder_id=params.folder_id,
            model=params.model,
            variable_values=params.variable_values,
        )

        if isinstance(result, ServiceError):
            return ToolResult.error(result.message, error_code=result.code)

        template = result["template"]
        version = result["version"]
        variable_names = result["variable_names"]
        prompt_config = result["prompt_config"]

        info = key_value_block(
            [
                ("ID", f"`{template.id}`"),
                ("Name", template.name),
                ("Description", template.description or "—"),
                (
                    "Variables",
                    (
                        ", ".join(
                            (
                                f"`{v}` → {result.get('variable_values', {}).get(v, ['—'])}"
                                if result.get("variable_values", {}).get(v)
                                else f"`{v}`"
                            )
                            for v in variable_names
                        )
                        if variable_names
                        else "—"
                    ),
                ),
                ("Version", f"{version.template_version} (draft, default)"),
                ("Version ID", f"`{version.id}`"),
                ("Created", format_datetime(template.created_at)),
            ]
        )

        content = section("Prompt Template Created", info)

        # Show preview of prompt config
        if prompt_config and isinstance(prompt_config, list):
            cfg = prompt_config[0]
            config_obj = cfg.get("configuration", {})
            model_name = config_obj.get("model", cfg.get("model", "—"))
            messages = cfg.get("messages", [])
            content += (
                f"\n\n### Prompt Config\n\n**Model:** {model_name}\n\n**Messages:**\n"
            )
            for msg in messages[:5]:
                role = msg.get("role", "?")
                text = msg.get("content", "")
                if isinstance(text, list):
                    parts = [
                        p.get("text", "")
                        for p in text
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    text = " ".join(parts)
                content += f"- **{role}**: {truncate(str(text), 200)}\n"

        content += "\n\n_Use `commit_prompt_version` to commit the draft, or `create_prompt_version` to add new versions._"

        return ToolResult(
            content=content,
            data={
                "template_id": result["template_id"],
                "name": template.name,
                "version_id": result["version_id"],
                "version": result["version_number"],
                "variable_names": variable_names,
            },
        )
