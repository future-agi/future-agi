"""One persisted result column per composite binding.

A composite binding's result column is identified by its dataset, source and
source_id. Its name and data type are attributes: the type follows the saved
version that runs (aggregation on stores a score, off stores a summary), so a
switch between saved versions updates the existing column instead of adding a
second one beside it.
"""

import json

from django.db import transaction

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset


def _locked(dataset_id, source, source_id, data_type):
    """The binding's existing result column, row-locked.

    Normally there is exactly one. If several already exist for the same
    identity, the oldest one that has the requested type wins, then the oldest
    overall, so repeated runs keep writing the same column.
    """
    columns = list(
        Column.objects.select_for_update()
        .filter(dataset_id=dataset_id, source=source, source_id=source_id)
        .order_by("created_at", "id")
    )
    typed = (column for column in columns if column.data_type == data_type)
    return next(typed, columns[0] if columns else None)


def _retype(column, data_type, kept_row_ids=(), row_ids=None):
    """Align a locked column with the type the selected version writes.

    Cells written under the previous type hold another version's results, so
    the cells of the rows this run evaluates are reset to the pre-run state
    used before every rerun (empty value, running) and the run that follows
    rewrites them with values of the new type. A row-scoped run (row_ids)
    resets only its own rows: every other row keeps its completed result until
    a run evaluates it. A row in kept_row_ids holds a settled experiment
    failed-row outcome that the run neither resets nor re-evaluates, so its
    cell is left as it is.
    """
    if column.data_type == data_type:
        return False
    column.data_type = data_type
    column.save(update_fields=["data_type", "updated_at"])
    cells = Cell.objects.filter(column=column, deleted=False)
    if row_ids:
        cells = cells.filter(row_id__in=row_ids)
    if kept_row_ids:
        cells = cells.exclude(row_id__in=kept_row_ids)
    cells.update(
        value="",
        value_infos=json.dumps({}),
        status=CellStatus.RUNNING.value,
    )
    return True


def get_or_create_result_column(
    column_config, experiment_dataset=None, upstream_column=None, row_ids=None
):
    """Return the binding's result column, retyped for this run, creating it once.

    The retype resets only the rows the run evaluates: row_ids for a
    row-scoped run, every row otherwise. An experiment run holds the
    experiment row, the lock its failed-row writer and whole-experiment stop
    take, while it retypes: the retype keeps each settled failed-row outcome
    that the run's priming and evaluation skip.
    """
    from tfc.temporal.experiments.run_policy import (
        experiment_write_lock,
        rows_holding_failed_row_outcomes,
    )

    dataset = column_config["dataset"]
    experiment_id = (
        experiment_dataset.experiment_id if experiment_dataset is not None else None
    )
    with experiment_write_lock(experiment_id), transaction.atomic():
        # Serialises creation and column_order updates per dataset.
        dataset_obj = Dataset.no_workspace_objects.select_for_update().get(
            id=dataset.id
        )
        column = _locked(
            dataset.id,
            column_config["source"],
            column_config["source_id"],
            column_config["data_type"],
        )
        if column is not None:
            kept_row_ids = set()
            if experiment_id and column.data_type != column_config["data_type"]:
                kept_row_ids = rows_holding_failed_row_outcomes(
                    dataset, column, upstream_column, row_ids=row_ids
                )
            _retype(column, column_config["data_type"], kept_row_ids, row_ids)
            return column
        column = Column.objects.create(**column_config)
        if experiment_dataset:
            column.status = StatusType.RUNNING.value
            column.save(update_fields=["status"])
            experiment_dataset.columns.add(column)
        else:
            order = dataset_obj.column_order or []
            order.append(str(column.id))
            dataset_obj.column_order = order
            dataset_obj.save(update_fields=["column_order"])
    return column


def retype_existing_result_column(metric, column, data_type):
    """Dataset precreation: retype a composite's existing result column in place."""
    if metric.template.template_type != "composite":
        return column
    with transaction.atomic():
        locked = (
            _locked(column.dataset_id, column.source, column.source_id, data_type)
            or column
        )
        _retype(locked, data_type)
    return locked
