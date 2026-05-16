from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class CreateTraceAnnotationInput(PydanticBaseModel):
    span_id: str = Field(
        description="The ID of the observation span to annotate (required — annotations are always on spans)",
    )
    annotation_label_id: UUID = Field(
        description="The UUID of the annotation label to use"
    )
    value: Optional[str] = Field(
        default=None,
        description="String value for text/categorical labels",
    )
    value_float: Optional[float] = Field(
        default=None,
        description="Numeric value for numeric/star labels",
    )
    value_bool: Optional[bool] = Field(
        default=None,
        description="Boolean value for thumbs_up_down labels",
    )
    value_str_list: Optional[List[str]] = Field(
        default=None,
        description="List of strings for categorical labels",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=5000,
        description="Optional notes to attach to the span",
    )


@register_tool
class CreateTraceAnnotationTool(BaseTool):
    name = "create_trace_annotation"
    description = (
        "Creates an annotation on an observation span. "
        "span_id is required — annotations are always on spans, not traces. "
        "Provide the appropriate value field based on the label type: "
        "value (text/categorical), value_float (numeric/star), "
        "value_bool (thumbs_up_down)."
    )
    category = "tracing"
    input_model = CreateTraceAnnotationInput

    def execute(
        self, params: CreateTraceAnnotationInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace_annotation import TraceAnnotation

        from ._annotation_validation import validate_annotation_value

        # Validate label exists and belongs to the user's organization
        try:
            label = AnnotationsLabels.objects.get(
                id=params.annotation_label_id,
                organization=context.organization,
            )
        except AnnotationsLabels.DoesNotExist:
            return ToolResult.not_found(
                "Annotation Label", str(params.annotation_label_id)
            )

        # Validate span exists
        try:
            span = ObservationSpan.objects.select_related("trace").get(
                id=params.span_id, project__organization=context.organization
            )
        except ObservationSpan.DoesNotExist:
            return ToolResult.not_found("Span", params.span_id)

        # Resolve the trace from the span
        trace = span.trace
        if not trace:
            return ToolResult.error(
                f"Span '{params.span_id}' is not associated with any trace.",
                error_code="VALIDATION_ERROR",
            )

        # Validate annotation label belongs to the span's project
        if label.project_id and label.project_id != span.project_id:
            return ToolResult.error(
                f'Annotation label "{label.name}" does not belong to the span\'s project.',
                error_code="VALIDATION_ERROR",
            )

        # Validate annotation value against label type
        validation_error = validate_annotation_value(
            label,
            value=params.value,
            value_float=params.value_float,
            value_bool=params.value_bool,
            value_str_list=params.value_str_list,
        )
        if validation_error:
            return ToolResult.error(validation_error, error_code="VALIDATION_ERROR")

        # Derive the raw annotation value for Score model conversion
        raw_value = _get_raw_value(params)

        # Convert to Score.value JSON format (same as UI's _to_score_value)
        score_value = _to_score_value(label.type, raw_value)

        updated_by = str(context.user.id)

        # Check for existing TraceAnnotation (duplicate detection) — update instead of creating duplicate
        lookup_kwargs = {
            "annotation_label": label,
            "user": context.user,
            "observation_span": span,
        }

        existing = TraceAnnotation.objects.filter(**lookup_kwargs).first()
        if existing:
            existing.annotation_value = params.value
            existing.annotation_value_float = params.value_float
            existing.annotation_value_bool = params.value_bool
            existing.annotation_value_str_list = params.value_str_list
            existing.trace = trace
            existing.updated_by = updated_by
            existing.save()
        else:
            TraceAnnotation.objects.create(
                trace=trace,
                observation_span=span,
                annotation_label=label,
                annotation_value=params.value,
                annotation_value_float=params.value_float,
                annotation_value_bool=params.value_bool,
                annotation_value_str_list=params.value_str_list,
                user=context.user,
                updated_by=updated_by,
            )

        # Write to unified Score model. Resolve a default queue item so
        # the upsert is scoped by queue_item — see create_score.py for the
        # rationale; per-queue Score uniqueness needs every write to land
        # in a queue context.
        from model_hub.utils.annotation_queue_helpers import (
            resolve_default_queue_item_for_source,
        )

        default_item = resolve_default_queue_item_for_source(
            "observation_span", span, context.organization, context.user
        )
        if default_item is None:
            return ToolResult.error(
                "Cannot resolve a default annotation queue for this span. "
                "Per-queue Score uniqueness requires every score to live "
                "in a queue context.",
                error_code="NO_DEFAULT_QUEUE_SCOPE",
            )
        Score.no_workspace_objects.update_or_create(
            observation_span_id=span.pk,
            label_id=label.pk,
            annotator_id=context.user.pk,
            queue_item=default_item,
            deleted=False,
            defaults={
                "source_type": "observation_span",
                "value": score_value,
                "score_source": "human",
                "notes": "",
                "organization": context.organization,
            },
        )

        # Create/update span notes if provided
        if params.notes:
            from tracer.models.span_notes import SpanNotes

            try:
                span_note = SpanNotes.objects.get(
                    span=span, created_by_user=context.user
                )
                span_note.notes = params.notes
                span_note.save(update_fields=["notes"])
            except SpanNotes.DoesNotExist:
                SpanNotes.objects.create(
                    span=span,
                    notes=params.notes,
                    created_by_user=context.user,
                    created_by_annotator=str(context.user.id),
                )

        is_update = existing is not None
        annotation_obj = (
            existing
            if is_update
            else TraceAnnotation.objects.filter(**lookup_kwargs).first()
        )

        display_value = _format_display_value(params)
        info = key_value_block(
            [
                ("ID", f"`{annotation_obj.id}`" if annotation_obj else "—"),
                ("Label", label.name),
                ("Type", label.type),
                ("Span", f"`{params.span_id}`"),
                ("Trace", f"`{trace.id}`"),
                ("Value", display_value),
                ("Score Value", str(score_value)),
                ("Updated By", updated_by),
            ]
            + (
                [("Note", "Existing annotation updated instead of creating duplicate")]
                if is_update
                else []
            )
        )

        title = "Trace Annotation Updated" if is_update else "Trace Annotation Created"
        content = section(title, info)

        return ToolResult(
            content=content,
            data={
                "annotation_id": str(annotation_obj.id) if annotation_obj else None,
                "label": label.name,
                "label_type": label.type,
                "span_id": params.span_id,
                "trace_id": str(trace.id),
                "updated": is_update,
            },
        )


def _get_raw_value(params: CreateTraceAnnotationInput):
    """Extract the raw annotation value from params for Score conversion."""
    if params.value is not None:
        return params.value
    if params.value_float is not None:
        return params.value_float
    if params.value_bool is not None:
        return "up" if params.value_bool else "down"
    if params.value_str_list is not None:
        return params.value_str_list
    return None


def _to_score_value(annotation_type, given_value):
    """Convert annotation value to Score.value JSON format.

    Matches the backend's _to_score_value in observation_span.py so that
    annotations created via MCP are stored identically to those created
    via the UI.
    """
    from model_hub.models.choices import AnnotationTypeChoices

    if annotation_type == AnnotationTypeChoices.STAR.value:
        return {"rating": float(given_value)}
    elif annotation_type == AnnotationTypeChoices.NUMERIC.value:
        return {"value": float(given_value)}
    elif annotation_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        return {"value": str(given_value)}
    elif annotation_type == AnnotationTypeChoices.CATEGORICAL.value:
        return {
            "selected": given_value if isinstance(given_value, list) else [given_value]
        }
    else:
        # text and fallback
        return {"text": str(given_value)}


def _format_display_value(params: CreateTraceAnnotationInput) -> str:
    if params.value is not None:
        return params.value
    if params.value_float is not None:
        return str(params.value_float)
    if params.value_bool is not None:
        return str(params.value_bool)
    if params.value_str_list is not None:
        return str(params.value_str_list)
    return "—"
