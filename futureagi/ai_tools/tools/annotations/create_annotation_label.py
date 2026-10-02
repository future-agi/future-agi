from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool

VALID_LABEL_TYPES = {"text", "numeric", "categorical", "star", "thumbs_up_down"}


class CreateAnnotationLabelInput(PydanticBaseModel):
    name: str = Field(description="Name for the label", min_length=1, max_length=255)
    label_type: str = Field(
        description=("Type of label: text, numeric, categorical, star, thumbs_up_down")
    )
    description: Optional[str] = Field(
        default=None, description="Optional description of this label"
    )
    project_id: Optional[UUID] = Field(
        default=None,
        description="Optional project UUID to scope this label to a specific project",
    )
    settings: Optional[dict] = Field(
        default=None,
        description=(
            "Type-specific settings (required for all types except thumbs_up_down and text). "
            "star: {no_of_stars: 5}. "
            "numeric: {min: 0, max: 10, step_size: 1, display_type: 'slider'|'button'}. "
            "categorical: {options: [{label: 'Good'}, {label: 'Bad'}], multi_choice: false, "
            "auto_annotate: false, rule_prompt: '', strategy: null}. "
            "All 5 categorical fields are required. strategy must be 'Rag' or null. "
            "text: {placeholder, max_length, min_length} (optional)."
        ),
    )


@register_tool
class CreateAnnotationLabelTool(BaseTool):
    name = "create_annotation_label"
    description = (
        "Creates a reusable annotation label. Labels define how annotators "
        "rate or classify dataset rows. Supports 5 types: text (free text), "
        "numeric (range with slider/buttons), categorical (multiple choice), "
        "star (1-N star rating), thumbs_up_down."
    )
    category = "annotations"
    input_model = CreateAnnotationLabelInput

    def execute(
        self, params: CreateAnnotationLabelInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_annotations import AnnotationsLabels

        if params.label_type not in VALID_LABEL_TYPES:
            return ToolResult.error(
                f"Invalid label type '{params.label_type}'. "
                f"Valid types: {', '.join(sorted(VALID_LABEL_TYPES))}",
                error_code="VALIDATION_ERROR",
            )

        # Validate project if provided
        project = None
        if params.project_id:
            from tracer.models.project import Project

            try:
                project = Project.objects.get(
                    id=params.project_id,
                    organization=context.organization,
                )
            except Project.DoesNotExist:
                return ToolResult.not_found("Project", str(params.project_id))

        # Check for duplicate name+type (scoped to project if provided)
        duplicate_filter = {
            "name": params.name,
            "type": params.label_type,
            "organization": context.organization,
            "workspace": context.workspace,
        }
        if project:
            duplicate_filter["project"] = project
        else:
            duplicate_filter["project__isnull"] = True

        if AnnotationsLabels.objects.filter(**duplicate_filter).exists():
            return ToolResult.error(
                f"A label named '{params.name}' with type '{params.label_type}' "
                "already exists in this workspace"
                + (f" for project '{project.name}'." if project else "."),
                error_code="VALIDATION_ERROR",
            )

        # Validate settings per label type (matches API serializer validation)
        settings = params.settings or {}
        settings_error = _validate_label_settings(params.label_type, settings)
        if settings_error:
            return ToolResult.error(settings_error, error_code="VALIDATION_ERROR")

        label = AnnotationsLabels(
            name=params.name,
            type=params.label_type,
            description=params.description or "",
            settings=settings,
            organization=context.organization,
            workspace=context.workspace,
            project=project,
        )
        label.save()

        info = key_value_block(
            [
                ("ID", f"`{label.id}`"),
                ("Name", label.name),
                ("Type", label.type),
                ("Description", label.description or "—"),
                ("Project", f"`{project.id}` ({project.name})" if project else "—"),
                ("Settings", str(label.settings) if label.settings else "—"),
            ]
        )

        content = section("Annotation Label Created", info)

        return ToolResult(
            content=content,
            data={
                "label_id": str(label.id),
                "name": label.name,
                "type": label.type,
                "project_id": str(project.id) if project else None,
            },
        )


def _validate_label_settings(label_type: str, settings: dict) -> str | None:
    """Validate required settings per label type, matching API serializer behavior."""
    if label_type == "numeric":
        required_fields = {"min", "max", "step_size", "display_type"}
        missing_fields = sorted(required_fields - set(settings))
        if missing_fields:
            return (
                "Numeric labels require 'min', 'max', 'step_size', and "
                f"'display_type' in settings. Missing: {', '.join(missing_fields)}."
            )
        try:
            min_val = float(settings["min"])
            max_val = float(settings["max"])
            step_size = float(settings["step_size"])
        except (TypeError, ValueError):
            return "'min', 'max', and 'step_size' must be numbers."
        if min_val >= max_val:
            return "'min' must be less than 'max'."
        if step_size <= 0:
            return "'step_size' must be greater than 0."
        if settings["display_type"] not in {"slider", "button"}:
            return "'display_type' must be either 'slider' or 'button'."

    elif label_type == "star":
        if "no_of_stars" not in settings:
            return "Star labels require 'no_of_stars' in settings (e.g., {no_of_stars: 5})."
        try:
            stars = int(settings["no_of_stars"])
        except (TypeError, ValueError):
            return "'no_of_stars' must be an integer."
        if stars < 1:
            return "'no_of_stars' must be at least 1."

    elif label_type == "categorical":
        required_fields = {
            "rule_prompt",
            "multi_choice",
            "options",
            "auto_annotate",
            "strategy",
        }
        missing_fields = sorted(required_fields - set(settings))
        if missing_fields:
            return (
                "Categorical labels require 'options', 'multi_choice', "
                "'rule_prompt', 'auto_annotate', and 'strategy' in settings. "
                f"Missing: {', '.join(missing_fields)}."
            )
        options = settings["options"]
        if not isinstance(options, list) or len(options) < 2:
            return "'options' must contain at least two objects with a 'label' key."
        for opt in options:
            if not isinstance(opt, dict) or "label" not in opt:
                return "Each option must be a dict with a 'label' key."
        option_labels = [str(opt.get("label") or "").strip().casefold() for opt in options]
        if any(not label for label in option_labels):
            return "Option labels cannot be empty."
        if len(option_labels) != len(set(option_labels)):
            return "Categorical option labels must be unique."
        if not isinstance(settings["multi_choice"], bool):
            return "'multi_choice' must be a boolean."
        if not isinstance(settings["rule_prompt"], str):
            return "'rule_prompt' must be a string."
        if settings["strategy"] not in {"Rag", None}:
            return "'strategy' must be 'Rag' or null."
        if not isinstance(settings["auto_annotate"], bool):
            return "'auto_annotate' must be a boolean."

    # text and thumbs_up_down have no required settings
    return None
