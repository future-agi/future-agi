import json

import pytest
from rest_framework import status

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.contracts import (
    DatasetUpdateCellValueRequestSerializer,
    DatasetRowDataRequestSerializer,
    DatasetTableQuerySerializer,
)
from model_hub.utils.annotation_queue_helpers import _filter_dataset_cells
from model_hub.views.develop_dataset import GetDatasetTableView


def _filter(column_id, filter_type, filter_op, filter_value=None):
    config = {
        "filter_type": filter_type,
        "filter_op": filter_op,
    }
    if filter_value is not None:
        config["filter_value"] = filter_value
    return {"column_id": str(column_id), "filter_config": config}


@pytest.fixture
def dataset_filter_seed(organization, workspace):
    dataset = Dataset.objects.create(
        name="Filter dataset",
        organization=organization,
        workspace=workspace,
    )
    text_col = Column.objects.create(
        name="status",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    bool_col = Column.objects.create(
        name="passed",
        data_type=DataTypeChoices.BOOLEAN.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    rows = [
        Row.objects.create(dataset=dataset, order=1),
        Row.objects.create(dataset=dataset, order=2),
        Row.objects.create(dataset=dataset, order=3),
    ]
    Cell.objects.create(dataset=dataset, row=rows[0], column=text_col, value="Alpha")
    Cell.objects.create(dataset=dataset, row=rows[1], column=text_col, value="Beta")
    Cell.objects.create(dataset=dataset, row=rows[2], column=text_col, value="")
    Cell.objects.create(dataset=dataset, row=rows[0], column=bool_col, value="true")
    Cell.objects.create(dataset=dataset, row=rows[1], column=bool_col, value="false")
    Cell.objects.create(dataset=dataset, row=rows[2], column=bool_col, value="")
    return dataset, rows, text_col, bool_col


def _apply(dataset, filters, columns):
    return list(
        GetDatasetTableView()
        ._apply_filters(
            Cell.objects.filter(dataset=dataset),
            Row.objects.filter(dataset=dataset),
            filters,
            [],
            {str(column.id): column for column in columns},
        )
        .order_by("order")
    )


def test_dataset_table_text_in_and_not_in_filters(dataset_filter_seed):
    dataset, rows, text_col, bool_col = dataset_filter_seed

    assert (
        _apply(
            dataset,
            [_filter(text_col.id, "text", "in", ["alpha", "beta"])],
            [text_col, bool_col],
        )
        == rows[:2]
    )
    assert (
        _apply(
            dataset,
            [_filter(text_col.id, "text", "not_in", ["alpha"])],
            [text_col, bool_col],
        )
        == rows[1:]
    )


def test_dataset_table_boolean_not_equals_and_null_filters(dataset_filter_seed):
    dataset, rows, text_col, bool_col = dataset_filter_seed

    assert (
        _apply(
            dataset,
            [_filter(bool_col.id, "boolean", "not_equals", "true")],
            [text_col, bool_col],
        )
        == rows[1:]
    )
    assert _apply(
        dataset,
        [_filter(text_col.id, "text", "is_null")],
        [text_col, bool_col],
    ) == [rows[2]]
    assert (
        _apply(
            dataset,
            [_filter(text_col.id, "text", "is_not_null")],
            [text_col, bool_col],
        )
        == rows[:2]
    )


def test_dataset_table_query_serializer_rejects_camel_case_aliases():
    serializer = DatasetTableQuerySerializer(
        data={
            "filters": json.dumps([]),
            "pageSize": "10",
            "currentPageIndex": "0",
            "columnConfigOnly": "false",
        }
    )

    assert not serializer.is_valid()
    assert "pageSize" in serializer.errors
    assert "currentPageIndex" in serializer.errors
    assert "columnConfigOnly" in serializer.errors


def test_dataset_row_data_request_rejects_legacy_filter_shape(dataset_filter_seed):
    _dataset, rows, text_col, _bool_col = dataset_filter_seed
    serializer = DatasetRowDataRequestSerializer(
        data={
            "row_id": str(rows[0].id),
            "filters": [
                {
                    "column_id": str(text_col.id),
                    "filterConfig": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Alpha",
                    },
                }
            ],
        }
    )

    assert not serializer.is_valid()
    assert "filters" in serializer.errors


def test_dataset_update_cell_value_request_rejects_legacy_aliases(dataset_filter_seed):
    _dataset, rows, text_col, _bool_col = dataset_filter_seed
    serializer = DatasetUpdateCellValueRequestSerializer(
        data={
            "rowId": str(rows[0].id),
            "columnId": str(text_col.id),
            "newValue": "Gamma",
        }
    )

    assert not serializer.is_valid()
    assert "rowId" in serializer.errors
    assert "columnId" in serializer.errors
    assert "newValue" in serializer.errors


def test_dataset_table_api_rejects_legacy_query_aliases(
    auth_client, dataset_filter_seed
):
    dataset, _rows, _text_col, _bool_col = dataset_filter_seed

    response = auth_client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/",
        {
            "pageSize": "10",
            "currentPageIndex": "0",
            "columnConfigOnly": "false",
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_update_cell_value_api_rejects_legacy_payload_aliases(
    auth_client, dataset_filter_seed
):
    dataset, rows, text_col, _bool_col = dataset_filter_seed
    cell = Cell.objects.get(dataset=dataset, row=rows[0], column=text_col)

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "rowId": str(rows[0].id),
            "columnId": str(text_col.id),
            "newValue": "Gamma",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    cell.refresh_from_db()
    assert cell.value == "Alpha"


def test_update_cell_value_api_accepts_canonical_payload(
    auth_client, dataset_filter_seed
):
    dataset, rows, text_col, _bool_col = dataset_filter_seed
    cell = Cell.objects.get(dataset=dataset, row=rows[0], column=text_col)

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(rows[0].id),
            "column_id": str(text_col.id),
            "new_value": "Gamma",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    cell.refresh_from_db()
    assert cell.value == "Gamma"


def test_dataset_row_data_api_rejects_legacy_filter_shape(
    auth_client, dataset_filter_seed
):
    dataset, rows, text_col, _bool_col = dataset_filter_seed

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/get-row-data/",
        {
            "row_id": str(rows[0].id),
            "filters": [
                {
                    "column_id": str(text_col.id),
                    "filterConfig": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Alpha",
                    },
                }
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.fixture
def array_col_seed(organization, workspace):
    """A dataset with an array-typed column (e.g. PDF-to-text / extracted
    entities) whose cells are stored as ``json.dumps([...])``."""
    dataset = Dataset.objects.create(
        name="Array filter dataset",
        organization=organization,
        workspace=workspace,
    )
    arr_col = Column.objects.create(
        name="pdf_to_text",
        data_type=DataTypeChoices.ARRAY.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    rows = [
        Row.objects.create(dataset=dataset, order=1),
        Row.objects.create(dataset=dataset, order=2),
        Row.objects.create(dataset=dataset, order=3),
    ]
    Cell.objects.create(
        dataset=dataset, row=rows[0], column=arr_col,
        value=json.dumps(["CIRCULAR NO. 123 dated 2024"]),
    )
    Cell.objects.create(
        dataset=dataset, row=rows[1], column=arr_col,
        value=json.dumps(["internal memo, no reference"]),
    )
    Cell.objects.create(
        dataset=dataset, row=rows[2], column=arr_col,
        value=json.dumps(["see CIRCULAR appendix"]),
    )
    return dataset, rows, arr_col


@pytest.mark.django_db
def test_dataset_table_array_contains_list_value(array_col_seed):
    """The UI sends ``filter_value`` as a list (``["CIRCULAR"]``) for array
    columns. ``contains`` must match the same rows a scalar text search does —
    it must not stringify the list into a Python repr that can never match.
    """
    dataset, rows, arr_col = array_col_seed

    # Sanity: the data and search term are fine — a scalar text search matches.
    assert _apply(
        dataset,
        [_filter(arr_col.id, "text", "contains", "CIRCULAR")],
        [arr_col],
    ) == [rows[0], rows[2]]

    # The array-typed list payload the UI actually sends must match the same rows.
    assert _apply(
        dataset,
        [_filter(arr_col.id, "array", "contains", ["CIRCULAR"])],
        [arr_col],
    ) == [rows[0], rows[2]]


@pytest.mark.django_db
def test_dataset_table_array_contains_not_contains_and_multi_term(array_col_seed):
    """not_contains is the exact complement of contains, and a multi-element
    list matches on any element (OR)."""
    dataset, rows, arr_col = array_col_seed

    # not_contains ["CIRCULAR"] → the rows contains did NOT return.
    assert _apply(
        dataset,
        [_filter(arr_col.id, "array", "not_contains", ["CIRCULAR"])],
        [arr_col],
    ) == [rows[1]]

    # multi-element list → any element matches (OR): "memo" hits row 1 only,
    # "CIRCULAR" hits rows 0 and 2 → union of all three rows.
    assert _apply(
        dataset,
        [_filter(arr_col.id, "array", "contains", ["memo", "CIRCULAR"])],
        [arr_col],
    ) == [rows[0], rows[1], rows[2]]


@pytest.mark.django_db
def test_dataset_table_none_value_contains_is_noop(dataset_filter_seed):
    """A text ``contains`` sent without a filter_value degrades to a no-op
    (matches every row) instead of matching the literal repr of ``None``. Pins
    the unified None handling that ``or_text_filter_q`` shares across both sites.
    """
    dataset, rows, text_col, bool_col = dataset_filter_seed

    assert (
        _apply(
            dataset,
            [_filter(text_col.id, "text", "contains")],
            [text_col, bool_col],
        )
        == rows
    )


@pytest.mark.django_db
def test_filter_dataset_cells_array_contains_list_value(array_col_seed):
    """The annotation-queue cell filter (``_filter_dataset_cells``) has the same
    list-payload path and must match per-element, not the list's repr."""
    dataset, rows, arr_col = array_col_seed
    cells = Cell.objects.filter(column=arr_col)

    matched = _filter_dataset_cells(cells, "array", "contains", ["CIRCULAR"], "array")
    assert set(matched.values_list("row_id", flat=True)) == {rows[0].id, rows[2].id}

    # in/not_in keep exact-value membership (a separate branch, unchanged by the
    # fix) — it matches the whole stored cell value, not a substring.
    exact = _filter_dataset_cells(
        cells, "array", "in", [json.dumps(["see CIRCULAR appendix"])], "array"
    )
    assert set(exact.values_list("row_id", flat=True)) == {rows[2].id}
