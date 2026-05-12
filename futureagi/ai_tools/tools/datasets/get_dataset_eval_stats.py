from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_number,
    format_status,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool


class GetDatasetEvalStatsInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. If omitted, recent dataset candidates are returned.",
    )


@register_tool
class GetDatasetEvalStatsTool(BaseTool):
    name = "get_dataset_eval_stats"
    description = (
        "Returns statistics for all evaluations on a dataset including "
        "pass/fail counts, average scores, completion status, and error counts."
    )
    category = "datasets"
    input_model = GetDatasetEvalStatsInput

    def execute(
        self, params: GetDatasetEvalStatsInput, context: ToolContext
    ) -> ToolResult:
        from django.db.models import Avg, Count, Q

        from ai_tools.tools.datasets._utils import (
            candidate_datasets_result,
            resolve_dataset_for_tool,
        )
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
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

        total_rows = Row.objects.filter(dataset=dataset, deleted=False).count()

        evals = (
            UserEvalMetric.objects.filter(dataset=dataset, deleted=False)
            .select_related("template")
            .order_by("-created_at")
        )

        if not evals.exists():
            return ToolResult(
                content=section(
                    f"Eval Stats: {dataset.name}",
                    "_No evaluations configured on this dataset._",
                ),
                data={"evals": [], "total_rows": total_rows},
            )

        rows = []
        data_list = []
        for em in evals:
            # Find result column
            col = Column.objects.filter(
                dataset=dataset,
                source_id=str(em.id),
                source="evaluation",
                deleted=False,
            ).first()

            completed = 0
            passed = 0
            failed = 0
            errors = 0
            avg_score = None

            if col:
                cells = Cell.objects.filter(column=col, dataset=dataset, deleted=False)
                total_cells = cells.count()
                completed = cells.exclude(value="").exclude(value__isnull=True).count()

                # Count pass/fail for boolean outputs
                if col.data_type == "boolean":
                    passed = cells.filter(
                        Q(value__iexact="true")
                        | Q(value__iexact="passed")
                        | Q(value__iexact="pass")
                    ).count()
                    failed = cells.filter(
                        Q(value__iexact="false")
                        | Q(value__iexact="failed")
                        | Q(value__iexact="fail")
                    ).count()
                elif col.data_type == "float":
                    # Calculate average score
                    try:
                        scores = [
                            float(c.value)
                            for c in cells
                            if c.value
                            and c.value.replace(".", "").replace("-", "").isdigit()
                        ]
                        if scores:
                            avg_score = sum(scores) / len(scores)
                    except (ValueError, TypeError):
                        pass

                errors = cells.filter(status="failed").count()

            pct = f"{completed}/{total_rows}" if total_rows > 0 else "—"

            rows.append(
                [
                    em.name or "—",
                    format_status(em.status),
                    pct,
                    str(passed) if passed else "—",
                    str(failed) if failed else "—",
                    format_number(avg_score) if avg_score is not None else "—",
                    str(errors) if errors else "0",
                ]
            )

            data_list.append(
                {
                    "id": str(em.id),
                    "name": em.name,
                    "status": em.status,
                    "completed": completed,
                    "total_rows": total_rows,
                    "passed": passed,
                    "failed": failed,
                    "avg_score": avg_score,
                    "errors": errors,
                }
            )

        table = markdown_table(
            ["Eval", "Status", "Completed", "Passed", "Failed", "Avg Score", "Errors"],
            rows,
        )

        content = section(
            f"Eval Stats: {dataset.name} ({len(data_list)} evals, {total_rows} rows)",
            table,
        )

        return ToolResult(
            content=content,
            data={"evals": data_list, "total_rows": total_rows},
        )
