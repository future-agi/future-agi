import uuid

import structlog
from django.db import connection, transaction

from model_hub.models.choices import DatasetSourceChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

logger = structlog.get_logger(__name__)


def _copy_cells_raw_sql(source_dataset, snapshot_dataset, column_mapping, row_mapping):
    """Copy cells from source to snapshot entirely DB-side via INSERT ... SELECT.

    Avoids the per-cell Python round-trip that dominates runtime for large
    datasets in the migration command. Relies on psycopg3 passing Python
    lists as Postgres arrays for the id-remap join.

    Returns the number of cells inserted.
    """
    if not column_mapping or not row_mapping:
        return 0

    old_col_ids = [str(old_id) for old_id in column_mapping.keys()]
    new_col_ids = [str(new_col.id) for new_col in column_mapping.values()]
    old_row_ids = [str(old_id) for old_id in row_mapping.keys()]
    new_row_ids = [str(new_row.id) for new_row in row_mapping.values()]

    sql = """
        WITH col_map AS (
            SELECT unnest(%s::uuid[]) AS old_id, unnest(%s::uuid[]) AS new_id
        ),
        row_map AS (
            SELECT unnest(%s::uuid[]) AS old_id, unnest(%s::uuid[]) AS new_id
        )
        INSERT INTO model_hub_cell (
            id, dataset_id, column_id, row_id,
            value, value_infos, feedback_info, status, column_metadata,
            prompt_tokens, completion_tokens, response_time,
            deleted, deleted_at, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            %s::uuid,
            cm.new_id,
            rm.new_id,
            c.value, c.value_infos, c.feedback_info, c.status, c.column_metadata,
            c.prompt_tokens, c.completion_tokens, c.response_time,
            false, NULL, NOW(), NOW()
        FROM model_hub_cell c
        JOIN col_map cm ON c.column_id = cm.old_id
        JOIN row_map rm ON c.row_id = rm.old_id
        WHERE c.dataset_id = %s AND c.deleted = false
    """

    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            [
                old_col_ids,
                new_col_ids,
                old_row_ids,
                new_row_ids,
                str(snapshot_dataset.id),
                str(source_dataset.id),
            ],
        )
        return cursor.rowcount


@transaction.atomic
def create_dataset_snapshot(
    source_dataset, experiment, batch_size=5000, use_raw_sql_cells=False
):
    """
    Create a scoped snapshot of a dataset for experiment isolation.

    Copies columns and selected rows/cells from source_dataset into a new
    snapshot dataset. The snapshot becomes the single source of truth
    for all experiment data (original data + result columns + eval columns).

    Args:
        source_dataset: The original Dataset to snapshot.
        experiment: The ExperimentsTable instance to attach the snapshot to.
        batch_size: Chunk size for bulk operations.

    Returns:
        tuple: (snapshot_dataset, column_mapping, row_mapping)
            - column_mapping: {old_column_id: new_column} dict
            - row_mapping: {old_row_id: new_row} dict
    """
    logger.info(
        "Creating dataset snapshot",
        source_dataset_id=str(source_dataset.id),
        experiment_id=str(experiment.id),
    )

    from model_hub.services.experiment_run_scope import snapshot_source_rows

    source_rows = snapshot_source_rows(source_dataset, experiment)

    # 1. Create snapshot Dataset
    snapshot_dataset = Dataset(
        id=uuid.uuid4(),
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
        name=f"[Snapshot] {source_dataset.name}",
        organization=source_dataset.organization,
        workspace=source_dataset.workspace,
        model_type=source_dataset.model_type,
        column_config=source_dataset.column_config or {},
        dataset_config=source_dataset.dataset_config or {},
        column_order=[],  # Will be updated after columns are copied
    )
    snapshot_dataset.save()

    # 2. Copy Columns — preserve source/data_type, track provenance via source_id
    # Exclude experiment_evaluation columns — they belong to specific experiments
    # and should not propagate to new snapshots.
    source_columns = Column.objects.filter(
        dataset=source_dataset, deleted=False
    ).exclude(source=SourceChoices.EXPERIMENT_EVALUATION.value)
    column_mapping = {}  # old_col_id -> new_column

    new_columns = []
    for col in source_columns:
        new_col = Column(
            id=uuid.uuid4(),
            name=col.name,
            data_type=col.data_type,
            dataset=snapshot_dataset,
            source=col.source,
            source_id=str(col.id),  # Track provenance back to original column
            metadata=col.metadata or {},
            status=col.status,
        )
        column_mapping[col.id] = new_col
        new_columns.append(new_col)

    if new_columns:
        Column.objects.bulk_create(new_columns, batch_size=batch_size)

    logger.info(
        "Copied columns",
        count=len(new_columns),
        experiment_id=str(experiment.id),
    )

    # 3. Update column_order on snapshot dataset using the mapping
    if source_dataset.column_order:
        new_column_order = []
        for old_col_id_str in source_dataset.column_order:
            try:
                old_col_id = uuid.UUID(old_col_id_str)
                if old_col_id in column_mapping:
                    new_column_order.append(str(column_mapping[old_col_id].id))
            except (ValueError, KeyError):
                continue
        snapshot_dataset.column_order = new_column_order
        Dataset.objects.filter(id=snapshot_dataset.id).update(
            column_order=new_column_order
        )

    # 4. Copy Rows — preserve order and metadata
    row_mapping = {}  # old_row_id -> new_row

    new_rows = []
    for row in source_rows:
        new_row = Row(
            id=uuid.uuid4(),
            dataset=snapshot_dataset,
            order=row.order,
            metadata=row.metadata or {},
        )
        row_mapping[row.id] = new_row
        new_rows.append(new_row)

    if new_rows:
        Row.objects.bulk_create(new_rows, batch_size=batch_size)

    logger.info(
        "Copied rows",
        count=len(new_rows),
        experiment_id=str(experiment.id),
    )

    # 5. Copy Cells — either via raw SQL (fast path, opt-in) or Python loop
    if use_raw_sql_cells:
        total_cells = _copy_cells_raw_sql(
            source_dataset, snapshot_dataset, column_mapping, row_mapping
        )
    else:
        total_cells = 0
        cell_batch = []

        source_cells = Cell.objects.filter(
            dataset=source_dataset, deleted=False
        ).select_related("column", "row")

        for cell in source_cells.iterator(chunk_size=batch_size):
            new_col = column_mapping.get(cell.column_id)
            new_row = row_mapping.get(cell.row_id)

            if not new_col or not new_row:
                continue

            cell_batch.append(
                Cell(
                    id=uuid.uuid4(),
                    dataset=snapshot_dataset,
                    column=new_col,
                    row=new_row,
                    value=cell.value,
                    value_infos=cell.value_infos,
                    feedback_info=cell.feedback_info,
                    status=cell.status,
                    column_metadata=cell.column_metadata,
                    prompt_tokens=cell.prompt_tokens,
                    completion_tokens=cell.completion_tokens,
                    response_time=cell.response_time,
                )
            )

            if len(cell_batch) >= batch_size:
                Cell.objects.bulk_create(cell_batch, batch_size=batch_size)
                total_cells += len(cell_batch)
                cell_batch = []

        # Flush remaining cells
        if cell_batch:
            Cell.objects.bulk_create(cell_batch, batch_size=batch_size)
            total_cells += len(cell_batch)

    logger.info(
        "Copied cells",
        count=total_cells,
        experiment_id=str(experiment.id),
    )

    # 6. Link snapshot to experiment
    experiment.snapshot_dataset = snapshot_dataset
    experiment.save(update_fields=["snapshot_dataset"])

    logger.info(
        "Dataset snapshot complete",
        snapshot_dataset_id=str(snapshot_dataset.id),
        experiment_id=str(experiment.id),
        columns=len(column_mapping),
        rows=len(row_mapping),
        cells=total_cells,
    )

    return snapshot_dataset, column_mapping, row_mapping
