"""Scoped split membership operations shared by API, grid and saved views."""

import uuid

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from model_hub.models.dataset_splits import DatasetSplit, DatasetSplitMembership
from model_hub.models.dataset_views import DatasetSavedView
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.utils.workspace_scope import legacy_dataset_filter
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_workspace,
)
from tfc.permissions.utils import get_effective_workspace_level

NO_SPLIT = "__none__"
SPLIT_FILTER = "system:splits"


def scoped_dataset(request, dataset_id, *, write=False, lock=False):
    organization = getattr(request, "organization", None) or get_current_organization()
    workspace = getattr(request, "workspace", None) or get_current_workspace()
    if (
        not organization
        or not workspace
        or workspace.organization_id != organization.id
    ):
        raise PermissionDenied("An active organization and workspace are required.")
    level = get_effective_workspace_level(request.user, workspace.id)
    if level is None or (write and level < Level.MEMBER):
        raise PermissionDenied(
            "Workspace write access required."
            if write
            else "Workspace access required."
        )
    # Datasets in this exact workspace; the default workspace also gets its
    # organization's legacy datasets that have no workspace.
    datasets = Dataset.no_workspace_objects.filter(
        legacy_dataset_filter(workspace),
        organization=organization,
        deleted=False,
    )
    if lock:
        # Lock only the dataset row.
        datasets = datasets.select_for_update(of=("self",))
    dataset = get_object_or_404(datasets, id=dataset_id)
    if (
        write
        and dataset.workspace_id is None
        and not request.user.can_write_to_workspace(workspace)
    ):
        # A dataset with no workspace is changed by exactly the people who may
        # edit and delete its rows: the same check authentication applies to
        # every row and cell write.
        raise PermissionDenied("Workspace write access required.")
    return dataset


def live_rows(dataset):
    return Row.no_workspace_objects.filter(dataset=dataset, deleted=False)


def memberships(dataset):
    return DatasetSplitMembership.objects.filter(
        split__dataset=dataset, row__dataset=dataset, row__deleted=False
    )


def split_result(split):
    return {
        "id": str(split.id),
        "name": split.name,
        "color": split.color,
        "description": split.description,
        "row_count": memberships(split.dataset).filter(split=split).count(),
    }


def list_splits(dataset):
    counts = {
        str(item["split_id"]): item["count"]
        for item in memberships(dataset).values("split_id").annotate(count=Count("id"))
    }
    splits = [
        {
            "id": str(split.id),
            "name": split.name,
            "color": split.color,
            "description": split.description,
            "row_count": counts.get(str(split.id), 0),
        }
        for split in DatasetSplit.objects.filter(dataset=dataset)
    ]
    return {
        "splits": splits,
        "no_split_count": live_rows(dataset)
        .exclude(id__in=memberships(dataset).values("row_id"))
        .count(),
    }


def validate_split_ids(dataset, ids, *, allow_none=False):
    if (
        not isinstance(ids, list)
        or len(ids) > 100
        or any(not isinstance(value, str) for value in ids)
    ):
        raise ValidationError({"split_ids": "Expected at most 100 split identifiers."})
    actual = set(ids) - ({NO_SPLIT} if allow_none else set())
    try:
        parsed = {uuid.UUID(value) for value in actual}
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({"split_ids": "Invalid split identifier."}) from None
    available = set(
        DatasetSplit.objects.filter(dataset=dataset, id__in=parsed).values_list(
            "id", flat=True
        )
    )
    if parsed != available:
        raise ValidationError({"split_ids": "Unknown or inaccessible dataset split."})
    return parsed


def validate_split_filter(config, dataset=None):
    values = config.get("filter_value")
    if not (
        config.get("filter_type") == "categorical"
        and config.get("filter_op") == "in"
        and isinstance(values, list)
        and 0 < len(values) <= 100
        and all(isinstance(value, str) for value in values)
    ):
        raise ValidationError({"filters": "Invalid dataset split filter."})
    try:
        for value in values:
            if value != NO_SPLIT:
                uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValidationError({"filters": "Invalid split identifier."}) from None
    if dataset is not None:
        validate_split_ids(dataset, values, allow_none=True)


def validate_request_split_filters(request, dataset_id):
    filters = [
        item
        for item in request.validated_query_data.get("filters", [])
        if item.get("column_id") == SPLIT_FILTER
    ]
    if filters:
        dataset = scoped_dataset(request, dataset_id)
        for item in filters:
            validate_split_filter(item.get("filter_config") or {}, dataset)


def filter_split_rows(rows, config):
    validate_split_filter(config)
    values = config["filter_value"]
    own = DatasetSplitMembership.objects.filter(
        row_id__in=rows.values("id"), split__dataset_id__in=rows.values("dataset_id")
    )
    matching = Q(
        id__in=own.filter(split_id__in=[v for v in values if v != NO_SPLIT]).values(
            "row_id"
        )
    )
    if NO_SPLIT in values:
        matching |= ~Q(id__in=own.values("row_id"))
    return rows.filter(matching)


def update_saved_split_names(dataset, old_name, new_name=None):
    """Preserve named view filters on rename; do not silently broaden on deletion."""
    for view in (
        DatasetSavedView.objects.filter(dataset=dataset).select_for_update().iterator()
    ):
        state = view.state if isinstance(view.state, dict) else {}
        names = state.get("splits", "").split(",")
        if old_name not in names:
            continue
        if new_name is None:
            if not view.deleted:
                raise ValidationError(
                    "Remove this split from saved views before deleting it."
                )
            continue
        view.state = {
            **state,
            "splits": ",".join(
                new_name if name == old_name else name for name in names
            ),
        }
        view.save(update_fields=["state"])


def touch_dataset(dataset):
    """Invalidate signed exact-table continuations when projected splits change."""
    Dataset.no_workspace_objects.filter(pk=dataset.id).update(updated_at=timezone.now())


def project_split_membership(rows, dataset, request, checkpoint):
    """Project a bounded page within the table reader's existing wall budget."""
    checkpoint(before_statement=True)
    if not DatasetSplit.objects.filter(dataset=dataset).exists():
        return
    organization = getattr(request, "organization", None) or get_current_organization()
    workspace = getattr(request, "workspace", None) or get_current_workspace()
    if (
        not organization
        or not workspace
        or dataset.organization_id != organization.id
        or (dataset.workspace_id is not None and dataset.workspace_id != workspace.id)
        or workspace.organization_id != organization.id
    ):
        raise PermissionDenied(
            "Dataset split scope does not match the active workspace."
        )
    if dataset.workspace_id is None:
        # The same rule as scoped_dataset, so a legacy dataset with no
        # workspace keeps the splits its default workspace gave it.
        checkpoint(before_statement=True)
        if not Dataset.no_workspace_objects.filter(
            legacy_dataset_filter(workspace), pk=dataset.pk
        ).exists():
            raise PermissionDenied(
                "Dataset split scope does not match the active workspace."
            )
    by_row = {}
    # The parent has already authorized the dataset; bind both sides of each link.
    checkpoint(before_statement=True)
    pairs = (
        memberships(dataset)
        .filter(row_id__in=[r["row_id"] for r in rows])
        .order_by("split_id")
        .values_list("row_id", "split_id")
    )
    for row_id, split_id in pairs.iterator(chunk_size=500):
        checkpoint()
        by_row.setdefault(str(row_id), []).append(str(split_id))
    for row in rows:
        row.setdefault("row_meta", {})["split_ids"] = by_row.get(row["row_id"], [])


@transaction.atomic
def change_membership(request, dataset_id, data):
    dataset = scoped_dataset(request, dataset_id, write=True, lock=True)
    row_ids = set(data["row_ids"])
    rows = list(live_rows(dataset).select_for_update().filter(id__in=row_ids))
    if {row.id for row in rows} != row_ids:
        raise ValidationError({"row_ids": "Unknown or inaccessible dataset row."})
    add = validate_split_ids(dataset, [str(value) for value in data["add"]])
    remove = validate_split_ids(dataset, [str(value) for value in data["remove"]])
    memberships(dataset).filter(row_id__in=row_ids, split_id__in=remove).delete()
    DatasetSplitMembership.objects.bulk_create(
        [
            DatasetSplitMembership(row=row, split_id=split_id)
            for row in rows
            for split_id in add
        ],
        ignore_conflicts=True,
    )
    touch_dataset(dataset)
    return {"updated": len(rows)}
