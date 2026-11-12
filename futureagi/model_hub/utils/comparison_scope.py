"""Authorization for multi-dataset requests and cached comparison metadata."""

from django.core.exceptions import ValidationError
from django.http import Http404

from model_hub.models.develop_dataset import Row
from model_hub.utils.workspace_scope import (
    request_organization,
    scoped_dataset_queryset,
)


def require_comparison_datasets(request, dataset_ids):
    ids = {str(identifier) for identifier in dataset_ids}
    if not ids or request_organization(request) is None:
        raise Http404("Dataset not found")
    try:
        datasets = scoped_dataset_queryset(request).filter(id__in=ids)
        if {str(identifier) for identifier in datasets.values_list("id", flat=True)} != ids:
            raise Http404("Dataset not found")
    except (ValidationError, ValueError, TypeError) as exc:
        raise Http404("Dataset not found") from exc
    return datasets


def require_comparison_rows(dataset_ids, dataset_info):
    """A caller-supplied dataset->row map must preserve every parent relationship."""
    ids = {str(identifier) for identifier in dataset_ids}
    pairs = set()
    try:
        for rows in (dataset_info or {}).values():
            for dataset_id, row_id in rows.items():
                if str(dataset_id) not in ids:
                    raise Http404("Row not found")
                pairs.add((str(dataset_id), str(row_id)))
        if not pairs:
            return
        found = {
            (str(dataset_id), str(row_id))
            for dataset_id, row_id in Row.objects.filter(
                dataset_id__in=ids, id__in=[row for _, row in pairs], deleted=False,
            ).values_list("dataset_id", "id")
        }
        if found != pairs:
            raise Http404("Row not found")
    except (AttributeError, ValidationError, ValueError, TypeError) as exc:
        raise Http404("Row not found") from exc


def require_comparison_metadata(request, metadata):
    ids = metadata.get("comparison_datasets", [])
    base_id = metadata.get("base_dataset_id")
    if base_id:
        ids = [*ids, base_id]
    require_comparison_datasets(request, ids)
    require_comparison_rows(ids, metadata.get("dataset_info", {}))
    return ids
