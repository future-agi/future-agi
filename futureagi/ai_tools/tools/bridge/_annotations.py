"""Bridge registration for AnnotationsLabelsViewSet + AnnotationsViewSet."""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.develop_annotations import (
    AnnotationsLabelsViewSet,
    AnnotationsViewSet,
)

# entity 'annotations_labels' is awkward — use 'annotation_label' for the LLM
expose_to_mcp(
    category="annotations",
    tools={
        "list": {
            "name": "list_annotation_labels",
            # TH-4667: the custom list validates query params with
            # AnnotationLabelsListQuerySerializer (strict; fields include
            # search/page/limit) INLINE rather than via @validated_request,
            # so auto-detection can't see the search support — declare it.
            # page/page_size auto-detect from the paginator (page + limit).
            "list_params": {"search": "search"},
        },
        "retrieve": {"name": "get_annotation_label"},
        "create": {"name": "create_annotation_label"},
        "update": {"name": "update_annotation_label"},
        "destroy": {"name": "delete_annotation_label"},
    },
)(AnnotationsLabelsViewSet)

expose_to_mcp(
    category="annotations",
    tools={
        "list": {"name": "list_annotations"},
        "retrieve": {"name": "get_annotation"},
        # create_annotation: creates a dataset annotation task. The view now
        # auto-assigns the creator when no assignees are given, so the creator
        # can immediately submit values (TH-5398).
        "create": {"name": "create_annotation"},
        # submit_annotation: the update_cells detail action submits label /
        # response-field values for the task's rows. detail=True so the bridge
        # injects the `id` (annotation id) into the input (TH-5398).
        "update_cells": {
            "name": "submit_annotation",
            "detail": True,
            "method": "POST",
            # Packet E (flagged by Packet A): pin the handler's REAL validator.
            # The old serializer_class fallback exposed the create-shaped
            # AnnotationsSerializer, whose fields the @validated_request
            # wrapper (StrictInputSerializer) rejects — the tool could never
            # actually deliver label values. The real request body is
            # label_values / response_field_values (see description).
            "serializer": "UpdateAnnotationCellsRequestSerializer",
            "description": (
                "Submit annotation values for a dataset-annotation task's "
                "rows. label_values is a list of {row_id, label_id, value, "
                "column_id, description?, time_taken?} objects; "
                "response_field_values is a list of {row_id, column_id, "
                "value} objects. Provide at least one of the two lists. Only "
                "users assigned to the annotation task may submit."
            ),
        },
    },
)(AnnotationsViewSet)


# ---------------------------------------------------------------------------
# Phase 3A — destructive @actions (confirmation-gated; see PHASES.md 3A and
# ai_tools/confirmations.py). execution_policy pinned explicitly for grep.
# ---------------------------------------------------------------------------


def _preview_bulk_delete_annotations(params: dict, context) -> str:
    from model_hub.models.develop_annotations import Annotations

    ids = params.get("annotation_ids") or []
    annotations = list(
        Annotations.objects.filter(id__in=ids).values_list(
            "name", "id", "dataset__name"
        )
    )
    lines = [
        f"Will delete **{len(annotations)} annotation task(s)** "
        f"(of {len(ids)} requested), including their annotation columns and "
        "submitted cell values:"
    ]
    for name, aid, dataset_name in annotations[:10]:
        lines.append(
            f"- '{name}' (`{str(aid)[:8]}…`) on dataset '{dataset_name}'"
        )
    if len(annotations) > 10:
        lines.append(f"- … and {len(annotations) - 10} more")
    lines.append("")
    lines.append("This cannot be undone.")
    return "\n".join(lines)


def _preview_reset_annotations(params: dict, context) -> str:
    from model_hub.models.develop_annotations import Annotations

    annotation_id = params.get("id")
    row_id = params.get("row_id")
    annotation = (
        Annotations.objects.filter(id=annotation_id)
        .values_list("name", "dataset__name")
        .first()
    )
    label = (
        f"'{annotation[0]}' (dataset '{annotation[1]}')"
        if annotation
        else f"`{annotation_id}` (not found)"
    )
    return (
        f"Will RESET your submitted annotation values for row `{row_id}` of "
        f"annotation task {label} — the row's cell values you annotated are "
        "cleared so it can be re-annotated.\n\n"
        "This data loss cannot be undone; the values must be re-entered."
    )


expose_to_mcp(
    category="annotations",
    tools={
        # bulk_destroy: BulkDestroyAnnotationsRequestSerializer auto-resolves
        # (annotation_ids).
        "bulk_destroy": {
            "name": "bulk_delete_annotations",
            "entity": "annotation",
            "execution_policy": "destructive",
            "confirm_preview": _preview_bulk_delete_annotations,
            "description": (
                "Bulk delete annotation tasks by id, including their "
                "annotation columns and submitted values. DESTRUCTIVE: "
                "requires user confirmation (preview first, then re-call "
                "with confirm=true)."
            ),
        },
        # reset_annotations: detail action (annotation id) +
        # ResetAnnotationsRequestSerializer (row_id). Only assigned users
        # may reset (enforced in-method by the view).
        "reset_annotations": {
            "name": "reset_annotations",
            "entity": "annotation",
            "execution_policy": "destructive",
            "confirm_preview": _preview_reset_annotations,
            "description": (
                "Reset the calling user's submitted annotation values for "
                "one row of an annotation task (clears the row's annotated "
                "cell values so it can be re-annotated). DESTRUCTIVE data "
                "loss: requires user confirmation (preview first, then "
                "re-call with confirm=true)."
            ),
        },
    },
)(AnnotationsViewSet)
