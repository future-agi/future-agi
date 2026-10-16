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


class CreateProjectInput(PydanticBaseModel):
    name: str = Field(description="Name of the project")
    trace_type: str = Field(
        default="observe",
        description="Project type: 'experiment' or 'observe'",
    )
    model_type: str = Field(
        default="GenerativeLLM",
        description=(
            "Model type for the project. Valid options: "
            "GenerativeLLM, GenerativeImage, GenerativeVideo, MultiModal, "
            "TTS, STT, BinaryClassification, Regression, ObjectDetection, "
            "Segmentation, Numeric, ScoreCategorical, Ranking"
        ),
    )
    description: Optional[str] = Field(
        default=None, description="Optional description stored in metadata"
    )


@register_tool
class CreateProjectTool(BaseTool):
    name = "create_project"
    description = (
        "Creates a new tracing project. Projects are containers for traces "
        "and observations. Specify the name, type (experiment/observe), "
        "and model type."
    )
    category = "tracing"
    input_model = CreateProjectInput

    def execute(self, params: CreateProjectInput, context: ToolContext) -> ToolResult:

        from model_hub.models.ai_model import AIModel
        from tracer.models.project import Project
        from tracer.services.simulation_project import (
            RESERVED_PROJECT_NAME_MESSAGE,
            agent_id_in_simulation_project_name,
        )

        if agent_id_in_simulation_project_name(params.name) is not None:
            return ToolResult.error(
                RESERVED_PROJECT_NAME_MESSAGE, error_code="VALIDATION_ERROR"
            )

        # Validate model_type
        valid_model_types = [choice.value for choice in AIModel.ModelTypes]
        if params.model_type not in valid_model_types:
            return ToolResult.error(
                f"Invalid model_type '{params.model_type}'. Valid options: {', '.join(valid_model_types)}",
                error_code="VALIDATION_ERROR",
            )

        # Validate trace_type
        valid_types = ["experiment", "observe"]
        if params.trace_type not in valid_types:
            return ToolResult.error(
                f"Invalid trace_type '{params.trace_type}'. Valid options: {', '.join(valid_types)}",
                error_code="VALIDATION_ERROR",
            )

        # Check for duplicate name within the same workspace
        # (BaseModelManager auto-filters by workspace and deleted=False)
        existing = Project.objects.filter(
            name=params.name,
            trace_type=params.trace_type,
            organization=context.organization,
        ).first()
        if existing:
            return ToolResult.error(
                f"A project named '{params.name}' with type '{params.trace_type}' already exists "
                f"in this workspace (ID: {existing.id}). Use a different name or update the existing project.",
                error_code="VALIDATION_ERROR",
            )

        metadata = {}
        if params.description:
            metadata["description"] = params.description

        from tracer.utils.helper import get_default_project_version_config

        project = Project(
            name=params.name,
            trace_type=params.trace_type,
            model_type=params.model_type,
            metadata=metadata if metadata else None,
            organization=context.organization,
            workspace=context.workspace,
            user=context.user,
            config=get_default_project_version_config(),
        )
        project.save()

        info = key_value_block(
            [
                ("Project ID", f"`{project.id}`"),
                ("Name", project.name),
                ("Type", project.trace_type),
                ("Model Type", project.model_type),
                ("Description", params.description or "—"),
                (
                    "Link",
                    dashboard_link(
                        "project", str(project.id), label="View in Dashboard"
                    ),
                ),
            ]
        )

        content = section("Project Created", info)

        return ToolResult(
            content=content,
            data={
                "project_id": str(project.id),
                "name": project.name,
                "trace_type": project.trace_type,
            },
        )
