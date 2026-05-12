from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_number,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetExperimentDataInput(PydanticBaseModel):
    experiment_id: str = Field(
        default="",
        description="Name or UUID of the experiment. Omit it to list candidate experiments.",
    )
    limit: int = Field(default=10, ge=1, le=50, description="Max rows to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")


@register_tool
class GetExperimentDataTool(BaseTool):
    name = "get_experiment_data"
    description = (
        "Returns paginated row-level data for an experiment, showing "
        "the input, each variant's output, and evaluation scores side by side."
    )
    category = "experiments"
    input_model = GetExperimentDataInput

    def execute(
        self, params: GetExperimentDataInput, context: ToolContext
    ) -> ToolResult:

        from ai_tools.tools.experiments._utils import (
            candidate_experiments_result,
            resolve_experiment_for_tool,
        )
        from model_hub.models.develop_dataset import Cell, Column, Row
        from model_hub.models.experiments import ExperimentsTable

        if not params.experiment_id:
            return candidate_experiments_result(
                context,
                "Experiment Required For Data",
            )

        experiment_obj, unresolved = resolve_experiment_for_tool(
            params.experiment_id,
            context,
            title="Experiment Required For Data",
        )
        if unresolved:
            return unresolved

        try:
            experiment = ExperimentsTable.objects.select_related("dataset").get(
                id=experiment_obj.id
            )
        except ExperimentsTable.DoesNotExist:
            return candidate_experiments_result(
                context,
                "Experiment Not Found",
                f"Experiment `{experiment_obj.id}` was not found. Use one of these experiments.",
            )

        if (
            experiment.dataset
            and experiment.dataset.organization_id != context.organization.id
        ):
            return candidate_experiments_result(
                context,
                "Experiment Not Found",
                f"Experiment `{params.experiment_id}` is not available in this workspace.",
            )

        dataset = experiment.dataset
        if not dataset:
            return ToolResult.error(
                "Experiment has no dataset.",
                error_code="VALIDATION_ERROR",
            )

        # Get base dataset columns (non-eval, non-experiment)
        base_columns = (
            Column.objects.filter(
                dataset=dataset,
                deleted=False,
            )
            .exclude(source="evaluation")
            .exclude(source="annotation_label")[:5]
        )

        # Get experiment variant datasets
        variant_datasets = list(experiment.experiments_datasets.all())

        # Get rows
        total_rows = Row.objects.filter(dataset=dataset, deleted=False).count()
        rows = list(
            Row.objects.filter(dataset=dataset, deleted=False).order_by("order")[
                params.offset : params.offset + params.limit
            ]
        )

        if not rows:
            return ToolResult(
                content=section(
                    f"Experiment Data: {experiment.name}",
                    f"_No rows found (offset={params.offset})._",
                ),
                data={"rows": [], "total": total_rows},
            )

        row_ids = [r.id for r in rows]

        # Build headers: base columns + variant outputs
        headers = [col.name for col in base_columns]
        for vds in variant_datasets:
            headers.append(f"{truncate(vds.name, 20)} Output")

        # Get cells for base columns
        base_col_ids = [c.id for c in base_columns]
        base_cells = Cell.objects.filter(
            row_id__in=row_ids, column_id__in=base_col_ids, deleted=False
        )
        base_cell_map = {}  # (row_id, col_id) -> value
        for c in base_cells:
            base_cell_map[(c.row_id, c.column_id)] = (
                truncate(c.value, 80) if c.value else "—"
            )

        # Get cells for variant datasets
        variant_col_map = {}  # variant_ds_id -> column
        for vds in variant_datasets:
            # Variant output columns are in the variant dataset's columns
            vcols = vds.columns.filter(deleted=False).exclude(source="evaluation")[:1]
            if vcols:
                variant_col_map[vds.id] = vcols[0]

        variant_cells = {}  # (row_id, variant_ds_id) -> value
        for vds_id, vcol in variant_col_map.items():
            cells = Cell.objects.filter(row_id__in=row_ids, column=vcol, deleted=False)
            for c in cells:
                variant_cells[(c.row_id, vds_id)] = (
                    truncate(c.value, 80) if c.value else "—"
                )

        # Build table rows
        table_rows = []
        data_rows = []
        for row in rows:
            table_row = []
            data_row = {"row_id": str(row.id), "order": row.order}

            for col in base_columns:
                val = base_cell_map.get((row.id, col.id), "—")
                table_row.append(val)
                data_row[col.name] = val

            for vds in variant_datasets:
                val = variant_cells.get((row.id, vds.id), "—")
                table_row.append(val)
                data_row[f"variant_{vds.name}"] = val

            table_rows.append(table_row)
            data_rows.append(data_row)

        table = markdown_table(headers, table_rows)

        info = key_value_block(
            [
                ("Experiment", experiment.name),
                ("Status", experiment.status or "—"),
                (
                    "Showing",
                    f"Rows {params.offset + 1}-{params.offset + len(rows)} of {total_rows}",
                ),
                ("Variants", str(len(variant_datasets))),
                (
                    "Link",
                    dashboard_link(
                        "experiment", str(experiment.id), label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section(f"Experiment Data: {experiment.name}", info)
        content += f"\n\n{table}"

        if total_rows > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more rows._"
            )

        return ToolResult(
            content=content,
            data={"rows": data_rows, "total": total_rows},
        )
