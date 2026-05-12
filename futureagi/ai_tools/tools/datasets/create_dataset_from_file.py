import base64
import os
import uuid
from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from model_hub.constants import (
    MAX_DATASET_NAME_LENGTH,
    MAX_FILE_SIZE_BYTES,
)


class CreateDatasetFromFileInput(PydanticBaseModel):
    name: str = Field(
        default="",
        description="Name for the new dataset",
        max_length=MAX_DATASET_NAME_LENGTH,
    )
    file_content_base64: str = Field(
        default="",
        description=(
            "Base64-encoded file content. Supported formats: "
            "CSV (.csv), JSON (.json), JSONL (.jsonl), Excel (.xls, .xlsx)."
        ),
    )
    file_name: str = Field(
        default="",
        description=(
            "Original file name with extension (e.g. 'data.csv', 'records.jsonl'). "
            "Used to detect file format."
        ),
    )
    model_type: Optional[str] = Field(
        default=None,
        description="Model type for the dataset (e.g. 'GenerativeLLM')",
    )


@register_tool
class CreateDatasetFromFileTool(BaseTool):
    name = "create_dataset_from_file"
    description = (
        "Creates a dataset by uploading file content (CSV, JSON, JSONL, Excel). "
        "The file is validated, parsed, and processed in the background. "
        "Returns the dataset ID and estimated row/column counts."
    )
    category = "datasets"
    input_model = CreateDatasetFromFileInput

    def execute(
        self, params: CreateDatasetFromFileInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.services.dataset_service import (
            ServiceError,
            create_dataset_from_file,
        )

        if not params.file_content_base64 or not params.file_name:
            content = section(
                "File Dataset Import Requirements",
                (
                    "Provide `file_content_base64` and `file_name` to import a local "
                    "file. If the file is not available to Falcon, create the dataset "
                    "with `create_dataset` and add rows with `add_dataset_rows`."
                ),
            )
            return ToolResult(
                content=content,
                data={
                    "requires_file_content_base64": not bool(
                        params.file_content_base64
                    ),
                    "requires_file_name": not bool(params.file_name),
                    "fallback_tools": ["create_dataset", "add_dataset_rows"],
                },
            )

        # Decode base64 content
        try:
            file_content = base64.b64decode(params.file_content_base64)
        except Exception:
            return ToolResult(
                content=section(
                    "File Dataset Import Requirements",
                    (
                        "`file_content_base64` must be valid base64. Ask the user for "
                        "the file content, or create the dataset manually with "
                        "`create_dataset` and `add_dataset_rows` if the data is already "
                        "present in the chat."
                    ),
                ),
                data={
                    "status": "requires_valid_base64",
                    "fallback_tools": ["create_dataset", "add_dataset_rows"],
                },
            )

        # Validate decoded size
        if len(file_content) > MAX_FILE_SIZE_BYTES:
            return ToolResult.error(
                f"File size ({len(file_content)} bytes) exceeds the maximum "
                f"allowed limit of {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB.",
                error_code="VALIDATION_ERROR",
            )

        dataset_name = params.name.strip()
        if not dataset_name:
            base_name = os.path.splitext(params.file_name)[0].strip() or "dataset"
            dataset_name = f"{base_name[:40]}_{uuid.uuid4().hex[:8]}"

        local_storage_markers = (
            "s3",
            "minio",
            "invalidaccesskeyid",
            "access key",
            "storage",
            "upload",
        )

        try:
            result = create_dataset_from_file(
                file_content=file_content,
                file_name=params.file_name,
                name=dataset_name,
                organization=context.organization,
                workspace=context.workspace,
                user=context.user,
                model_type=params.model_type,
            )
        except Exception as exc:
            from ai_tools.error_codes import code_from_exception

            message = str(exc)
            is_local_storage_issue = any(
                marker in message.lower() for marker in local_storage_markers
            )
            if is_local_storage_issue:
                content = section(
                    "File Dataset Import Not Completed",
                    (
                        f"{message}\n\n"
                        "This local Docker environment cannot upload the file to "
                        "object storage. Create the dataset directly with "
                        "`create_dataset` and then add rows with `add_dataset_rows`."
                    ),
                )
                return ToolResult(
                    content=content,
                    data={
                        "status": "local_storage_unavailable",
                        "message": message,
                        "fallback_tools": ["create_dataset", "add_dataset_rows"],
                    },
                )
            return ToolResult.error(message, error_code=code_from_exception(exc))

        if isinstance(result, ServiceError):
            recoverable_codes = {"VALIDATION_ERROR", "NOT_FOUND"}
            message = result.message or ""
            if result.code == "DUPLICATE_NAME":
                from model_hub.models.develop_dataset import Dataset

                existing = (
                    Dataset.objects.filter(
                        name=dataset_name,
                        organization=context.organization,
                        deleted=False,
                    )
                    .order_by("-created_at")
                    .first()
                )
                content = section(
                    "Dataset Already Exists",
                    (
                        f"A dataset named `{dataset_name}` already exists. "
                        "Use the existing dataset, choose a new name, or delete the "
                        "test dataset if this was a cleanup workflow."
                    ),
                )
                data = {
                    "status": "duplicate_name",
                    "name": dataset_name,
                    "message": message,
                }
                if existing:
                    data.update(
                        {
                            "dataset_id": str(existing.id),
                            "dataset_name": existing.name,
                        }
                    )
                    content += "\n\n" + key_value_block(
                        [
                            ("Dataset ID", f"`{existing.id}`"),
                            ("Name", existing.name),
                            (
                                "Link",
                                dashboard_link(
                                    "dataset",
                                    existing.id,
                                    label="View Existing Dataset",
                                ),
                            ),
                        ]
                    )
                return ToolResult(content=content, data=data)

            is_local_storage_issue = any(
                marker in message.lower() for marker in local_storage_markers
            )
            if result.code in recoverable_codes or is_local_storage_issue:
                content = section(
                    "File Dataset Import Not Completed",
                    (
                        f"{message}\n\n"
                        "If this is a local Docker environment without object storage, "
                        "create the dataset directly with `create_dataset` and then add "
                        "rows with `add_dataset_rows`."
                    ),
                )
                return ToolResult(
                    content=content,
                    data={
                        "status": "import_not_completed",
                        "error_code": result.code,
                        "message": message,
                        "fallback_tools": ["create_dataset", "add_dataset_rows"],
                    },
                )
            return ToolResult.error(result.message, error_code=result.code)

        info = key_value_block(
            [
                ("Dataset ID", f"`{result['dataset_id']}`"),
                ("Name", result["name"]),
                ("Estimated Rows", str(result["estimated_rows"])),
                ("Estimated Columns", str(result["estimated_columns"])),
                ("Status", result["processing_status"]),
                (
                    "Link",
                    dashboard_link(
                        "dataset", result["dataset_id"], label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section("Dataset Created from File", info)
        content += (
            "\n\n_File is being processed in the background. "
            "Rows will appear shortly._"
        )

        return ToolResult(content=content, data=result)
