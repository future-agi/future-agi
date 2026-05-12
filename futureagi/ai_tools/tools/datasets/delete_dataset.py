from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import section
from ai_tools.registry import register_tool
from model_hub.constants import MAX_BATCH_DELETE_SIZE


class DeleteDatasetInput(PydanticBaseModel):
    dataset_ids: list[str] = Field(
        default_factory=list,
        description="List of dataset names or UUIDs to delete. Omit to list candidates.",
        max_length=MAX_BATCH_DELETE_SIZE,
    )


@register_tool
class DeleteDatasetTool(BaseTool):
    name = "delete_dataset"
    description = (
        "Soft-deletes one or more datasets. "
        "This marks datasets as deleted; they will no longer appear in listings. "
        "Call without IDs to list candidate datasets first."
    )
    category = "datasets"
    input_model = DeleteDatasetInput

    def execute(self, params: DeleteDatasetInput, context: ToolContext) -> ToolResult:

        from ai_tools.tools.datasets._utils import candidate_datasets_result
        from ai_tools.tools.datasets._utils import resolve_dataset_for_tool
        from model_hub.services.dataset_service import ServiceError
        from model_hub.services.dataset_service import delete_datasets as svc_delete

        if not params.dataset_ids:
            return candidate_datasets_result(
                context,
                "Dataset Required For Delete",
                "Choose one or more dataset IDs to delete.",
            )

        # Resolve each identifier to a dataset UUID
        resolved_ids = []
        for identifier in params.dataset_ids:
            ds, dataset_result = resolve_dataset_for_tool(
                identifier, context, "Dataset Required For Delete"
            )
            if dataset_result:
                return dataset_result
            resolved_ids.append(str(ds.id))

        result = svc_delete(
            dataset_ids=resolved_ids,
            organization=context.organization,
        )

        if isinstance(result, ServiceError):
            return ToolResult.error(result.message, error_code=result.code)

        lines = [f"**Deleted {result['deleted']} dataset(s):**"]
        for name in result["names"]:
            lines.append(f"- {name}")

        return ToolResult(
            content=section("Datasets Deleted", "\n".join(lines)),
            data={"deleted": result["deleted"], "names": result["names"]},
        )
