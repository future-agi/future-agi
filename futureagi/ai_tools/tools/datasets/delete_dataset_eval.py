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
            # Stop any in-flight eval runner before deleting columns
            from model_hub.models.choices import StatusType

            if user_eval.status in (
                StatusType.RUNNING.value,
                StatusType.NOT_STARTED.value,
                StatusType.EXPERIMENT_EVALUATION.value,
            ):
                try:
                    from tfc.utils.distributed_state import evaluation_tracker

                    evaluation_tracker.request_cancel(
                        user_eval.id, reason="eval_deleted"
                    )
                except Exception:
                    pass
                from model_hub.utils.eval_cell_status import mark_eval_cells_stopped

                mark_eval_cells_stopped(
                    user_eval, reason="Evaluation deleted by user"
                )

            # Find eval columns + all dependent reason columns
            from django.db.models import Q

            all_cols = list(
                Column.objects.filter(
                    Q(source_id=str(user_eval.id))
                    | Q(source_id__endswith=f"-sourceid-{user_eval.id}"),
                    dataset=dataset,
                    deleted=False,
                )
            )
            col_ids = [c.id for c in all_cols]
            col_id_strs = {str(c) for c in col_ids}

            if col_ids:
                # Bulk delete cells
                cells_deleted = Cell.objects.filter(
                    column_id__in=col_ids, deleted=False
                ).update(deleted=True)

                # Update dependent metrics BEFORE deleting columns —
                # get_metrics_using_column scopes by dataset via the
                # Column row, which must still be visible (deleted=False).
                eval_col_ids = [
                    str(c.id) for c in all_cols
                    if c.source_id == str(user_eval.id)
                ]
                for ecid in eval_col_ids:
                    metrics = UserEvalMetric.get_metrics_using_column(
                        str(dataset.organization.id), ecid,
                    )
                    if metrics:
                        UserEvalMetric.objects.filter(
                            id__in=[m.id for m in metrics]
                        ).update(column_deleted=True)

                # Now safe to delete columns
                columns_deleted = Column.objects.filter(
                    id__in=col_ids
                ).update(deleted=True)

                # Update column_order and column_config
                new_order = [
                    c for c in (dataset.column_order or [])
                    if c not in col_id_strs
                ]
                new_config = {
                    k: v
                    for k, v in (dataset.column_config or {}).items()
                    if k not in col_id_strs
                }
                Dataset.objects.filter(id=dataset.id).update(
                    column_order=new_order, column_config=new_config
                )

            user_eval.deleted = True
            user_eval.save(update_fields=["deleted"])
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
