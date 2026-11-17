"""Copy a dataset into another workspace of the caller's organization.

A copy carries the dataset's rows and input columns, exactly like Duplicate,
into a new dataset in the target workspace; the source stays where it is.

Moving the dataset itself is refused. A move would re-home rows, columns and
cells that work in the source workspace can still be adding references to,
and nothing coordinates those writers with a move: the annotation queue
add-items path, for one, inserts QueueItems for a dataset's rows in its own
transaction without touching the dataset row, so no lock a move takes waits
for that insert and no check it runs can see it. An insert that commits after
the move would leave the source workspace pointing at a row it can no longer
see. A move therefore answers 409 after the same authorization checks as a
copy, previews included, and locks and writes nothing.
"""

import uuid
from dataclasses import dataclass

from django.db.models import Count
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)

from accounts.models.workspace import Workspace
from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.validators.dataset_validators import available_dataset_copy_name
from model_hub.views.develop_dataset import (
    _request_dataset_queryset,
    _request_organization,
)
from tfc.middleware.workspace_context import get_current_workspace
from tfc.utils.error_codes import get_error_message

BATCH_SIZE = 1000


class TransferConflict(APIException):
    status_code = 409
    default_code = "dataset_transfer_conflict"
    default_detail = "Datasets can't be moved between workspaces. Copy it instead."


@dataclass(frozen=True)
class Transfer:
    mode: str
    dataset: Dataset
    source_workspace: Workspace | None
    target: Workspace
    name: str
    plan: dict


def _workspace_item(workspace):
    if workspace is None:
        return None
    return {"id": str(workspace.id), "name": workspace.display_name or workspace.name}


def _request_workspace(request):
    return getattr(request, "workspace", None) or get_current_workspace()


def _source_dataset(request, dataset_id):
    # Resolve visibility through the request's organization and workspace
    # scope first, then read the dataset row itself.
    visible = (
        _request_dataset_queryset(request)
        .filter(pk=dataset_id)
        .values_list("pk", flat=True)
        .first()
    )
    if visible is None:
        raise NotFound("Dataset not found.")
    return Dataset.no_workspace_objects.get(pk=visible)


def _target_workspace(request, organization, to_workspace_id):
    target = Workspace.no_workspace_objects.filter(
        pk=to_workspace_id, organization=organization, is_active=True
    ).first()
    if target is None:
        raise NotFound("Workspace not found.")
    if not request.user.can_write_to_workspace(target):
        raise PermissionDenied("You can't add datasets to that workspace.")
    return target


def _copy_name(dataset, organization, requested):
    if requested is None:
        return available_dataset_copy_name(dataset.name, organization)
    # Dataset names are unique across the whole organization.
    if Dataset.no_workspace_objects.filter(
        organization=organization, name=requested
    ).exists():
        raise ValidationError(get_error_message("DATASET_EXIST_IN_ORG"))
    return requested


def _plan(dataset):
    columns = Column.no_workspace_objects.filter(dataset=dataset)
    counts = dict(columns.values_list("source").annotate(n=Count("id")).order_by())
    copied = counts.get(SourceChoices.OTHERS.value, 0)
    return {
        "rows": Row.no_workspace_objects.filter(dataset=dataset).count(),
        "columns": copied,
        "columns_left_out": sum(counts.values()) - copied,
        # A copy is a new dataset; the source keeps its folder.
        "leaves_folder": False,
    }


def prepare(request, dataset_id, to_workspace_id, mode, new_dataset_name=None):
    """Validate a copy and describe it; never writes. Refuses a move."""
    organization = _request_organization(request)
    dataset = _source_dataset(request, dataset_id)
    source_workspace = dataset.workspace or _request_workspace(request)
    if source_workspace is not None and not request.user.can_write_to_workspace(
        source_workspace
    ):
        raise PermissionDenied("You can't change datasets in this workspace.")
    target = _target_workspace(request, organization, to_workspace_id)
    if source_workspace is not None and target.pk == source_workspace.pk:
        raise ValidationError("The dataset is already in that workspace.")
    if mode != "copy":
        # Every move, preview or not (see the module docstring).
        raise TransferConflict()
    return Transfer(
        mode=mode,
        dataset=dataset,
        source_workspace=source_workspace,
        target=target,
        name=_copy_name(dataset, organization, new_dataset_name),
        plan=_plan(dataset),
    )


def describe(transfer, *, dry_run, dataset_id=None):
    return {
        "mode": transfer.mode,
        "dry_run": dry_run,
        "source_dataset_id": str(transfer.dataset.id),
        "dataset_id": None if dataset_id is None else str(dataset_id),
        "dataset_name": transfer.name,
        "from_workspace": _workspace_item(transfer.source_workspace),
        "to_workspace": _workspace_item(transfer.target),
        "plan": transfer.plan,
    }


def copy_dataset(request, transfer):
    """Copy rows and input columns into the target workspace (caller's atomic block)."""
    source = transfer.dataset
    copy = Dataset.no_workspace_objects.create(
        id=uuid.uuid4(),
        name=transfer.name,
        organization=source.organization,
        workspace=transfer.target,
        model_type=source.model_type,
        dataset_config=source.dataset_config,
        user=request.user,
    )
    column_ids = {}
    columns = []
    for column in Column.no_workspace_objects.filter(
        dataset=source, source=SourceChoices.OTHERS.value
    ).order_by("created_at", "id"):
        column_ids[str(column.id)] = uuid.uuid4()
        columns.append(
            Column(
                id=column_ids[str(column.id)],
                dataset=copy,
                name=column.name,
                data_type=column.data_type,
                source=SourceChoices.OTHERS.value,
            )
        )
    Column.no_workspace_objects.bulk_create(columns, batch_size=BATCH_SIZE)
    copy.column_order = [
        str(column_ids[c]) for c in source.column_order or [] if c in column_ids
    ]
    copy.column_config = {
        str(column_ids[c]): config
        for c, config in (source.column_config or {}).items()
        if c in column_ids
    }
    copy.save()

    row_ids = {}
    rows = []
    for row_id, order in (
        Row.no_workspace_objects.filter(dataset=source)
        .order_by("order", "id")
        .values_list("id", "order")
    ):
        row_ids[row_id] = uuid.uuid4()
        rows.append(Row(id=row_ids[row_id], dataset=copy, order=order))
    Row.no_workspace_objects.bulk_create(rows, batch_size=BATCH_SIZE)

    cells = []
    for row_id, column_id, value, value_infos in (
        Cell.no_workspace_objects.filter(
            row__dataset=source,
            row__deleted=False,
            column_id__in=[uuid.UUID(c) for c in column_ids],
        )
        .order_by("id")
        .values_list("row_id", "column_id", "value", "value_infos")
        .iterator(chunk_size=BATCH_SIZE)
    ):
        if row_id not in row_ids:
            continue
        cells.append(
            Cell(
                id=uuid.uuid4(),
                dataset=copy,
                row_id=row_ids[row_id],
                column_id=column_ids[str(column_id)],
                value=value,
                value_infos=value_infos,
            )
        )
        if len(cells) >= BATCH_SIZE:
            Cell.no_workspace_objects.bulk_create(cells)
            cells = []
    Cell.no_workspace_objects.bulk_create(cells)
    return copy
