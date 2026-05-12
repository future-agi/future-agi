from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.datasets._utils import (
    candidate_dataset_evals_result,
    resolve_dataset_evals,
    resolve_dataset_for_tool,
)


class RunDatasetEvalsInput(PydanticBaseModel):
    dataset_id: str = Field(
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'"
    )
    eval_ids: list[str] = Field(
        default_factory=list,
        description="List of configured dataset eval names or UserEvalMetric UUIDs to run",
        max_length=20,
    )


@register_tool
class RunDatasetEvalsTool(BaseTool):
    name = "run_dataset_evals"
    description = (
        "Starts running one or more configured evaluations on all rows of a dataset. "
        "The evals must already be configured via add_dataset_eval. "
        "Processing happens asynchronously — use list_dataset_evals to check status."
    )
    category = "datasets"
    input_model = RunDatasetEvalsInput

    def execute(self, params: RunDatasetEvalsInput, context: ToolContext) -> ToolResult:

        from model_hub.models.choices import (
            CellStatus,
            DataTypeChoices,
            SourceChoices,
            StatusType,
        )
        from model_hub.models.develop_dataset import Cell, Column
        from model_hub.models.evals_metric import UserEvalMetric

        dataset, unresolved = resolve_dataset_for_tool(
            params.dataset_id,
            context,
            title="Dataset Required For Running Evals",
        )
        if unresolved:
            return unresolved

        if not params.eval_ids:
            return candidate_dataset_evals_result(
                dataset,
                title="Dataset Evals Required To Run",
                detail="Provide `eval_ids` using one or more configured eval IDs or names.",
            )

        evals, missing = resolve_dataset_evals(dataset, params.eval_ids)

        if not evals:
            return candidate_dataset_evals_result(
                dataset,
                title="Dataset Evals Not Found",
                detail=(
                    "No matching evals found for: "
                    + ", ".join(f"`{item}`" for item in missing)
                    + ". Use one of these configured eval IDs or names."
                ),
            )

        # Check for column_deleted
        for em in evals:
            if em.column_deleted:
                return ToolResult.error(
                    f"Column for eval '{em.name}' has been deleted. Cannot run.",
                    error_code="VALIDATION_ERROR",
                )

        # Always ensure reason_column flag is set in eval config (eval runner
        # checks this to decide whether to create reason cells)
        for em in evals:
            if not em.config.get("reason_column"):
                em.config["reason_column"] = True
                em.save(update_fields=["config"])

        # Update status for all evals (matches StartEvalsProcess)
        eval_ids = [str(e.id) for e in evals]
        UserEvalMetric.objects.filter(
            id__in=eval_ids,
            dataset=dataset,
            deleted=False,
        ).update(status=StatusType.NOT_STARTED.value)

        # Update existing cells to RUNNING status
        Cell.objects.filter(column__source_id__in=eval_ids, deleted=False).update(
            status=CellStatus.RUNNING.value
        )

        # Ensure output + reason columns exist (matches StartEvalsProcess)
        existing_columns = {
            str(col.source_id): col
            for col in Column.objects.filter(
                dataset=dataset, source=SourceChoices.EVALUATION.value
            )
        }

        column_order = dataset.column_order or []
        queued = []

        for em in evals:
            source_id = str(em.id)
            column = existing_columns.get(source_id)

            if not column:
                output_type = "boolean"
                if em.template and em.template.config:
                    output_map = {
                        "reason": "text",
                        "score": "float",
                        "choices": "array",
                        "Pass/Fail": "boolean",
                    }
                    output_type = output_map.get(
                        em.template.config.get("output", ""), "boolean"
                    )

                column = Column.objects.create(
                    source_id=source_id,
                    name=em.name,
                    data_type=output_type,
                    source=SourceChoices.EVALUATION.value,
                    dataset=dataset,
                )

                # Create reason column (matches StartEvalsProcess)
                reason_source_id = f"{column.id}-sourceid-{em.id}"
                reason_column = Column.objects.create(
                    name=f"{em.name}-reason",
                    data_type=DataTypeChoices.TEXT.value,
                    source=SourceChoices.EVALUATION_REASON.value,
                    dataset=dataset,
                    source_id=reason_source_id,
                )

                column_order.extend([str(column.id), str(reason_column.id)])

                # Update column_config for visibility
                config = dataset.column_config or {}
                config[str(column.id)] = {"is_visible": True, "is_frozen": None}
                config[str(reason_column.id)] = {"is_visible": True, "is_frozen": None}
                dataset.column_config = config
            else:
                # Reconcile: ensure reason column exists and is in column_order
                # (matches StartEvalsProcess reconciliation logic)
                reason_col = Column.objects.filter(
                    dataset=dataset,
                    source=SourceChoices.EVALUATION_REASON.value,
                    source_id__startswith=f"{column.id}-sourceid-",
                    deleted=False,
                ).first()
                if not reason_col:
                    # Reason column doesn't exist — create it
                    reason_source_id = f"{column.id}-sourceid-{em.id}"
                    reason_col = Column.objects.create(
                        name=f"{em.name}-reason",
                        data_type=DataTypeChoices.TEXT.value,
                        source=SourceChoices.EVALUATION_REASON.value,
                        dataset=dataset,
                        source_id=reason_source_id,
                    )
                    config = dataset.column_config or {}
                    config[str(reason_col.id)] = {"is_visible": True, "is_frozen": None}
                    dataset.column_config = config
                if reason_col and str(reason_col.id) not in column_order:
                    if str(column.id) in column_order:
                        eval_idx = column_order.index(str(column.id))
                        column_order.insert(eval_idx + 1, str(reason_col.id))
                    else:
                        column_order.append(str(reason_col.id))

            # Set cells to RUNNING for this eval
            Cell.objects.filter(column__source_id=source_id, deleted=False).update(
                status=CellStatus.RUNNING.value
            )

            queued.append(em.name)

        # Save updated column order if changed
        if column_order != (dataset.column_order or []):
            dataset.column_order = column_order
            dataset.save(update_fields=["column_order", "column_config"])

        info = key_value_block(
            [
                ("Dataset", dataset.name),
                ("Evals Queued", str(len(queued))),
                ("Eval Names", ", ".join(queued) if queued else "—"),
                ("Skipped", str(len(missing)) if missing else "None"),
                (
                    "Link",
                    dashboard_link(
                        "dataset", str(dataset.id), label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section("Evals Started", info)
        content += "\n\n_Evaluations are running asynchronously. Use `list_dataset_evals` to check progress._"

        return ToolResult(
            content=content,
            data={"queued": queued, "missing": missing},
        )
