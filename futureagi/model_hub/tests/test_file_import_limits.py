"""Bound atomic transactions before allocating row/cell model instances."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from model_hub.models.develop_dataset import Dataset
from model_hub.services import file_import_limits as limits
from model_hub.views.datasets.create import file_upload
from model_hub.views.datasets.create.file_upload import normalize_cell_value


@pytest.mark.parametrize("shape", [(100_001, 1), (1, 1_001), (100_000, 11)])
def test_transaction_shape_limits(shape):
    data = SimpleNamespace(shape=shape, columns=SimpleNamespace(is_unique=True))
    with pytest.raises(ValueError, match="Split the file"):
        limits.check_import_shape(data)


def test_boundary_shape_is_accepted():
    limits.check_import_shape(
        SimpleNamespace(shape=(100_000, 10), columns=SimpleNamespace(is_unique=True))
    )


def test_normalized_payload_bound_counts_utf8_bytes(monkeypatch):
    monkeypatch.setattr(limits, "MAX_IMPORT_VALUE_BYTES", 5)
    data = pd.DataFrame({"input": ["日本"]})
    with pytest.raises(ValueError, match="Split the file"):
        limits.prepare_import_values(
            data,
            [SimpleNamespace(name="input", data_type="text")],
            set(),
            normalize_cell_value,
        )


def test_preparation_preserves_structured_json_and_nulls():
    values = limits.prepare_import_values(
        pd.DataFrame({"input": [{"ok": True}, None]}),
        [SimpleNamespace(name="input", data_type="json")],
        set(),
        normalize_cell_value,
    )
    assert values == {"input": {0: '{"ok": true}', 1: None}}


def test_duplicate_trimmed_headers_are_rejected():
    with pytest.raises(ValueError, match="Column names must be unique"):
        limits.check_import_shape(pd.DataFrame([[1, 2]], columns=["input", "input"]))


@pytest.fixture
def upload_effects(monkeypatch):
    accounting = MagicMock(
        side_effect=lambda *args, **kwargs: SimpleNamespace(
            status="success", save=lambda: None
        )
    )
    storage = MagicMock(return_value="minio://fixture.csv")
    queue = MagicMock()
    monkeypatch.setattr(
        file_upload, "log_and_deduct_cost_for_resource_request", accounting
    )
    monkeypatch.setattr(file_upload, "upload_file_to_minio", storage)
    monkeypatch.setattr(file_upload.process_dataset_from_file, "delay", queue)
    return accounting, storage, queue


def _upload_csv(auth_client, content):
    return auth_client.post(
        "/model-hub/develops/create-dataset-from-local-file/",
        {
            "new_dataset_name": "Shape validation upload",
            "model_type": "GenerativeLLM",
            "file": SimpleUploadedFile("rows.csv", content.encode("utf-8")),
        },
        format="multipart",
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content",
    [
        "input\n" + "x\n" * 100_001,
        ",".join(f"field_{i}" for i in range(1_001))
        + "\n"
        + ",".join("x" for _ in range(1_001))
        + "\n",
        ",".join(f"field_{i}" for i in range(11))
        + "\n"
        + (",".join("x" for _ in range(11)) + "\n") * 90_910,
    ],
    ids=["rows", "columns", "cells"],
)
def test_upload_refuses_oversized_shapes_before_side_effects(
    auth_client, upload_effects, content
):
    response = _upload_csv(auth_client, content)

    assert response.status_code == 400, response.json()
    assert response.json()["message"] == (
        "File import exceeds 100,000 rows, 1,000 columns or 1,000,000 cells. "
        "Split the file into smaller imports."
    )
    assert [effect.call_count for effect in upload_effects] == [0, 0, 0]
    assert (
        Dataset.no_workspace_objects.filter(name="Shape validation upload").count() == 0
    )


@pytest.mark.django_db
def test_upload_refuses_headers_that_collide_after_trimming(
    auth_client, upload_effects
):
    response = _upload_csv(auth_client, "input, input \na,b\n")

    assert response.status_code == 400, response.json()
    assert response.json()["message"] == (
        "Column names must be unique after trimming whitespace."
    )
    assert [effect.call_count for effect in upload_effects] == [0, 0, 0]
    assert (
        Dataset.no_workspace_objects.filter(name="Shape validation upload").count() == 0
    )


@pytest.mark.django_db
def test_upload_accepts_the_shape_boundary(auth_client, upload_effects, monkeypatch):
    monkeypatch.setattr(limits, "MAX_IMPORT_ROWS", 2)
    monkeypatch.setattr(limits, "MAX_IMPORT_COLUMNS", 2)
    monkeypatch.setattr(limits, "MAX_IMPORT_CELLS", 4)

    response = _upload_csv(auth_client, " input ,answer\na,b\nc,d\n")

    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    assert (result["estimated_rows"], result["estimated_columns"]) == (2, 2)
    assert result["processing_status"] == "queued"
    assert [effect.call_count for effect in upload_effects] == [2, 1, 1]
    assert (
        Dataset.no_workspace_objects.filter(name="Shape validation upload").count() == 1
    )
