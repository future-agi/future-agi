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


class ListEvalGroupsInput(PydanticBaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    search: str | None = Field(
        default=None,
        description="Search eval groups by name (case-insensitive)",
    )


@register_tool
class ListEvalGroupsTool(BaseTool):
    name = "list_eval_groups"
    description = (
        "Lists evaluation groups (bundles of related eval templates). "
        "Returns group name, number of templates, description, and creation time."
    )
    category = "evaluations"
    input_model = ListEvalGroupsInput

    def execute(self, params: ListEvalGroupsInput, context: ToolContext) -> ToolResult:

        from model_hub.models.eval_groups import EvalGroup

        qs = EvalGroup.objects.prefetch_related("eval_templates").order_by(
            "-created_at"
        )

        if params.search:
            qs = qs.filter(name__icontains=params.search)

        total = qs.count()
        groups = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for g in groups:
            template_count = g.eval_templates.count()
            template_names = [t.name for t in g.eval_templates.all()[:3]]
            templates_str = ", ".join(template_names)
            if template_count > 3:
                templates_str += f" (+{template_count - 3})"

            rows.append(
                [
                    f"`{g.id}`",
                    truncate(g.name, 40),
                    str(template_count),
                    truncate(templates_str, 50) if templates_str else "—",
                    format_datetime(g.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(g.id),
                    "name": g.name,
                    "template_count": template_count,
                    "description": g.description,
                    "is_sample": g.is_sample,
                }
            )

        table = markdown_table(
            ["ID", "Name", "Templates", "Included Templates", "Created"], rows
        )

        showing = f"Showing {len(rows)} of {total}"
        content = section(f"Eval Groups ({total})", f"{showing}\n\n{table}")

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(content=content, data={"groups": data_list, "total": total})
