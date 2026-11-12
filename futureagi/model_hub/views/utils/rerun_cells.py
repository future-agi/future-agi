"""Every row a dynamic-column rerun processes has a destination cell to refill.

RerunOperationView clears only the cells the column already has (to RUNNING,
value None) before it dispatches the rerun. A row without one (an earlier run
that raised for the row wrote none, or the row or its source cell came later)
gets the same cleared cell here, before the rerun processes anything, so the
rerun stores every row by its existing-cell rule: the row's result as a PASS
cell, or None as an ERROR cell with the reason.

dynamic_columns' rerun activities call this, so it runs whichever module's copy
an install registers (develop_dataset's copies delegate to them or call it
themselves).
"""

import json

from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Cell

# Unexpected exceptions may contain provider/request/internal details. Activities
# log those on the server, but store only this controlled public reason.
ROW_PROCESSING_ERROR = "Unable to process this row. Please try again."
MISSING_SOURCE_ERROR = "Source cell is missing for this row."


def prepare_source_rerun_cells(
    row_ids, dataset_id, new_column_id, *, selected_row_ids=None
):
    """Prepare source rows and finalize missing sources within a rerun's scope.

    ``row_ids`` contains active source rows. ``selected_row_ids``, when supplied,
    is the caller's live dataset selection, not just the source subset. Prepare
    every selected destination, including rows absent from both columns, so the
    tracking wrapper cannot substitute its generic no-output error.

    Without a selection, retain the legacy whole-column finalization and create
    destinations only for rows with a source.
    """
    row_ids = list(row_ids)
    if selected_row_ids is not None:
        selected_row_ids = set(map(str, selected_row_ids))
        row_ids = [row_id for row_id in row_ids if str(row_id) in selected_row_ids]
    give_every_rerun_row_a_cell(
        row_ids if selected_row_ids is None else selected_row_ids,
        dataset_id,
        new_column_id,
    )
    destinations = Cell.objects.filter(column_id=new_column_id, deleted=False)
    if selected_row_ids is not None:
        destinations = destinations.filter(row_id__in=selected_row_ids)
    destinations.exclude(row_id__in=row_ids).update(
        value=None,
        value_infos=json.dumps({"reason": MISSING_SOURCE_ERROR}),
        status=CellStatus.ERROR.value,
    )


def give_every_rerun_row_a_cell(row_ids, dataset_id, new_column_id):
    """Create a cleared ``new_column_id`` cell for each row in ``row_ids``
    that has none.

    Pass the rows the rerun will process. Rows that already have a cell are
    left alone, so a retried activity creates nothing twice.
    """
    has_cell = {
        str(row_id)
        for row_id in Cell.objects.filter(
            column_id=new_column_id, deleted=False
        ).values_list("row_id", flat=True)
    }
    missing = []
    for row_id in row_ids:
        if str(row_id) in has_cell:
            continue
        has_cell.add(str(row_id))
        missing.append(
            Cell(
                dataset_id=dataset_id,
                column_id=new_column_id,
                row_id=row_id,
                value=None,
                value_infos=json.dumps({}),
                status=CellStatus.RUNNING.value,
            )
        )
    if missing:
        Cell.objects.bulk_create(missing, batch_size=1000)
