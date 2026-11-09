"""Upload publication is atomic, including across separate DB connections."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection
from django.db.models.query import QuerySet

from model_hub.models.choices import DataTypeChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views.datasets.create import file_upload
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def upload(organization, workspace, user, monkeypatch, tmp_path):
    dataset = Dataset.objects.create(
        name="Atomic upload",
        organization=organization,
        workspace=workspace,
        user=user,
        dataset_config={
            "dataset_source_local": True,
            "file_processing_status": "queued",
            "eval_recommendations": [],
        },
    )
    path = tmp_path / "data.csv"
    path.write_text(
        "input,output\n" + "".join(f"input-{i},output-{i}\n" for i in range(501))
    )
    monkeypatch.setattr(file_upload, "download_file_from_minio", lambda *_: str(path))
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request", None
    )
    # Admission tests must never start a real Temporal/model workflow on RED.
    monkeypatch.setattr(
        "model_hub.tasks.run_prompt.process_prompts_single.apply_async",
        lambda *args, **kwargs: None,
    )
    return dataset


def run_upload(dataset):
    set_workspace_context(
        workspace=dataset.workspace, organization=dataset.organization
    )
    try:
        file_upload.process_dataset_from_file.run_sync(
            str(dataset.id), "minio://fixture", "data.csv"
        )
    finally:
        close_old_connections()
        clear_workspace_context()


def assert_complete(dataset):
    dataset.refresh_from_db()
    assert dataset.dataset_config["file_processing_status"] == "completed"
    assert Row.objects.filter(dataset=dataset).count() == 501
    assert Column.objects.filter(dataset=dataset).count() == 2
    assert Cell.objects.filter(dataset=dataset).count() == 1002
    for cell in Cell.objects.filter(dataset=dataset).select_related("row", "column"):
        assert cell.value == f"{cell.column.name}-{cell.row.order}"


@pytest.mark.parametrize("pause_batch", [1, 2])
def test_no_partial_rows_visible_between_cell_batches(
    upload, auth_client, monkeypatch, pause_batch
):
    entered, resume = Event(), Event()
    original = QuerySet.bulk_create
    calls = 0

    def pause(queryset, objects, *args, **kwargs):
        nonlocal calls
        if queryset.model is Cell:
            calls += 1
            if calls == pause_batch:
                entered.set()
                assert resume.wait(30), "reader did not release upload"
        return original(queryset, objects, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "bulk_create", pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(run_upload, upload)
        try:
            assert entered.wait(30), "upload never reached cell insertion"
            assert Row.objects.filter(dataset=upload).count() == 0
            assert Cell.objects.filter(dataset=upload).count() == 0
            response = auth_client.get(
                f"/model-hub/develops/{upload.id}/get-dataset-table/"
            )
            assert response.status_code == 200, response.data
            assert response.data["result"]["table"] == []
            assert response.data["result"]["is_processing_data"] is True
            for response in [
                auth_client.get(f"/model-hub/develops/{upload.id}/download_dataset/"),
                auth_client.post(
                    f"/model-hub/develops/clone-dataset/{upload.id}/", {}, format="json"
                ),
            ]:
                assert response.status_code == 409
                assert response.data["code"] == "dataset_processing"
        finally:
            resume.set()
            task.result(timeout=30)
    assert_complete(upload)
    download = auth_client.get(f"/model-hub/develops/{upload.id}/download_dataset/")
    assert download.status_code == 200
    content = b"".join(download.streaming_content).decode()
    assert "input-500,output-500" in content
    clone = auth_client.post(
        f"/model-hub/develops/clone-dataset/{upload.id}/", {}, format="json"
    )
    assert clone.status_code == 200, clone.data
    copied = Dataset.objects.get(id=clone.data["result"]["dataset_id"])
    assert_complete(copied)


def test_cell_write_failure_rolls_back_and_records_failed(upload, monkeypatch):
    original = QuerySet.bulk_create
    calls = 0

    def fail_second_batch(queryset, objects, *args, **kwargs):
        nonlocal calls
        if queryset.model is Cell:
            calls += 1
            if calls == 2:
                raise RuntimeError("injected cell write failure")
        return original(queryset, objects, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "bulk_create", fail_second_batch)
    with pytest.raises(RuntimeError, match="injected cell write failure"):
        run_upload(upload)
    upload.refresh_from_db()
    assert upload.dataset_config["file_processing_status"] == "failed"
    assert (
        "injected cell write failure" in upload.dataset_config["file_processing_error"]
    )
    assert not Row.objects.filter(dataset=upload).exists()
    assert not Cell.objects.filter(dataset=upload).exists()
    assert not Column.objects.filter(dataset=upload).exists()
    assert not upload.column_config
    assert not upload.column_order


def test_media_dispatch_runs_only_after_commit(upload, monkeypatch):
    monkeypatch.setattr(
        file_upload, "determine_data_type", lambda _: DataTypeChoices.IMAGE.value
    )
    observed = []

    def dispatch(dataset_id):
        assert not connection.in_atomic_block
        dataset = Dataset.objects.get(id=dataset_id)
        assert dataset.dataset_config["file_processing_status"] == "completed"
        assert Cell.objects.filter(dataset=dataset).count() == 1002
        observed.append(dataset_id)

    with patch.object(file_upload.process_media_uploads, "delay", side_effect=dispatch):
        run_upload(upload)
    assert observed == [str(upload.id)]


def test_completed_import_can_run_with_all_inputs_and_retry_is_idempotent(
    upload, auth_client
):
    from model_hub.views.run_prompt import populate_placeholders

    run_upload(upload)
    assert_complete(upload)
    # Temporal redelivery must neither append a second table nor relabel it failed.
    run_upload(upload)
    assert_complete(upload)
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "{{input}} / {{output}}"}],
        }
    ]
    with patch(
        "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
    ) as dispatch:
        response = auth_client.post(
            "/model-hub/develops/add_run_prompt_column/",
            {
                "dataset_id": str(upload.id),
                "name": "Generated",
                "config": {"messages": messages, "output_format": "string"},
            },
            format="json",
        )
    assert response.status_code == 200, response.data
    dispatch.assert_called_once()
    output_column = Column.objects.get(dataset=upload, name="Generated")
    last_row = Row.objects.get(dataset=upload, order=500)
    rendered = populate_placeholders(
        messages, upload.id, last_row.id, output_column.id, "", template_format="jinja2"
    )
    assert rendered[0]["content"][0]["text"] == "input-500 / output-500"


def test_value_preparation_failure_cannot_be_reported_completed(upload, monkeypatch):
    original = file_upload.normalize_cell_value

    def fail(value):
        if value == "input-1":
            raise ValueError("injected normalization failure")
        return original(value)

    monkeypatch.setattr(file_upload, "normalize_cell_value", fail)
    with pytest.raises(ValueError, match="injected normalization failure"):
        run_upload(upload)
    upload.refresh_from_db()
    assert upload.dataset_config["file_processing_status"] == "failed"
    assert not Row.objects.filter(dataset=upload).exists()


@pytest.mark.parametrize("state", ["queued", "processing", "creating_cells", "failed"])
@pytest.mark.parametrize(
    "action",
    ["download", "clone", "duplicate_rows", "duplicate_dataset", "run", "eval"],
)
def test_consumers_refuse_unready_imports(upload, auth_client, state, action):
    upload.dataset_config["file_processing_status"] = state
    upload.save()
    calls = {
        "download": ("get", f"/model-hub/develops/{upload.id}/download_dataset/", {}),
        "clone": ("post", f"/model-hub/develops/clone-dataset/{upload.id}/", {}),
        "duplicate_rows": (
            "post",
            f"/model-hub/datasets/{upload.id}/duplicate-rows/",
            {"selected_all_rows": True},
        ),
        "duplicate_dataset": (
            "post",
            f"/model-hub/datasets/{upload.id}/duplicate/",
            {"name": "Copy", "selected_all_rows": True},
        ),
        "run": (
            "post",
            "/model-hub/develops/add_run_prompt_column/",
            {
                "dataset_id": str(upload.id),
                "name": "Output",
                "config": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "{{input}}"}],
                        }
                    ]
                },
            },
        ),
        "eval": (
            "post",
            f"/model-hub/develops/{upload.id}/start_evals_process/",
            {"user_eval_ids": ["00000000-0000-0000-0000-000000000001"]},
        ),
    }
    method, url, body = calls[action]
    response = getattr(auth_client, method)(url, body, format="json")
    assert response.status_code == 409, getattr(response, "data", None)
    assert response.data["code"] == (
        "dataset_import_failed" if state == "failed" else "dataset_processing"
    )
