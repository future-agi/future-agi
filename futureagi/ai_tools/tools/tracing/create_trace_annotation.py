import logging
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from model_hub.models.choices import QueueItemSourceType


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
        from tracer.models.project import Project
        from tracer.models.trace import Trace
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.services.clickhouse.v2 import get_reader

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

        # Validate span exists (CH read replaces ObservationSpan ORM .get;
        # FK traversal to Trace is now a separate PG lookup, and org-tenant
        # scope is verified via the Project model).
        with get_reader() as reader:
            span = reader.get(str(params.span_id))
        if span is None:
            return ToolResult.not_found("Span", params.span_id)
        if not Project.objects.filter(
            id=span.project_id, organization=context.organization
        ).exists():
            return ToolResult.not_found("Span", params.span_id)
        # Resolve the trace from the span (still PG since Trace not migrated)
        trace = Trace.objects.filter(id=span.trace_id).first() if span.trace_id else None
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

        # Check for existing TraceAnnotation (duplicate detection) — update
        # instead of creating duplicate.
        # Codex wave-2 P1 (2026-05-26): `span` is now a CHSpan dataclass
        # (not a Django ObservationSpan instance). Django FK *_id fields
        # accept the raw id string directly; the bare-FK form
        # (`observation_span=span`) is invalid model usage. Use
        # `observation_span_id=span.id` consistently.
        lookup_kwargs = {
            "annotation_label": label,
            "user": context.user,
            "observation_span_id": span.id,
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
                observation_span_id=span.id,
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
            QueueItemSourceType.OBSERVATION_SPAN.value,
            span,
            context.organization,
            context.user,
        )
        if default_item is None:
            return ToolResult.error(
                "Cannot resolve a default annotation queue for this span. "
                "Per-queue Score uniqueness requires every score to live "
                "in a queue context.",
                error_code="NO_DEFAULT_QUEUE_SCOPE",
            )
        Score.no_workspace_objects.update_or_create(
            observation_span_id=span.id,
            label_id=label.pk,
            annotator_id=context.user.pk,
            queue_item=default_item,
            deleted=False,
            defaults={
                "source_type": QueueItemSourceType.OBSERVATION_SPAN.value,
                "value": score_value,
                "score_source": "human",
                "notes": "",
                "organization": context.organization,
                # Denormalized tracer project id for cheap label discovery.
                **(
                    {"tracer_project_id": span.project_id}
                    if span.project_id
                    else {}
                ),
            },
        )

        # Create/update span notes if provided.
        # Codex wave-2 P1: `span` is a CHSpan; SpanNotes.span is a Django FK
        # to PG ObservationSpan. Use the *_id form. Guard against missing
        # PG row (CH-only span) — span notes are annotator commentary, not
        # load-bearing; degrade gracefully so the score/annotation write
        # earlier in this function doesn't get unwound by an IntegrityError.
        if params.notes:
            from django.db import IntegrityError
            from tracer.models.observation_span import ObservationSpan
            from tracer.models.span_notes import SpanNotes

            pg_span_exists = ObservationSpan.no_workspace_objects.filter(
                id=span.id
            ).exists()
            if not pg_span_exists:
                logger.warning(
                    "create_trace_annotation_span_notes_skipped",
                    span_id=str(span.id),
                    reason="CH span has no matching PG ObservationSpan row",
                )
            else:
                try:
                    span_note = SpanNotes.objects.get(
                        span_id=span.id, created_by_user=context.user
                    )
                    span_note.notes = params.notes
                    span_note.save(update_fields=["notes"])
                except SpanNotes.DoesNotExist:
                    try:
                        SpanNotes.objects.create(
                            span_id=span.id,
                            notes=params.notes,
                            created_by_user=context.user,
                            created_by_annotator=str(context.user.id),
                        )
                    except IntegrityError as e:
                        logger.warning(
                            "create_trace_annotation_span_notes_integrity",
                            span_id=str(span.id),
                            error=str(e),
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
