"""W5-C-001: file appends must not fabricate text for omitted columns."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.tests.test_dataset_schema import _client, snapshot
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _tenant
from model_hub.views import develop_dataset as views
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def append_dataset(db, monkeypatch, urlconf_loaded_without_workspace_context):
    # Keep authentication, multipart parsing, FileProcessor, ORM and table reads
    # real. External metering is unrelated to the imported cell values.
    monkeypatch.setattr(views, "log_and_deduct_cost_for_resource_request", None)
    try:
        tenant = _tenant("w5-c-append")
        dataset = tenant["dataset"]
        prompt = tenant["column"]
        prompt.name = "prompt"
        prompt.save(update_fields=["name"])
        expected = Column.no_workspace_objects.create(
            dataset=dataset,
            name="expected",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        for row, value in zip(tenant["rows"], ["None", "false"]):
            Cell.no_workspace_objects.create(
                dataset=dataset,
                row=row,
                column=expected,
                value=value,
            )
        dataset.column_order = [str(prompt.id), str(expected.id)]
        dataset.column_config = {
            str(column.id): {"is_visible": True, "is_frozen": None}
            for column in (prompt, expected)
        }
        dataset.save(update_fields=["column_order", "column_config"])
        yield tenant, _client(tenant), (prompt, expected)
    finally:
        clear_workspace_context()


@pytest.mark.parametrize(
    "filename,content_type,content,prompt_value,expected_value",
    [
        pytest.param(
            "more.csv",
            "text/csv",
            b"prompt\nWhat is the capital of France?\n",
            "What is the capital of France?",
            "",
            id="partial-column-csv",
        ),
        pytest.param(
            "more.csv", "text/csv", b"prompt\nNone\n", "None", "",
            id="literal-none-csv",
        ),
        pytest.param(
            "more.csv", "text/csv", b"prompt\n0\n", "0", "",
            id="zero-text-csv",
        ),
        pytest.param(
            "more.csv", "text/csv", b"prompt\nfalse\n", "false", "",
            id="false-text-csv",
        ),
        pytest.param(
            "more.csv",
            "text/csv",
            b"prompt,expected\nA supplied answer,None\n",
            "A supplied answer",
            "None",
            id="present-literal-none-control",
        ),
        pytest.param(
            "more.json", "application/json", b'[{"prompt": 0}]', "0", "",
            id="native-zero-json",
        ),
        pytest.param(
            "more.json", "application/json", b'[{"prompt": false}]', "False", "",
            id="native-false-json",
        ),
    ],
)
def test_file_append_keeps_omitted_cells_empty(
    append_dataset, filename, content_type, content, prompt_value, expected_value
):
    tenant, client, columns = append_dataset
    dataset = tenant["dataset"]
    old_rows = list(
        Row.no_workspace_objects.filter(dataset=dataset).order_by("id").values()
    )
    old_row_ids = [row["id"] for row in old_rows]
    old_cells = list(
        Cell.no_workspace_objects.filter(dataset=dataset).order_by("id").values()
    )
    old_cell_ids = [cell["id"] for cell in old_cells]

    response = client.post(
        "/model-hub/develops/add_rows_from_file/",
        {
            "dataset_id": str(dataset.id),
            "file": SimpleUploadedFile(
                filename, content, content_type=content_type
            ),
        },
        format="multipart",
    )
    assert response.status_code == 200, response.content

    rows = Row.no_workspace_objects.filter(dataset=dataset)
    assert rows.count() == len(old_rows) + 1
    appended_row = rows.exclude(id__in=old_row_ids).get()
    assert appended_row.order == max(row["order"] for row in old_rows) + 1
    assert list(rows.filter(id__in=old_row_ids).order_by("id").values()) == old_rows
    cells = Cell.no_workspace_objects.filter(dataset=dataset)
    assert cells.count() == len(old_cells) + len(columns)
    assert list(cells.filter(id__in=old_cell_ids).order_by("id").values()) == old_cells
    assert Column.no_workspace_objects.filter(dataset=dataset).count() == 2

    appended_cells = list(cells.filter(row=appended_row))
    assert len(appended_cells) == len(columns)
    by_column = {cell.column_id: cell for cell in appended_cells}
    assert set(by_column) == {column.id for column in columns}
    assert by_column[columns[0].id].value == prompt_value
    if expected_value == "":
        # Empty string and null are both empty; the fabricated text "None" is not.
        assert by_column[columns[1].id].value in (None, "")
    else:
        assert by_column[columns[1].id].value == expected_value

    table_response = client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/"
    )
    assert table_response.status_code == 200, table_response.content
    table_rows = {
        row["row_id"]: row for row in table_response.json()["result"]["table"]
    }
    assert set(table_rows) == {str(row.id) for row in rows}
    for column in columns:
        assert (
            table_rows[str(appended_row.id)][str(column.id)]["cell_value"]
            == by_column[column.id].value
        )
    for cell in old_cells:
        assert (
            table_rows[str(cell["row_id"])][str(cell["column_id"])]["cell_value"]
            == cell["value"]
        )


def test_file_append_rejects_omitted_required_cells_without_writes(append_dataset):
    tenant, client, _ = append_dataset
    dataset = tenant["dataset"]
    dataset.dataset_config = {
        **(dataset.dataset_config or {}),
        "schema": {
            "enforce": "reject",
            "declared": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "expected": {"type": "string"},
                },
                "required": ["prompt", "expected"],
            },
        },
    }
    dataset.save(update_fields=["dataset_config"])
    before = snapshot(tenant)

    response = client.post(
        "/model-hub/develops/add_rows_from_file/",
        {
            "dataset_id": str(dataset.id),
            "file": SimpleUploadedFile(
                "more.csv",
                b"prompt\nWhat is the capital of France?\n",
                content_type="text/csv",
            ),
        },
        format="multipart",
    )

    assert response.status_code == 400, response.content
    assert "required fields are empty: expected" in str(response.json()["message"])
    assert snapshot(tenant) == before
