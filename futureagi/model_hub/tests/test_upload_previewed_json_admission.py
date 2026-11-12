"""F32 admission: only uploads the route admitted while ON use the previewed reader.

The switch is the Django setting DSX_PREVIEWED_JSON_UPLOAD_ENABLED (default
off). The route records the admission on the queued dataset; the worker reads
that record, never the file name alone, and refuses an admitted job that
reaches it while the switch is off.
"""

import json
import os
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.services import file_import_admission as admission
from model_hub.services import file_import_json
from model_hub.utils.file_reader import FileProcessor
from model_hub.views.datasets.create import file_upload

UPLOAD = "/model-hub/develops/create-dataset-from-local-file/"
CATALOG = json.dumps(
    [
        {"sku": "007", "title": "Desk lamp", "price": 12.5},
        {"sku": "042", "title": "Café chair", "price": 40.0},
    ]
)
CATALOG_CELLS = [["007", "Desk lamp", "12.5"], ["042", "Café chair", "40"]]


@pytest.fixture
def switch(settings):
    def turn(state):
        settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED = state

    return turn


@pytest.fixture
def reader_calls(monkeypatch):
    calls = []
    original = file_import_json.read_json_import

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(file_import_json, "read_json_import", spy)
    return calls


@pytest.fixture
def file_processor_calls(monkeypatch):
    calls = []
    original = FileProcessor.process_file

    def spy(file_obj):
        calls.append(getattr(file_obj, "name", None))
        return original(file_obj=file_obj)

    monkeypatch.setattr(FileProcessor, "process_file", staticmethod(spy))
    return calls


@pytest.fixture
def worker(monkeypatch, tmp_path):
    """Runs the file worker on `content`; records downloads and media handoffs."""
    record = SimpleNamespace(downloads=[], media=[])

    def run(dataset, filename, content):
        path = tmp_path / filename
        path.write_text(content, encoding="utf-8")

        def download(file_url, original_filename=None):
            record.downloads.append(file_url)
            return str(path)

        monkeypatch.setattr(file_upload, "download_file_from_minio", download)
        monkeypatch.setattr(
            file_upload.process_media_uploads,
            "delay",
            lambda *args, **kwargs: record.media.append(args),
        )
        file_upload.process_dataset_from_file.run_sync(
            str(dataset.id), f"minio://{filename}", filename
        )
        dataset.refresh_from_db()

    record.run = run
    return record


def _admitted_dataset(organization, workspace, user, filename, reader):
    return Dataset.objects.create(
        name=f"Admitted {filename}",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type="GenerativeLLM",
        dataset_config={
            "dataset_source_local": True,
            "file_processing_status": "queued",
            "original_filename": filename,
            "file_url": f"minio://{filename}",
            admission.READER_KEY: reader,
        },
    )


def _table_counts(dataset):
    return (
        Column.objects.filter(dataset=dataset).count(),
        Row.objects.filter(dataset=dataset).count(),
        Cell.objects.filter(dataset=dataset).count(),
    )


def _cells(dataset):
    order = dataset.column_order
    values = {
        (cell.row_id, str(cell.column_id)): cell.value
        for cell in Cell.objects.filter(dataset=dataset, deleted=False)
    }
    rows = Row.objects.filter(dataset=dataset, deleted=False).order_by("order")
    return [[values[(row.id, column_id)] for column_id in order] for row in rows]


def test_switch_admits_only_an_explicit_true(settings):
    del settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED
    assert admission.previewed_json_enabled() is False
    for value in (False, None, "true", "1", 1):
        settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED = value
        assert admission.previewed_json_enabled() is False, value
    settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED = True
    assert admission.previewed_json_enabled() is True


def test_setting_defaults_off_without_its_env_var():
    if admission.SETTING in os.environ:
        pytest.skip(f"{admission.SETTING} is set in this environment")
    from django.conf import settings as live_settings

    assert getattr(live_settings, admission.SETTING) is False


def _request(headers=None, org_api_key=None):
    request = SimpleNamespace(headers=headers or {})
    if org_api_key is not None:
        request.org_api_key = org_api_key
    return request


@pytest.mark.parametrize(
    ("enabled", "filename", "source", "request_kwargs", "reader"),
    [
        (True, "catalog.json", "build", {}, admission.PREVIEWED_JSON),
        (True, "Tickets.JSONL", "build", {}, admission.PREVIEWED_JSON),
        (False, "catalog.json", "build", {}, None),
        (None, "catalog.json", "build", {}, None),
        (True, "regions.csv", "build", {}, None),
        (True, "sheet.xlsx", "build", {}, None),
        (True, "sheet.xls", "build", {}, None),
        (True, "catalog.json", "sdk", {}, None),
        (True, "catalog.json", "observe", {}, None),
        (True, "catalog.json", "build", {"headers": {"X-Api-Key": "key"}}, None),
        (True, "catalog.json", "build", {"org_api_key": object()}, None),
    ],
    ids=[
        "json",
        "jsonl",
        "off",
        "off-none",
        "csv",
        "xlsx",
        "xls",
        "sdk-source",
        "other-source",
        "api-key-header",
        "api-key-auth",
    ],
)
def test_route_admits_previewed_json_only_for_signed_in_dialog_uploads(
    switch, enabled, filename, source, request_kwargs, reader
):
    switch(enabled)
    assert admission.upload_reader(_request(**request_kwargs), filename, source) == (
        reader
    )


@pytest.mark.parametrize(
    ("enabled", "config", "decision"),
    [
        (True, {}, (None, None)),
        (False, {}, (None, None)),
        (True, None, (None, None)),
        (
            True,
            {admission.READER_KEY: admission.PREVIEWED_JSON},
            (admission.PREVIEWED_JSON, None),
        ),
        (
            False,
            {admission.READER_KEY: admission.PREVIEWED_JSON},
            (None, admission.HELD_MESSAGE),
        ),
        (
            True,
            {admission.READER_KEY: "previewed_json_v0"},
            (None, admission.HELD_MESSAGE),
        ),
        (True, {admission.READER_KEY: ""}, (None, admission.HELD_MESSAGE)),
    ],
    ids=[
        "unmarked-on",
        "unmarked-off",
        "no-config",
        "marked-on",
        "marked-off",
        "unknown-marker",
        "empty-marker",
    ],
)
def test_worker_follows_the_recorded_admission(switch, enabled, config, decision):
    switch(enabled)
    assert admission.worker_reader(config) == decision


@pytest.mark.parametrize(
    ("enabled", "filename"),
    [(False, "catalog.json"), (None, "catalog.json"), (True, "catalog.csv")],
    ids=["off", "off-none", "not-json"],
)
def test_reader_entry_refuses_without_parsing(switch, reader_calls, enabled, filename):
    switch(enabled)
    upload = SimpleUploadedFile(filename, CATALOG.encode("utf-8"))

    assert admission.read_previewed_json(upload, filename) == (
        None,
        None,
        admission.HELD_MESSAGE,
    )
    assert reader_calls == []


def test_reader_entry_reads_previewed_records_while_on(switch, reader_calls):
    switch(True)
    upload = SimpleUploadedFile("catalog.json", CATALOG.encode("utf-8"))

    frame, data_types, error = admission.read_previewed_json(upload, "catalog.json")

    assert error is None
    assert frame.columns.tolist() == ["sku", "title", "price"]
    assert frame.values.tolist() == CATALOG_CELLS
    assert data_types == ("text", "text", "float")
    assert len(reader_calls) == 1


@pytest.mark.django_db(transaction=True)
def test_admitted_job_reaching_an_off_worker_is_refused_before_download(
    organization, workspace, user, switch, worker, reader_calls, file_processor_calls
):
    switch(False)
    dataset = _admitted_dataset(
        organization, workspace, user, "catalog.json", admission.PREVIEWED_JSON
    )

    for _delivery in range(2):  # first delivery, then a redelivery
        worker.run(dataset, "catalog.json", CATALOG)

        config = dataset.dataset_config
        assert config["file_processing_status"] == "failed"
        assert config["file_processing_error"] == admission.HELD_MESSAGE
        assert config["file_processing_failed_at"]
        assert config[admission.READER_KEY] == admission.PREVIEWED_JSON
        assert _table_counts(dataset) == (0, 0, 0)
    assert worker.downloads == []
    assert worker.media == []
    assert file_processor_calls == []
    assert reader_calls == []


@pytest.mark.django_db(transaction=True)
def test_unknown_admission_record_is_refused_while_on(
    organization, workspace, user, switch, worker, reader_calls, file_processor_calls
):
    switch(True)
    dataset = _admitted_dataset(
        organization, workspace, user, "catalog.json", "previewed_json_v0"
    )

    worker.run(dataset, "catalog.json", CATALOG)

    assert dataset.dataset_config["file_processing_status"] == "failed"
    assert dataset.dataset_config["file_processing_error"] == admission.HELD_MESSAGE
    assert _table_counts(dataset) == (0, 0, 0)
    assert worker.downloads == []
    assert file_processor_calls == []
    assert reader_calls == []


@pytest.mark.django_db(transaction=True)
def test_admitted_job_publishes_previewed_cells_while_on(
    organization, workspace, user, switch, worker, reader_calls, file_processor_calls
):
    switch(True)
    dataset = _admitted_dataset(
        organization, workspace, user, "catalog.json", admission.PREVIEWED_JSON
    )

    worker.run(dataset, "catalog.json", CATALOG)

    assert dataset.dataset_config["file_processing_status"] == "completed"
    assert _cells(dataset) == CATALOG_CELLS
    assert len(reader_calls) == 1
    assert file_processor_calls == []


@pytest.mark.django_db(transaction=True)
def test_published_admitted_upload_redelivered_while_off_keeps_its_table(
    organization, workspace, user, switch, worker, reader_calls
):
    switch(True)
    dataset = _admitted_dataset(
        organization, workspace, user, "catalog.json", admission.PREVIEWED_JSON
    )
    worker.run(dataset, "catalog.json", CATALOG)
    published = (_table_counts(dataset), _cells(dataset), list(worker.downloads))
    reader_calls.clear()
    switch(False)

    worker.run(dataset, "catalog.json", CATALOG)

    assert dataset.dataset_config["file_processing_status"] == "completed"
    assert (_table_counts(dataset), _cells(dataset), worker.downloads) == published
    assert reader_calls == []


class _SuccessfulResourceCallLog:
    status = "created"

    def save(self):
        return None


@pytest.fixture
def upload_route(monkeypatch):
    queued = []
    monkeypatch.setattr(
        file_upload,
        "log_and_deduct_cost_for_resource_request",
        lambda *args, **kwargs: _SuccessfulResourceCallLog(),
    )
    monkeypatch.setattr(
        file_upload,
        "upload_file_to_minio",
        lambda file_obj, object_key, org_id=None: f"minio://{object_key}",
    )
    monkeypatch.setattr(
        file_upload.process_dataset_from_file,
        "delay",
        lambda *args, **kwargs: queued.append(args),
    )
    # The API-key branch reports an SDK create; keep it off the network.
    monkeypatch.setattr(file_upload, "get_mixpanel_properties", lambda **kwargs: {})
    monkeypatch.setattr(
        file_upload, "track_mixpanel_event", lambda *args, **kwargs: None
    )
    return queued


def _post(auth_client, name, filename, content, extra=None, **headers):
    return auth_client.post(
        UPLOAD,
        {
            "new_dataset_name": name,
            "model_type": "GenerativeLLM",
            "file": SimpleUploadedFile(filename, content.encode("utf-8")),
            **(extra or {}),
        },
        format="multipart",
        **headers,
    )


@pytest.mark.django_db
def test_route_records_the_admission_on_the_queued_dataset_while_on(
    auth_client, switch, upload_route, reader_calls, file_processor_calls
):
    switch(True)

    response = _post(auth_client, "Admitted catalog", "catalog.json", CATALOG)

    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    assert (result["estimated_rows"], result["estimated_columns"]) == (2, 3)
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.dataset_config[admission.READER_KEY] == admission.PREVIEWED_JSON
    assert upload_route == [
        (str(dataset.id), dataset.dataset_config["file_url"], "catalog.json")
    ]
    assert len(reader_calls) == 1
    assert file_processor_calls == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("extra", "headers"),
    [({"source": "sdk"}, {}), ({}, {"HTTP_X_API_KEY": "sdk-key"})],
    ids=["sdk-source", "api-key-header"],
)
def test_route_keeps_file_processor_for_sdk_uploads_while_on(
    auth_client,
    switch,
    upload_route,
    reader_calls,
    file_processor_calls,
    extra,
    headers,
):
    switch(True)

    response = _post(
        auth_client, "SDK catalog", "catalog.json", CATALOG, extra=extra, **headers
    )

    assert response.status_code == status.HTTP_200_OK, response.json()
    dataset = Dataset.no_workspace_objects.get(
        id=response.json()["result"]["dataset_id"]
    )
    assert admission.READER_KEY not in dataset.dataset_config
    assert file_processor_calls == ["catalog.json"]
    assert reader_calls == []


@pytest.mark.django_db(transaction=True)
def test_upload_admitted_while_on_is_refused_if_the_switch_turns_off_before_its_worker(
    auth_client, switch, upload_route, worker, reader_calls, file_processor_calls
):
    switch(True)
    response = _post(auth_client, "Switched catalog", "catalog.json", CATALOG)
    assert response.status_code == status.HTTP_200_OK, response.json()
    dataset = Dataset.no_workspace_objects.get(
        id=response.json()["result"]["dataset_id"]
    )
    reader_calls.clear()
    switch(False)

    worker.run(dataset, "catalog.json", CATALOG)

    assert dataset.dataset_config["file_processing_status"] == "failed"
    assert dataset.dataset_config["file_processing_error"] == admission.HELD_MESSAGE
    assert _table_counts(dataset) == (0, 0, 0)
    assert worker.downloads == []
    assert reader_calls == []
    assert file_processor_calls == []
