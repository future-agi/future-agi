from typing import List, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from model_hub.models.choices import QueueItemSourceType

logger = structlog.get_logger(__name__)


class UpdateTraceAnnotationInput(PydanticBaseModel):
    annotation_id: UUID = Field(description="The UUID of the annotation to update")
    value: Optional[str] = Field(
        default=None,
        description="Updated string value for text/categorical labels",
    )
    value_float: Optional[float] = Field(
        default=None,
        description="Updated numeric value for numeric/star labels",
    )
    value_bool: Optional[bool] = Field(
        default=None,
        description="Updated boolean value for thumbs_up_down labels",
    )
    value_str_list: Optional[List[str]] = Field(
        default=None,
        description="Updated list of strings for categorical labels",
    )


@register_tool
class UpdateTraceAnnotationTool(BaseTool):
    name = "update_trace_annotation"
    description = (
        "Updates an existing trace annotation's value. Provide the annotation ID "
        "and the new value(s) to update."
    )
    category = "tracing"
    input_model = UpdateTraceAnnotationInput

    def execute(
        self, params: UpdateTraceAnnotationInput, context: ToolContext
    ) -> ToolResult:

        from tracer.models.trace_annotation import TraceAnnotation

        from ._annotation_validation import validate_annotation_value

        try:
            annotation = TraceAnnotation.objects.select_related("annotation_label").get(
                id=params.annotation_id,
                trace__project__organization=context.organization,
            )
        except TraceAnnotation.DoesNotExist:
            return ToolResult.not_found("Trace Annotation", str(params.annotation_id))

        # Check if any update value is provided
        if (
            params.value is None
            and params.value_float is None
            and params.value_bool is None
            and params.value_str_list is None
        ):
            return ToolResult.error(
                "Provide at least one of: value, value_float, value_bool, or value_str_list to update.",
                error_code="VALIDATION_ERROR",
            )

        # Validate annotation value against label type
        if annotation.annotation_label:
            validation_error = validate_annotation_value(
                annotation.annotation_label,
                value=params.value,
                value_float=params.value_float,
                value_bool=params.value_bool,
                value_str_list=params.value_str_list,
            )
            if validation_error:
                return ToolResult.error(validation_error, error_code="VALIDATION_ERROR")

        # Track changes
        changes = []

        if params.value is not None:
            old_val = annotation.annotation_value
            annotation.annotation_value = params.value
            changes.append(f"value: '{old_val}' -> '{params.value}'")

        if params.value_str_list is not None and len(params.value_str_list) > 0:
            old_val = annotation.annotation_value_str_list
            annotation.annotation_value_str_list = params.value_str_list
            changes.append(f"value_str_list: {old_val} -> {params.value_str_list}")

        if params.value_float is not None:
            old_val = annotation.annotation_value_float
            annotation.annotation_value_float = params.value_float
            changes.append(f"value_float: {old_val} -> {params.value_float}")

        if params.value_bool is not None:
            old_val = annotation.annotation_value_bool
            annotation.annotation_value_bool = params.value_bool
            changes.append(f"value_bool: {old_val} -> {params.value_bool}")

        # Codex wave-2 P1 (2026-05-26): the legacy TraceAnnotation write
        # used to commit BEFORE the Score sync. If CH was lagging or the
        # span had no PG row, the Score path silently skipped — leaving
        # TraceAnnotation updated but Score stale. Wrap the entire write
        # block in `transaction.atomic()` so any CH-lookup failure or
        # queue-scope-unresolvable case rolls the annotation update back
        # too. Callers see a clean error and can retry.
        from django.db import transaction

        annotation.updated_by = str(context.user.id)

        # Sync to unified Score model to keep data consistent
        label = annotation.annotation_label

        with transaction.atomic():
            annotation.save()

            if label and annotation.observation_span_id:
                from model_hub.models.score import Score

                from .create_trace_annotation import _to_score_value

                # Derive the raw value for score conversion
                raw_value = None
                if annotation.annotation_value is not None:
                    raw_value = annotation.annotation_value
                elif annotation.annotation_value_float is not None:
                    raw_value = annotation.annotation_value_float
                elif annotation.annotation_value_bool is not None:
                    raw_value = "up" if annotation.annotation_value_bool else "down"
                elif annotation.annotation_value_str_list is not None:
                    raw_value = annotation.annotation_value_str_list

                if raw_value is not None:
                    # Resolve default queue item — same rationale as in
                    # create_trace_annotation.py: per-queue Score uniqueness
                    # demands every write be scoped by queue_item.
                    from model_hub.utils.annotation_queue_helpers import (
                        resolve_default_queue_item_for_source,
                    )

                    # Fetch the span from CH 25.3 (was
                    # ObservationSpan.objects.filter(...).first()). The
                    # subsequent resolve_default_queue_item_for_source now
                    # duck-types CHSpan via project_id, so threading the
                    # CH-loaded dataclass through works without a
                    # cross-store FK join.
                    span_obj = None
                    if annotation.observation_span_id:
                        from tracer.models.project import Project
                        from tracer.services.clickhouse.v2 import get_reader

                        with get_reader() as reader:
                            span_obj = reader.get(
                                str(annotation.observation_span_id)
                            )
                        # Tenant gate: the legacy ORM `.get()` joined
                        # project__organization implicitly; preserve that
                        # boundary via an explicit PG lookup on the
                        # CH-loaded project_id.
                        if span_obj is not None and not Project.objects.filter(
                            id=span_obj.project_id,
                            organization=context.organization,
                        ).exists():
                            span_obj = None
                    default_item = (
                        resolve_default_queue_item_for_source(
                            QueueItemSourceType.OBSERVATION_SPAN.value,
                            span_obj,
                            context.organization,
                            context.user,
                        )
                        if span_obj
                        else None
                    )
                    if default_item is None:
                        # Codex P1: bail loudly so the surrounding atomic
                        # rolls back the annotation update. Returning
                        # success-with-stale-Score caused exactly the
                        # consistency drift codex flagged.
                        raise RuntimeError(
                            "Cannot resolve default annotation queue for "
                            f"span {annotation.observation_span_id} "
                            "(CH miss / cross-org / queue unresolved). "
                            "Per-queue Score uniqueness requires queue "
                            "scope; refusing partial write."
                        )
                    score_value = _to_score_value(label.type, raw_value)
                    Score.no_workspace_objects.update_or_create(
                        observation_span_id=annotation.observation_span_id,
                        label_id=label.pk,
                        annotator_id=annotation.user_id or context.user.pk,
                        queue_item=default_item,
                        deleted=False,
                        defaults={
                            "source_type": QueueItemSourceType.OBSERVATION_SPAN.value,
                            "value": score_value,
                            "score_source": "human",
                            "organization": context.organization,
                            # Denormalized tracer project id for cheap label discovery.
                            **(
                                {"tracer_project_id": span_obj.project_id}
                                if getattr(span_obj, "project_id", None)
                                else {}
                            ),
                        },
                    )

        label_name = label.name if label else "—"

        info = key_value_block(
            [
                ("Annotation ID", f"`{annotation.id}`"),
                ("Label", label_name),
                ("Changes", "; ".join(changes)),
                ("Updated By", annotation.updated_by or "—"),
            ]
        )

        content = section("Annotation Updated", info)

        return ToolResult(
            content=content,
            data={
                "annotation_id": str(annotation.id),
                "label": label_name,
                "changes": changes,
            },
        )
