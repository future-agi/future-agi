from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class CreateEvalGroupInput(PydanticBaseModel):
    name: Optional[str] = Field(
        default=None,
        description="Name for the eval group",
        min_length=1,
        max_length=255,
    )
    description: Optional[str] = Field(
        default=None, description="Description of the eval group"
    )
    eval_template_ids: Optional[list[UUID]] = Field(
        default=None,
        description=(
            "List of eval template IDs to include in the group. Omit to list "
            "candidate templates with IDs."
        ),
    )


@register_tool
class CreateEvalGroupTool(BaseTool):
    name = "create_eval_group"
    description = (
        "Creates an evaluation group (a bundle of related eval templates). "
        "Groups make it easy to apply multiple evaluations at once."
    )
    category = "evaluations"
    input_model = CreateEvalGroupInput

    def execute(self, params: CreateEvalGroupInput, context: ToolContext) -> ToolResult:

        from model_hub.models.evals_metric import EvalTemplate
        from model_hub.services.eval_group import create_eval_group as svc_create
        from ai_tools.formatting import format_datetime, markdown_table, truncate

        if not params.name or not params.eval_template_ids:
            from django.db.models import Q

            templates = list(
                EvalTemplate.no_workspace_objects.filter(
                    Q(organization=context.organization) | Q(organization__isnull=True)
                ).order_by("-created_at")[:10]
            )
            rows = []
            data = []
            for template in templates:
                config = template.config or {}
                output_type = (
                    config.get("output", "—") if isinstance(config, dict) else "—"
                )
                rows.append(
                    [
                        f"`{template.id}`",
                        truncate(template.name, 36),
                        template.owner or "—",
                        output_type,
                        format_datetime(template.created_at),
                    ]
                )
                data.append(
                    {
                        "id": str(template.id),
                        "name": template.name,
                        "owner": template.owner,
                        "output_type": output_type,
                    }
                )

            missing_fields = []
            if not params.name:
                missing_fields.append("name")
            if not params.eval_template_ids:
                missing_fields.append("eval_template_ids")

            detail = (
                "Provide a group `name` and at least one `eval_template_ids` value "
                "from the candidates below, then call `create_eval_group` again."
            )
            if not rows:
                detail = (
                    "No evaluation templates were found. Create an eval template "
                    "first, then call `create_eval_group` with its template ID."
                )
            return ToolResult.needs_input(
                section(
                    "Eval Group Inputs Required",
                    f"{detail}\n\n"
                    + markdown_table(
                        ["ID", "Name", "Owner", "Output", "Created"], rows
                    ),
                ),
                data={"candidate_templates": data},
                missing_fields=missing_fields,
            )

        # Validate templates exist
        template_ids = [str(tid) for tid in params.eval_template_ids]
        from django.db.models import Q

        templates = EvalTemplate.no_workspace_objects.filter(
            Q(organization=context.organization) | Q(organization__isnull=True),
            id__in=template_ids,
        )
        found_ids = {str(t.id) for t in templates}
        missing = [tid for tid in template_ids if tid not in found_ids]
        if missing:
            return ToolResult.error(
                f"Template(s) not found: {', '.join(missing)}. "
                "Use list_eval_templates to see available templates.",
                error_code="NOT_FOUND",
            )

        try:
            result = svc_create(
                name=params.name,
                description=params.description or "",
                eval_template_ids=template_ids,
                user=context.user,
                workspace=context.workspace,
            )
        except Exception as e:
            if "unique_eval_group_name_workspace_not_deleted" in str(e):
                return ToolResult.error(
                    f"An eval group named '{params.name}' already exists in this workspace.",
                    error_code="VALIDATION_ERROR",
                )
            return ToolResult.error(
                f"Failed to create eval group: {str(e)}",
                error_code="INTERNAL_ERROR",
            )

        template_names = [t.name for t in templates]

        group_id = result.get("id", "")
        group_name = result.get("name", params.name)

        info = key_value_block(
            [
                ("ID", f"`{group_id}`"),
                ("Name", group_name),
                ("Templates", str(len(template_names))),
                ("Included", ", ".join(template_names[:5])),
            ]
        )

        content = section("Eval Group Created", info)

        return ToolResult(
            content=content,
            data={
                "group_id": str(group_id),
                "name": group_name,
                "template_count": len(template_names),
                "template_names": template_names,
            },
        )
