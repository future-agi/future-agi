from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import (
    resolve_prompt_template_for_tool,
    resolve_prompt_version,
)


class GetPromptVersionInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidates.",
    )
    version_id: str = Field(
        default="",
        description="Version UUID or version name. Omit to list candidate versions.",
    )


@register_tool
class GetPromptVersionTool(BaseTool):
    name = "get_prompt_version"
    description = (
        "Returns full details of a specific prompt version including "
        "the complete prompt config (all messages, model, temperature, etc.), "
        "evaluation results, labels, and metadata."
    )
    category = "prompts"
    input_model = GetPromptVersionInput

    def execute(
        self, params: GetPromptVersionInput, context: ToolContext
    ) -> ToolResult:
        template, template_result = resolve_prompt_template_for_tool(
            params.template_id,
            context,
            "Prompt Template Required",
        )
        if template_result:
            return template_result

        version, version_result = resolve_prompt_version(
            template,
            params.version_id,
            "Prompt Version Required",
        )
        if version_result:
            return version_result

        labels = list(version.labels.values_list("name", flat=True))

        info = key_value_block(
            [
                ("Template", template.name),
                ("Version", version.template_version),
                ("Version ID", f"`{version.id}`"),
                ("Is Default", "Yes" if version.is_default else "No"),
                ("Is Draft", "Yes" if version.is_draft else "No"),
                ("Labels", ", ".join(labels) if labels else "—"),
                ("Commit Message", version.commit_message or "—"),
                ("Created", format_datetime(version.created_at)),
                ("Updated", format_datetime(version.updated_at)),
            ]
        )

        content = section(
            f"Prompt Version: {template.name} {version.template_version}",
            info,
        )

        # Show full prompt config
        if version.prompt_config_snapshot:
            configs = version.prompt_config_snapshot
            # prompt_config_snapshot is stored as a single dict (not a list)
            if isinstance(configs, dict):
                configs = [configs]
            if isinstance(configs, list):
                for i, cfg in enumerate(configs):
                    messages = cfg.get("messages", [])
                    config_obj = cfg.get("configuration", {})
                    model_name = config_obj.get("model") or cfg.get("model", "—")
                    providers = config_obj.get("model_detail", {}).get(
                        "providers"
                    ) or cfg.get("providers", "—")

                    label = f"Config {i + 1}" if len(configs) > 1 else "Prompt Config"
                    content += f"\n\n### {label}\n\n"

                    config_info = key_value_block(
                        [
                            ("Model", model_name),
                            ("Provider", providers if providers else "—"),
                            ("Temperature", str(config_obj.get("temperature", "—"))),
                            ("Max Tokens", str(config_obj.get("max_tokens", "—"))),
                            ("Top P", str(config_obj.get("top_p", "—"))),
                            (
                                "Frequency Penalty",
                                str(config_obj.get("frequency_penalty", "—")),
                            ),
                            (
                                "Presence Penalty",
                                str(config_obj.get("presence_penalty", "—")),
                            ),
                        ]
                    )
                    content += config_info

                    # Messages
                    if messages:
                        content += "\n\n**Messages:**\n\n"
                        for msg in messages:
                            role = msg.get("role", "unknown")
                            msg_content = msg.get("content", "")
                            if isinstance(msg_content, list):
                                parts = [
                                    p.get("text", "")
                                    for p in msg_content
                                    if p.get("type") == "text"
                                ]
                                msg_content = " ".join(parts)
                            content += f"**{role}:**\n```\n{truncate(str(msg_content), 1000)}\n```\n\n"

        # Variables
        if version.variable_names:
            content += "\n\n### Variables\n\n"
            if isinstance(version.variable_names, dict):
                for var_name, var_value in version.variable_names.items():
                    content += f"- `{var_name}`: {truncate(str(var_value), 100)}\n"
            elif isinstance(version.variable_names, list):
                for var_name in version.variable_names:
                    content += f"- `{var_name}`\n"

        # Evaluation results
        if version.evaluation_results and isinstance(version.evaluation_results, dict):
            content += "\n\n### Evaluation Results\n\n"
            for metric_name, result in version.evaluation_results.items():
                if isinstance(result, dict):
                    score = result.get("score", result.get("average", "—"))
                    content += f"- **{metric_name}**: {score}\n"
                else:
                    content += f"- **{metric_name}**: {result}\n"

        return ToolResult(
            content=content,
            data={
                "id": str(version.id),
                "template_id": str(template.id),
                "version": version.template_version,
                "is_default": version.is_default,
                "is_draft": version.is_draft,
                "labels": labels,
                "prompt_config": version.prompt_config_snapshot,
                "variable_names": version.variable_names,
                "evaluation_results": version.evaluation_results,
            },
        )
