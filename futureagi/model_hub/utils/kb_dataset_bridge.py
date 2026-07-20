from __future__ import annotations

from typing import Iterator

from model_hub.models.develop_dataset import Column, Dataset, Row


def dataset_rows_to_documents(
    dataset: Dataset,
    columns: list[str],
) -> Iterator[tuple[str, dict]]:
    if not columns:
        raise ValueError("At least one column must be selected.")

    column_map: dict[str, str] = {}
    for col in Column.objects.filter(dataset=dataset):
        if col.name in columns:
            column_map[col.id] = col.name

    valid_column_ids = set(column_map.keys())
    if not valid_column_ids:
        raise ValueError(
            f"None of the requested columns {columns!r} exist in dataset "
            f"'{dataset.name}'. Available: {list(Column.objects.filter(dataset=dataset).values_list('name', flat=True))}"
        )

    cell_data: dict[str, dict[str, str]] = {}
    for cell in (
        Cell.objects.filter(dataset=dataset, column_id__in=valid_column_ids)
        .select_related("row", "column")
        .iterator()
    ):
        row_id = cell.row_id
        if row_id not in cell_data:
            cell_data[row_id] = {}
        col_name = column_map[cell.column_id]
        value = cell.value or ""
        if value.strip():
            cell_data[row_id][col_name] = value

    rows = Row.objects.filter(dataset=dataset).order_by("order").only("id", "order")
    for row in rows:
        row_values = cell_data.get(row.id, {})
        if not row_values:
            continue

        parts = [f"{col}: {row_values[col]}" for col in columns if col in row_values]
        if not parts:
            continue

        text = "\n".join(parts)
        metadata = {
            "dataset_id": str(dataset.id),
            "dataset_name": dataset.name,
            "row_index": row.order,
            "columns_used": columns,
        }
        yield text, metadata


def get_dataset_column_names(dataset: Dataset) -> list[str]:
    return list(
        Column.objects.filter(dataset=dataset)
        .order_by("id")
        .values_list("name", flat=True)
    )
