from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section, truncate
from ai_tools.tools.annotation_queues._utils import clean_ref, uuid_text


def _annotation_qs(context: ToolContext):
    from model_hub.models.develop_annotations import Annotations

    return (
        Annotations.objects.select_related("dataset")
        .filter(organization=context.organization, deleted=False)
        .order_by("-created_at")
    )


def candidate_annotations_result(
    context: ToolContext,
    title: str = "Annotation Task Candidates",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = _annotation_qs(context)
    search = clean_ref(search)
    if search:
        qs = qs.filter(name__icontains=search)
    annotations = list(qs[:10])

    rows = [
        [
            annotation.name,
            f"`{annotation.id}`",
            annotation.dataset.name if annotation.dataset else "-",
            str(annotation.labels.count()),
        ]
        for annotation in annotations
    ]
    if rows:
        body = (detail + "\n\n" if detail else "") + markdown_table(
            ["Name", "ID", "Dataset", "Labels"],
            rows,
        )
    else:
        body = detail or "No annotation tasks found in this workspace."

    return ToolResult(
        content=section(title, body),
        data={
            "requires_annotation_id": True,
            "annotations": [
                {
                    "id": str(annotation.id),
                    "name": annotation.name,
                    "dataset_id": (
                        str(annotation.dataset_id) if annotation.dataset_id else None
                    ),
                }
                for annotation in annotations
            ],
        },
    )


def resolve_annotation(
    annotation_ref: Any,
    context: ToolContext,
    title: str = "Annotation Task Candidates",
) -> tuple[Any | None, ToolResult | None]:
    ref = clean_ref(annotation_ref)
    if not ref:
        return None, candidate_annotations_result(context, title)

    from model_hub.models.develop_annotations import Annotations

    qs = _annotation_qs(context)
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            annotation = qs.get(id=ref_uuid)
            return annotation, None

        exact = qs.filter(name__iexact=ref)
        if exact.count() == 1:
            return exact.first(), None
        if exact.count() > 1:
            return None, candidate_annotations_result(
                context,
                "Multiple Annotation Tasks Matched",
                f"More than one annotation task matched `{ref}`. Use one of these IDs.",
                search=ref,
            )

        fuzzy = qs.filter(name__icontains=ref)
        if fuzzy.count() == 1:
            return fuzzy.first(), None
    except (Annotations.DoesNotExist, ValidationError, ValueError, TypeError):
        pass

    return None, candidate_annotations_result(
        context,
        "Annotation Task Not Found",
        f"Annotation task `{ref}` was not found. Use one of these IDs instead.",
        search="" if ref_uuid else ref,
    )


def _label_qs(context: ToolContext):
    from model_hub.models.develop_annotations import AnnotationsLabels

    qs = AnnotationsLabels.objects.filter(
        organization=context.organization,
        deleted=False,
    )
    if context.workspace:
        qs = qs.filter(Q(workspace=context.workspace) | Q(workspace__isnull=True))
    return qs.only(
        "id",
        "name",
        "type",
        "settings",
        "description",
        "created_at",
        "project_id",
        "organization_id",
        "workspace_id",
    ).order_by("-created_at")


def candidate_labels_result(
    context: ToolContext,
    title: str = "Annotation Label Candidates",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = _label_qs(context)
    search = clean_ref(search)
    if search:
        qs = qs.filter(name__icontains=search)
    labels = list(qs[:10])

    rows = [
        [
            label.name,
            f"`{label.id}`",
            label.type or "-",
            truncate(label.description, 60) if label.description else "-",
        ]
        for label in labels
    ]
    if rows:
        body = (detail + "\n\n" if detail else "") + markdown_table(
            ["Name", "ID", "Type", "Description"],
            rows,
        )
    else:
        body = detail or "No annotation labels found in this workspace."

    return ToolResult(
        content=section(title, body),
        data={
            "requires_label_id": True,
            "labels": [
                {"id": str(label.id), "name": label.name, "type": label.type}
                for label in labels
            ],
        },
    )


def resolve_label(
    label_ref: Any,
    context: ToolContext,
    title: str = "Annotation Label Candidates",
) -> tuple[Any | None, ToolResult | None]:
    from model_hub.models.develop_annotations import AnnotationsLabels

    ref = clean_ref(label_ref)
    if not ref:
        return None, candidate_labels_result(context, title)

    qs = _label_qs(context)
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            return qs.get(id=ref_uuid), None

        exact = qs.filter(name__iexact=ref)
        if exact.count() == 1:
            return exact.first(), None
        if exact.count() > 1:
            return None, candidate_labels_result(
                context,
                "Multiple Annotation Labels Matched",
                f"More than one annotation label matched `{ref}`. Use one of these IDs.",
                search=ref,
            )

        fuzzy = qs.filter(name__icontains=ref)
        if fuzzy.count() == 1:
            return fuzzy.first(), None
    except (AnnotationsLabels.DoesNotExist, ValidationError, ValueError, TypeError):
        pass

    return None, candidate_labels_result(
        context,
        "Annotation Label Not Found",
        f"Annotation label `{ref}` was not found. Use one of these IDs instead.",
        search="" if ref_uuid else ref,
    )


def _dataset_qs(context: ToolContext):
    from model_hub.models.develop_dataset import Dataset

    return (
        Dataset.objects.filter(organization=context.organization, deleted=False)
        .exclude(scenarios__isnull=False)
        .order_by("-created_at")
    )


def annotation_creation_requirements_result(
    context: ToolContext,
    title: str = "Annotation Creation Requirements",
    detail: str = "",
    dataset_search: str = "",
    label_search: str = "",
) -> ToolResult:
    dataset_qs = _dataset_qs(context)
    dataset_search = clean_ref(dataset_search)
    if dataset_search:
        dataset_qs = dataset_qs.filter(name__icontains=dataset_search)
    datasets = list(dataset_qs[:8])

    label_qs = _label_qs(context)
    label_search = clean_ref(label_search)
    if label_search:
        label_qs = label_qs.filter(name__icontains=label_search)
    labels = list(label_qs[:8])

    parts = []
    if detail:
        parts.append(detail)
    parts.append("Required fields: `name`, `dataset_id`, and at least one `label_ids` value.")

    if datasets:
        parts.append(
            markdown_table(
                ["Dataset", "ID", "Source"],
                [
                    [
                        dataset.name or "Untitled",
                        f"`{dataset.id}`",
                        dataset.source or "-",
                    ]
                    for dataset in datasets
                ],
            )
        )
    else:
        parts.append("No datasets found in this workspace.")

    if labels:
        parts.append(
            markdown_table(
                ["Label", "ID", "Type"],
                [[label.name, f"`{label.id}`", label.type or "-"] for label in labels],
            )
        )
    else:
        parts.append("No annotation labels found in this workspace.")

    return ToolResult(
        content=section(title, "\n\n".join(parts)),
        data={
            "requires": ["name", "dataset_id", "label_ids"],
            "datasets": [
                {"id": str(dataset.id), "name": dataset.name}
                for dataset in datasets
            ],
            "labels": [
                {"id": str(label.id), "name": label.name, "type": label.type}
                for label in labels
            ],
        },
    )


def resolve_dataset(
    dataset_ref: Any,
    context: ToolContext,
    title: str = "Dataset Candidates For Annotation",
) -> tuple[Any | None, ToolResult | None]:
    from model_hub.models.develop_dataset import Dataset

    ref = clean_ref(dataset_ref)
    if not ref:
        return None, annotation_creation_requirements_result(context, title)

    qs = _dataset_qs(context)
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            return qs.get(id=ref_uuid), None

        exact = qs.filter(name__iexact=ref)
        if exact.count() == 1:
            return exact.first(), None
        if exact.count() > 1:
            return None, annotation_creation_requirements_result(
                context,
                "Multiple Datasets Matched",
                f"More than one dataset matched `{ref}`. Use one of these IDs.",
                dataset_search=ref,
            )

        fuzzy = qs.filter(name__icontains=ref)
        if fuzzy.count() == 1:
            return fuzzy.first(), None
    except (Dataset.DoesNotExist, ValidationError, ValueError, TypeError):
        pass

    return None, annotation_creation_requirements_result(
        context,
        "Dataset Not Found",
        f"Dataset `{ref}` was not found. Use one of these dataset IDs instead.",
        dataset_search="" if ref_uuid else ref,
    )
