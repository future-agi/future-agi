"""LC-01: file ingestion must persist structured cells as JSON, not repr."""

import json
from io import StringIO
from unittest.mock import patch

import pytest

from model_hub.models.choices import DataTypeChoices, SourceChoices, determine_data_type
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.utils.file_reader import FileProcessor
from model_hub.views.datasets.create.file_upload import (
    handle_columns_bulk,
    normalize_cell_value,
)


@pytest.mark.parametrize(
    "value",
    [
        {"a": 1},
        {"nested": {"enabled": True, "missing": None}, "text": "O'Brien"},
        [{"a": False}, None, "日本語"],
        ["one"],
        [],
        {},
    ],
)
def test_structured_cell_value_round_trips_as_json(value):
    assert json.loads(normalize_cell_value(value)) == value


@pytest.mark.parametrize("value", [None, float("nan"), "", "  "])
def test_empty_scalar_values_still_normalize_to_null(value):
    assert normalize_cell_value(value) is None


@pytest.mark.django_db
@pytest.mark.parametrize("lines", [False, True], ids=["json", "jsonl"])
def test_file_reader_to_persisted_json_cells(organization, workspace, lines):
    records = [
        {"payload": {"a": 1, "nested": {"ok": True, "missing": None}}},
        {"payload": {}},
    ]
    content = (
        "\n".join(json.dumps(row) for row in records) if lines else json.dumps(records)
    )
    frame = FileProcessor._read_json_file(StringIO(content), lines=lines)
    data_type = determine_data_type(frame["payload"])
    assert data_type == DataTypeChoices.JSON.value
    dataset = Dataset.objects.create(
        name="JSON round trip", organization=organization, workspace=workspace
    )
    column = Column.objects.create(
        name="payload",
        dataset=dataset,
        data_type=data_type,
        source=SourceChoices.OTHERS.value,
    )
    rows = {
        index: Row.objects.create(dataset=dataset, order=index).id
        for index in frame.index
    }
    with patch("model_hub.views.datasets.create.file_upload.close_old_connections"):
        completed, errors = handle_columns_bulk(
            dataset.id, frame.to_dict(), {"payload": column.id}, rows
        )
    assert (completed, errors) == (1, 0)
    for index, row_id in rows.items():
        value = Cell.objects.get(dataset=dataset, row_id=row_id, column=column).value
        assert json.loads(value) == records[index]["payload"]
