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
        "list": {"name": "list_annotation_labels"},
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
        },
    },
)(AnnotationsViewSet)
