"""Regressions for readers spanning publication and failed post-commit dispatch."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection
from django.db.models.query import QuerySet

from model_hub.models.choices import DataTypeChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests import test_upload_readiness as upload_fixtures
from model_hub.tests.test_upload_readiness import assert_complete, run_upload
from model_hub.views.datasets.create import file_upload
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = pytest.mark.django_db(transaction=True)
upload = upload_fixtures.upload


@pytest.mark.parametrize("pause_at", ["columns", "dataset"])
def test_grid_reader_cannot_span_empty_columns_and_published_rows(
    upload, auth_client, monkeypatch, pause_at
):
    ready, resume = Event(), Event()
    original = QuerySet._fetch_all
    reader_thread = None
    paused = False

    def pause_after_empty_inventory(queryset):
        nonlocal paused
        original(queryset)
        if get_ident() != reader_thread or paused:
            return
        at_inventory = (
            pause_at == "columns"
            and queryset.model is Column
            and queryset._result_cache == []
        )
        at_admission = (
            pause_at == "dataset"
            and queryset.model is Dataset
            and queryset._result_cache
            and queryset._result_cache[0].id == upload.id
        )
        if at_inventory or at_admission:
            paused = True
            ready.set()
            assert resume.wait(30), "writer did not release grid reader"

    monkeypatch.setattr(QuerySet, "_fetch_all", pause_after_empty_inventory)

    def read_grid():
        nonlocal reader_thread
        reader_thread = get_ident()
        set_workspace_context(
            workspace=upload.workspace, organization=upload.organization
        )
        try:
            return auth_client.get(
                f"/model-hub/develops/{upload.id}/get-dataset-table/"
            )
        finally:
            # A lifecycle short-circuit must never need a column inventory.
            ready.set()
            close_old_connections()
            clear_workspace_context()

    with ThreadPoolExecutor(max_workers=1) as executor:
        reader = executor.submit(read_grid)
        try:
            assert ready.wait(30), "grid reader did not reach the publication window"
            run_upload(upload)
            assert_complete(upload)
        finally:
            resume.set()
        response = reader.result(timeout=30)

    assert response.status_code == 200, response.data
    assert response.data["result"]["is_processing_data"] is True
    assert response.data["result"]["table"] == []
    assert paused == (pause_at == "dataset"), (
        "processing grids must return after admission, before inventorying columns"
    )
    complete = auth_client.get(f"/model-hub/develops/{upload.id}/get-dataset-table/")
    assert complete.status_code == 200, complete.data
    assert complete.data["result"]["is_processing_data"] is False
    assert len(complete.data["result"]["table"]) == 10
    assert len(complete.data["result"]["column_config"]) == 2


@pytest.mark.parametrize("state", ["queued", "processing", "creating_cells", "failed"])
@pytest.mark.parametrize("columns_only", [False, True])
def test_processing_grid_never_reads_table_inventory(
    upload, auth_client, monkeypatch, state, columns_only
):
    upload.dataset_config["file_processing_status"] = state
    upload.save(update_fields=["dataset_config"])
    original = QuerySet._fetch_all

    def reject_inventory(queryset):
        assert queryset.model not in (Column, Row, Cell), "processing inventory read"
        return original(queryset)

    monkeypatch.setattr(QuerySet, "_fetch_all", reject_inventory)
    response = auth_client.get(
        f"/model-hub/develops/{upload.id}/get-dataset-table/",
        {"column_config_only": str(columns_only).lower()},
    )
    if state == "failed":
        assert response.status_code == 409, response.data
        assert response.data["code"] == "dataset_import_failed"
        return
    assert response.status_code == 200, response.data
    assert response.data["result"]["column_config"] == []
    assert response.data["result"]["is_processing_data"] is True
    if not columns_only:
        assert response.data["result"]["table"] == []
        assert response.data["result"]["metadata"]["total_rows"] == 0


def test_failed_media_dispatch_retries_completed_import_without_recreating_table(
    upload, monkeypatch
):
    monkeypatch.setattr(
        file_upload, "determine_data_type", lambda _: DataTypeChoices.IMAGE.value
    )
    attempts = []

    def dispatch(dataset_id):
        assert not connection.in_atomic_block
        dataset = Dataset.objects.get(id=dataset_id)
        assert dataset.dataset_config["file_processing_status"] == "completed"
        assert Cell.objects.filter(dataset=dataset).count() == 1002
        attempts.append(dataset_id)
        if len(attempts) == 1:
            raise RuntimeError("injected scheduling outage")

    with patch.object(file_upload.process_media_uploads, "delay", side_effect=dispatch):
        with pytest.raises(RuntimeError, match="injected scheduling outage"):
            run_upload(upload)
        assert_complete(upload)
        upload.refresh_from_db()
        assert upload.dataset_config["file_media_dispatch_status"] == "pending"
        assert "file_processing_error" not in upload.dataset_config
        row_ids = set(Row.objects.filter(dataset=upload).values_list("id", flat=True))
        cell_ids = set(Cell.objects.filter(dataset=upload).values_list("id", flat=True))
        column_ids = set(
            Column.objects.filter(dataset=upload).values_list("id", flat=True)
        )
        # A retry must use durable publication state, not the removed source file.
        with patch.object(
            file_upload,
            "download_file_from_minio",
            side_effect=AssertionError("completed import was parsed again"),
        ):
            run_upload(upload)
            run_upload(upload)
        assert attempts == [str(upload.id), str(upload.id)]
        assert_complete(upload)
        upload.refresh_from_db()
        assert upload.dataset_config["file_media_dispatch_status"] == "dispatched"
        assert row_ids == set(
            Row.objects.filter(dataset=upload).values_list("id", flat=True)
        )
        assert cell_ids == set(
            Cell.objects.filter(dataset=upload).values_list("id", flat=True)
        )
        assert column_ids == set(
            Column.objects.filter(dataset=upload).values_list("id", flat=True)
        )
