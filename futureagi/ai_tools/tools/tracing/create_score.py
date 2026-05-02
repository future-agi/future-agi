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


class CreateScoreInput(PydanticBaseModel):
    trace_id: UUID = Field(description="The UUID of the trace to annotate")
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
        "value (text/categorical), value_float (numeric/star), value_bool (thumbs_up_down)."
    )
    category = "tracing"
    input_model = CreateScoreInput

    def execute(self, params: CreateScoreInput, context: ToolContext) -> ToolResult:

        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace import Trace

        from ._annotation_validation import validate_annotation_value
        from .create_trace_annotation import _to_score_value

        # Validate trace
        try:
            trace = Trace.objects.get(
                id=params.trace_id, project__organization=context.organization
            )
        except Trace.DoesNotExist:
            return ToolResult.not_found("Trace", str(params.trace_id))

        # Validate label
        try:
            label = AnnotationsLabels.objects.get(id=params.annotation_label_id)
        except AnnotationsLabels.DoesNotExist:
            return ToolResult.not_found(
                "Annotation Label", str(params.annotation_label_id)
            )

        # Validate span if provided
        span = None
        if params.observation_span_id:
            from tracer.models.observation_span import ObservationSpan

            try:
                span = ObservationSpan.objects.get(
                    id=params.observation_span_id,
                    project__organization=context.organization,
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
            return ToolResult.error(
                "Provide at least one of: value, value_float, value_bool, or value_str_list.",
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

        # Score-only path. Production readers are unified on the ``Score``
        # model post-deprecation; the ``TraceAnnotation`` write that used to
        # accompany this call was dropped to avoid the API identity split
        # (returning a TraceAnnotation ID while readers expect Score IDs).
        raw_value = _get_raw_value(params)
        score_value = _to_score_value(label.type, raw_value)

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
        else:
            score_lookup["trace_id"] = trace.pk
            score_defaults["source_type"] = "trace"

        annotation, created = Score.no_workspace_objects.update_or_create(
            **score_lookup, defaults=score_defaults
        )
        is_update = not created

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
