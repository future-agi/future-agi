"""
Dataset-to-Graph bridge service.

Handles column synchronization between graph versions and datasets,
building input payloads from dataset rows, and executing rows.
"""

from collections import defaultdict
from typing import Any
from uuid import UUID

import structlog
from django.db import transaction

from agent_playground.models.choices import GraphVersionStatus, PortDirection
from agent_playground.models.graph_dataset import GraphDataset
from agent_playground.models.graph_version import GraphVersion
from agent_playground.utils.graph import get_exposed_ports_for_versions
from agent_playground.utils.graph_validation import validate_version_for_activation
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Row
from tfc.temporal.agent_playground.client import start_graph_execution

logger = structlog.get_logger(__name__)


def _get_exposed_input_display_names(graph_version_id: UUID) -> set[str]:
    """Return display_names of unconnected input ports for a graph version."""
    ports_by_version = get_exposed_ports_for_versions([graph_version_id])
    ports = ports_by_version.get(graph_version_id, [])
    return {p["display_name"] for p in ports if p["direction"] == PortDirection.INPUT}


def _ensure_minimum_row(dataset) -> None:
    """Create a single row with empty cells if the dataset has no rows."""
    if Row.no_workspace_objects.filter(dataset=dataset).exists():
        return

    columns = Column.no_workspace_objects.filter(dataset=dataset)
    if not columns.exists():
        return

    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    Cell.no_workspace_objects.bulk_create(
        [Cell(dataset=dataset, row=row, column=col, value=None) for col in columns]
    )


def _create_columns_for_keys(dataset, keys: set[str]) -> None:
    """Create Column objects for a set of port keys, backfill cells for existing rows, and update column_order."""
    new_columns = []
    for key in sorted(keys):
        column = Column.no_workspace_objects.create(
            name=key,
            data_type=DataTypeChoices.TEXT.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
        )
        new_columns.append(column)

    # Backfill: create empty cells for every existing row × new column
    existing_rows = Row.no_workspace_objects.filter(dataset=dataset)
    if existing_rows.exists() and new_columns:
        cells_to_create = [
            Cell(dataset=dataset, row=row, column=column, value=None)
            for row in existing_rows
            for column in new_columns
        ]
        Cell.no_workspace_objects.bulk_create(cells_to_create)

    new_column_ids = [str(c.id) for c in new_columns]
    dataset.column_order = list(dataset.column_order or []) + new_column_ids
    dataset.save()


def sync_dataset_columns(
    graph,
    version: GraphVersion,
    old_active_version_id: UUID | None = None,
) -> None:
    """
    Add-only column sync: ensure every exposed input port has a column.

    - Restores soft-deleted columns whose name matches a needed port name.
    - Creates new columns for names not in DB at all.
    - Never deletes or soft-deletes any columns.

    Called on every granular CRUD operation (node/port/connection) and on
    version activation. The ``old_active_version_id`` parameter is accepted
    for backward compatibility but is no longer used.
    """
    try:
        graph_dataset = GraphDataset.no_workspace_objects.get(graph=graph)
    except GraphDataset.DoesNotExist:
        return

    dataset = graph_dataset.dataset
    needed_names = _get_exposed_input_display_names(version.id)
    if not needed_names:
        return

    # Include soft-deleted columns so we can restore them instead of duplicating
    all_columns = Column.all_objects.filter(dataset=dataset)
    existing_by_name: dict[str, Column] = {}
    for col in all_columns:
        existing_by_name[col.name] = col

    to_restore: list[Column] = []
    to_create: set[str] = set()

    for name in needed_names:
        col = existing_by_name.get(name)
        if col is None:
            to_create.add(name)
        elif col.deleted:
            to_restore.append(col)

    # Restore soft-deleted columns and their cells
    if to_restore:
        restore_ids = [c.id for c in to_restore]
        Column.all_objects.filter(id__in=restore_ids).update(
            deleted=False, deleted_at=None
        )
        Cell.all_objects.filter(column_id__in=restore_ids).update(
            deleted=False, deleted_at=None
        )
        # Re-add restored column IDs to column_order
        current_order = set(dataset.column_order or [])
        restored_id_strs = [
            str(cid) for cid in restore_ids if str(cid) not in current_order
        ]
        if restored_id_strs:
            dataset.column_order = list(dataset.column_order or []) + restored_id_strs
            dataset.save()

    if to_create:
        _create_columns_for_keys(dataset, to_create)

    _ensure_minimum_row(dataset)


def commit_draft_prompt_versions(version: GraphVersion) -> int:
    """
    Commit all draft PromptVersions linked to a graph version's nodes.

    When a graph version is activated, any prompt versions still in draft
    state should be marked as committed (is_draft=False).

    Returns the number of PromptVersions updated.
    """
    from model_hub.models.run_prompt import PromptVersion

    return PromptVersion.no_workspace_objects.filter(
        prompt_template_nodes__node__graph_version=version,
        prompt_template_nodes__node__deleted=False,
        prompt_template_nodes__deleted=False,
        is_draft=True,
    ).update(is_draft=False)


def activate_version_and_sync(
    graph, version: GraphVersion, commit_message: str | None = None
) -> None:
    """
    Promote a version to active and sync dataset columns.

    Handles the full activation flow:
    1. Deactivate current active version
    2. Set the given version to active
    3. Sync dataset columns with new version's input ports
    """

    validate_version_for_activation(version)

    with transaction.atomic():
        old_active_qs = GraphVersion.no_workspace_objects.filter(
            graph=graph, status=GraphVersionStatus.ACTIVE
        )
        old_active_qs.update(status=GraphVersionStatus.INACTIVE)

        version.status = GraphVersionStatus.ACTIVE
        if commit_message:
            version.commit_message = commit_message
        version.save()

        # Commit draft prompt versions linked to this graph version's nodes (TH-3780)
        commit_draft_prompt_versions(version)

    try:
        sync_dataset_columns(
            graph=graph,
            version=version,
        )
    except Exception:
        logger.exception(
            "Failed to sync dataset columns on version activation",
            graph_id=str(graph.id),
            version_id=str(version.id),
        )


def build_input_payload(graph_version: GraphVersion, row: Row) -> dict[str, Any]:
    """
    Build an input_payload dict from a dataset row for graph execution.

    Maps cell values by column name to port keys.
    All values are strings (no type conversion).
    """
    port_keys = _get_exposed_input_display_names(graph_version.id)

    # Get cells for this row
    cells = Cell.no_workspace_objects.filter(row=row).select_related("column")

    payload = {}
    for cell in cells:
        if cell.column.name in port_keys:
            payload[cell.column.name] = cell.value or ""

    return payload


def _build_payload_from_cells(port_keys: set[str], cells: list[Cell]) -> dict[str, Any]:
    """Build an input_payload dict from pre-fetched cells and port keys."""
    payload = {}
    for cell in cells:
        if cell.column.name in port_keys:
            payload[cell.column.name] = cell.value
    return payload


def execute_rows(
    graph_version: GraphVersion,
    dataset,
    row_ids: list[UUID] | None = None,
    task_queue: str = "tasks_l",
) -> list[str]:
    """
    Execute dataset rows as individual graph executions.

    Args:
        graph_version: The active GraphVersion to execute.
        dataset: The Dataset containing rows.
        row_ids: Optional list of specific row IDs to execute. If None, executes all rows.
        task_queue: Temporal task queue used for dispatch.

    Returns:
        List of graph_execution_id strings.
    """
    rows_qs = Row.no_workspace_objects.filter(dataset=dataset).order_by("order")

    if row_ids:
        rows_qs = rows_qs.filter(id__in=row_ids)

    rows = list(rows_qs)
    port_keys = _get_exposed_input_display_names(graph_version.id)

    if not rows:
        if row_ids:
            return []

        if not port_keys:
            execution_id = start_graph_execution(
                graph_version_id=str(graph_version.id),
                input_payload={},
                task_queue=task_queue,
            )
            return [execution_id]

        return []

    row_id_list = [r.id for r in rows]
    all_cells = Cell.no_workspace_objects.filter(row_id__in=row_id_list).select_related(
        "column"
    )

    cells_by_row: dict[UUID, list[Cell]] = defaultdict(list)
    for cell in all_cells:
        cells_by_row[cell.row_id].append(cell)

    execution_ids = []
    for row in rows:
        payload = _build_payload_from_cells(port_keys, cells_by_row.get(row.id, []))
        execution_id = start_graph_execution(
            graph_version_id=str(graph_version.id),
            input_payload=payload,
            task_queue=task_queue,
        )
        execution_ids.append(execution_id)

    return execution_ids
