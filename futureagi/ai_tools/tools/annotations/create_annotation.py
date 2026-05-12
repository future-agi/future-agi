from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotations._utils import (
    annotation_creation_requirements_result,
    resolve_dataset,
)
from ai_tools.tools.annotation_queues._utils import resolve_labels


class CreateAnnotationInput(PydanticBaseModel):
    name: Optional[str] = Field(
        default=None,
        description="Name for the annotation task", min_length=1, max_length=255
    )
    dataset_id: Optional[str] = Field(
        default=None,
        description="The UUID or exact name of the dataset to annotate",
    )
    label_ids: Optional[list[str]] = Field(
        default=None,
        description="Annotation label UUIDs or exact label names to use in this task",
    )
    responses: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Number of annotators required per row",
    )


@register_tool
class CreateAnnotationTool(BaseTool):
    name = "create_annotation"
    description = (
        "Creates a new annotation task for a dataset. "
        "Requires a dataset and at least one annotation label. "
        "Users can be assigned later via the dashboard."
    )
    category = "annotations"
    input_model = CreateAnnotationInput

    def execute(
        self, params: CreateAnnotationInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_annotations import Annotations
        from model_hub.services.annotation_service import process_annotation_columns

        if not params.name or not params.dataset_id or not params.label_ids:
            return annotation_creation_requirements_result(
                context,
                detail=(
                    "`create_annotation` can create the task after these fields are "
                    "provided."
                ),
            )

        dataset, unresolved_dataset = resolve_dataset(
            params.dataset_id,
            context,
            title="Dataset Required For Annotation",
        )
        if unresolved_dataset:
            return unresolved_dataset

        labels, missing = resolve_labels(params.label_ids, context)
        if missing or not labels:
            detail = (
                f"Could not resolve these label refs: `{', '.join(missing)}`."
                if missing
                else "At least one label is required."
            )
            return annotation_creation_requirements_result(
                context,
                "Annotation Labels Required",
                detail,
            )

        # Create annotation task
        annotation = Annotations(
            name=params.name.strip(),
            dataset=dataset,
            responses=params.responses,
            organization=context.organization,
            workspace=context.workspace,
        )
        annotation.save()

        # Add labels
        annotation.labels.set(labels)

        # Create columns via shared service (matches view's process_new_annotaion)
        columns_created = process_annotation_columns(annotation, labels)

        info = key_value_block(
            [
                ("ID", f"`{annotation.id}`"),
                ("Name", annotation.name),
                ("Dataset", dataset.name),
                ("Labels", str(len(labels))),
                ("Responses Required", str(params.responses)),
                ("Columns Created", str(columns_created)),
            ]
        )

        content = section("Annotation Task Created", info)
        content += "\n\n_Assign users via the dashboard to start annotation._"

        return ToolResult(
            content=content,
            data={
                "annotation_id": str(annotation.id),
                "name": annotation.name,
                "dataset_id": str(dataset.id),
                "label_count": len(labels),
                "columns_created": columns_created,
            },
        )
