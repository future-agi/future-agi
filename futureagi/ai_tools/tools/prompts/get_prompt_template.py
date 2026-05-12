from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetPromptTemplateInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. Omit to list candidate templates with IDs.",
    )


@register_tool
class GetPromptTemplateTool(BaseTool):
    name = "get_prompt_template"
    description = (
        "Returns detailed information about a prompt template including "
        "its default version's prompt config (messages, model, variables), "
        "recent versions list, and evaluation configs."
    )
    category = "prompts"
    input_model = GetPromptTemplateInput

    def execute(
        self, params: GetPromptTemplateInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.resolvers import resolve_prompt_template
        from model_hub.models.run_prompt import PromptTemplate, PromptVersion

        if not params.template_id:
            templates = PromptTemplate.objects.order_by("-updated_at")[:10]
            rows = [
                [
                    template.name,
                    f"`{template.id}`",
                    format_datetime(template.updated_at),
                ]
                for template in templates
            ]
            if not rows:
                return ToolResult(
                    content="No prompt templates found in this workspace.",
                    data={"templates": []},
                )
            return ToolResult(
                content=section(
                    "Candidate Prompt Templates",
                    markdown_table(["Name", "ID", "Updated"], rows),
                ),
                data={
                    "templates": [
                        {"id": str(template.id), "name": template.name}
                        for template in templates
                    ]
                },
            )

        template_obj, err = resolve_prompt_template(
            params.template_id, context.organization, context.workspace
        )
        if err:
            return ToolResult.error(err, error_code="NOT_FOUND")

        try:
            template = PromptTemplate.objects.get(id=template_obj.id)
        except PromptTemplate.DoesNotExist:
            return ToolResult.not_found("Prompt Template", str(template_obj.id))

        # Get default version (or latest committed, or latest)
        default_ver = PromptVersion.objects.filter(
            original_template=template, deleted=False, is_default=True
        ).first()
        if not default_ver:
            default_ver = (
                PromptVersion.objects.filter(
                    original_template=template, deleted=False, is_draft=False
                )
                .order_by("-created_at")
                .first()
            )
        if not default_ver:
            default_ver = (
                PromptVersion.objects.filter(original_template=template, deleted=False)
                .order_by("-created_at")
                .first()
            )

        # Build info block
        info_pairs = [
            ("ID", f"`{template.id}`"),
            ("Name", template.name),
            ("Description", template.description or "—"),
            ("Folder", template.prompt_folder.name if template.prompt_folder else "—"),
            (
                "Variables",
                ", ".join(template.variable_names) if template.variable_names else "—",
            ),
            ("Created", format_datetime(template.created_at)),
            ("Updated", format_datetime(template.updated_at)),
            (
                "Link",
                f"[View in Dashboard]({dashboard_link('prompt_template', template.id, context.workspace)})",
            ),
        ]
        content = section(
            f"Prompt Template: {template.name}", key_value_block(info_pairs)
        )

        # Default version details
        version_data = None
        if default_ver:
            ver_info = key_value_block(
                [
                    ("Version", default_ver.template_version),
                    ("Version ID", f"`{default_ver.id}`"),
                    ("Is Default", "Yes" if default_ver.is_default else "No"),
                    ("Is Draft", "Yes" if default_ver.is_draft else "No"),
                    ("Commit Message", default_ver.commit_message or "—"),
                ]
            )
            content += f"\n\n### Default Version\n\n{ver_info}"

            # Show prompt config snapshot
            if default_ver.prompt_config_snapshot:
                configs = default_ver.prompt_config_snapshot
                # prompt_config_snapshot is stored as a single dict (not a list)
                if isinstance(configs, dict):
                    configs = [configs]
                if isinstance(configs, list) and configs:
                    for i, cfg in enumerate(configs):
                        config_section = cfg.get("configuration", {})
                        model_name = config_section.get("model") or cfg.get(
                            "model", "—"
                        )
                        messages = cfg.get("messages", [])
                        msg_preview = ""
                        for msg in messages[:3]:
                            role = msg.get("role", "?")
                            text = msg.get("content", "")
                            if isinstance(text, list):
                                # Extract text from content list
                                parts = [
                                    p.get("text", "")
                                    for p in text
                                    if p.get("type") == "text"
                                ]
                                text = " ".join(parts)
                            msg_preview += (
                                f"  - **{role}**: {truncate(str(text), 200)}\n"
                            )
                        if len(messages) > 3:
                            msg_preview += (
                                f"  - _...{len(messages) - 3} more messages_\n"
                            )

                        config_section = cfg.get("configuration", {})
                        temp = config_section.get("temperature", "—")
                        max_tok = config_section.get("max_tokens", "—")

                        label = (
                            f"Config {i + 1}" if len(configs) > 1 else "Prompt Config"
                        )
                        content += f"\n\n### {label}\n\n"
                        content += f"**Model:** {model_name} | **Temperature:** {temp} | **Max Tokens:** {max_tok}\n\n"
                        content += f"**Messages:**\n{msg_preview}"

            version_data = {
                "id": str(default_ver.id),
                "version": default_ver.template_version,
                "is_default": default_ver.is_default,
                "is_draft": default_ver.is_draft,
                "prompt_config": default_ver.prompt_config_snapshot,
            }

        # Recent versions list
        recent_versions = PromptVersion.objects.filter(
            original_template=template, deleted=False
        ).order_by("-created_at")[:10]

        if recent_versions:
            ver_rows = []
            for v in recent_versions:
                labels = list(v.labels.values_list("name", flat=True))
                label_str = ", ".join(labels) if labels else "—"
                ver_rows.append(
                    [
                        v.template_version,
                        f"`{str(v.id)}`",
                        "Yes" if v.is_default else "—",
                        "Draft" if v.is_draft else "Committed",
                        label_str,
                        truncate(v.commit_message, 30) if v.commit_message else "—",
                        format_datetime(v.created_at),
                    ]
                )
            content += "\n\n### Recent Versions\n\n"
            content += markdown_table(
                ["Version", "ID", "Default", "Status", "Labels", "Message", "Created"],
                ver_rows,
            )

        return ToolResult(
            content=content,
            data={
                "id": str(template.id),
                "name": template.name,
                "description": template.description,
                "variable_names": template.variable_names or [],
                "default_version": version_data,
            },
        )
