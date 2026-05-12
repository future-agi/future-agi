from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section, truncate
from ai_tools.registry import register_tool
from ai_tools.tools.prompts._utils import (
    resolve_prompt_template_for_tool,
    resolve_prompt_version,
)


class ComparePromptVersionsInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidates.",
    )
    version_id_a: str = Field(
        default="",
        description="First version UUID or version name to compare.",
    )
    version_id_b: str = Field(
        default="",
        description="Second version UUID or version name to compare.",
    )


@register_tool
class ComparePromptVersionsTool(BaseTool):
    name = "compare_prompt_versions"
    description = (
        "Compares two prompt versions side by side. "
        "Shows differences in model, messages, configuration, "
        "and evaluation results between two versions."
    )
    category = "prompts"
    input_model = ComparePromptVersionsInput

    def execute(
        self, params: ComparePromptVersionsInput, context: ToolContext
    ) -> ToolResult:
        template, template_result = resolve_prompt_template_for_tool(
            params.template_id,
            context,
            "Prompt Template Required",
        )
        if template_result:
            return template_result

        ver_a, version_result = resolve_prompt_version(
            template,
            params.version_id_a,
            "First Prompt Version Required",
        )
        if version_result:
            return version_result

        ver_b, version_result = resolve_prompt_version(
            template,
            params.version_id_b,
            "Second Prompt Version Required",
        )
        if version_result:
            return version_result

        def extract_config(ver):
            """Extract key info from a version's prompt config."""
            config = ver.prompt_config_snapshot
            if not config:
                return {
                    "model": "—",
                    "messages": [],
                    "temperature": "—",
                    "max_tokens": "—",
                }
            # prompt_config_snapshot is stored as a single dict (not a list)
            if isinstance(config, dict):
                cfg = config
            elif isinstance(config, list) and config:
                cfg = config[0]
            else:
                return {
                    "model": "—",
                    "messages": [],
                    "temperature": "—",
                    "max_tokens": "—",
                }
            conf_obj = cfg.get("configuration", {})
            return {
                "model": conf_obj.get("model") or cfg.get("model", "—"),
                "messages": cfg.get("messages", []),
                "temperature": conf_obj.get("temperature", "—"),
                "max_tokens": conf_obj.get("max_tokens", "—"),
            }

        cfg_a = extract_config(ver_a)
        cfg_b = extract_config(ver_b)

        info = key_value_block(
            [
                ("Template", template.name),
                ("Version A", f"{ver_a.template_version} (`{str(ver_a.id)}`)"),
                ("Version B", f"{ver_b.template_version} (`{str(ver_b.id)}`)"),
            ]
        )
        content = section("Prompt Version Comparison", info)

        # Model comparison
        content += "\n\n### Configuration\n\n"
        content += (
            f"| Setting | {ver_a.template_version} | {ver_b.template_version} |\n"
        )
        content += "| --- | --- | --- |\n"
        content += f"| Model | {cfg_a['model']} | {cfg_b['model']} |\n"
        content += (
            f"| Temperature | {cfg_a['temperature']} | {cfg_b['temperature']} |\n"
        )
        content += f"| Max Tokens | {cfg_a['max_tokens']} | {cfg_b['max_tokens']} |\n"
        content += f"| Is Default | {'Yes' if ver_a.is_default else 'No'} | {'Yes' if ver_b.is_default else 'No'} |\n"
        content += f"| Is Draft | {'Yes' if ver_a.is_draft else 'No'} | {'Yes' if ver_b.is_draft else 'No'} |\n"

        # Message comparison
        def format_messages(messages):
            lines = []
            for msg in messages:
                role = msg.get("role", "?")
                text = msg.get("content", "")
                if isinstance(text, list):
                    parts = [p.get("text", "") for p in text if p.get("type") == "text"]
                    text = " ".join(parts)
                lines.append(f"**{role}:** {truncate(str(text), 300)}")
            return "\n\n".join(lines) if lines else "_No messages_"

        content += f"\n\n### Messages — {ver_a.template_version}\n\n"
        content += format_messages(cfg_a["messages"])
        content += f"\n\n### Messages — {ver_b.template_version}\n\n"
        content += format_messages(cfg_b["messages"])

        # Eval results comparison
        eval_a = ver_a.evaluation_results or {}
        eval_b = ver_b.evaluation_results or {}
        all_metrics = set(list(eval_a.keys()) + list(eval_b.keys()))

        if all_metrics:
            content += "\n\n### Evaluation Results\n\n"
            content += (
                f"| Metric | {ver_a.template_version} | {ver_b.template_version} |\n"
            )
            content += "| --- | --- | --- |\n"
            for metric in sorted(all_metrics):
                val_a = eval_a.get(metric, "—")
                val_b = eval_b.get(metric, "—")
                if isinstance(val_a, dict):
                    val_a = val_a.get("score", val_a.get("average", "—"))
                if isinstance(val_b, dict):
                    val_b = val_b.get("score", val_b.get("average", "—"))
                content += f"| {metric} | {val_a} | {val_b} |\n"

        return ToolResult(
            content=content,
            data={
                "version_a": {
                    "id": str(ver_a.id),
                    "version": ver_a.template_version,
                    "model": cfg_a["model"],
                },
                "version_b": {
                    "id": str(ver_b.id),
                    "version": ver_b.template_version,
                    "model": cfg_b["model"],
                },
            },
        )
