"""F32 baseline controls: uploads the previewed JSON reader must not touch.

With DSX_PREVIEWED_JSON_UPLOAD_ENABLED off, or for any upload it does not
admit, create-from-local-file and the file worker behave as before the
feature: FileProcessor validates and reads the file, column types are
inferred from its frame, and the queued dataset carries the same config.
Each case compares the stored table with that FileProcessor pipeline, so it
passes on the pre-feature code and must keep passing with the feature.
"""

import importlib
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from model_hub.models.choices import determine_data_type
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.services import dataset_service
from model_hub.utils.file_reader import FileProcessor
from model_hub.views.datasets.create import file_upload

SETTING = "DSX_PREVIEWED_JSON_UPLOAD_ENABLED"
UPLOAD = "/model-hub/develops/create-dataset-from-local-file/"

CATALOG = json.dumps(
    [
        {"sku": "007", "title": "Desk lamp", "in_stock": True, "price": 12.5},
        {"sku": "042", "title": "Café chair", "in_stock": False, "price": 40.0},
        {"sku": "100", "title": "Shelf", "in_stock": True, "price": 0.75},
    ]
)
TICKETS = (
    '{"ticket": "0012", "summary": "Refund not received", "replies": 3}\n'
    '{"ticket": "0013", "summary": "Läuft nicht", "replies": 0}\n'
)
REGIONS = "region,quantity\ncatalog-west,2\ncatalog-east,7\ncatalog-west,3\n"
# Files FileProcessor reads; their stored tables are compared with it.
READABLE = [
    ("regions.csv", REGIONS),
    ("catalog.json", CATALOG),
    ("tickets.jsonl", TICKETS),
]
READABLE_IDS = ["csv", "json", "jsonl"]
# The keys create-from-local-file stores on a queued dataset before F32.
QUEUED_CONFIG_KEYS = {
    "dataset_source_local",
    "file_processing_status",
    "file_processing_queued_at",
    "original_filename",
    "file_url",
    "estimated_rows",
    "estimated_columns",
}


def _switch(settings, state):
    if state == "unset":
        del settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED
    else:
        setattr(settings, SETTING, state == "on")


@pytest.fixture
def file_processor_calls(monkeypatch):
    """The names of the files FileProcessor.process_file read."""
    calls = []
    original = FileProcessor.process_file

    def spy(file_obj):
        calls.append(getattr(file_obj, "name", None))
        return original(file_obj=file_obj)

    monkeypatch.setattr(FileProcessor, "process_file", staticmethod(spy))
    return calls


@pytest.fixture
def previewed_reader_calls(monkeypatch):
    """Calls of the previewed JSON reader; none can happen where it is absent."""
    calls = []
    try:
        reader = importlib.import_module("model_hub.services.file_import_json")
    except ModuleNotFoundError:
        return calls
    original = reader.read_json_import

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(reader, "read_json_import", spy)
    return calls


def _file_processor_frame(filename, content):
    return FileProcessor.process_file(
        file_obj=SimpleUploadedFile(filename, content.encode("utf-8"))
    )


def _baseline_table(filename, content):
    """The table the pre-F32 worker stores: FileProcessor rows, inferred types."""
    frame, error = _file_processor_frame(filename, content)
    assert error is None, error
    frame.columns = frame.columns.str.strip()
    frame = frame.map(
        lambda value: (value.strip() or None) if isinstance(value, str) else value
    )
    frame.reset_index(drop=True, inplace=True)
    names = list(frame.columns)
    columns = [(name, determine_data_type(frame[name])) for name in names]
    cells = [
        [file_upload.normalize_cell_value(frame[name][index]) for name in names]
        for index in frame.index
    ]
    return columns, cells


def _stored(dataset):
    columns = {
        str(column.id): column
        for column in Column.objects.filter(dataset=dataset, deleted=False)
    }
    ordered = [columns[column_id] for column_id in dataset.column_order]
    rows = list(Row.objects.filter(dataset=dataset, deleted=False).order_by("order"))
    values = {
        (cell.row_id, cell.column_id): cell.value
        for cell in Cell.objects.filter(dataset=dataset, deleted=False)
    }
    return (
        [(column.name, column.data_type) for column in ordered],
        [[values[(row.id, column.id)] for column in ordered] for row in rows],
    )


def _queued_dataset(organization, workspace, user, filename):
    return Dataset.objects.create(
        name=f"Baseline {filename}",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type="GenerativeLLM",
        dataset_config={
            "dataset_source_local": True,
            "file_processing_status": "queued",
            "original_filename": filename,
            "file_url": f"minio://{filename}",
        },
    )


def _run_worker(monkeypatch, tmp_path, dataset, filename, content):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        file_upload,
        "download_file_from_minio",
        lambda file_url, original_filename=None: str(path),
    )
    monkeypatch.setattr(
        file_upload.process_media_uploads, "delay", lambda *args, **kwargs: None
    )
    file_upload.process_dataset_from_file.run_sync(
        str(dataset.id), f"minio://{filename}", filename
    )
    dataset.refresh_from_db()


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
    return queued


def _post(auth_client, name, filename, content):
    return auth_client.post(
        UPLOAD,
        {
            "new_dataset_name": name,
            "model_type": "GenerativeLLM",
            "file": SimpleUploadedFile(filename, content.encode("utf-8")),
        },
        format="multipart",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["unset", "off"])
@pytest.mark.parametrize(("filename", "content"), READABLE, ids=READABLE_IDS)
def test_route_off_validates_with_file_processor_and_queues_baseline_config(
    auth_client,
    workspace,
    settings,
    upload_route,
    file_processor_calls,
    previewed_reader_calls,
    state,
    filename,
    content,
):
    _switch(settings, state)
    frame, error = _file_processor_frame(filename, content)
    assert error is None, error
    file_processor_calls.clear()

    response = _post(auth_client, f"Off {state} {filename}", filename, content)

    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    assert (result["estimated_rows"], result["estimated_columns"]) == frame.shape
    assert result["processing_status"] == "queued"
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.workspace_id == workspace.id
    assert set(dataset.dataset_config) == QUEUED_CONFIG_KEYS
    assert upload_route == [
        (str(dataset.id), dataset.dataset_config["file_url"], filename)
    ]
    assert file_processor_calls == [filename]
    assert previewed_reader_calls == []


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["unset", "off"])
def test_route_off_refuses_what_file_processor_refuses(
    auth_client,
    settings,
    upload_route,
    file_processor_calls,
    previewed_reader_calls,
    state,
):
    _switch(settings, state)
    name = f"Off {state} broken"

    response = _post(auth_client, name, "broken.json", '[{"a": 1}')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert upload_route == []
    assert not Dataset.no_workspace_objects.filter(name=name).exists()
    assert file_processor_calls == ["broken.json"]
    assert previewed_reader_calls == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("state", ["unset", "off", "on"])
@pytest.mark.parametrize(("filename", "content"), READABLE, ids=READABLE_IDS)
def test_worker_keeps_file_processor_for_unmarked_jobs(
    organization,
    workspace,
    user,
    settings,
    monkeypatch,
    tmp_path,
    file_processor_calls,
    previewed_reader_calls,
    state,
    filename,
    content,
):
    # An unmarked job (queued before F32, by the SDK service, or while OFF)
    # keeps its pre-F32 table even after the switch is turned on.
    _switch(settings, state)
    expected = _baseline_table(filename, content)
    file_processor_calls.clear()
    dataset = _queued_dataset(organization, workspace, user, filename)

    _run_worker(monkeypatch, tmp_path, dataset, filename, content)

    assert dataset.dataset_config["file_processing_status"] == "completed"
    assert _stored(dataset) == expected
    assert file_processor_calls == [filename]
    assert previewed_reader_calls == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("filename", "content"), READABLE, ids=READABLE_IDS)
def test_sdk_service_upload_keeps_file_processor_while_on(
    organization,
    workspace,
    user,
    settings,
    monkeypatch,
    tmp_path,
    file_processor_calls,
    previewed_reader_calls,
    filename,
    content,
):
    settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED = True
    expected = _baseline_table(filename, content)
    file_processor_calls.clear()
    queued = []
    monkeypatch.setattr(
        dataset_service, "_check_resource_limit", lambda *args, **kwargs: True
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

    result = dataset_service.create_dataset_from_file(
        file_content=content.encode("utf-8"),
        file_name=filename,
        name=f"SDK {filename}",
        organization=organization,
        workspace=workspace,
        user=user,
    )

    assert isinstance(result, dict), result
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    # An omitted model_type keeps the model default instead of storing None.
    assert dataset.model_type == "GenerativeLLM"
    assert set(dataset.dataset_config) == QUEUED_CONFIG_KEYS
    assert queued == [(str(dataset.id), dataset.dataset_config["file_url"], filename)]
    assert file_processor_calls == [filename]

    _run_worker(monkeypatch, tmp_path, dataset, filename, content)

    assert dataset.dataset_config["file_processing_status"] == "completed"
    assert _stored(dataset) == expected
    assert file_processor_calls == [filename, filename]
    assert previewed_reader_calls == []


@pytest.mark.django_db
def test_sdk_service_upload_stores_explicit_model_type(
    organization, workspace, user, monkeypatch
):
    queued = []
    monkeypatch.setattr(
        dataset_service, "_check_resource_limit", lambda *args, **kwargs: True
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

    result = dataset_service.create_dataset_from_file(
        file_content=REGIONS.encode("utf-8"),
        file_name="regions.csv",
        name="SDK explicit model type",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type="GenerativeImage",
    )

    assert isinstance(result, dict), result
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.model_type == "GenerativeImage"
    assert queued == [
        (str(dataset.id), dataset.dataset_config["file_url"], "regions.csv")
    ]


@pytest.mark.django_db(transaction=True)
def test_route_and_worker_keep_file_processor_for_csv_while_on(
    auth_client,
    workspace,
    settings,
    monkeypatch,
    tmp_path,
    upload_route,
    file_processor_calls,
    previewed_reader_calls,
):
    settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED = True
    expected = _baseline_table("regions.csv", REGIONS)
    file_processor_calls.clear()

    response = _post(auth_client, "On regions.csv", "regions.csv", REGIONS)

    assert response.status_code == status.HTTP_200_OK, response.json()
    dataset = Dataset.no_workspace_objects.get(
        id=response.json()["result"]["dataset_id"]
    )
    assert set(dataset.dataset_config) == QUEUED_CONFIG_KEYS

    _run_worker(monkeypatch, tmp_path, dataset, "regions.csv", REGIONS)

    assert _stored(dataset) == expected
    assert file_processor_calls == ["regions.csv", "regions.csv"]
    assert previewed_reader_calls == []
