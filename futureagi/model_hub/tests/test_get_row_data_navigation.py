from unittest.mock import patch

import pytest
from django.urls import reverse

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views.develop_dataset import GetRowDataView


def test_normalize_search_accepts_json_encoded_raw_string():
    assert GetRowDataView()._normalize_search('"needle"') == {
        "key": "needle",
        "type": ["text", "image", "audio"],
    }


@pytest.fixture
def drawer_navigation_dataset(organization, workspace):
    dataset = Dataset.objects.create(
        name="Drawer navigation dataset",
        organization=organization,
        workspace=workspace,
    )
    score_column = Column.objects.create(
        name="score",
        data_type=DataTypeChoices.INTEGER.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    label_column = Column.objects.create(
        name="label",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(score_column.id), str(label_column.id)]
    dataset.save(update_fields=["column_order"])

    high_row = Row.objects.create(dataset=dataset, order=1)
    low_row = Row.objects.create(dataset=dataset, order=2)
    mid_row = Row.objects.create(dataset=dataset, order=3)

    high_score_cell = Cell.objects.create(
        dataset=dataset, column=score_column, row=high_row, value="30"
    )
    low_score_cell = Cell.objects.create(
        dataset=dataset, column=score_column, row=low_row, value="10"
    )
    mid_score_cell = Cell.objects.create(
        dataset=dataset, column=score_column, row=mid_row, value="20"
    )
    Cell.objects.create(
        dataset=dataset, column=label_column, row=high_row, value="keep needle high"
    )
    Cell.objects.create(
        dataset=dataset, column=label_column, row=low_row, value="keep no match"
    )
    Cell.objects.create(
        dataset=dataset, column=label_column, row=mid_row, value="keep needle mid"
    )

    return {
        "dataset": dataset,
        "score_column": score_column,
        "label_column": label_column,
        "rows": {
            "high": high_row,
            "low": low_row,
            "mid": mid_row,
        },
        "score_cells": {
            "high": high_score_cell,
            "low": low_score_cell,
            "mid": mid_score_cell,
        },
    }


@pytest.mark.django_db
def test_get_row_data_sort_uses_sorted_order_for_next_ids(
    auth_client, drawer_navigation_dataset
):
    dataset = drawer_navigation_dataset["dataset"]
    score_column = drawer_navigation_dataset["score_column"]
    rows = drawer_navigation_dataset["rows"]
    url = reverse("get-row-data", kwargs={"dataset_id": dataset.id})

    response = auth_client.post(
        url,
        {
            "row_id": str(rows["low"].id),
            "sort": [
                {
                    "columnId": str(score_column.id),
                    "type": "ascending",
                }
            ],
        },
        format="json",
    )

    assert response.status_code == 200
    next_row_ids = [
        str(row_id) for row_id in response.data["result"]["next"]["row_id"][:2]
    ]
    assert next_row_ids == [
        str(rows["mid"].id),
        str(rows["high"].id),
    ]


@pytest.mark.django_db
def test_get_row_data_accepts_raw_search_and_applies_filter_search_sort(
    auth_client, drawer_navigation_dataset
):
    dataset = drawer_navigation_dataset["dataset"]
    score_column = drawer_navigation_dataset["score_column"]
    label_column = drawer_navigation_dataset["label_column"]
    rows = drawer_navigation_dataset["rows"]
    score_cells = drawer_navigation_dataset["score_cells"]
    url = reverse("get-row-data", kwargs={"dataset_id": dataset.id})

    search_results = [
        (score_cells["high"].id, True, []),
        (score_cells["mid"].id, True, []),
    ]
    with patch(
        "model_hub.views.develop_dataset.SQLQueryHandler.search_cells_by_text",
        return_value=search_results,
    ):
        response = auth_client.post(
            url,
            {
                "row_id": str(rows["mid"].id),
                "filters": [
                    {
                        "columnId": str(label_column.id),
                        "filterConfig": {
                            "filterType": "text",
                            "filterOp": "contains",
                            "filterValue": "keep",
                        },
                    }
                ],
                "search": "needle",
                "sort": [
                    {
                        "columnId": str(score_column.id),
                        "type": "ascending",
                    }
                ],
            },
            format="json",
        )

    assert response.status_code == 200
    next_row_ids = [
        str(row_id) for row_id in response.data["result"]["next"]["row_id"]
    ]
    assert next_row_ids == [str(rows["high"].id)]
