"""The dataset-column value readers answer from PostgreSQL, the dataset's store.

Both readers used to read the ``model_hub_cell`` ClickHouse mirror. PeerDB
creates it ``ORDER BY id``, so every lookup read the whole table (dev,
2026-09-25: 14.3M rows / 19.5 GB, 22-24 s for a 12-cell column), and it trails
every write by a CDC batch: a value typed into a fresh dataset was missing from
the picker while the table's own exact filter, which reads PostgreSQL, found it
(B04). ``lagging_mirror`` stands in for that mirror: it is enabled and has not
received any of these cells.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

URL = "/tracer/dashboard/filter_values/"


@pytest.fixture
def lagging_mirror():
    empty = MagicMock()
    empty.return_value.execute_ch_query.return_value = MagicMock(data=[])
    with (
        patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True),
        patch("tracer.views.dashboard.AnalyticsQueryService", empty),
        patch(
            "tracer.services.clickhouse.client.is_clickhouse_enabled",
            return_value=True,
        ),
        patch("tracer.services.clickhouse.query_service.AnalyticsQueryService", empty),
    ):
        yield


@pytest.fixture
def dataset(organization, workspace):
    from model_hub.models.develop_dataset import Dataset

    return Dataset.objects.create(
        name="values", organization=organization, workspace=workspace
    )


def _column(dataset, *, data_type="text", source="OTHERS"):
    from model_hub.models.choices import StatusType
    from model_hub.models.develop_dataset import Column

    return Column.objects.create(
        id=uuid.uuid4(),
        name=f"column-{uuid.uuid4().hex[:6]}",
        data_type=data_type,
        source=source,
        status=StatusType.RUNNING.value,
        dataset=dataset,
    )


def _cell(column, value, **fields):
    from model_hub.models.develop_dataset import Cell, Row

    row = Row.objects.create(dataset=column.dataset, order=Row.objects.count())
    return Cell.objects.create(
        dataset=column.dataset, column=column, row=row, value=value, **fields
    )


def _suggestions(client, column, **params):
    response = client.get(
        URL,
        {
            "source": "dataset_column",
            "metric_name": str(column.id),
            "dataset_id": str(column.dataset_id),
            **params,
        },
    )
    assert response.status_code == 200, response.content
    return [option["value"] for option in response.json()["result"]["values"]]


@pytest.mark.django_db
def test_a_freshly_written_value_is_suggested_immediately(
    auth_client, dataset, lagging_mirror
):
    column = _column(dataset)
    _cell(column, "qa_value_alpha")

    assert _suggestions(auth_client, column) == ["qa_value_alpha"]


@pytest.mark.django_db
def test_an_edited_cell_suggests_only_its_current_value(
    auth_client, dataset, lagging_mirror
):
    column = _column(dataset)
    cell = _cell(column, "before-edit")
    cell.value = "after-edit"
    cell.save()

    assert _suggestions(auth_client, column) == ["after-edit"]


@pytest.mark.django_db
def test_deleted_and_empty_cells_are_not_suggested(
    auth_client, dataset, lagging_mirror
):
    column = _column(dataset)
    _cell(column, "kept")
    _cell(column, "")
    _cell(column, None)
    # Deleting a row soft-deletes its cells; the table no longer shows them.
    _cell(column, "deleted-row").delete()

    assert _suggestions(auth_client, column) == ["kept"]


@pytest.mark.django_db
def test_other_columns_and_datasets_are_not_suggested(
    auth_client, dataset, organization, workspace, lagging_mirror
):
    from model_hub.models.develop_dataset import Dataset

    column = _column(dataset)
    _cell(column, "mine")
    _cell(_column(dataset), "same-dataset-other-column")
    other = Dataset.objects.create(
        name="other", organization=organization, workspace=workspace
    )
    _cell(_column(other), "other-dataset")

    assert _suggestions(auth_client, column) == ["mine"]


@pytest.mark.django_db
def test_search_narrows_case_insensitively(auth_client, dataset, lagging_mirror):
    column = _column(dataset)
    for value in ("English", "Spanish", "French"):
        _cell(column, value)

    assert _suggestions(auth_client, column, search="ISH") == ["English", "Spanish"]
    assert _suggestions(auth_client, column, search="ish", page_size=50) == [
        "English",
        "Spanish",
    ]


@pytest.mark.django_db
def test_evaluation_choices_decode_labels_and_same_cell_literals(
    auth_client, dataset, lagging_mirror
):
    column = _column(dataset, data_type="array", source="evaluation")
    _cell(column, "['neutral']")
    _cell(column, "{'choice': 'positive', 'score': 0.9}")
    literal = {"output": "choices", "data": {"result": "[west]"}}
    _cell(column, "[west]", value_infos=literal)
    # Historical cells store the metadata JSON once more as a JSON string.
    _cell(
        column,
        "[east]",
        value_infos=json.dumps({"output": "choices", "data": {"choice": "[east]"}}),
    )
    # Metadata about another value never makes this storage a literal label.
    _cell(column, "['north']", value_infos={"output": "choices", "data": "[west]"})

    assert _suggestions(auth_client, column) == [
        "[east]",
        "[west]",
        "neutral",
        "north",
        "positive",
    ]
    assert _suggestions(auth_client, column, search="WES") == ["[west]"]


@pytest.mark.django_db
def test_ai_filter_grounding_reads_the_current_column(dataset, lagging_mirror):
    from model_hub.views import ai_filter

    column = _column(dataset)
    _cell(column, "qa_value_alpha")
    _cell(column, "qa_value_beta").delete()
    _cell(_column(dataset), "qa_value_other_column")

    assert ai_filter._fetch_dataset_column_values(
        dataset.id, column.id, search_query="QA_VALUE"
    ) == ["qa_value_alpha"]
