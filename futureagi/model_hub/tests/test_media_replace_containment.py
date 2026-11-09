"""A refused media replacement must preserve the previously saved cell.

These tests exercise the real view method with an in-memory persistence boundary;
the incomplete-document-link case also executes the real storage validation.
No database, network, or object storage is needed.
"""

import copy
import inspect
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model_hub.models.choices import CellStatus, DataTypeChoices
from model_hub.views import develop_dataset as views
from tfc.utils import storage


MEDIA = ["image", "audio", "document", "images"]
HELPERS = {
    "image": "upload_image_to_s3",
    "audio": "upload_audio_to_s3_duration",
    "document": "upload_document_to_s3",
    "images": "upload_image_to_s3",
}


class SavedCell:
    def __init__(self, media_type):
        self.column = SimpleNamespace(
            data_type=getattr(DataTypeChoices, media_type.upper()).value,
            source="others", name="Attachment",
        )
        self.column_metadata = {"audio_duration_seconds": 12}
        self.value = "https://saved.example/original.pdf"
        self.value_infos = json.dumps({
            "document_name": "original.pdf", "annotation": "keep this metadata",
        })
        self.status = CellStatus.ERROR.value
        self.persisted = self.snapshot()
        self.original = copy.deepcopy(self.persisted)
        self.saves = 0

    def snapshot(self):
        return {name: copy.deepcopy(getattr(self, name))
                for name in ("value", "value_infos", "status", "column_metadata")}

    def save(self):
        self.saves += 1
        self.persisted = self.snapshot()


@pytest.fixture
def update(monkeypatch):
    def invoke(media_type, new_value="https://replacement.example/new.pdf", files=None):
        cell = SavedCell(media_type)
        queryset = Mock()
        queryset.filter.return_value.select_related.return_value.first.return_value = cell
        monkeypatch.setattr(views, "Cell", SimpleNamespace(objects=queryset))
        monkeypatch.setattr(views, "_request_row_queryset", Mock(return_value=Mock()))
        gm = SimpleNamespace(
            bad_request=lambda message: SimpleNamespace(status_code=400, data=message),
            success_response=lambda message: SimpleNamespace(status_code=200, data=message),
            internal_server_error_response=lambda message: SimpleNamespace(status_code=500, data=message),
        )
        view = views.UpdateCellValueView()
        view._gm = gm
        payload = {"row_id": "row-1", "column_id": "column-1", "new_value": new_value}
        request = SimpleNamespace(validated_data=payload, data=payload, FILES=files or {})
        response = inspect.unwrap(views.UpdateCellValueView.post)(view, request, dataset_id="dataset-1")
        return response, cell

    return invoke


def assert_preserved(cell):
    assert cell.persisted == cell.original, "refused replacement overwrote the saved cell"
    assert cell.snapshot() == cell.original, "refused replacement mutated the in-memory cell"
    assert cell.saves == 0, "a refused replacement must not call save"


def test_incomplete_document_link_preserves_saved_cell(update, monkeypatch):
    # This is the reported UI payload; validation must reject it before I/O.
    monkeypatch.setattr(views, "upload_document_to_s3", storage.upload_document_to_s3)
    download = Mock(side_effect=AssertionError("invalid URL must not be downloaded"))
    client = Mock(side_effect=AssertionError("invalid URL must not reach storage"))
    monkeypatch.setattr(storage, "download_document_from_url", download)
    monkeypatch.setattr(storage, "get_storage_client", client)

    response, cell = update("document", "http://")

    assert response.status_code == 400
    assert response.data == storage.get_storage_error_message("INVALID_URL")
    download.assert_not_called()
    client.assert_not_called()
    assert_preserved(cell)


@pytest.mark.parametrize("media_type", MEDIA)
def test_upload_failure_preserves_saved_cell(update, monkeypatch, media_type):
    upload = Mock(side_effect=ValueError("storage unavailable"))
    monkeypatch.setattr(views, HELPERS[media_type], upload)

    response, cell = update(media_type)

    assert response.status_code == 400
    upload.assert_called_once()
    assert_preserved(cell)


def test_partial_multi_image_failure_preserves_saved_cell(update, monkeypatch):
    upload = Mock(side_effect=["https://saved.example/first.png", ValueError("second failed")])
    monkeypatch.setattr(views, "upload_image_to_s3", upload)

    response, cell = update("images", json.dumps([
        "https://replacement.example/first.png", "https://replacement.example/second.png",
    ]))

    assert response.status_code == 400
    assert upload.call_count == 2
    assert_preserved(cell)


@pytest.mark.parametrize("media_type", ["image", "audio", "document"])
def test_file_read_failure_preserves_saved_cell(update, monkeypatch, media_type):
    file = SimpleNamespace(name="replacement.pdf", size=10, content_type="application/pdf",
                           read=Mock(side_effect=OSError("file read failed")))
    upload = Mock(side_effect=AssertionError("unreadable file must not be uploaded"))
    monkeypatch.setattr(views, HELPERS[media_type], upload)

    response, cell = update(media_type, file, files={"new_value": file})

    assert response.status_code == 400
    file.read.assert_called_once()
    upload.assert_not_called()
    assert_preserved(cell)


@pytest.mark.parametrize("media_type", MEDIA)
def test_successful_replacement_saves_new_value(update, monkeypatch, media_type):
    replacement = "https://saved.example/replacement.pdf"
    result = (replacement, 3) if media_type == "audio" else replacement
    upload = Mock(return_value=result)
    monkeypatch.setattr(views, HELPERS[media_type], upload)

    response, cell = update(media_type)

    assert response.status_code == 200
    upload.assert_called_once()
    assert cell.saves == 1
    expected = json.dumps([replacement]) if media_type == "images" else replacement
    assert cell.persisted["value"] == expected
    assert cell.persisted["status"] == CellStatus.PASS.value
    metadata = json.loads(cell.persisted["value_infos"])
    if media_type in ("audio", "document"):
        assert metadata[f"{media_type}_url"] == replacement
        assert metadata["annotation"] == "keep this metadata"
    else:
        assert metadata == {}


@pytest.mark.parametrize("media_type", MEDIA)
@pytest.mark.parametrize("empty", [None, "", "  ", []], ids=["null", "empty", "blank", "empty-list"])
def test_explicit_clear_still_clears_saved_cell(update, monkeypatch, media_type, empty):
    upload = Mock(side_effect=AssertionError("explicit clear must not upload"))
    monkeypatch.setattr(views, HELPERS[media_type], upload)

    response, cell = update(media_type, empty)

    assert response.status_code == 200
    upload.assert_not_called()
    assert cell.saves == 1
    assert cell.persisted["value"] is None
    assert cell.persisted["value_infos"] == "{}"
    assert cell.persisted["status"] == CellStatus.PASS.value


@pytest.mark.parametrize("media_type", MEDIA)
@pytest.mark.parametrize("value", [
    "not-a-url", "example.com/document.pdf", {"url": "not-a-url"},
    ["not-a-url"], False, 0, {},
], ids=["unrecognized", "schemeless", "mapping", "list", "false", "zero", "empty-object"])
def test_unrecognized_media_replacement_preserves_saved_cell(update, monkeypatch, media_type, value):
    helper = HELPERS[media_type]
    monkeypatch.setattr(views, helper, getattr(storage, helper))
    boundaries = []
    for name in ("get_storage_client", "download_image_from_url",
                 "download_audio_from_url", "download_document_from_url"):
        boundary = Mock(side_effect=AssertionError("invalid media must not reach I/O"))
        monkeypatch.setattr(storage, name, boundary)
        boundaries.append(boundary)

    response, cell = update(media_type, value)

    assert response.status_code == 400
    assert_preserved(cell)
    for boundary in boundaries:
        boundary.assert_not_called()


@pytest.mark.parametrize("value", [
    [None, "", False], '[null, "", false]', "{}", '\"\"',
], ids=["empty-entries", "json-empty-entries", "json-object", "json-empty-string"])
def test_images_without_usable_entries_preserve_saved_cell(update, monkeypatch, value):
    upload = Mock(side_effect=AssertionError("no image to upload"))
    monkeypatch.setattr(views, "upload_image_to_s3", upload)

    response, cell = update("images", value)

    assert response.status_code == 400
    upload.assert_not_called()
    assert_preserved(cell)


@pytest.mark.parametrize("empty", ["[]", "[  ]"], ids=["json-empty-list", "json-spaced-empty-list"])
def test_explicit_json_empty_images_list_keeps_existing_clear_behavior(update, monkeypatch, empty):
    upload = Mock(side_effect=AssertionError("explicit clear must not upload"))
    monkeypatch.setattr(views, "upload_image_to_s3", upload)

    response, cell = update("images", empty)

    assert response.status_code == 200
    upload.assert_not_called()
    assert cell.saves == 1
    assert cell.persisted["value"] == "[]"
    assert cell.persisted["value_infos"] == "{}"
    assert cell.persisted["status"] == CellStatus.PASS.value
