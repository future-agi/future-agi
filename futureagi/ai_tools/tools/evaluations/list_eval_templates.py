from typing import Literal, Optional

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


class ListEvalTemplatesInput(PydanticBaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    owner: Optional[str] = Field(
        default=None,
        description="Filter by owner: 'system' (built-in) or 'user' (custom)",
    )
    search: Optional[str] = Field(
        default=None,
        description="Search eval templates by name (case-insensitive)",
    )
    eval_type: Optional[Literal["llm", "code", "agent"]] = Field(
        default=None,
        description="Filter by eval type: 'llm', 'code', or 'agent'.",
    )
    template_type: Optional[Literal["single", "composite"]] = Field(
        default=None,
        description="Filter by template type: 'single' or 'composite'.",
    )
    output_type: Optional[Literal["pass_fail", "percentage", "deterministic"]] = Field(
        default=None,
        description="Filter by output type: 'pass_fail', 'percentage', or 'deterministic'.",
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Filter by tags (returns templates with any of the given tags).",
    )


@register_tool
class ListEvalTemplatesTool(BaseTool):
    name = "list_eval_templates"
    description = (
        "Lists available evaluation templates. Returns name, type (LLM/code/agent), "
        "template type (single/composite), output type, tags, and description. "
        "Use this to discover what evaluations are available before running them."
    )
    category = "evaluations"
    input_model = ListEvalTemplatesInput

    def execute(
        self, params: ListEvalTemplatesInput, context: ToolContext
    ) -> ToolResult:

        from django.db.models import Q

        from model_hub.models.evals_metric import EvalTemplate

        qs = EvalTemplate.no_workspace_objects.filter(
            Q(organization=context.organization) | Q(organization__isnull=True),
            deleted=False,
        ).order_by("-created_at")

        if params.owner:
            qs = qs.filter(owner=params.owner.lower())
        if params.search:
            qs = qs.filter(name__icontains=params.search)
        if params.eval_type:
            qs = qs.filter(eval_type=params.eval_type)
        if params.template_type:
            qs = qs.filter(template_type=params.template_type)
        if params.output_type:
            qs = qs.filter(output_type_normalized=params.output_type)
        if params.tags:
            qs = qs.filter(eval_tags__overlap=params.tags)

        total = qs.count()
        templates = qs[params.offset : params.offset + params.limit]

        eval_type_labels = {"llm": "LLM", "code": "Code", "agent": "Agent"}

        rows = []
        data_list = []
        for t in templates:
            tags = ", ".join(t.eval_tags[:3]) if t.eval_tags else "—"
            if t.eval_tags and len(t.eval_tags) > 3:
                tags += f" (+{len(t.eval_tags) - 3})"

            et = eval_type_labels.get(t.eval_type or "", t.eval_type or "—")
            tt = t.template_type or "single"
            ot = t.output_type_normalized or "—"

            rows.append(
                [
                    f"`{t.id}`",
                    truncate(t.name, 35),
                    t.owner or "—",
                    et,
                    tt,
                    ot,
                    tags,
                ]
            )
            data_list.append(
                {
                    "id": str(t.id),
                    "name": t.name,
                    "owner": t.owner,
                    "eval_type": t.eval_type,
                    "template_type": t.template_type or "single",
                    "output_type": t.output_type_normalized,
                    "tags": t.eval_tags,
                    "description": t.description,
                    "model": t.model,
                }
            )

        table = markdown_table(
            ["ID", "Name", "Owner", "Eval Type", "Template", "Output", "Tags"], rows
        )

        filters_desc = []
        if params.owner:
            filters_desc.append(f"owner: {params.owner}")
        if params.search:
            filters_desc.append(f"search: '{params.search}'")
        if params.eval_type:
            filters_desc.append(f"eval_type: {params.eval_type}")
        if params.template_type:
            filters_desc.append(f"template_type: {params.template_type}")
        if params.output_type:
            filters_desc.append(f"output_type: {params.output_type}")

        showing = f"Showing {len(rows)} of {total}"
        if filters_desc:
            showing += f" ({', '.join(filters_desc)})"

        content = section(f"Eval Templates ({total})", f"{showing}\n\n{table}")

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(
            content=content, data={"templates": data_list, "total": total}
        )
