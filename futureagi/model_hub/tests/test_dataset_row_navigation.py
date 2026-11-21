"""Row adjacency must stay within the dataset table's active search."""

from unittest.mock import Mock
from uuid import UUID

import pytest
from rest_framework import status

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.contracts import DatasetRowDataRequestSerializer
from model_hub.views import develop_dataset as views


def test_row_data_request_accepts_table_search_context():
    search = {"key": "Needle", "type": ["text"]}
    serializer = DatasetRowDataRequestSerializer(
        data={"row_id": str(UUID(int=1)), "search": search}
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["search"] == search


@pytest.mark.parametrize("search", [[], 7, "not-json"])
def test_row_data_request_rejects_non_object_search(search):
    serializer = DatasetRowDataRequestSerializer(
        data={"row_id": str(UUID(int=1)), "search": search}
    )

    assert not serializer.is_valid()
    assert "search" in serializer.errors


@pytest.fixture
def navigation_dataset(organization, workspace, monkeypatch):
    dataset = Dataset.objects.create(
        name="Search navigation", organization=organization, workspace=workspace
    )
    text_column = Column.objects.create(
        name="text",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    group_column = Column.objects.create(
        name="group",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    rank_column = Column.objects.create(
        name="rank",
        dataset=dataset,
        data_type=DataTypeChoices.INTEGER.value,
        source=SourceChoices.OTHERS.value,
    )
    hidden_column = Column.objects.create(
        name="experiment",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value,
    )
    rows = []
    matches = []
    for index, (text, group, rank) in enumerate(
        [
            ("Needle C", "keep", "30"),
            ("Haystack", "keep", "15"),
            ("Needle A", "keep", "10"),
            ("Needle D", "drop", "5"),
            ("Needle B", "keep", "20"),
            ("Needle B", "keep", "20"),
        ]
    ):
        row = Row.objects.create(dataset=dataset, id=UUID(int=index + 1), order=index)
        rows.append(row)
        text_cell = Cell.objects.create(
            dataset=dataset, row=row, column=text_column, value=text
        )
        Cell.objects.create(dataset=dataset, row=row, column=group_column, value=group)
        Cell.objects.create(dataset=dataset, row=row, column=rank_column, value=rank)
        if text.startswith("Needle"):
            matches.append((text_cell.id, True, [[0, 5]]))

    # The table excludes experiment columns in a regular dataset. A match in
    # only such a column must not pull a row back into the drawer's search set.
    hidden_cell = Cell.objects.create(
        dataset=dataset, row=rows[1], column=hidden_column, value="Needle hidden"
    )
    matches.append((hidden_cell.id, True, [[0, 5]]))
    search_query = Mock(return_value=matches)
    monkeypatch.setattr(views.SQLQueryHandler, "search_cells_by_text", search_query)
    return dataset, rows, group_column, rank_column, search_query


@pytest.mark.parametrize("sort_type", [None, "ascending", "descending"])
@pytest.mark.parametrize("filtered", [False, True])
def test_row_navigation_intersects_search_filters_and_stable_sort(
    auth_client, navigation_dataset, monkeypatch, sort_type, filtered
):
    dataset, rows, group_column, rank_column, search_query = navigation_dataset
    payload = {"search": {"key": "Needle", "type": ["text"]}}
    if filtered:
        payload["filters"] = [
            {
                "column_id": str(group_column.id),
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "keep",
                },
            }
        ]
    if sort_type:
        payload["sort"] = [{"column_id": str(rank_column.id), "type": sort_type}]

    expected_indices = {
        None: [0, 2, 3, 4, 5],
        "ascending": [3, 2, 4, 5, 0],
        "descending": [0, 4, 5, 2, 3],
    }[sort_type]
    if filtered:
        expected_indices = [index for index in expected_indices if index != 3]
    expected_ids = [str(rows[index].id) for index in expected_indices]
    # Keep the existing bounded response, including the stable UUID tie-break
    # for equal sort values, rather than loading the entire matching dataset.
    monkeypatch.setattr(views, "DATASET_ROW_ADJACENCY_MAX_ROWS", 2)
    url = f"/model-hub/develops/{dataset.id}/get-row-data/"
    for position, row_id in enumerate(expected_ids):
        response = auth_client.post(url, {**payload, "row_id": row_id}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        result = response.json()["result"]
        assert result["current"]["row_id"] == row_id
        assert result["next"]["row_id"] == expected_ids[position + 1 : position + 3]

    search_query.assert_called_with("needle", dataset.id)
    excluded = auth_client.post(
        url, {**payload, "row_id": str(rows[1].id)}, format="json"
    )
    assert excluded.status_code == status.HTTP_404_NOT_FOUND


def test_row_navigation_empty_search_keeps_default_order(
    auth_client, navigation_dataset
):
    dataset, rows, _group_column, _rank_column, search_query = navigation_dataset
    url = f"/model-hub/develops/{dataset.id}/get-row-data/"
    for extra_payload in ({}, {"search": {}}):
        response = auth_client.post(
            url, {"row_id": str(rows[0].id), **extra_payload}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.json()["result"]["next"]["row_id"] == [
            str(row.id) for row in rows[1:]
        ]
    search_query.assert_not_called()


def test_row_navigation_no_search_matches_does_not_fall_back_to_all_rows(
    auth_client, navigation_dataset
):
    dataset, rows, _group_column, _rank_column, search_query = navigation_dataset
    search_query.return_value = []
    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/get-row-data/",
        {"row_id": str(rows[0].id), "search": {"key": "missing", "type": ["text"]}},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    search_query.assert_called_once_with("missing", dataset.id)
