from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from model_hub.constants import MAX_DATASET_NAME_LENGTH


class UpdateDatasetInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. Examples: 'my-qa-dataset' or '550e8400-e29b-41d4-a716-446655440000'",
    )
    name: str | None = Field(
        default=None,
        description="New name for the dataset",
        min_length=1,
        max_length=MAX_DATASET_NAME_LENGTH,
    )
    model_type: str | None = Field(
        default=None,
        description="New model type for the dataset",
    )


@register_tool
class UpdateDatasetTool(BaseTool):
    name = "update_dataset"
    description = (
        "Updates a dataset's name or model type. Provide at least one field to update."
    )
    category = "datasets"
    input_model = UpdateDatasetInput

    def execute(self, params: UpdateDatasetInput, context: ToolContext) -> ToolResult:

        from ai_tools.resolvers import resolve_dataset
        from ai_tools.tools.datasets._utils import candidate_datasets_result
        from model_hub.services.dataset_service import ServiceError
        from model_hub.services.dataset_service import update_dataset as svc_update

        if not params.dataset_id:
            return candidate_datasets_result(
                context,
                "Dataset Required",
                "Provide `dataset_id` plus `name` or `model_type` to update a dataset.",
            )

        if not params.name and not params.model_type:
            return candidate_datasets_result(
                context,
                "Dataset Update Requirements",
                "Provide at least one update field: `name` or `model_type`.",
            )

        dataset, error = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if error:
            return ToolResult.error(error, error_code="NOT_FOUND")

        # Name update via service (handles duplicate check with org scope)
        if params.name:
            result = svc_update(
                dataset_id=str(dataset.id),
                name=params.name,
                organization=context.organization,
            )
            if isinstance(result, ServiceError):
                return ToolResult.error(result.message, error_code=result.code)
            dataset.refresh_from_db()

        changes = []
        if params.name:
            changes.append(f"Name updated to `{params.name}`")
        if params.model_type:
            old = dataset.model_type
            dataset.model_type = params.model_type
            dataset.save(update_fields=["model_type"])
            changes.append(f"Model Type: `{old}` → `{params.model_type}`")

        info = key_value_block(
            [
                ("Dataset ID", f"`{dataset.id}`"),
                ("Name", dataset.name),
                ("Changes", "\n".join(changes)),
                (
                    "Link",
                    dashboard_link(
                        "dataset", str(dataset.id), label="View in Dashboard"
                    ),
                ),
            ]
        )

        return ToolResult(
            content=section("Dataset Updated", info),
            data={"dataset_id": str(dataset.id), "name": dataset.name},
        )
