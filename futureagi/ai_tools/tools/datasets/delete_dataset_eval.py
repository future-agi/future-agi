from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.datasets._utils import resolve_dataset_eval, resolve_dataset_for_tool


class DeleteDatasetEvalInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset UUID or exact dataset name. Omit to list candidates.",
    )
    eval_id: str = Field(
        default="",
        description="Dataset eval UUID or exact eval/template name. Omit to list candidates.",
    )
    delete_column: bool = Field(
        default=True,
        description=(
            "If true, also deletes the result column and all cell values. "
            "If false, just hides the eval from the sidebar."
        ),
    )
    dry_run: bool = Field(
        default=True,
        description="Preview the delete/hide impact without mutating anything.",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Must be true with dry_run=false to delete or hide the eval.",
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

        from model_hub.models.develop_dataset import Cell, Column
        from model_hub.models.evals_metric import UserEvalMetric

        dataset, unresolved = resolve_dataset_for_tool(
            params.dataset_id,
            context,
            title="Dataset Required For Eval Delete",
        )
        if unresolved:
            return unresolved

        user_eval, unresolved = resolve_dataset_eval(
            dataset,
            params.eval_id,
            title="Dataset Eval Required For Delete",
        )
        if unresolved:
            return unresolved

        eval_name = user_eval.name
        cells_deleted = 0
        columns_deleted = 0

        result_cols = Column.objects.filter(
            dataset=dataset,
            source_id=str(user_eval.id),
            deleted=False,
        )
        result_col_ids = [str(col.id) for col in result_cols]
        reason_cols = Column.objects.filter(
            dataset=dataset,
            deleted=False,
            source_id__regex=(
                "^(" + "|".join(result_col_ids) + ")-sourceid-"
                if result_col_ids
                else r"^$"
            ),
        )
        impacted_cells = (
            Cell.objects.filter(
                column__in=list(result_cols) + list(reason_cols),
                dataset=dataset,
                deleted=False,
            ).count()
            if params.delete_column
            else 0
        )

        if params.dry_run or not params.confirm_delete:
            info = key_value_block(
                [
                    ("Dataset", dataset.name),
                    ("Eval", eval_name),
                    (
                        "Planned Action",
                        "Delete eval and result columns"
                        if params.delete_column
                        else "Hide eval from sidebar",
                    ),
                    (
                        "Columns Affected",
                        str(result_cols.count() + reason_cols.count())
                        if params.delete_column
                        else "0",
                    ),
                    ("Cells Affected", str(impacted_cells)),
                    ("Mutation", "Not applied"),
                    (
                        "To Apply",
                        "Call with `dry_run=false` and `confirm_delete=true`.",
                    ),
                ]
            )
            return ToolResult(
                content=section("Dataset Eval Delete Preview", info),
                data={
                    "dataset_id": str(dataset.id),
                    "eval_id": str(user_eval.id),
                    "dry_run": True,
                    "requires_confirmation": True,
                    "columns_affected": result_cols.count() + reason_cols.count(),
                    "cells_affected": impacted_cells,
                },
            )

        if params.delete_column:
            # Find and delete result column + reason columns
            affected_column_ids = []
            for col in result_cols:
                # Delete cells
                cells_deleted += Cell.objects.filter(
                    column=col, dataset=dataset, deleted=False
                ).update(deleted=True)
                affected_column_ids.append(str(col.id))

                # Delete reason columns (source_id contains column ID)
                related_reason_cols = Column.objects.filter(
                    dataset=dataset,
                    source_id__startswith=f"{col.id}-sourceid-",
                    deleted=False,
                )
                deleted_rcs = set()
                for rc in related_reason_cols:
                    cells_deleted += Cell.objects.filter(
                        column=rc, dataset=dataset, deleted=False
                    ).update(deleted=True)
                    deleted_rcs.add(str(rc.id))
                    affected_column_ids.append(str(rc.id))
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
            for column_id in affected_column_ids:
                metrics = UserEvalMetric.get_metrics_using_column(
                    str(dataset.organization.id),
                    column_id,
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
