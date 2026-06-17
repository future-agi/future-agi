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
