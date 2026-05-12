from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid, resolve_eval_template


class GetEvalPlaygroundInput(PydanticBaseModel):
    eval_template_id: str = Field(
        default="",
        description="The UUID or name of the eval template to get playground data for",
    )


@register_tool
class GetEvalPlaygroundTool(BaseTool):
    name = "get_eval_playground"
    description = (
        "Returns eval template playground data for testing. "
        "Includes the template's criteria, configuration schema, "
        "required/optional input keys, model, and output type. "
        "Use this to understand what inputs are needed before testing."
    )
    category = "evaluations"
    input_model = GetEvalPlaygroundInput

    def execute(
        self, params: GetEvalPlaygroundInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.evals_metric import EvalTemplate

        from model_hub.utils.eval_validators import validate_eval_template_org_access

        def candidates_result(detail: str = "") -> ToolResult:
            from django.db.models import Q

            templates = EvalTemplate.no_workspace_objects.filter(
                Q(organization=context.organization) | Q(organization__isnull=True)
            ).order_by("-created_at")[:10]
            rows = [
                f"- `{template.id}` — {template.name} ({template.owner or 'unknown owner'})"
                for template in templates
            ]
            content = section(
                "Eval Playground Candidates",
                (
                    (detail + "\n\n" if detail else "")
                    + "Choose one of these eval templates, then call "
                    "`get_eval_playground` with `eval_template_id`.\n\n"
                    + ("\n".join(rows) if rows else "No eval templates found.")
                ),
            )
            return ToolResult(
                content=content,
                data={
                    "templates": [
                        {"id": str(template.id), "name": template.name}
                        for template in templates
                    ]
                },
            )

        eval_ref = str(params.eval_template_id or "").strip()
        if not eval_ref:
            return candidates_result()

        resolved_template, err = resolve_eval_template(eval_ref, context.organization)
        if err:
            return candidates_result(err)

        try:
            template = validate_eval_template_org_access(
                resolved_template.id if resolved_template else eval_ref,
                context.organization,
            )
        except (EvalTemplate.DoesNotExist, ValueError, TypeError):
            detail = (
                f"Eval template `{eval_ref}` was not found."
                if is_uuid(eval_ref)
                else f"No eval template matched `{eval_ref}`."
            )
            return candidates_result(detail)

        config = template.config or {}
        output_type = config.get("output", "—") if isinstance(config, dict) else "—"
        required_keys = (
            config.get("required_keys", []) if isinstance(config, dict) else []
        )
        optional_keys = (
            config.get("optional_keys", []) if isinstance(config, dict) else []
        )
        eval_type_labels = {"llm": "LLM", "code": "Code", "agent": "Agent"}
        eval_type = eval_type_labels.get(template.eval_type or "", template.eval_type or "—")
        template_type = template.template_type or "single"

        info = key_value_block(
            [
                ("ID", f"`{template.id}`"),
                ("Name", template.name),
                ("Owner", template.owner or "—"),
                ("Eval Type", eval_type),
                ("Template Type", template_type),
                ("Output Type", output_type),
                ("Model", template.model or "—"),
            ]
        )

        content = section(f"Eval Playground: {template.name}", info)

        if template.description:
            content += f"\n\n### Description\n\n{truncate(template.description, 500)}"

        if template.criteria:
            content += (
                f"\n\n### Criteria / Prompt\n\n{truncate(template.criteria, 1000)}"
            )

        if template.choices:
            content += "\n\n### Choices\n\n"
            if isinstance(template.choices, list):
                for choice in template.choices:
                    content += f"- {choice}\n"
            else:
                content += f"```json\n{truncate(str(template.choices), 500)}\n```"

        # Input schema
        content += "\n\n### Input Schema\n\n"
        if required_keys:
            content += f"**Required:** {', '.join(f'`{k}`' for k in required_keys)}\n\n"
        else:
            content += "**Required:** _none_\n\n"

        if optional_keys:
            content += f"**Optional:** {', '.join(f'`{k}`' for k in optional_keys)}\n\n"

        # Config parameter details
        config_params = config.get("config", {}) if isinstance(config, dict) else {}
        if config_params and isinstance(config_params, dict):
            content += "\n\n### Parameter Details\n\n"
            for param_name, param_info in list(config_params.items())[:15]:
                desc = param_info if isinstance(param_info, str) else str(param_info)
                content += f"- **{param_name}**: {truncate(desc, 200)}\n"

        content += (
            "\n\n_Use `test_eval_template` with the required keys as `mapping` "
            "to test this evaluation._"
        )

        return ToolResult(
            content=content,
            data={
                "id": str(template.id),
                "name": template.name,
                "template_type": template_type,
                "output_type": output_type,
                "required_keys": required_keys,
                "optional_keys": optional_keys,
                "criteria": template.criteria,
                "choices": template.choices,
                "model": template.model,
                "config": config,
            },
        )
