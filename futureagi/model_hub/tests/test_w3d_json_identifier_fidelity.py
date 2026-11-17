"""Legacy JSON imports must retain identifiers in storage, grid reads and CSV."""

import csv
import json
from io import StringIO
from unittest.mock import Mock

import pandas as pd
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from model_hub.models.choices import DataTypeChoices, determine_data_type
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.services.file_import_admission import READER_KEY
from model_hub.utils.file_reader import FileProcessor
from model_hub.views.datasets.create import file_upload

UPLOAD = "/model-hub/develops/create-dataset-from-local-file/"
RECORDS = [
    {
        "code": "00123",
        "prompt": "first",
        "quantity": 123,
        "price": 1.25,
        "active": True,
        "missing": None,
        "payload": {"code": "00123", "ok": True, "empty": None},
        "tags": ["00123", "first"],
    },
    {
        "code": "00456",
        "prompt": "second",
        "quantity": 456,
        "price": 2.75,
        "active": False,
        "missing": None,
        "payload": {},
        "tags": [],
    },
]


def _content(records, extension):
    text = (
        "\n".join(json.dumps(record) for record in records)
        if extension == "jsonl"
        else json.dumps(records)
    )
    return text.encode("utf-8")


def _read(records, extension):
    frame, error = FileProcessor.process_file(
        SimpleUploadedFile(f"identifiers.{extension}", _content(records, extension))
    )
    assert error is None, error
    return frame


@pytest.mark.parametrize("extension", ["json", "jsonl"])
def test_legacy_reader_preserves_quoted_identifiers(extension):
    frame = _read(RECORDS, extension)

    assert frame["code"].tolist() == ["00123", "00456"]
    assert determine_data_type(frame["code"]) == DataTypeChoices.TEXT.value
    assert frame["prompt"].tolist() == ["first", "second"]
    assert frame["quantity"].tolist() == [123, 456]
    assert determine_data_type(frame["quantity"]) == DataTypeChoices.INTEGER.value
    assert frame["price"].tolist() == [1.25, 2.75]
    assert determine_data_type(frame["price"]) == DataTypeChoices.FLOAT.value
    assert frame["active"].tolist() == [True, False]
    assert determine_data_type(frame["active"]) == DataTypeChoices.BOOLEAN.value
    assert frame["missing"].isna().all()
    for name in ("payload", "tags"):
        assert frame[name].tolist() == [record[name] for record in RECORDS]


@pytest.mark.parametrize("extension", ["json", "jsonl"])
def test_legacy_reader_does_not_infer_string_scalars(extension):
    records = [
        {"code": "001.50", "number": "123", "created_at": "2026-11-09T01:02:03Z"},
        {"code": "002e3", "number": "456", "created_at": "2026-11-10T01:02:03Z"},
    ]
    frame = _read(records, extension)

    for name in records[0]:
        assert frame[name].tolist() == [record[name] for record in records]
    assert determine_data_type(frame["code"]) == DataTypeChoices.TEXT.value
    # Ordinary numeric text can still be inferred by the column classifier.
    assert determine_data_type(frame["number"]) == DataTypeChoices.INTEGER.value


@pytest.mark.parametrize("extension", ["json", "jsonl"])
def test_legacy_reader_keeps_mixed_identifier_and_native_number(extension):
    frame = _read([{"code": "00123"}, {"code": 456}, {"code": None}], extension)

    assert frame["code"].iloc[:2].tolist() == ["00123", 456]
    assert pd.isna(frame["code"].iloc[2])
    assert determine_data_type(frame["code"]) == DataTypeChoices.TEXT.value


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(
            {"code": ["00123", "00456"], "quantity": [123, 456]},
            id="column-arrays",
        ),
        pytest.param(
            {
                "code": {"first": "00123", "second": "00456"},
                "quantity": {"first": 123, "second": 456},
            },
            id="indexed-columns",
        ),
    ],
)
def test_legacy_reader_retains_supported_column_shapes(document):
    frame, error = FileProcessor.process_file(
        SimpleUploadedFile("columns.json", json.dumps(document).encode("utf-8"))
    )

    assert error is None, error
    assert frame["code"].tolist() == ["00123", "00456"]
    assert frame["quantity"].tolist() == [123, 456]


def test_legacy_reader_retains_single_record_fallback():
    record = {"code": "00123", "tags": ["one", "two"], "payload": [{"ok": True}]}
    frame, error = FileProcessor.process_file(
        SimpleUploadedFile("record.json", json.dumps(record).encode("utf-8"))
    )

    assert error is None, error
    assert frame.to_dict(orient="records") == [record]
    assert determine_data_type(frame["code"]) == DataTypeChoices.TEXT.value


@pytest.mark.parametrize(
    "values",
    [
        pytest.param(["00123", "00456"], id="zero-prefixed"),
        pytest.param(["00", "01"], id="not-boolean"),
        pytest.param(["+00123", "-00456"], id="signed"),
        pytest.param(["001.50", "002.75"], id="no-float-fallback"),
        pytest.param(["001e2", "002E+3"], id="no-exponent-fallback"),
        pytest.param(["00123", "456"], id="mixed-numeric-text"),
        pytest.param(["00123", 456], id="mixed-native-number"),
        pytest.param([None, "00123"], id="nullable"),
        pytest.param([" 00123 ", "00456"], id="surrounding-whitespace"),
    ],
)
def test_zero_prefixed_identifier_inference_stays_text(values):
    assert determine_data_type(pd.Series(values)) == DataTypeChoices.TEXT.value


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        pytest.param([123, 456], "integer", id="native-integers"),
        pytest.param([0, 1], "integer", id="native-zero-one"),
        pytest.param([1.25, 2.75], "float", id="native-floats"),
        pytest.param([True, False], "boolean", id="native-booleans"),
        pytest.param(["123", "456"], "integer", id="integer-text"),
        pytest.param(["0", "2"], "integer", id="single-zero"),
        pytest.param(["-12", "+34"], "integer", id="signed-integer-text"),
        pytest.param(["0.25", "0.75"], "float", id="fractional-text"),
        pytest.param(["1e2", "2e3"], "float", id="exponent-text"),
        pytest.param(["true", "false"], "boolean", id="boolean-text"),
        pytest.param(["first", "second"], "text", id="ordinary-text"),
        pytest.param([None, None], "text", id="empty-column"),
    ],
)
def test_ordinary_column_types_are_unchanged(values, expected):
    assert determine_data_type(pd.Series(values)) == expected


@pytest.mark.parametrize("extension", ["json", "jsonl"])
def test_legacy_reader_still_reports_invalid_json(extension):
    frame, error = FileProcessor.process_file(
        SimpleUploadedFile(f"broken.{extension}", b'{"code": "00123"')
    )

    assert frame is None
    assert error.startswith(f"Invalid {extension.upper()} format:")


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
@pytest.mark.parametrize("state", ["unset", "false"])
@pytest.mark.parametrize("extension", ["json", "jsonl"])
def test_authenticated_legacy_upload_preserves_saved_grid_and_download_identifiers(
    auth_client, workspace, settings, monkeypatch, tmp_path, state, extension
):
    """Only external storage, dispatch and usage I/O are replaced, not persistence."""
    if state == "unset":
        del settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED
    else:
        settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED = False
    filename = f"identifiers.{extension}"
    content = _content(RECORDS, extension)
    local_file = tmp_path / filename
    queued = []
    uploaded = []

    def upload(file_obj, object_key, org_id=None):
        file_obj.seek(0)
        payload = file_obj.read()
        uploaded.append(payload)
        local_file.write_bytes(payload)
        return f"minio://{object_key}"

    def download(file_url, original_filename=None):
        assert file_url == queued[0][1]
        assert original_filename == filename
        return str(local_file)

    parser = Mock(wraps=FileProcessor.process_file)
    previewed = Mock(side_effect=AssertionError("The previewed reader must stay off"))
    media_dispatch = Mock()
    monkeypatch.setattr(FileProcessor, "process_file", staticmethod(parser))
    monkeypatch.setattr(file_upload, "read_previewed_json", previewed)
    monkeypatch.setattr(file_upload, "log_and_deduct_cost_for_resource_request", None)
    monkeypatch.setattr(file_upload, "upload_file_to_minio", upload)
    monkeypatch.setattr(file_upload, "download_file_from_minio", download)
    monkeypatch.setattr(file_upload.process_media_uploads, "delay", media_dispatch)
    monkeypatch.setattr(
        file_upload.process_dataset_from_file, "delay", lambda *args: queued.append(args)
    )

    response = auth_client.post(
        UPLOAD,
        {
            "new_dataset_name": f"Identifier fidelity {state} {extension}",
            "model_type": "GenerativeLLM",
            "file": SimpleUploadedFile(filename, content),
        },
        format="multipart",
    )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.workspace_id == workspace.id
    assert READER_KEY not in dataset.dataset_config
    assert result["estimated_rows"] == len(RECORDS)
    assert result["processing_status"] == "queued"
    assert parser.call_count == 1
    assert uploaded == [content]
    assert queued == [(str(dataset.id), dataset.dataset_config["file_url"], filename)]

    # Execute the real worker after the HTTP request, against its persisted job.
    file_upload.process_dataset_from_file.run_sync(*queued[0])
    dataset.refresh_from_db()
    assert dataset.dataset_config["file_processing_status"] == "completed"
    assert READER_KEY not in dataset.dataset_config
    assert parser.call_count == 2
    previewed.assert_not_called()
    media_dispatch.assert_not_called()

    columns = {
        column.name: column
        for column in Column.objects.filter(dataset=dataset, deleted=False)
    }
    rows = list(Row.objects.filter(dataset=dataset, deleted=False).order_by("order"))
    cells = {
        (cell.row_id, cell.column_id): cell.value
        for cell in Cell.objects.filter(dataset=dataset, deleted=False)
    }
    assert len(rows) == len(RECORDS)
    assert len(cells) == len(RECORDS) * len(RECORDS[0])
    assert [cells[(row.id, columns["code"].id)] for row in rows] == ["00123", "00456"]
    assert {name: column.data_type for name, column in columns.items()} == {
        "code": "text",
        "prompt": "text",
        "quantity": "integer",
        "price": "float",
        "active": "boolean",
        "missing": "text",
        "payload": "json",
        "tags": "array",
    }
    for row, record in zip(rows, RECORDS, strict=True):
        for name, value in record.items():
            saved = cells[(row.id, columns[name].id)]
            if isinstance(value, (dict, list)):
                assert json.loads(saved) == value
            else:
                assert saved == (None if value is None else str(value))

    grid = auth_client.get(f"/model-hub/develops/{dataset.id}/get-dataset-table/")
    assert grid.status_code == 200, grid.content
    grid_rows = {row["row_id"]: row for row in grid.json()["result"]["table"]}
    assert [
        grid_rows[str(row.id)][str(columns["code"].id)]["cell_value"] for row in rows
    ] == ["00123", "00456"]

    exported = auth_client.get(f"/model-hub/develops/{dataset.id}/download_dataset/")
    assert exported.status_code == 200
    try:
        csv_text = b"".join(exported.streaming_content).decode("utf-8")
    finally:
        exported.close()
    exported_rows = list(csv.DictReader(StringIO(csv_text)))
    assert [row["code"] for row in exported_rows] == ["00123", "00456"]
    for row, record in zip(exported_rows, RECORDS, strict=True):
        assert row["prompt"] == record["prompt"]
        assert int(row["quantity"]) == record["quantity"]
        assert float(row["price"]) == record["price"]
        assert row["active"] == str(record["active"])
        assert row["missing"] == ""
        assert json.loads(row["payload"]) == record["payload"]
        assert json.loads(row["tags"]) == record["tags"]
