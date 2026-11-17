"""JSON and JSONL uploads store the rows, columns and values the dialog previewed.

Only the public entry points are used: the create-from-local-file route and
the file worker that publishes the table. Every case here runs with the F32
switch (DSX_PREVIEWED_JSON_UPLOAD_ENABLED) on; worker jobs carry the admission
record the route writes. test_upload_previewed_json_off.py covers the switch
off and the uploads it never admits. A whole number the preview can only show
rounded is refused, by the route before anything is created and by the worker.
"""

import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.services.file_import_admission import PREVIEWED_JSON, READER_KEY
from model_hub.views.datasets.create import file_upload

UPLOAD = "/model-hub/develops/create-dataset-from-local-file/"

# The same records as e2e/flows/datasets/create-upload-records.json.
CATALOG = [
    {
        "sku": "007",
        "title": "Desk lamp",
        "in_stock": True,
        "price": 12.5,
        "qty": 2,
        "restocked_at": "2026-05-01T10:00:00Z",
        "dims": {"w": 20, "h": 45},
        "note": None,
    },
    {
        "sku": "042",
        "title": "Café chair",
        "in_stock": False,
        "price": 40.0,
        "qty": None,
        "restocked_at": "2026-05-02T11:30:00Z",
        "dims": {"w": 48, "h": 90},
        "note": "ships flat",
    },
    {
        "sku": "100",
        "title": "Shelf",
        "in_stock": True,
        "price": 0.75,
        "qty": 11,
        "restocked_at": "2026-05-03T09:15:00Z",
        "dims": {"w": 80, "h": 180},
    },
]
CATALOG_COLUMNS = [
    ("sku", "text"),
    ("title", "text"),
    ("in_stock", "boolean"),
    ("price", "float"),
    ("qty", "integer"),
    ("restocked_at", "datetime"),
    ("dims", "json"),
    ("note", "text"),
]
CATALOG_CELLS = [
    [
        "007",
        "Desk lamp",
        "true",
        "12.5",
        "2",
        "2026-05-01T10:00:00Z",
        '{"w": 20, "h": 45}',
        None,
    ],
    [
        "042",
        "Café chair",
        "false",
        "40",
        None,
        "2026-05-02T11:30:00Z",
        '{"w": 48, "h": 90}',
        "ships flat",
    ],
    [
        "100",
        "Shelf",
        "true",
        "0.75",
        "11",
        "2026-05-03T09:15:00Z",
        '{"w": 80, "h": 180}',
        None,
    ],
]

# The same lines as e2e/flows/datasets/create-upload-records.jsonl: line 3 is
# not a JSON object and is skipped, line 4 is blank.
TICKETS = (
    '{"ticket": "0012", "summary": "Refund not received", "urgent": true, '
    '"score": 0.5, "replies": 3, "labels": ["billing", "refund"]}\n'
    '{"ticket": "0013", "summary": "Läuft nicht", "urgent": false, '
    '"score": 1.0, "replies": 0, "labels": []}\n'
    "not a record\n"
    "\n"
    '{"ticket": "0014", "summary": "Password reset", "urgent": false, '
    '"score": 0.25, "replies": null, "labels": ["account"]}\n'
)
TICKET_COLUMNS = [
    ("ticket", "text"),
    ("summary", "text"),
    ("urgent", "boolean"),
    ("score", "float"),
    ("replies", "integer"),
    ("labels", "array"),
]
TICKET_CELLS = [
    ["0012", "Refund not received", "true", "0.5", "3", '["billing", "refund"]'],
    ["0013", "Läuft nicht", "false", "1", "0", "[]"],
    ["0014", "Password reset", "false", "0.25", None, '["account"]'],
]

# The dialog's JSON.parse previews every whole number up to
# Number.MAX_SAFE_INTEGER exactly, so these are stored with the previewed text.
SAFE_NUMBERS = (
    '{"max": 9007199254740991, "min": -9007199254740991, "big": 1e21,'
    ' "ids": [9007199254740991]}'
)
SAFE_NUMBER_COLUMNS = [
    ("max", "integer"),
    ("min", "integer"),
    ("big", "float"),
    ("ids", "array"),
]
SAFE_NUMBER_CELLS = [
    ["9007199254740991", "-9007199254740991", "1e+21", "[9007199254740991]"]
]
# Larger whole numbers are previewed rounded (9007199254740993 as
# 9007199254740992, 10**21 as 1e+21): the upload is refused instead. The
# preview's side is pinned in frontend/src/sections/develop/AddDatasetDrawer/
# __tests__/previewNumbers.test.js with the same files.
ROUNDED_BY_PREVIEW = [
    (
        "single-id.json",
        '{"id": 9007199254740993}',
        "The number 9007199254740993 can't be imported exactly.",
    ),
    (
        "ids.json",
        '[{"id": 1}, {"id": 9007199254740993}]',
        "The number 9007199254740993 can't be imported exactly.",
    ),
    (
        "zetta.json",
        '{"rows": [{"n": 1000000000000000000000}]}',
        "The number 1000000000000000000000 can't be imported exactly.",
    ),
    (
        "nested.jsonl",
        '{"a": "x"}\n{"a": "y", "meta": {"ids": [-9007199254740993]}}\n',
        "Line 2: The number -9007199254740993 can't be imported exactly.",
    ),
]
ROUNDED_IDS = ["single-object", "array", "beyond-1e21", "jsonl-nested"]


class _SuccessfulResourceCallLog:
    status = "created"

    def save(self):
        return None


@pytest.fixture(autouse=True)
def previewed_json_on(settings):
    settings.DSX_PREVIEWED_JSON_UPLOAD_ENABLED = True


def _queued_dataset(organization, workspace, user, filename):
    return Dataset.objects.create(
        name=f"Upload {filename}",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type="GenerativeLLM",
        dataset_config={
            "dataset_source_local": True,
            "file_processing_status": "queued",
            "original_filename": filename,
            "file_url": f"minio://{filename}",
            READER_KEY: PREVIEWED_JSON,
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("filename", "content", "columns", "cells"),
    [
        ("catalog.json", json.dumps(CATALOG), CATALOG_COLUMNS, CATALOG_CELLS),
        ("tickets.jsonl", TICKETS, TICKET_COLUMNS, TICKET_CELLS),
    ],
    ids=["json", "jsonl"],
)
def test_worker_stores_previewed_columns_types_and_cells(
    organization,
    workspace,
    user,
    monkeypatch,
    tmp_path,
    filename,
    content,
    columns,
    cells,
):
    dataset = _queued_dataset(organization, workspace, user, filename)

    _run_worker(monkeypatch, tmp_path, dataset, filename, content)

    config = dataset.dataset_config
    assert config["file_processing_status"] == "completed", config
    assert (config["total_rows"], config["total_columns"]) == (len(cells), len(columns))
    assert _stored(dataset) == (columns, cells)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("content", "cells"),
    [
        ('{"rows": [{"a": "x1"}, {"a": "x2"}]}', [["x1"], ["x2"]]),
        ('{"a": "only", "b": 7}', [["only", "7"]]),
    ],
    ids=["rows-wrapper", "single-object"],
)
def test_worker_reads_wrapped_and_single_records(
    organization, workspace, user, monkeypatch, tmp_path, content, cells
):
    dataset = _queued_dataset(organization, workspace, user, "records.json")

    _run_worker(monkeypatch, tmp_path, dataset, "records.json", content)

    assert dataset.dataset_config["file_processing_status"] == "completed"
    assert _stored(dataset)[1] == cells


@pytest.mark.django_db(transaction=True)
def test_worker_stores_safe_whole_numbers_with_the_previewed_text(
    organization, workspace, user, monkeypatch, tmp_path
):
    dataset = _queued_dataset(organization, workspace, user, "bounds.json")

    _run_worker(monkeypatch, tmp_path, dataset, "bounds.json", SAFE_NUMBERS)

    assert dataset.dataset_config["file_processing_status"] == "completed"
    assert _stored(dataset) == (SAFE_NUMBER_COLUMNS, SAFE_NUMBER_CELLS)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("filename", "content", "message"), ROUNDED_BY_PREVIEW, ids=ROUNDED_IDS
)
def test_worker_refuses_whole_numbers_the_preview_rounds(
    organization, workspace, user, monkeypatch, tmp_path, filename, content, message
):
    dataset = _queued_dataset(organization, workspace, user, filename)

    with pytest.raises(Exception, match=re.escape(message)):
        _run_worker(monkeypatch, tmp_path, dataset, filename, content)

    dataset.refresh_from_db()
    config = dataset.dataset_config
    assert config["file_processing_status"] == "failed"
    assert message in config["file_processing_error"]
    assert not Column.objects.filter(dataset=dataset).exists()
    assert not Row.objects.filter(dataset=dataset).exists()
    assert not Cell.objects.filter(dataset=dataset).exists()


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
@pytest.mark.parametrize(
    ("filename", "content", "rows", "columns"),
    [
        ("tickets.jsonl", TICKETS, 3, 6),
        ("wrapped.json", '{"rows": [{"a": "x1", "b": 1}, {"a": "x2"}]}', 2, 2),
        ("single.json", '{"a": "only", "b": 7}', 1, 2),
        ("bounds.json", SAFE_NUMBERS, 1, 4),
    ],
    ids=["jsonl-skipped-line", "rows-wrapper", "single-object", "safe-integers"],
)
def test_route_estimates_the_previewed_records(
    auth_client, workspace, upload_route, filename, content, rows, columns
):
    response = _post(auth_client, f"Estimate {filename}", filename, content)

    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    assert (result["estimated_rows"], result["estimated_columns"]) == (rows, columns)
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.workspace_id == workspace.id
    assert dataset.dataset_config[READER_KEY] == PREVIEWED_JSON
    assert upload_route and upload_route[0][0] == str(dataset.id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("constant.json", '[{"a": NaN}]'),
        ("broken.json", '[{"a": 1}'),
        ("objects-only.jsonl", "[1, 2]\nnot a record\n"),
        ("empty.json", "[]"),
    ],
    ids=["nan-literal", "invalid-json", "no-object-lines", "no-records"],
)
def test_route_refuses_files_the_preview_cannot_read(
    auth_client, upload_route, filename, content
):
    response = _post(auth_client, f"Refuse {filename}", filename, content)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert upload_route == []
    assert not Dataset.no_workspace_objects.filter(name=f"Refuse {filename}").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("filename", "content", "message"), ROUNDED_BY_PREVIEW, ids=ROUNDED_IDS
)
def test_route_refuses_whole_numbers_the_preview_rounds(
    auth_client, upload_route, filename, content, message
):
    response = _post(auth_client, f"Rounded {filename}", filename, content)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    # The dialog shows this message (UploadFileModal onError).
    assert response.json()["message"].startswith(message)
    assert upload_route == []
    assert not Dataset.no_workspace_objects.filter(name=f"Rounded {filename}").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("model_type", [None, "", "   ", "generative_llm"])
def test_upload_defaults_or_normalizes_the_model_type(
    auth_client, upload_route, model_type
):
    body = {
        "new_dataset_name": "Default model upload",
        "file": SimpleUploadedFile("rows.csv", b"input\nhello\n"),
    }
    if model_type is not None:
        body["model_type"] = model_type

    response = auth_client.post(UPLOAD, body, format="multipart")

    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.model_type == "GenerativeLLM"
    assert result["dataset_model_type"] == "GenerativeLLM"
    assert result["processing_status"] == "queued"
    assert upload_route == [
        (str(dataset.id), dataset.dataset_config["file_url"], "rows.csv")
    ]


@pytest.mark.django_db
def test_invalid_upload_model_type_is_refused_before_side_effects(
    auth_client, monkeypatch
):
    from model_hub.serializers.contracts import (
        CreateDatasetFromLocalFileRequestSerializer,
    )

    effects = []
    for target, name in (
        (file_upload, "log_and_deduct_cost_for_resource_request"),
        (file_upload, "upload_file_to_minio"),
        (file_upload.process_dataset_from_file, "delay"),
    ):
        effect = MagicMock()
        monkeypatch.setattr(target, name, effect)
        effects.append(effect)
    body = {
        "new_dataset_name": "Invalid model upload",
        "model_type": "not-a-model",
        "file": SimpleUploadedFile("rows.csv", b"input\nhello\n"),
    }
    serializer = CreateDatasetFromLocalFileRequestSerializer(data=body)
    assert serializer.is_valid() is False
    assert serializer.errors == {"model_type": ['"not-a-model" is not a valid choice.']}

    response = auth_client.post(UPLOAD, body, format="multipart")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert [effect.call_count for effect in effects] == [0, 0, 0]
    assert Dataset.no_workspace_objects.filter(name="Invalid model upload").count() == 0


def _activity_attempt(monkeypatch, attempt):
    from temporalio import activity

    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(activity, "info", lambda: SimpleNamespace(attempt=attempt))


def _progress(auth_client, dataset):
    response = auth_client.get(
        f"/model-hub/develops/dataset-creation-progress/{dataset.id}/"
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    return (
        result["processing_status"],
        result["is_processing"],
        result["is_completed"],
        result["is_failed"],
    )


def test_file_import_has_an_explicit_retry_budget():
    assert file_upload.process_dataset_from_file._metadata["max_retries"] == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("attempt", [1, 2])
def test_transient_import_failure_keeps_progress_pending_until_success(
    auth_client, organization, workspace, user, monkeypatch, tmp_path, attempt
):
    dataset = _queued_dataset(organization, workspace, user, "retry.json")
    _activity_attempt(monkeypatch, attempt)
    monkeypatch.setattr(
        file_upload,
        "download_file_from_minio",
        MagicMock(side_effect=ConnectionError("Temporary storage failure")),
    )

    with pytest.raises(ConnectionError, match="^Temporary storage failure$"):
        file_upload.process_dataset_from_file.run_sync(
            str(dataset.id), "minio://retry.json", "retry.json"
        )

    dataset.refresh_from_db()
    assert dataset.dataset_config["file_processing_status"] == "retrying"
    assert dataset.dataset_config.get("file_processing_failed_at") is None
    assert _progress(auth_client, dataset) == ("retrying", True, False, False)
    assert Row.objects.filter(dataset=dataset).count() == 0
    _activity_attempt(monkeypatch, attempt + 1)
    _run_worker(monkeypatch, tmp_path, dataset, "retry.json", '{"input": "recovered"}')

    assert _progress(auth_client, dataset) == ("completed", False, True, False)
    assert dataset.dataset_config.get("file_processing_error") is None
    assert _stored(dataset) == ([("input", "text")], [["recovered"]])
    assert Row.objects.filter(dataset=dataset).count() == 1
    assert Cell.objects.filter(dataset=dataset).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("non_retryable", [False, True])
def test_terminal_import_failure_reports_failed_progress(
    auth_client, organization, workspace, user, monkeypatch, non_retryable
):
    from temporalio.exceptions import ApplicationError

    dataset = _queued_dataset(organization, workspace, user, "failed.json")
    _activity_attempt(monkeypatch, 1 if non_retryable else 3)
    failure = (
        ApplicationError("Import refused", non_retryable=True)
        if non_retryable
        else ConnectionError("Storage still unavailable")
    )
    monkeypatch.setattr(
        file_upload, "download_file_from_minio", MagicMock(side_effect=failure)
    )

    with pytest.raises(type(failure)) as caught:
        file_upload.process_dataset_from_file.run_sync(
            str(dataset.id), "minio://failed.json", "failed.json"
        )

    assert caught.value is failure
    dataset.refresh_from_db()
    assert dataset.dataset_config["file_processing_error"] == str(failure)
    assert _progress(auth_client, dataset) == ("failed", False, False, True)
    assert Column.objects.filter(dataset=dataset).count() == 0
    assert Row.objects.filter(dataset=dataset).count() == 0
    assert Cell.objects.filter(dataset=dataset).count() == 0
