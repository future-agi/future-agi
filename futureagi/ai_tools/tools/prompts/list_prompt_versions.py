from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class ListPromptVersionsInput(PydanticBaseModel):
    template_id: str = Field(
        default="",
        description="Name or UUID of the prompt template. If omitted, candidates are returned.",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    include_drafts: bool = Field(default=True, description="Include draft versions")


@register_tool
class ListPromptVersionsTool(BaseTool):
    name = "list_prompt_versions"
    description = (
        "Lists all versions of a prompt template. "
        "Shows version number, status (draft/committed), labels, "
        "model, commit message, and creation date."
    )
    category = "prompts"
    input_model = ListPromptVersionsInput

    def execute(
        self, params: ListPromptVersionsInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.run_prompt import PromptTemplate, PromptVersion

        from ai_tools.resolvers import resolve_prompt_template
        from ai_tools.tools.prompts._utils import candidate_prompt_templates_result

        if not params.template_id:
            return candidate_prompt_templates_result(context)

        template_obj, err = resolve_prompt_template(
            params.template_id, context.organization, context.workspace
        )
        if err:
            return candidate_prompt_templates_result(
                context,
                "Prompt Template Not Found",
                f"{err} Use one of these template IDs or exact names.",
            )

        try:
            template = PromptTemplate.objects.get(id=template_obj.id)
        except PromptTemplate.DoesNotExist:
            return ToolResult.not_found("Prompt Template", str(template_obj.id))

        qs = PromptVersion.objects.filter(
            original_template=template, deleted=False
        ).order_by("-created_at")

        if not params.include_drafts:
            qs = qs.filter(is_draft=False)

        total = qs.count()
        versions = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for v in versions:
            labels = list(v.labels.values_list("name", flat=True))
            label_str = ", ".join(labels) if labels else "—"

            # Extract model from prompt config (model lives in configuration.model)
            model_name = "—"
            snapshot = v.prompt_config_snapshot
            if snapshot:
                # prompt_config_snapshot is stored as a single dict (not a list)
                if isinstance(snapshot, dict):
                    conf = snapshot.get("configuration", {})
                    model_name = conf.get("model") or snapshot.get("model", "—")
                elif isinstance(snapshot, list):
                    for cfg in snapshot:
                        conf = cfg.get("configuration", {})
                        m = conf.get("model") or cfg.get("model")
                        if m:
                            model_name = m
                            break

            rows.append(
                [
                    v.template_version,
                    f"`{v.id}`",
                    "Yes" if v.is_default else "—",
                    "Draft" if v.is_draft else "Committed",
                    label_str,
                    model_name,
                    truncate(v.commit_message, 30) if v.commit_message else "—",
                    format_datetime(v.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(v.id),
                    "version": v.template_version,
                    "is_default": v.is_default,
                    "is_draft": v.is_draft,
                    "labels": labels,
                    "model": model_name,
                    "commit_message": v.commit_message,
                }
            )

        table = markdown_table(
            [
                "Version",
                "ID",
                "Default",
                "Status",
                "Labels",
                "Model",
                "Message",
                "Created",
            ],
            rows,
        )

        showing = f"Showing {len(rows)} of {total}"
        content = section(
            f"Versions: {template.name} ({total})",
            f"{showing}\n\n{table}",
        )

        if total > params.offset + params.limit:
            content += f"\n\n_Use offset={params.offset + params.limit} to see more._"

        return ToolResult(content=content, data={"versions": data_list, "total": total})
