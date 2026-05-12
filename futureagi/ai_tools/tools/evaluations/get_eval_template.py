from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetEvalTemplateInput(PydanticBaseModel):
    eval_template_id: str = Field(
        default="", description="Name or UUID of the eval template to retrieve"
    )


def _template_candidates_result(
    context: ToolContext,
    title: str = "Eval Template Candidates",
    detail: str = "",
) -> ToolResult:
    from django.db.models import Q
    from model_hub.models.evals_metric import EvalTemplate

    templates = list(
        EvalTemplate.no_workspace_objects.filter(
            Q(organization=context.organization) | Q(organization__isnull=True)
        ).order_by("-created_at")[:10]
    )
    rows = [
        [
            f"`{template.id}`",
            truncate(template.name, 48),
            template.owner or "unknown",
        ]
        for template in templates
    ]
    candidates = (
        markdown_table(["ID", "Name", "Owner"], rows)
        if rows
        else "No eval templates found."
    )
    body = (
        detail
        or "Choose one of these eval templates, then call `get_eval_template` "
        "with `eval_template_id`."
    )
    return ToolResult(
        content=section(title, f"{body}\n\n{candidates}"),
        data={
            "templates": [
                {"id": str(template.id), "name": template.name}
                for template in templates
            ],
            "requires_eval_template_id": True,
            "lookup_error": detail or None,
        },
    )


@register_tool
class GetEvalTemplateTool(BaseTool):
    name = "get_eval_template"
    description = (
        "Returns detailed information about an evaluation template including "
        "its type (LLM/code/agent), instructions, configuration, required variables, "
        "output type, scoring, and version history. For composite evals, also shows "
        "children and aggregation config."
    )
    category = "evaluations"
    input_model = GetEvalTemplateInput

    def execute(self, params: GetEvalTemplateInput, context: ToolContext) -> ToolResult:
        from ai_tools.resolvers import resolve_eval_template
        from model_hub.models.evals_metric import (
            CompositeEvalChild,
            EvalTemplate,
            EvalTemplateVersion,
        )

        if not params.eval_template_id:
            return _template_candidates_result(context)

        template_obj, err = resolve_eval_template(
            params.eval_template_id, context.organization
        )
        if err:
            return _template_candidates_result(
                context,
                detail=(
                    f"{err}\n\nUse one of these IDs or exact names, or call "
                    "`list_eval_templates` with a search term."
                ),
            )

        try:
            template = EvalTemplate.objects.get(id=template_obj.id)
        except EvalTemplate.DoesNotExist:
            return ToolResult.not_found("Eval Template", str(template_obj.id))

        config = template.config or {}
        required_keys = (
            config.get("required_keys", []) if isinstance(config, dict) else []
        )
        optional_keys = (
            config.get("optional_keys", []) if isinstance(config, dict) else []
        )

        tags = ", ".join(template.eval_tags) if template.eval_tags else "-"
        eval_type_labels = {"llm": "LLM-as-a-Judge", "code": "Code", "agent": "Agent"}
        eval_type = eval_type_labels.get(
            template.eval_type or "", template.eval_type or "-"
        )
        template_type = template.template_type or "single"
        output_type = template.output_type_normalized or config.get("output", "-")
        version_count = EvalTemplateVersion.objects.filter(
            eval_template=template
        ).count()

        info_pairs = [
            ("ID", f"`{template.id}`"),
            ("Name", template.name),
            ("Owner", template.owner or "-"),
            ("Eval Type", eval_type),
            ("Template Type", template_type),
            ("Output Type", output_type),
            (
                "Pass Threshold",
                str(template.pass_threshold)
                if template.pass_threshold is not None
                else "0.5",
            ),
            ("Model", template.model or "-"),
            ("Tags", tags),
            ("Versions", str(version_count)),
            ("Created", format_datetime(template.created_at)),
        ]

        if template.eval_type == "agent":
            info_pairs.append(("Agent Mode", config.get("agent_mode", "-")))
            knowledge_bases = config.get("knowledge_bases", [])
            if knowledge_bases:
                info_pairs.append(("Knowledge Bases", str(len(knowledge_bases))))

        if template.eval_type == "code":
            info_pairs.append(("Code Language", config.get("language", "python")))

        info_pairs.append(("Template Format", config.get("template_format", "mustache")))

        content = section(f"Eval Template: {template.name}", key_value_block(info_pairs))

        if template.description:
            content += f"\n\n### Description\n\n{truncate(template.description, 500)}"

        if template.criteria:
            label = "Code" if template.eval_type == "code" else "Instructions"
            content += f"\n\n### {label}\n\n{truncate(template.criteria, 1000)}"

        if template.choices:
            content += "\n\n### Choices\n\n"
            if isinstance(template.choices, list):
                for choice in template.choices:
                    score = ""
                    if template.choice_scores and choice in template.choice_scores:
                        score = f" (score: {template.choice_scores[choice]})"
                    content += f"- {choice}{score}\n"
            else:
                content += f"```json\n{truncate(str(template.choices), 500)}\n```"

        if required_keys:
            content += (
                "\n\n### Required Variables\n\n"
                + ", ".join(f"`{key}`" for key in required_keys)
            )

        if optional_keys:
            content += (
                "\n\n### Optional Variables\n\n"
                + ", ".join(f"`{key}`" for key in optional_keys)
            )

        if template_type == "composite":
            children = (
                CompositeEvalChild.objects.filter(parent=template, deleted=False)
                .select_related("child")
                .order_by("order")
            )
            if children.exists():
                aggregation = (
                    template.aggregation_function
                    if template.aggregation_enabled
                    else "disabled"
                )
                content += "\n\n### Composite Children\n\n"
                content += f"**Aggregation:** {aggregation}\n"
                if template.composite_child_axis:
                    content += f"**Child Axis:** {template.composite_child_axis}\n"
                content += "\n"
                for link in children:
                    child = link.child
                    child_type = eval_type_labels.get(
                        child.eval_type or "", child.eval_type or "-"
                    )
                    content += f"- {child.name} ({child_type}, weight: {link.weight})\n"

        if version_count > 0:
            versions = (
                EvalTemplateVersion.objects.filter(eval_template=template)
                .order_by("-version_number")[:3]
            )
            content += "\n\n### Recent Versions\n\n"
            for version in versions:
                default_marker = " **(default)**" if version.is_default else ""
                content += (
                    f"- V{version.version_number}{default_marker} - "
                    f"{format_datetime(version.created_at)}\n"
                )
            if version_count > 3:
                content += f"\n_{version_count - 3} more versions available._"

        data = {
            "id": str(template.id),
            "name": template.name,
            "owner": template.owner,
            "eval_type": template.eval_type,
            "template_type": template_type,
            "description": template.description,
            "output_type": output_type,
            "pass_threshold": template.pass_threshold,
            "choice_scores": template.choice_scores,
            "required_keys": required_keys,
            "optional_keys": optional_keys,
            "tags": template.eval_tags,
            "criteria": template.criteria,
            "choices": template.choices,
            "model": template.model,
            "version_count": version_count,
        }

        return ToolResult(content=content, data=data)
