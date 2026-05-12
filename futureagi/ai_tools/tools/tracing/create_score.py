from typing import List, Optional
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class CreateScoreInput(PydanticBaseModel):
    trace_id: str = Field(
        default="",
        description="Trace UUID or exact trace name. Omit to list candidates.",
    )
    annotation_label_id: str = Field(
        default="",
        description="Annotation label UUID or exact label name. Omit to list candidates.",
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
    observation_span_id: Optional[str] = Field(
        default=None,
        description="Optional span ID to annotate at the span level instead of trace level",
    )
    value_str_list: Optional[List[str]] = Field(
        default=None,
        description="List of strings for categorical labels",
    )


@register_tool
class CreateScoreTool(BaseTool):
    name = "create_score"
    description = (
        "Creates a score/annotation on a trace or observation span. "
        "Provide the annotation_label_id and the appropriate value field based on label type: "
        "value (text/categorical), value_float (numeric/star), value_bool (thumbs_up_down). "
        "If trace or label IDs are missing, returns candidates instead of failing."
    )
    category = "tracing"
    input_model = CreateScoreInput

    def execute(self, params: CreateScoreInput, context: ToolContext) -> ToolResult:

        from model_hub.models.score import Score
        from tracer.models.trace_annotation import TraceAnnotation

        from ._annotation_validation import validate_annotation_value
        from .create_trace_annotation import _to_score_value
        from ._utils import (
            candidate_annotation_labels_result,
            resolve_annotation_label,
            resolve_trace,
        )

        trace, unresolved = resolve_trace(
            params.trace_id,
            context,
            title="Trace Required For Score",
        )
        if unresolved:
            return unresolved

        label, unresolved = resolve_annotation_label(
            params.annotation_label_id,
            context,
            title="Annotation Label Required For Score",
        )
        if unresolved:
            return unresolved

        if label.project_id and label.project_id != trace.project_id:
            return candidate_annotation_labels_result(
                context,
                "Annotation Label Project Mismatch",
                (
                    f"Label `{label.name}` belongs to a different project than "
                    f"trace `{trace.id}`. Choose a label scoped to the trace project "
                    "or an organization-level label."
                ),
            )

        # Validate span if provided
        span = None
        if params.observation_span_id:
            from tracer.models.observation_span import ObservationSpan

            try:
                span = ObservationSpan.objects.get(
                id=params.observation_span_id,
                project__organization=context.organization,
                trace=trace,
            )
            except ObservationSpan.DoesNotExist:
                return ToolResult.not_found("Span", params.observation_span_id)

        # Ensure at least one value is provided
        if (
            params.value is None
            and params.value_float is None
            and params.value_bool is None
            and params.value_str_list is None
        ):
            return ToolResult.needs_input(
                "Provide at least one score value for the selected label. "
                "Use `value` for text/categorical labels, `value_float` for "
                "numeric/star labels, `value_bool` for thumbs_up_down labels, "
                "or `value_str_list` for multi-select categorical labels.",
                data={
                    "trace_id": str(trace.id),
                    "annotation_label_id": str(label.id),
                    "label_type": label.type,
                    "requires_score_value": True,
                },
                missing_fields=["value"],
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

        # Derive raw value for Score conversion
        raw_value = _get_raw_value(params)
        score_value = _to_score_value(label.type, raw_value)
        updated_by = str(context.user.id)

        # Duplicate detection — update existing instead of creating duplicate
        lookup_kwargs = {
            "annotation_label": label,
            "user": context.user,
        }
        if span:
            lookup_kwargs["observation_span"] = span
        else:
            lookup_kwargs["trace"] = trace
            lookup_kwargs["observation_span__isnull"] = True

        existing = TraceAnnotation.objects.filter(**lookup_kwargs).first()
        is_update = existing is not None

        if existing:
            existing.annotation_value = params.value
            existing.annotation_value_float = params.value_float
            existing.annotation_value_bool = params.value_bool
            existing.annotation_value_str_list = params.value_str_list
            existing.trace = trace
            existing.updated_by = updated_by
            existing.save()
            annotation = existing
        else:
            annotation = TraceAnnotation.objects.create(
                trace=trace,
                annotation_label=label,
                annotation_value=params.value,
                annotation_value_float=params.value_float,
                annotation_value_bool=params.value_bool,
                annotation_value_str_list=params.value_str_list,
                observation_span=span,
                user=context.user,
                updated_by=updated_by,
            )

        # Write to unified Score model (matches BulkAnnotationView behavior)
        score_lookup = {
            "label_id": label.pk,
            "annotator_id": context.user.pk,
            "deleted": False,
        }
        score_defaults = {
            "value": score_value,
            "score_source": "human",
            "notes": "",
            "organization": context.organization,
        }

        if span:
            score_lookup["observation_span_id"] = span.pk
            score_defaults["source_type"] = "observation_span"
            if hasattr(span, "project") and span.project:
                score_defaults["project"] = span.project
        else:
            score_lookup["trace_id"] = trace.pk
            score_defaults["source_type"] = "trace"
            if hasattr(trace, "project") and trace.project:
                score_defaults["project"] = trace.project

        Score.no_workspace_objects.update_or_create(
            **score_lookup, defaults=score_defaults
        )

        # Determine display value
        display_value = _format_display_value(params)

        info = key_value_block(
            [
                ("ID", f"`{annotation.id}`"),
                ("Label", label.name),
                ("Label Type", label.type),
                ("Value", display_value),
                ("Score Value", str(score_value)),
                ("Trace", f"`{params.trace_id}`"),
                (
                    "Span",
                    (
                        f"`{params.observation_span_id}`"
                        if params.observation_span_id
                        else "—"
                    ),
                ),
            ]
            + (
                [("Note", "Existing annotation updated instead of creating duplicate")]
                if is_update
                else []
            )
        )

        title = "Score Updated" if is_update else "Score Created"
        content = section(title, info)

        return ToolResult(
            content=content,
            data={
                "annotation_id": str(annotation.id),
                "label": label.name,
                "label_type": label.type,
                "trace_id": str(params.trace_id),
                "updated": is_update,
            },
        )


def _get_raw_value(params: CreateScoreInput):
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


def _format_display_value(params: CreateScoreInput) -> str:
    if params.value is not None:
        return params.value
    if params.value_float is not None:
        return str(params.value_float)
    if params.value_bool is not None:
        return str(params.value_bool)
    if params.value_str_list is not None:
        return str(params.value_str_list)
    return "—"
