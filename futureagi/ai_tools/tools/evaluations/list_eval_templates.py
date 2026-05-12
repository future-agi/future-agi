
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class ListEvalTemplatesInput(PydanticBaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    owner: str | None = Field(
        default=None,
        description="Filter by owner: 'system' (built-in) or 'user' (custom)",
    )
    search: str | None = Field(
        default=None,
        description="Search eval templates by name (case-insensitive)",
    )

    @field_validator("limit", mode="before")
    @classmethod
    def clamp_limit(cls, value):
        try:
            return min(max(int(value), 1), 100)
        except (TypeError, ValueError):
            return 20


@register_tool
class ListEvalTemplatesTool(BaseTool):
    name = "list_eval_templates"
    description = (
        "Lists available evaluation templates (metrics). "
        "Returns template name, owner type (system/user), output type, "
        "tags, and description. Use this to discover what evaluation metrics "
        "are available before running evaluations."
    )
    category = "evaluations"
    input_model = ListEvalTemplatesInput

    def execute(
        self, params: ListEvalTemplatesInput, context: ToolContext
    ) -> ToolResult:

        from django.db.models import Q
        from model_hub.models.evals_metric import EvalTemplate

        qs = EvalTemplate.no_workspace_objects.filter(
            Q(organization=context.organization) | Q(organization__isnull=True)
        ).order_by("-created_at")

        if params.owner:
            qs = qs.filter(owner=params.owner.lower())
        if params.search:
            qs = qs.filter(name__icontains=params.search)

        total = qs.count()
        templates = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for t in templates:
            tags = ", ".join(t.eval_tags[:3]) if t.eval_tags else "—"
            if t.eval_tags and len(t.eval_tags) > 3:
                tags += f" (+{len(t.eval_tags) - 3})"

            config = t.config or {}
            output_type = config.get("output", "—") if isinstance(config, dict) else "—"

            rows.append(
                [
                    f"`{t.id}`",
                    truncate(t.name, 40),
                    t.owner or "—",
                    output_type,
                    tags,
                    format_datetime(t.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(t.id),
                    "name": t.name,
                    "owner": t.owner,
                    "output_type": output_type,
                    "tags": t.eval_tags,
                    "description": t.description,
                }
            )

        table = markdown_table(
            ["ID", "Name", "Owner", "Output", "Tags", "Created"], rows
        )

        showing = f"Showing {len(rows)} of {total}"
        if params.owner:
            showing += f" (owner: {params.owner})"
        if params.search:
            showing += f" (search: '{params.search}')"

        content = section(f"Eval Templates ({total})", f"{showing}\n\n{table}")

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(
            content=content, data={"templates": data_list, "total": total}
        )
