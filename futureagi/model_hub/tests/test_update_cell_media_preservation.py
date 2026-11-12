"""A refused replacement must never persist over an existing dataset cell.

Focused unit coverage of the real view method and document URL validator;
only the ORM boundary and external upload calls are mocked. No services.
"""
import copy
import inspect
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model_hub.views import develop_dataset as views
from tfc.utils import storage


@pytest.fixture
def cell_write(monkeypatch):
    original = {
        "value": "https://saved.example/old.pdf",
        "value_infos": json.dumps({"document_name": "old.pdf", "custom": "keep"}),
        "status": views.CellStatus.ERROR.value,
    }
    cell = SimpleNamespace(
        **original, id="cell", column_metadata={},
        column=SimpleNamespace(data_type="document", source="others"),
    )
    persisted = copy.deepcopy(original)

    def save():
        persisted.update({key: getattr(cell, key) for key in original})

    cell.save = Mock(side_effect=save)
    query = Mock()
    query.filter.return_value.select_related.return_value.first.return_value = cell
    monkeypatch.setattr(views.Cell, "objects", query)
    monkeypatch.setattr(views, "_request_row_queryset", Mock())

    def write(data_type, value, files=None):
        cell.column.data_type = data_type
        payload = {"row_id": "row", "column_id": "column", "new_value": value}
        request = SimpleNamespace(validated_data=payload, data=payload, FILES=files or {})
        return inspect.unwrap(views.UpdateCellValueView.post)(
            views.UpdateCellValueView(), request, dataset_id="dataset"
        )

    return cell, original, persisted, write


def assert_preserved(cell, original, persisted):
    assert persisted == original, "refusal overwrote the saved cell"
    assert {key: getattr(cell, key) for key in original} == original
    cell.save.assert_not_called()


def test_incomplete_document_link_preserves_saved_cell(cell_write, monkeypatch):
    cell, original, persisted, write = cell_write
    network = Mock(side_effect=AssertionError("invalid URL must not reach the network"))
    monkeypatch.setattr(storage, "download_document_from_url", network)
    monkeypatch.setattr(views, "upload_document_to_s3", storage.upload_document_to_s3)
    response = write("document", "http://")
    assert response.status_code == 400
    assert "not valid" in str(response.data)
    network.assert_not_called()
    assert_preserved(cell, original, persisted)


@pytest.mark.parametrize("data_type,helper", [
    ("image", "upload_image_to_s3"),
    ("audio", "upload_audio_to_s3_duration"),
    ("document", "upload_document_to_s3"),
    ("images", "upload_image_to_s3"),
])
def test_upload_failure_preserves_saved_cell(cell_write, monkeypatch, data_type, helper):
    cell, original, persisted, write = cell_write
    upload = Mock(side_effect=ValueError("Upload failed"))
    monkeypatch.setattr(views, helper, upload)
    response = write(data_type, "https://replacement.example/file")
    assert response.status_code == 400
    upload.assert_called_once()
    assert_preserved(cell, original, persisted)


def test_partial_images_failure_preserves_saved_cell(cell_write, monkeypatch):
    cell, original, persisted, write = cell_write
    upload = Mock(side_effect=["https://saved.example/new.png", ValueError("second failed")])
    monkeypatch.setattr(views, "upload_image_to_s3", upload)
    response = write("images", json.dumps(["https://new.example/a", "https://new.example/b"]))
    assert response.status_code == 400
    assert upload.call_count == 2
    assert_preserved(cell, original, persisted)


@pytest.mark.parametrize("data_type", ["image", "audio", "document", "images", "datetime"])
@pytest.mark.parametrize("empty", [None, "", "  "], ids=["null", "empty", "blank"])
def test_explicit_clear_still_clears(cell_write, data_type, empty):
    cell, _, persisted, write = cell_write
    response = write(data_type, empty)
    assert response.status_code == 200
    assert persisted == {"value": None, "value_infos": "{}", "status": views.CellStatus.PASS.value}
    cell.save.assert_called_once()


@pytest.mark.parametrize("data_type,helper", [
    ("image", "upload_image_to_s3"),
    ("audio", "upload_audio_to_s3_duration"),
    ("document", "upload_document_to_s3"),
    ("images", "upload_image_to_s3"),
])
def test_successful_replacement_is_saved(cell_write, monkeypatch, data_type, helper):
    cell, _, persisted, write = cell_write
    url = "https://saved.example/new"
    monkeypatch.setattr(views, helper, Mock(return_value=(url, 1) if data_type == "audio" else url))
    response = write(data_type, "https://replacement.example/file")
    assert response.status_code == 200
    assert persisted["value"] == (json.dumps([url]) if data_type == "images" else url)
    assert persisted["status"] == views.CellStatus.PASS.value
    cell.save.assert_called_once()


@pytest.mark.parametrize("data_type,helper", [
    ("image", "upload_image_to_s3"),
    ("audio", "upload_audio_to_s3_duration"),
    ("document", "upload_document_to_s3"),
    ("images", "upload_image_to_s3"),
])
@pytest.mark.parametrize("value", [
    "not-a-url", "example.com/document.pdf", {"url": "not-a-url"},
    ["not-a-url"], False, 0, {},
], ids=["unrecognized", "schemeless", "mapping", "list", "false", "zero", "empty-object"])
def test_unrecognized_media_replacement_preserves_saved_cell(
    cell_write, monkeypatch, data_type, helper, value,
):
    cell, original, persisted, write = cell_write
    monkeypatch.setattr(views, helper, getattr(storage, helper))
    boundaries = []
    for name in ("get_storage_client", "download_image_from_url",
                 "download_audio_from_url", "download_document_from_url"):
        boundary = Mock(side_effect=AssertionError("invalid media must not reach I/O"))
        monkeypatch.setattr(storage, name, boundary)
        boundaries.append(boundary)

    response = write(data_type, value)

    assert response.status_code == 400
    assert_preserved(cell, original, persisted)
    for boundary in boundaries:
        boundary.assert_not_called()


@pytest.mark.parametrize("value", [
    [None, "", False], '[null, "", false]', "{}", '""',
], ids=["empty-entries", "json-empty-entries", "json-object", "json-empty-string"])
def test_images_without_usable_entries_preserve_saved_cell(cell_write, monkeypatch, value):
    cell, original, persisted, write = cell_write
    upload = Mock(side_effect=AssertionError("no image to upload"))
    monkeypatch.setattr(views, "upload_image_to_s3", upload)

    response = write("images", value)

    assert response.status_code == 400
    upload.assert_not_called()
    assert_preserved(cell, original, persisted)


@pytest.mark.parametrize("data_type", ["image", "audio", "document", "images"])
def test_explicit_empty_list_still_clears(cell_write, data_type):
    cell, _, persisted, write = cell_write
    response = write(data_type, [])
    assert response.status_code == 200
    assert persisted == {"value": None, "value_infos": "{}", "status": views.CellStatus.PASS.value}
    cell.save.assert_called_once()


@pytest.mark.parametrize("empty", ["[]", "[  ]"], ids=["json-empty-list", "json-spaced-empty-list"])
def test_explicit_json_empty_images_list_keeps_existing_clear_behavior(cell_write, empty):
    cell, _, persisted, write = cell_write
    response = write("images", empty)
    assert response.status_code == 200
    assert persisted == {"value": "[]", "value_infos": "{}", "status": views.CellStatus.PASS.value}
    cell.save.assert_called_once()
