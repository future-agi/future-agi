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
    name: str | None = Field(
        default=None,
        description="Name for the label",
        min_length=1,
        max_length=255,
    )
    label_type: str = Field(
        default="categorical",
        description=("Type of label: text, numeric, categorical, star, thumbs_up_down"),
    )
    description: str | None = Field(
        default=None, description="Optional description of this label"
    )
    project_id: UUID | None = Field(
        default=None,
        description="Optional project UUID to scope this label to a specific project",
    )
    settings: dict | None = Field(
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

        label_name = (params.name or "").strip()
        if not label_name:
            return ToolResult(
                content=section(
                    "Annotation Label Details Required",
                    (
                        "Provide at least `name` before creating an annotation label. "
                        "`label_type` defaults to `categorical`; include `settings` "
                        "for numeric, star, or custom categorical labels."
                    ),
                ),
                data={
                    "requires_name": True,
                    "required_fields": ["name"],
                    "optional_fields": [
                        "label_type",
                        "description",
                        "project_id",
                        "settings",
                    ],
                },
            )

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
            "name": label_name,
            "type": params.label_type,
            "organization": context.organization,
            "workspace": context.workspace,
        }
        if project:
            duplicate_filter["project"] = project
        else:
            duplicate_filter["project__isnull"] = True

        existing_label = AnnotationsLabels.objects.filter(**duplicate_filter).first()
        if existing_label:
            content = section(
                "Annotation Label Already Exists",
                key_value_block(
                    [
                        ("ID", f"`{existing_label.id}`"),
                        ("Name", existing_label.name),
                        ("Type", existing_label.type),
                        (
                            "Project",
                            f"`{project.id}` ({project.name})" if project else "—",
                        ),
                    ]
                ),
            )
            return ToolResult(
                content=content,
                data={
                    "label_id": str(existing_label.id),
                    "name": existing_label.name,
                    "type": existing_label.type,
                    "project_id": str(project.id) if project else None,
                    "already_exists": True,
                },
            )

        # Validate settings per label type (matches API serializer validation).
        # Falcon frequently supplies only categorical options. Fill the serializer's
        # non-semantic defaults here so the first tool call succeeds.
        settings = _normalize_label_settings(params.label_type, params.settings or {})
        settings_error = _validate_label_settings(params.label_type, settings)
        if settings_error:
            return ToolResult.error(settings_error, error_code="VALIDATION_ERROR")

        label = AnnotationsLabels(
            name=label_name,
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


def _normalize_label_settings(label_type: str, settings: dict) -> dict:
    normalized = dict(settings)
    if label_type == "star":
        normalized.setdefault("no_of_stars", 5)
        return normalized
    if label_type == "numeric":
        normalized.setdefault("min", 0)
        normalized.setdefault("max", 10)
        normalized.setdefault("step_size", 1)
        normalized.setdefault("display_type", "slider")
        return normalized
    if label_type != "categorical":
        return normalized
    normalized.setdefault("options", [{"label": "Yes"}, {"label": "No"}])
    normalized.setdefault("multi_choice", False)
    normalized.setdefault("auto_annotate", False)
    normalized.setdefault("rule_prompt", "")
    normalized.setdefault("strategy", None)
    return normalized


def _validate_label_settings(label_type: str, settings: dict) -> str | None:
    """Validate required settings per label type, matching API serializer behavior."""
    if label_type == "numeric":
        if "min" not in settings or "max" not in settings:
            return "Numeric labels require 'min' and 'max' in settings."
        try:
            min_val = float(settings["min"])
            max_val = float(settings["max"])
        except (TypeError, ValueError):
            return "'min' and 'max' must be numbers."
        if min_val >= max_val:
            return "'min' must be less than 'max'."

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
        if "options" not in settings:
            return "Categorical labels require 'options' in settings (e.g., {options: [{label: 'Good'}, {label: 'Bad'}]})."
        options = settings["options"]
        if not isinstance(options, list) or len(options) == 0:
            return "'options' must be a non-empty list of objects with 'label' key."
        for opt in options:
            if not isinstance(opt, dict) or "label" not in opt:
                return "Each option must be a dict with a 'label' key."

    # text and thumbs_up_down have no required settings
    return None
