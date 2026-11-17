"""The dataset rows, columns and cells a call may show (T6 #7d).

A call's dataset row comes from ``CallExecution.row_id`` or, when that column
is null, from ``call_metadata["row_id"]``, which the ALK result PATCH used to
store exactly as the caller sent it. Row, Column and Cell have no workspace
field, so the model managers add no tenant filter to them, and ``all_objects``
adds no ``deleted`` filter either. Every call surface reads them through these
querysets instead: datasets of the execution's own organization only, live rows
and cells only. A row id from another organization, or a soft-deleted row,
reads exactly like a row id that names nothing.
"""

from model_hub.models.develop_dataset import Cell, Column, Row


def org_rows(organization_id):
    """Live rows of the organization's datasets."""
    if organization_id is None:
        return Row.objects.none()
    return Row.objects.filter(dataset__organization_id=organization_id)


def org_columns(organization_id):
    """Live columns of the organization's datasets."""
    if organization_id is None:
        return Column.objects.none()
    return Column.objects.filter(dataset__organization_id=organization_id)


def org_cells(organization_id):
    """Live cells of the live rows of the organization's datasets."""
    if organization_id is None:
        return Cell.objects.none()
    return Cell.objects.filter(
        row__deleted=False, row__dataset__organization_id=organization_id
    )
