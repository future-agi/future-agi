"""Uploaded and stored JSON files are bounded before either parser reads them."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import MemoryFileUploadHandler
from rest_framework import status

from model_hub.constants import MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.services import file_import_admission as admission
from model_hub.services import file_import_json
from model_hub.views.datasets.create import file_upload

UPLOAD = "/model-hub/develops/create-dataset-from-local-file/"
CONTENT = b'{"name": "sample"}\n'


@pytest.fixture(autouse=True)
def default_reader(monkeypatch):
    monkeypatch.delenv(admission.SETTING, raising=False)
    assert admission.previewed_json_enabled() is True


@pytest.fixture
def readers(monkeypatch):
    spies = SimpleNamespace(
        previewed=Mock(wraps=file_upload.read_previewed_json),
        json=Mock(wraps=file_import_json.read_json_import),
        legacy=Mock(wraps=file_upload.FileProcessor.process_file),
    )
    monkeypatch.setattr(file_upload, "read_previewed_json", spies.previewed)
    monkeypatch.setattr(file_import_json, "read_json_import", spies.json)
    monkeypatch.setattr(file_upload.FileProcessor, "process_file", spies.legacy)
    return spies


@pytest.fixture
def reported_upload(monkeypatch, settings):
    settings.FILE_UPLOAD_HANDLERS = [
        "django.core.files.uploadhandler.MemoryFileUploadHandler"
    ]
    settings.FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
    recorded = SimpleNamespace(size=MAX_FILE_SIZE_BYTES + 1, file=None, read=None)
    original_complete = MemoryFileUploadHandler.file_complete

    def complete(handler, file_size):
        upload = original_complete(handler, file_size)
        upload.size = recorded.size
        recorded.file = upload
        # InMemoryUploadedFile.read is a read-only property over its stream,
        # so spy on the stream itself.
        recorded.read = Mock(wraps=upload.file.read)
        upload.file.read = recorded.read
        return upload

    # Override the parsed upload's reported size, not the client's input stream.
    monkeypatch.setattr(MemoryFileUploadHandler, "file_complete", complete)
    return recorded


@pytest.fixture
def upload_side_effects(monkeypatch):
    effects = SimpleNamespace(
        store=Mock(return_value="minio://uploads/records"),
        queue=Mock(),
    )
    monkeypatch.setattr(file_upload, "upload_file_to_minio", effects.store)
    monkeypatch.setattr(file_upload.process_dataset_from_file, "delay", effects.queue)
    monkeypatch.setattr(file_upload, "log_and_deduct_cost_for_resource_request", None)
    return effects


def _post(auth_client, filename):
    return auth_client.post(
        UPLOAD,
        {
            "new_dataset_name": f"Upload {filename}",
            "model_type": "GenerativeLLM",
            "source": "build",
            "file": SimpleUploadedFile(filename, CONTENT),
        },
        format="multipart",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("filename", ["records.json", "records.jsonl"])
def test_route_refuses_oversized_json_before_reading_or_storing(
    auth_client, reported_upload, readers, upload_side_effects, filename
):
    before = Dataset.no_workspace_objects.count()

    response = _post(auth_client, filename)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    assert response.json()["message"] == (
        f"File size exceeds the maximum allowed limit of {MAX_FILE_SIZE_MB} MB."
    )
    assert reported_upload.file is not None
    assert reported_upload.file.size == MAX_FILE_SIZE_BYTES + 1
    reported_upload.read.assert_not_called()
    readers.previewed.assert_not_called()
    readers.json.assert_not_called()
    readers.legacy.assert_not_called()
    upload_side_effects.store.assert_not_called()
    upload_side_effects.queue.assert_not_called()
    assert Dataset.no_workspace_objects.count() == before
    assert not Dataset.no_workspace_objects.filter(name=f"Upload {filename}").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("filename", ["records.json", "records.jsonl"])
def test_route_accepts_json_at_the_reported_size_limit(
    auth_client, reported_upload, readers, upload_side_effects, filename
):
    reported_upload.size = MAX_FILE_SIZE_BYTES

    response = _post(auth_client, filename)

    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.dataset_config[admission.READER_KEY] == admission.PREVIEWED_JSON
    assert (result["estimated_rows"], result["estimated_columns"]) == (1, 1)
    readers.previewed.assert_called_once()
    readers.json.assert_called_once()
    readers.legacy.assert_not_called()
    reported_upload.read.assert_called_once()
    upload_side_effects.store.assert_called_once()
    upload_side_effects.queue.assert_called_once_with(
        str(dataset.id), dataset.dataset_config["file_url"], filename
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("filename", ["records.json", "records.jsonl"])
def test_worker_refuses_oversized_stored_json_before_opening(
    organization, workspace, user, monkeypatch, tmp_path, readers, filename
):
    file_url = f"minio://uploads/{filename}"
    dataset = Dataset.objects.create(
        name=f"Stored {filename}",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type="GenerativeLLM",
        dataset_config={
            "dataset_source_local": True,
            "file_processing_status": "queued",
            "original_filename": filename,
            "file_url": file_url,
            admission.READER_KEY: admission.PREVIEWED_JSON,
        },
    )
    stored_file = tmp_path / filename
    # A sparse file exercises the real stat without allocating the body in memory.
    with stored_file.open("wb") as stream:
        stream.truncate(MAX_FILE_SIZE_BYTES + 1)
    download = Mock(return_value=str(stored_file))
    open_file = Mock(side_effect=AssertionError("The stored file must not be opened"))
    media = Mock()
    monkeypatch.setattr(file_upload, "download_file_from_minio", download)
    monkeypatch.setattr(file_upload, "open", open_file, raising=False)
    monkeypatch.setattr(file_upload.process_media_uploads, "delay", media)

    with pytest.raises(ValueError) as error:
        file_upload.process_dataset_from_file.run_sync(
            str(dataset.id), file_url, filename
        )

    message = "File import exceeds 25 MiB. Split the file into smaller imports."
    assert str(error.value) == message
    dataset.refresh_from_db()
    assert dataset.dataset_config["file_processing_status"] == "failed"
    assert dataset.dataset_config["file_processing_error"] == message
    assert dataset.dataset_config["file_processing_failed_at"]
    assert dataset.dataset_config[admission.READER_KEY] == admission.PREVIEWED_JSON
    download.assert_called_once_with(file_url, filename)
    open_file.assert_not_called()
    readers.previewed.assert_not_called()
    readers.json.assert_not_called()
    readers.legacy.assert_not_called()
    media.assert_not_called()
    assert not Column.objects.filter(dataset=dataset).exists()
    assert not Row.objects.filter(dataset=dataset).exists()
    assert not Cell.objects.filter(dataset=dataset).exists()
    assert not stored_file.exists()
