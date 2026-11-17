"""Authorize annotation grid links before any cell or label mutation."""

from rest_framework.exceptions import NotFound

from model_hub.models.develop_dataset import Column, Row
from model_hub.utils.workspace_scope import (
    request_organization,
    scoped_dataset_queryset,
)


def annotation_dataset(request, annotation):
    organization = request_organization(request)
    if organization is None or annotation.dataset_id is None:
        raise NotFound("Dataset not found")
    dataset = scoped_dataset_queryset(request).filter(
        organization=organization, id=annotation.dataset_id,
    ).first()
    if dataset is None:
        raise NotFound("Dataset not found")
    return dataset


def validate_annotation_updates(request, annotation, label_updates, response_updates):
    dataset = annotation_dataset(request, annotation)
    updates = [*label_updates, *response_updates]
    row_ids = {update["row_id"] for update in updates}
    column_ids = {update["column_id"] for update in updates}
    if Row.objects.filter(dataset=dataset, deleted=False, id__in=row_ids).count() != len(row_ids):
        raise NotFound("Row not found")
    if Column.objects.filter(dataset=dataset, deleted=False, id__in=column_ids).count() != len(column_ids):
        raise NotFound("Column not found")
    label_ids = {update["label_id"] for update in label_updates}
    labels = annotation.labels.filter(
        organization=dataset.organization, deleted=False, id__in=label_ids,
    )
    if labels.count() != len(label_ids):
        raise NotFound("Annotation label not found")
    return dataset
