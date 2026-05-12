from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_status,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class ListDatasetEvalsInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. If omitted, recent dataset candidates are returned.",
    )
    search: Optional[str] = Field(
        default=None,
        description="Search evals by name (case-insensitive)",
    )


@register_tool
class ListDatasetEvalsTool(BaseTool):
    name = "list_dataset_evals"
    description = (
        "Lists all evaluation metrics configured on a specific dataset. "
        "Shows each eval's name, template, status, model, and column mapping. "
        "These are UserEvalMetric instances — evals applied to dataset columns."
    )
    category = "datasets"
    input_model = ListDatasetEvalsInput

    def execute(
        self, params: ListDatasetEvalsInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.tools.datasets._utils import resolve_dataset_for_tool
        from model_hub.models.develop_dataset import Dataset, Row
        from model_hub.models.evals_metric import UserEvalMetric

        if not params.dataset_id:
            datasets = list(
                Dataset.objects.filter(
                    organization=context.organization,
                    deleted=False,
                ).order_by("-created_at")[:10]
            )
            rows = [
                [
                    f"`{dataset.id}`",
                    dataset.name,
                    str(Row.objects.filter(dataset=dataset, deleted=False).count()),
                ]
                for dataset in datasets
            ]
            return ToolResult(
                content=section(
                    "Dataset Candidates",
                    markdown_table(["ID", "Name", "Rows"], rows)
                    if rows
                    else "No datasets found.",
                ),
                data={
                    "datasets": [
                        {"id": str(dataset.id), "name": dataset.name}
                        for dataset in datasets
                    ],
                    "requires_dataset_id": True,
                },
            )

        dataset, dataset_result = resolve_dataset_for_tool(
            params.dataset_id,
            context,
            title="Dataset Required",
        )
        if dataset_result:
            return dataset_result

        qs = (
            UserEvalMetric.objects.filter(
                dataset=dataset,
                deleted=False,
            )
            .select_related("template")
            .order_by("-created_at")
        )

        if params.search:
            qs = qs.filter(name__icontains=params.search)

        evals = list(qs)

        rows = []
        data_list = []
        for em in evals:
            template_name = em.template.name if em.template else "—"
            model = em.model or (em.template.model if em.template else "—") or "—"

            # Extract mapping info
            mapping = ""
            if em.config and isinstance(em.config, dict):
                mapping_dict = em.config.get("mapping", {})
                if mapping_dict:
                    mapping = ", ".join(
                        f"{k}→{str(v)}" for k, v in mapping_dict.items()
                    )

            rows.append(
                [
                    f"`{em.id}`",
                    em.name or "—",
                    template_name,
                    format_status(em.status),
                    model,
                    mapping or "—",
                ]
            )

            data_list.append(
                {
                    "id": str(em.id),
                    "name": em.name,
                    "template": template_name,
                    "template_id": str(em.template.id) if em.template else None,
                    "status": em.status,
                    "model": model,
                    "config": em.config,
                }
            )

        table = markdown_table(
            ["ID", "Name", "Template", "Status", "Model", "Mapping"], rows
        )

        content = section(
            f"Dataset Evals: {dataset.name} ({len(evals)})",
            table,
        )

        return ToolResult(
            content=content,
            data={"evals": data_list, "total": len(evals)},
        )
