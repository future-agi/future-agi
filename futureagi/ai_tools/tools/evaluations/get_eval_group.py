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


class GetEvalGroupInput(PydanticBaseModel):
    eval_group_id: str = Field(
        default="",
        description="Eval group name or UUID. If omitted, recent eval group candidates are returned.",
    )


@register_tool
class GetEvalGroupTool(BaseTool):
    name = "get_eval_group"
    description = (
        "Returns detailed information about an evaluation group including "
        "its member templates, required keys, and supported models."
    )
    category = "evaluations"
    input_model = GetEvalGroupInput

    def execute(self, params: GetEvalGroupInput, context: ToolContext) -> ToolResult:
        from django.db.models import Q

        from model_hub.models.eval_groups import EvalGroup
        from model_hub.models.evals_metric import EvalTemplate

        if not params.eval_group_id:
            groups = list(
                EvalGroup.objects.prefetch_related("eval_templates").order_by(
                    "-created_at"
                )[:10]
            )
            rows = [
                [
                    f"`{group.id}`",
                    truncate(group.name, 40),
                    str(group.eval_templates.count()),
                    "Yes" if group.is_sample else "No",
                ]
                for group in groups
            ]
            return ToolResult(
                content=section(
                    "Eval Group Candidates",
                    markdown_table(["ID", "Name", "Templates", "Sample"], rows)
                    if rows
                    else "No eval groups found.",
                ),
                data={
                    "groups": [
                        {"id": str(group.id), "name": group.name}
                        for group in groups
                    ],
                    "requires_eval_group_id": True,
                },
            )

        group_ref = params.eval_group_id.strip()
        try:
            query = (
                Q(id=group_ref, organization=context.organization, workspace=context.workspace)
                | Q(id=group_ref, is_sample=True)
                | Q(name__iexact=group_ref, organization=context.organization, workspace=context.workspace)
                | Q(name__iexact=group_ref, is_sample=True)
            )
            group = EvalGroup.no_workspace_objects.get(query)
        except EvalGroup.DoesNotExist:
            return ToolResult.not_found("Eval Group", group_ref)
        except (ValueError, TypeError):
            return ToolResult.not_found("Eval Group", group_ref)

        # Get member templates via through table
        template_ids = list(
            group.eval_templates.through.objects.filter(
                evalgroup_id=group.id
            ).values_list("evaltemplate_id", flat=True)
        )
        eval_templates = EvalTemplate.no_workspace_objects.filter(
            Q(organization=context.organization) | Q(organization__isnull=True),
            id__in=template_ids,
        )

        info = key_value_block(
            [
                ("ID", f"`{group.id}`"),
                ("Name", group.name),
                ("Description", group.description or "—"),
                ("Templates", str(len(template_ids))),
                ("Sample Group", "Yes" if group.is_sample else "No"),
                ("Created", format_datetime(group.created_at)),
            ]
        )

        content = section(f"Eval Group: {group.name}", info)

        # Members table
        rows = []
        members_data = []
        all_required_keys = set()
        models_sets = []

        for t in eval_templates:
            config = t.config or {}
            required_keys = (
                config.get("required_keys", []) if isinstance(config, dict) else []
            )
            models_list = config.get("models", []) if isinstance(config, dict) else []

            all_required_keys.update(required_keys)
            if models_list:
                models_sets.append(set(models_list))

            rows.append(
                [
                    f"`{str(t.id)}`",
                    truncate(t.name, 40),
                    t.owner or "—",
                    ", ".join(required_keys[:3]) if required_keys else "—",
                    ", ".join(t.eval_tags[:2]) if t.eval_tags else "—",
                ]
            )
            members_data.append(
                {
                    "id": str(t.id),
                    "name": t.name,
                    "owner": t.owner,
                    "required_keys": required_keys,
                    "tags": t.eval_tags,
                }
            )

        if rows:
            table = markdown_table(
                ["ID", "Name", "Owner", "Required Keys", "Tags"], rows
            )
            content += f"\n\n### Member Templates ({len(rows)})\n\n{table}"

        if all_required_keys:
            content += f"\n\n### All Required Keys\n\n{', '.join(f'`{k}`' for k in sorted(all_required_keys))}"

        if models_sets:
            models_intersection = (
                set.intersection(*models_sets)
                if len(models_sets) > 1
                else models_sets[0]
            )
            if models_intersection:
                content += (
                    f"\n\n### Common Models\n\n{', '.join(sorted(models_intersection))}"
                )

        return ToolResult(
            content=content,
            data={
                "group": {
                    "id": str(group.id),
                    "name": group.name,
                    "description": group.description,
                    "is_sample": group.is_sample,
                },
                "members": members_data,
                "required_keys": sorted(all_required_keys),
                "total_templates": len(template_ids),
            },
        )
