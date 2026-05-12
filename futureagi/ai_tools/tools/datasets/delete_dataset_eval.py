from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class DeleteDatasetEvalInput(PydanticBaseModel):
    dataset_id: UUID = Field(description="The UUID of the dataset")
    eval_id: UUID = Field(description="The UUID of the UserEvalMetric to delete")
    delete_column: bool = Field(
        default=True,
        description=(
            "If true, also deletes the result column and all cell values. "
            "If false, just hides the eval from the sidebar."
        ),
    )


@register_tool
class DeleteDatasetEvalTool(BaseTool):
    name = "delete_dataset_eval"
    description = (
        "Deletes an evaluation metric from a dataset. "
        "Can optionally delete the associated result column and cell values, "
        "or just hide the eval from the sidebar."
    )
    category = "datasets"
    input_model = DeleteDatasetEvalInput

    def execute(
        self, params: DeleteDatasetEvalInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_dataset import Cell, Column, Dataset
        from model_hub.models.evals_metric import UserEvalMetric

        try:
            dataset = Dataset.objects.get(
                id=params.dataset_id, deleted=False, organization=context.organization
            )
        except Dataset.DoesNotExist:
            return ToolResult.not_found("Dataset", str(params.dataset_id))

        try:
            user_eval = UserEvalMetric.objects.get(
                id=params.eval_id, dataset=dataset, deleted=False
            )
        except UserEvalMetric.DoesNotExist:
            return ToolResult.not_found("DatasetEval", str(params.eval_id))

        eval_name = user_eval.name
        cells_deleted = 0
        columns_deleted = 0

        if params.delete_column:
            # Find and delete result column + reason columns
            result_cols = Column.objects.filter(
                dataset=dataset,
                source_id=str(user_eval.id),
                deleted=False,
            )

            for col in result_cols:
                # Delete cells
                cells_deleted += Cell.objects.filter(
                    column=col, dataset=dataset, deleted=False
                ).update(deleted=True)

                # Delete reason columns (source_id contains column ID)
                reason_cols = Column.objects.filter(
                    dataset=dataset,
                    source_id__startswith=f"{col.id}-sourceid-",
                    deleted=False,
                )
                deleted_rcs = set()
                for rc in reason_cols:
                    cells_deleted += Cell.objects.filter(
                        column=rc, dataset=dataset, deleted=False
                    ).update(deleted=True)
                    deleted_rcs.add(str(rc.id))
                    rc.deleted = True
                    rc.save(update_fields=["deleted"])
                    columns_deleted += 1

                # Remove from column order
                col_id_str = str(col.id)
                if dataset.column_order and col_id_str in dataset.column_order:
                    dataset.column_order = [
                        c
                        for c in dataset.column_order
                        if c != col_id_str and c not in deleted_rcs
                    ]
                    dataset.save(update_fields=["column_order"])

                # Remove from column config
                dataset.column_config = {
                    k: v
                    for k, v in dataset.column_config.items()
                    if k not in deleted_rcs and k != col_id_str
                }
                dataset.save(update_fields=["column_config"])

                col.deleted = True
                col.save(update_fields=["deleted"])
                columns_deleted += 1

            user_eval.deleted = True
            user_eval.save(update_fields=["deleted"])

            # Update metrics for all affected columns
            metrics = UserEvalMetric.get_metrics_using_column(
                str(dataset.organization.id),
                str(col.id),
            )
            for metric in metrics:
                metric.column_deleted = True
                metric.save()
        else:
            user_eval.show_in_sidebar = False
            user_eval.save(update_fields=["show_in_sidebar"])

        info = key_value_block(
            [
                ("Eval", eval_name),
                (
                    "Action",
                    "Deleted" if params.delete_column else "Hidden from sidebar",
                ),
                (
                    "Columns Removed",
                    str(columns_deleted) if params.delete_column else "—",
                ),
                ("Cells Removed", str(cells_deleted) if params.delete_column else "—"),
            ]
        )

        return ToolResult(
            content=section("Dataset Eval Removed", info),
            data={
                "eval_name": eval_name,
                "deleted": params.delete_column,
                "columns_deleted": columns_deleted,
                "cells_deleted": cells_deleted,
            },
        )
