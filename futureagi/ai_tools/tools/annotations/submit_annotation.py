from typing import Any
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class LabelValue(PydanticBaseModel):
    row_id: UUID = Field(description="Row UUID to annotate")
    label_id: UUID = Field(description="Annotation label UUID")
    column_id: UUID = Field(description="Column UUID for this label/row")
    value: Any = Field(
        description=(
            "Annotation value. Type depends on label: "
            "NUMERIC → float, TEXT → string, "
            "CATEGORICAL → string or list of strings, "
            "STAR → int (1-N), THUMBS_UP_DOWN → 'thumbs_up' or 'thumbs_down'"
        ),
    )
    description: str | None = Field(
        default=None,
        description="Optional annotation description/comment",
    )


class SubmitAnnotationInput(PydanticBaseModel):
    annotation_id: UUID | None = Field(
        default=None,
        description="The UUID of the annotation task. Omit it to list requirements.",
    )
    label_values: list[LabelValue] = Field(
        default_factory=list,
        description="List of annotation values to submit",
        max_length=100,
    )


@register_tool
class SubmitAnnotationTool(BaseTool):
    name = "submit_annotation"
    description = (
        "Submits annotation values for rows in an annotation task. "
        "Use get_annotate_row to get the row data and column_ids first, "
        "then submit values for each label."
    )
    category = "annotations"
    input_model = SubmitAnnotationInput

    def execute(
        self, params: SubmitAnnotationInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_annotations import Annotations
        from model_hub.models.develop_dataset import Cell, Column

        if params.annotation_id is None or not params.label_values:
            annotations = Annotations.objects.select_related("dataset").order_by(
                "-created_at"
            )[:10]
            rows = [
                f"- `{annotation.id}` — {annotation.name}" for annotation in annotations
            ]
            return ToolResult(
                content=section(
                    "Submit Annotation Requirements",
                    (
                        "Provide `annotation_id` and `label_values`. Use `get_annotate_row` "
                        "first to retrieve row_id, label_id, column_id, and allowed values.\n\n"
                        + ("\n".join(rows) if rows else "No annotation tasks found.")
                    ),
                ),
                data={
                    "annotation_id_required": params.annotation_id is None,
                    "label_values_required": not params.label_values,
                },
            )

        try:
            annotation = Annotations.objects.get(id=params.annotation_id)
        except Annotations.DoesNotExist:
            return ToolResult.not_found("Annotation", str(params.annotation_id))

        # Check user is assigned
        if not annotation.assigned_users.filter(id=context.user.id).exists():
            return ToolResult(
                content=section(
                    "Cannot Submit Annotation",
                    (
                        "You are not assigned to this annotation task. Assign the "
                        "task to the current user first, or choose an annotation task "
                        "assigned to you."
                    ),
                ),
                data={
                    "annotation_id": str(annotation.id),
                    "annotation_name": annotation.name,
                    "requires_assignment": True,
                    "error_code": "PERMISSION_DENIED",
                },
            )

        from model_hub.models.develop_annotations import AnnotationsLabels

        # Pre-fetch labels for validation
        label_ids = {lv.label_id for lv in params.label_values}
        labels_by_id = {
            label.id: label
            for label in AnnotationsLabels.objects.filter(id__in=label_ids)
        }

        updated = 0
        errors = []

        for lv in params.label_values:
            try:
                col = Column.objects.get(
                    id=lv.column_id, dataset=annotation.dataset, deleted=False
                )
            except Column.DoesNotExist:
                errors.append(f"Column `{lv.column_id}` not found")
                continue

            # Validate value against label type constraints
            label = labels_by_id.get(lv.label_id)
            if label:
                validation_err = _validate_submit_value(label, lv.value)
                if validation_err:
                    errors.append(
                        f"Label `{label.name}` row `{lv.row_id}`: {validation_err}"
                    )
                    continue

            # Get or create cell
            cell, created = Cell.objects.get_or_create(
                row_id=lv.row_id,
                column=col,
                dataset=annotation.dataset,
                defaults={"value": ""},
            )

            # Store value
            value_str = str(lv.value) if lv.value is not None else ""
            cell.value = value_str
            cell.feedback_info = cell.feedback_info or {}
            cell.feedback_info["annotation"] = {
                "user_id": str(context.user.id),
                "description": lv.description or "",
                "label_id": str(lv.label_id),
                "annotation_id": str(params.annotation_id),
            }
            cell.save(update_fields=["value", "feedback_info", "updated_at"])
            updated += 1

        info = key_value_block(
            [
                ("Annotation", annotation.name),
                ("Values Submitted", str(updated)),
                ("Errors", str(len(errors)) if errors else "None"),
            ]
        )

        content = section("Annotation Submitted", info)
        if errors:
            content += "\n\n### Errors\n\n" + "\n".join(f"- {e}" for e in errors)

        return ToolResult(
            content=content,
            data={"updated": updated, "errors": errors},
            is_error=updated == 0 and len(errors) > 0,
        )


def _validate_submit_value(label, value) -> str | None:
    """Validate annotation value against label type and settings."""
    from model_hub.models.choices import AnnotationTypeChoices

    annotation_type = label.type
    settings = label.settings or {}

    if annotation_type == AnnotationTypeChoices.NUMERIC.value:
        try:
            num_val = float(value)
        except (TypeError, ValueError):
            return f"Expected numeric value, got '{value}'."
        min_val = settings.get("min")
        max_val = settings.get("max")
        if min_val is not None and num_val < min_val:
            return f"Value {num_val} is below minimum {min_val}."
        if max_val is not None and num_val > max_val:
            return f"Value {num_val} exceeds maximum {max_val}."

    elif annotation_type == AnnotationTypeChoices.STAR.value:
        try:
            star_val = float(value)
        except (TypeError, ValueError):
            return f"Expected star rating, got '{value}'."
        max_stars = settings.get("no_of_stars")
        if max_stars is not None and (star_val < 1 or star_val > max_stars):
            return f"Star rating must be between 1 and {max_stars}."

    elif annotation_type == AnnotationTypeChoices.CATEGORICAL.value:
        options = settings.get("options", [])
        if options:
            allowed = [opt.get("label") for opt in options]
            # Value may be a list or comma-separated string
            if isinstance(value, list):
                vals = value
            else:
                vals = [str(value).strip()]
            invalid = [v for v in vals if v not in allowed]
            if invalid:
                return f"Invalid option(s): {', '.join(invalid)}. Allowed: {', '.join(allowed)}"
            if not settings.get("multi_choice", True) and len(vals) > 1:
                return "Multiple values provided but multi_choice is disabled."

    elif annotation_type == AnnotationTypeChoices.TEXT.value:
        text = str(value) if value is not None else ""
        min_len = settings.get("min_length")
        max_len = settings.get("max_length")
        if min_len is not None and len(text) < min_len:
            return f"Text too short. Minimum length is {min_len}."
        if max_len is not None and len(text) > max_len:
            return f"Text too long. Maximum length is {max_len}."

    elif annotation_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        valid_values = {"thumbs_up", "thumbs_down", "True", "False", "true", "false"}
        if str(value) not in valid_values:
            return f"Expected 'thumbs_up' or 'thumbs_down', got '{value}'."

    return None
