from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.views.datasets.create import file_upload


class _SuccessfulResourceCallLog:
    status = "created"

    def save(self):
        return None


def _csv_file(name="rows.csv", content=b"input,output\nhello,world\n"):
    return SimpleUploadedFile(name, content, content_type="text/csv")


def _patch_usage(monkeypatch, module_path):
    # module_path kept for call-site readability; resource deducts now all go
    # through the billing boundary instead of per-module imports (TH-5971).
    del module_path
    calls = []

    def record_usage(self, *args, **kwargs):
        calls.append((args, kwargs))
        return _SuccessfulResourceCallLog()

    monkeypatch.setattr(
        "tfc.billing.boundary._EeBilling.log_and_deduct_resource",
        record_usage,
    )
    return calls


def _synthetic_create_payload(name, num_rows=10, columns=None, regenerate=None):
    payload = {
        "num_rows": num_rows,
        "columns": columns
        or [
            {
                "name": "answer",
                "data_type": "text",
                "description": "Answer",
                "property": "answer",
            }
        ],
        "dataset": {
            "name": name,
            "description": "Dataset",
            "objective": "Generate rows",
            "patterns": [],
        },
    }
    if regenerate is not None:
        payload["regenerate"] = regenerate
    return payload


def _synthetic_add_rows_payload(num_rows=10):
    return {
        "num_rows": num_rows,
        "fill_existing_rows": False,
        "columns": [
            {
                "name": "answer",
                "data_type": "text",
                "description": "Answer",
                "skip": False,
                "is_new": False,
                "property": "answer",
            }
        ],
        "dataset": {
            "description": "Dataset",
            "objective": "Generate rows",
            "patterns": [],
        },
    }


def _allow_synthetic_entitlement(monkeypatch):
    monkeypatch.setattr(
        "ee.usage.services.entitlements.Entitlements.check_feature",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason=""),
    )


@pytest.mark.django_db
def test_create_empty_dataset_sets_workspace_after_validation(
    auth_client, workspace, monkeypatch
):
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.empty_dataset",
    )

    response = auth_client.post(
        "/model-hub/develops/create-empty-dataset/",
        {
            "new_dataset_name": "Workspace Empty Dataset",
            "model_type": "GenerativeLLM",
            "row": 0,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    dataset_id = response.json()["result"]["dataset_id"]
    dataset = Dataset.no_workspace_objects.get(id=dataset_id)
    assert dataset.workspace_id == workspace.id
    assert len(usage_calls) == 1


@pytest.mark.django_db
def test_create_empty_dataset_accepts_legacy_model_type(
    auth_client, workspace, monkeypatch
):
    _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.empty_dataset",
    )

    response = auth_client.post(
        "/model-hub/develops/create-empty-dataset/",
        {
            "new_dataset_name": "Legacy Model Type Empty Dataset",
            "model_type": "generative_llm",
            "row": 0,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.workspace_id == workspace.id
    assert dataset.model_type == "GenerativeLLM"
    assert result["dataset_model_type"] == "GenerativeLLM"


@pytest.mark.django_db
def test_create_empty_dataset_duplicate_name_does_not_charge(
    auth_client, organization, workspace, user, monkeypatch
):
    Dataset.objects.create(
        name="Duplicate Empty Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.empty_dataset",
    )

    response = auth_client.post(
        "/model-hub/develops/create-empty-dataset/",
        {
            "new_dataset_name": "Duplicate Empty Dataset",
            "model_type": "GenerativeLLM",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []


@pytest.mark.django_db
def test_manual_dataset_sets_workspace_and_does_not_charge_invalid_request(
    auth_client, workspace, monkeypatch
):
    usage_calls = _patch_usage(monkeypatch, "model_hub.views.develop_dataset")

    invalid_response = auth_client.post(
        "/model-hub/develops/create-dataset-manually/",
        {
            "dataset_name": "Invalid Manual Dataset",
            "model_type": "GenerativeLLM",
            "number_of_rows": 0,
            "number_of_columns": 1,
        },
        format="json",
    )
    assert invalid_response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []

    response = auth_client.post(
        "/model-hub/develops/create-dataset-manually/",
        {
            "dataset_name": "Workspace Manual Dataset",
            "model_type": "GenerativeLLM",
            "number_of_rows": 2,
            "number_of_columns": 2,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    dataset_id = response.json()["result"]["dataset_id"]
    dataset = Dataset.no_workspace_objects.get(id=dataset_id)
    assert dataset.workspace_id == workspace.id
    assert Row.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 2
    assert (
        Column.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 2
    )
    assert len(usage_calls) == 2


@pytest.mark.django_db
def test_create_dataset_from_local_file_sets_workspace_and_progress_scope(
    auth_client, workspace, monkeypatch
):
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.file_upload",
    )
    queued_tasks = []
    monkeypatch.setattr(
        "model_hub.views.datasets.create.file_upload.upload_file_to_minio",
        lambda file_obj, object_key, org_id=None: f"minio://{object_key}",
    )
    monkeypatch.setattr(
        "model_hub.views.datasets.create.file_upload.process_dataset_from_file.delay",
        lambda *args, **kwargs: queued_tasks.append((args, kwargs)),
    )

    response = auth_client.post(
        "/model-hub/develops/create-dataset-from-local-file/",
        {
            "new_dataset_name": "Workspace Local File Dataset",
            "model_type": "GenerativeLLM",
            "file": _csv_file(),
        },
        format="multipart",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.workspace_id == workspace.id
    assert dataset.dataset_config["file_processing_status"] == "queued"
    assert result["estimated_rows"] == 1
    assert result["estimated_columns"] == 2
    assert len(usage_calls) == 2
    assert queued_tasks and queued_tasks[0][0][0] == str(dataset.id)

    progress_response = auth_client.get(
        f"/model-hub/develops/dataset-creation-progress/{dataset.id}/"
    )
    assert progress_response.status_code == status.HTTP_200_OK
    assert progress_response.json()["result"]["processing_status"] == "queued"


@pytest.mark.django_db
def test_create_dataset_from_local_file_duplicate_name_does_not_charge(
    auth_client, organization, workspace, user, monkeypatch
):
    Dataset.objects.create(
        name="Duplicate Local File Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.file_upload",
    )

    response = auth_client.post(
        "/model-hub/develops/create-dataset-from-local-file/",
        {
            "new_dataset_name": "Duplicate Local File Dataset",
            "model_type": "GenerativeLLM",
            "file": _csv_file(),
        },
        format="multipart",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []


@pytest.mark.django_db(transaction=True)
def test_process_dataset_from_file_skips_media_dispatch_for_text_only_csv(
    organization, workspace, user, monkeypatch, tmp_path
):
    dataset = Dataset.objects.create(
        name="Text Only Local File Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type="GenerativeLLM",
        dataset_config={
            "dataset_source_local": True,
            "file_processing_status": "queued",
            "original_filename": "text-only.csv",
            "file_url": "minio://text-only.csv",
        },
    )
    csv_path = tmp_path / "text-only.csv"
    csv_path.write_text("input,output\nhello,world\n", encoding="utf-8")
    media_calls = []

    monkeypatch.setattr(
        file_upload,
        "download_file_from_minio",
        lambda file_url, original_filename=None: str(csv_path),
    )
    monkeypatch.setattr(
        file_upload.process_media_uploads,
        "delay",
        lambda *args, **kwargs: media_calls.append((args, kwargs)),
    )

    file_upload.process_dataset_from_file.run_sync(
        str(dataset.id),
        "minio://text-only.csv",
        "text-only.csv",
    )

    dataset.refresh_from_db()
    assert dataset.dataset_config["file_processing_status"] == "completed"
    assert dataset.dataset_config["completed_columns"] == 2
    assert dataset.dataset_config["error_columns"] == 0
    assert media_calls == []
    assert Column.objects.filter(dataset=dataset, deleted=False).count() == 2
    assert Row.objects.filter(dataset=dataset, deleted=False).count() == 1
    assert Cell.objects.filter(dataset=dataset, deleted=False).count() == 2


@pytest.mark.django_db
def test_add_rows_from_file_rejects_other_workspace_before_usage_charge(
    auth_client, organization, user, monkeypatch
):
    other_workspace = Workspace.objects.create(
        name="Other File Import Workspace",
        organization=organization,
        created_by=user,
    )
    dataset = Dataset.no_workspace_objects.create(
        name="Other Workspace File Import Dataset",
        organization=organization,
        workspace=other_workspace,
        user=user,
    )
    Column.no_workspace_objects.create(
        name="input",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    usage_calls = _patch_usage(monkeypatch, "model_hub.views.develop_dataset")

    response = auth_client.post(
        "/model-hub/develops/add_rows_from_file/",
        {
            "dataset_id": str(dataset.id),
            "file": _csv_file(),
        },
        format="multipart",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert usage_calls == []
    assert Row.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 0
    assert Cell.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 0


def _create_experiment_dataset_fixture(
    organization,
    user,
    workspace,
    *,
    dataset_name="Experiment Source Dataset",
    experiment_dataset_name="Experiment Result Dataset",
):
    dataset = Dataset.no_workspace_objects.create(
        name=dataset_name,
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    input_column = Column.no_workspace_objects.create(
        name="input",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    result_column = Column.no_workspace_objects.create(
        name="experiment_result",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(input_column.id), str(result_column.id)]
    dataset.save(update_fields=["column_order"])

    first_row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    second_row = Row.no_workspace_objects.create(dataset=dataset, order=7)
    Cell.no_workspace_objects.create(
        dataset=dataset,
        column=input_column,
        row=first_row,
        value="first input",
    )
    Cell.no_workspace_objects.create(
        dataset=dataset,
        column=result_column,
        row=first_row,
        value="first result",
    )
    Cell.no_workspace_objects.create(
        dataset=dataset,
        column=input_column,
        row=second_row,
        value="second input",
    )
    Cell.no_workspace_objects.create(
        dataset=dataset,
        column=result_column,
        row=second_row,
        value="second result",
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name=f"{dataset_name} experiment",
        dataset=dataset,
        snapshot_dataset=dataset,
        column=input_column,
        status=StatusType.COMPLETED.value,
        prompt_config=[],
        user=user,
    )
    experiment_dataset = ExperimentDatasetTable.no_workspace_objects.create(
        name=experiment_dataset_name,
        experiment=experiment,
        status=StatusType.COMPLETED.value,
    )
    experiment_dataset.columns.add(result_column)
    experiment.experiments_datasets.add(experiment_dataset)
    return {
        "dataset": dataset,
        "input_column": input_column,
        "result_column": result_column,
        "rows": [first_row, second_row],
        "experiment": experiment,
        "experiment_dataset": experiment_dataset,
    }


@pytest.mark.django_db
def test_get_experiment_dataset_table_scopes_and_reports_total_rows(
    auth_client, organization, workspace, user
):
    fixture = _create_experiment_dataset_fixture(
        organization,
        user,
        workspace,
        dataset_name="Active Experiment Table Source",
        experiment_dataset_name="Active Experiment Table Result",
    )

    response = auth_client.get(
        "/model-hub/develops/"
        f"{fixture['experiment_dataset'].id}/get-experiment-dataset-table/"
        "?page_size=1&current_page_index=0"
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["metadata"]["dataset_name"] == "Active Experiment Table Result"
    assert result["metadata"]["experiment_id"] == str(fixture["experiment"].id)
    assert result["metadata"]["experiment_name"] == fixture["experiment"].name
    assert result["metadata"]["total_rows"] == 2
    assert result["metadata"]["total_pages"] == 2
    assert len(result["table"]) == 1
    assert result["table"][0]["row_id"] == str(fixture["rows"][0].id)
    assert result["table"][0][str(fixture["input_column"].id)]["cell_value"] == (
        "first input"
    )
    assert result["table"][0][str(fixture["result_column"].id)]["cell_value"] == (
        "first result"
    )
    assert {column["name"] for column in result["column_config"]} == {
        "input",
        "experiment_result",
    }


@pytest.mark.django_db
def test_experiment_dataset_routes_reject_other_workspace_before_usage_or_read(
    auth_client, organization, user, monkeypatch
):
    other_workspace = Workspace.objects.create(
        name="Other Experiment Dataset Workspace",
        organization=organization,
        created_by=user,
    )
    fixture = _create_experiment_dataset_fixture(
        organization,
        user,
        other_workspace,
        dataset_name="Other Experiment Table Source",
        experiment_dataset_name="Other Experiment Table Result",
    )
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.dataset_from_experiment",
    )

    table_response = auth_client.get(
        "/model-hub/develops/"
        f"{fixture['experiment_dataset'].id}/get-experiment-dataset-table/"
    )
    derived_response = auth_client.get(
        f"/model-hub/develops/get-derived-datasets/{fixture['dataset'].id}/"
    )
    create_response = auth_client.post(
        f"/model-hub/develops/{fixture['experiment_dataset'].id}/create-dataset/",
        {"name": "Should Not Be Created", "model_type": "GenerativeLLM"},
        format="json",
    )

    assert table_response.status_code == status.HTTP_404_NOT_FOUND
    assert derived_response.status_code == status.HTTP_404_NOT_FOUND
    assert create_response.status_code == status.HTTP_404_NOT_FOUND
    assert usage_calls == []
    assert not Dataset.no_workspace_objects.filter(
        name="Should Not Be Created",
        organization=organization,
        deleted=False,
    ).exists()


@pytest.mark.django_db
def test_get_derived_datasets_returns_scoped_individual_experiment_rows(
    auth_client, organization, workspace, user
):
    fixture = _create_experiment_dataset_fixture(
        organization,
        user,
        workspace,
        dataset_name="Active Derived Dataset Source",
        experiment_dataset_name="Active Derived Dataset Result",
    )

    response = auth_client.get(
        f"/model-hub/develops/get-derived-datasets/{fixture['dataset'].id}/"
    )

    assert response.status_code == status.HTTP_200_OK
    rows = response.json()["result"]
    assert rows == [
        {
            "id": str(fixture["experiment_dataset"].id),
            "name": "Active Derived Dataset Result",
            "experiment": {
                "id": str(fixture["experiment"].id),
                "name": fixture["experiment"].name,
            },
        }
    ]


@pytest.mark.django_db
def test_create_dataset_from_experiment_sets_workspace_and_clones_table(
    auth_client, organization, workspace, user, monkeypatch
):
    fixture = _create_experiment_dataset_fixture(
        organization,
        user,
        workspace,
        dataset_name="Active Experiment Clone Source",
        experiment_dataset_name="Active Experiment Clone Result",
    )
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.dataset_from_experiment",
    )
    monkeypatch.setattr(
        "model_hub.views.datasets.create.dataset_from_experiment.get_recommendations",
        lambda dataset: {},
    )

    response = auth_client.post(
        f"/model-hub/develops/{fixture['experiment_dataset'].id}/create-dataset/",
        {"name": "Dataset From Experiment Clone", "model_type": "GenerativeLLM"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    cloned_dataset = Dataset.no_workspace_objects.get(
        name="Dataset From Experiment Clone",
        organization=organization,
        deleted=False,
    )
    assert cloned_dataset.workspace_id == workspace.id
    assert len(usage_calls) == 1

    cloned_columns = Column.no_workspace_objects.filter(
        dataset=cloned_dataset,
        deleted=False,
    ).order_by("name")
    assert {column.name for column in cloned_columns} == {"experiment_result", "input"}
    cloned_column_ids = {str(column.id) for column in cloned_columns}
    cloned_dataset.refresh_from_db()
    assert set(cloned_dataset.column_order) == cloned_column_ids
    assert set(cloned_dataset.column_config.keys()) == cloned_column_ids

    cloned_rows = Row.no_workspace_objects.filter(
        dataset=cloned_dataset,
        deleted=False,
    )
    assert cloned_rows.count() == 2
    assert (
        Cell.no_workspace_objects.filter(
            dataset=cloned_dataset,
            deleted=False,
            value__in=["first input", "first result", "second input", "second result"],
        ).count()
        == 4
    )


@pytest.mark.django_db
def test_create_dataset_from_experiment_duplicate_name_does_not_charge(
    auth_client, organization, workspace, user, monkeypatch
):
    fixture = _create_experiment_dataset_fixture(
        organization,
        user,
        workspace,
        dataset_name="Duplicate Experiment Clone Source",
        experiment_dataset_name="Duplicate Experiment Clone Result",
    )
    Dataset.no_workspace_objects.create(
        name="Duplicate Experiment Clone",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.dataset_from_experiment",
    )

    response = auth_client.post(
        f"/model-hub/develops/{fixture['experiment_dataset'].id}/create-dataset/",
        {"name": "Duplicate Experiment Clone", "model_type": "GenerativeLLM"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []
    assert (
        Dataset.no_workspace_objects.filter(
            name="Duplicate Experiment Clone",
            organization=organization,
            deleted=False,
        ).count()
        == 1
    )


def _hf_first_rows_payload(column_name="text"):
    return {"features": [{"name": column_name, "type": {"dtype": "string"}}]}


def _patch_hf_preview(monkeypatch, module_path, column_name="text"):
    preview_calls = []

    def preview_dataset(*args, **kwargs):
        preview_calls.append((args, kwargs))
        return _hf_first_rows_payload(column_name)

    monkeypatch.setattr(
        f"{module_path}.load_hf_dataset_with_retries",
        preview_dataset,
    )
    return preview_calls


def _patch_hf_start_activity(monkeypatch, calls):
    def record_start_activity(activity_name, args, queue):
        calls.append((activity_name, args, queue))
        return "activity-id"

    monkeypatch.setattr(
        "tfc.temporal.drop_in.start_activity",
        record_start_activity,
    )


@pytest.mark.django_db
def test_create_huggingface_dataset_sets_workspace_after_validation(
    auth_client, workspace, monkeypatch
):
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.huggingface",
    )
    preview_calls = _patch_hf_preview(
        monkeypatch,
        "model_hub.views.datasets.create.huggingface",
    )
    monkeypatch.setattr(
        "model_hub.views.datasets.create.huggingface."
        "CreateDatasetFromHuggingFaceView.get_huggingface_dataset_info",
        lambda *args, **kwargs: {"num_rows": 2, "split": "train"},
    )
    activity_calls = []
    _patch_hf_start_activity(monkeypatch, activity_calls)

    response = auth_client.post(
        "/model-hub/develops/create-dataset-from-huggingface/",
        {
            "name": "Workspace HuggingFace Dataset",
            "model_type": "GenerativeLLM",
            "num_rows": 2,
            "huggingface_dataset_name": "future-agi/example",
            "huggingface_dataset_config": "default",
            "huggingface_dataset_split": "train",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    dataset_id = response.json()["result"]["dataset_id"]
    dataset = Dataset.no_workspace_objects.get(id=dataset_id)
    assert dataset.workspace_id == workspace.id
    assert Row.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 2
    assert (
        Column.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 1
    )
    assert len(usage_calls) == 2
    assert len(preview_calls) == 1
    assert activity_calls
    assert set(activity_calls[0][1][7].keys()) == {0, 1}


@pytest.mark.django_db
def test_create_huggingface_dataset_duplicate_name_does_not_charge_or_preview(
    auth_client, organization, workspace, user, monkeypatch
):
    Dataset.no_workspace_objects.create(
        name="Duplicate HuggingFace Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.huggingface",
    )
    preview_calls = _patch_hf_preview(
        monkeypatch,
        "model_hub.views.datasets.create.huggingface",
    )

    response = auth_client.post(
        "/model-hub/develops/create-dataset-from-huggingface/",
        {
            "name": "Duplicate HuggingFace Dataset",
            "model_type": "GenerativeLLM",
            "num_rows": 2,
            "huggingface_dataset_name": "future-agi/example",
            "huggingface_dataset_config": "default",
            "huggingface_dataset_split": "train",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []
    assert preview_calls == []


@pytest.mark.django_db
def test_add_huggingface_rows_rejects_other_workspace_before_preview_or_usage(
    auth_client, organization, user, monkeypatch
):
    other_workspace = Workspace.objects.create(
        name="Other HuggingFace Import Workspace",
        organization=organization,
        created_by=user,
    )
    dataset = Dataset.no_workspace_objects.create(
        name="Other Workspace HuggingFace Import Dataset",
        organization=organization,
        workspace=other_workspace,
        user=user,
    )
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.add_rows.huggingface",
    )
    preview_calls = _patch_hf_preview(
        monkeypatch,
        "model_hub.views.datasets.add_rows.huggingface",
    )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_rows_from_huggingface/",
        {
            "num_rows": 2,
            "huggingface_dataset_name": "future-agi/example",
            "huggingface_dataset_config": "default",
            "huggingface_dataset_split": "train",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert usage_calls == []
    assert preview_calls == []
    assert Row.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 0


@pytest.mark.django_db
def test_add_huggingface_rows_sets_columns_and_appends_after_max_order(
    auth_client, organization, workspace, user, monkeypatch
):
    dataset = Dataset.no_workspace_objects.create(
        name="Workspace HuggingFace Row Import Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    high_row = Row.no_workspace_objects.create(dataset=dataset, order=10)
    low_row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.add_rows.huggingface",
    )
    preview_calls = _patch_hf_preview(
        monkeypatch,
        "model_hub.views.datasets.add_rows.huggingface",
        column_name="hf_text",
    )
    activity_calls = []
    _patch_hf_start_activity(monkeypatch, activity_calls)

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_rows_from_huggingface/",
        {
            "num_rows": 2,
            "huggingface_dataset_name": "future-agi/example",
            "huggingface_dataset_config": "default",
            "huggingface_dataset_split": "train",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    rows = Row.no_workspace_objects.filter(dataset=dataset, deleted=False).order_by(
        "order"
    )
    assert list(rows.values_list("order", flat=True)) == [0, 10, 11, 12]
    column = Column.no_workspace_objects.get(dataset=dataset, name="hf_text")
    assert column.status == StatusType.RUNNING.value
    dataset.refresh_from_db()
    assert str(column.id) in dataset.column_order
    assert dataset.column_config[str(column.id)] == {
        "is_visible": True,
        "is_frozen": None,
    }
    assert (
        Cell.no_workspace_objects.filter(
            dataset=dataset,
            column=column,
            row__in=[high_row, low_row],
            value="",
            deleted=False,
        ).count()
        == 2
    )
    assert len(usage_calls) == 1
    assert len(preview_calls) == 1
    assert activity_calls
    queued_rows = activity_calls[0][1][7]
    assert set(queued_rows.keys()) == {0, 1}
    queued_row_orders = set(
        Row.no_workspace_objects.filter(id__in=queued_rows.values()).values_list(
            "order", flat=True
        )
    )
    assert queued_row_orders == {11, 12}


@pytest.mark.django_db
def test_create_synthetic_dataset_sets_workspace_and_does_not_charge_invalid_request(
    auth_client, workspace, monkeypatch
):
    _allow_synthetic_entitlement(monkeypatch)
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.synthetic",
    )
    queued_tasks = []
    monkeypatch.setattr(
        "model_hub.views.datasets.create.synthetic.create_synthetic_dataset.delay",
        lambda *args, **kwargs: queued_tasks.append((args, kwargs)),
    )

    invalid_response = auth_client.post(
        "/model-hub/develops/create-synthetic-dataset/",
        _synthetic_create_payload("Invalid Synthetic Dataset", num_rows=9),
        format="json",
    )

    assert invalid_response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []
    assert queued_tasks == []

    response = auth_client.post(
        "/model-hub/develops/create-synthetic-dataset/",
        _synthetic_create_payload("Workspace Synthetic Dataset"),
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    dataset_id = response.json()["result"]["data"]["id"]
    dataset = Dataset.no_workspace_objects.get(id=dataset_id)
    assert dataset.workspace_id == workspace.id
    assert dataset.synthetic_dataset_config["dataset"]["name"] == dataset.name
    assert Row.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 10
    assert (
        Column.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 1
    )
    assert len(usage_calls) == 2
    assert queued_tasks


@pytest.mark.django_db
def test_synthetic_config_rejects_other_workspace_before_usage_or_mutation(
    auth_client, organization, user, monkeypatch
):
    other_workspace = Workspace.objects.create(
        name="Other Synthetic Workspace",
        organization=organization,
        created_by=user,
    )
    original_config = _synthetic_create_payload("Other Synthetic Dataset")
    dataset = Dataset.no_workspace_objects.create(
        name="Other Synthetic Dataset",
        organization=organization,
        workspace=other_workspace,
        user=user,
        synthetic_dataset_config=original_config,
        column_order=[],
        column_config={},
    )
    column = Column.no_workspace_objects.create(
        name="answer",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.column_config = {str(column.id): {"is_visible": True, "is_frozen": None}}
    dataset.save(update_fields=["column_order", "column_config"])
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value="keep",
    )
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.synthetic",
    )
    queued_tasks = []
    monkeypatch.setattr(
        "model_hub.views.datasets.create.synthetic.generate_new_rows.delay",
        lambda *args, **kwargs: queued_tasks.append(("update_rows", args, kwargs)),
    )
    monkeypatch.setattr(
        "model_hub.views.datasets.add_rows.synthetic.generate_new_rows.delay",
        lambda *args, **kwargs: queued_tasks.append(("add_rows", args, kwargs)),
    )

    config_response = auth_client.get(
        f"/model-hub/develops/{dataset.id}/synthetic-config/"
    )
    update_response = auth_client.put(
        f"/model-hub/develops/{dataset.id}/update-synthetic-config/",
        _synthetic_create_payload("Other Synthetic Dataset"),
        format="json",
    )
    add_rows_response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_synthetic_data/",
        _synthetic_add_rows_payload(),
        format="json",
    )

    assert config_response.status_code == status.HTTP_404_NOT_FOUND
    assert update_response.status_code == status.HTTP_404_NOT_FOUND
    assert add_rows_response.status_code == status.HTTP_404_NOT_FOUND
    assert usage_calls == []
    assert queued_tasks == []
    dataset.refresh_from_db()
    assert dataset.synthetic_dataset_config == original_config
    assert Row.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 1
    assert (
        Column.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 1
    )
    assert Cell.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 1


@pytest.mark.django_db
def test_update_synthetic_invalid_regenerate_does_not_mutate_dataset(
    auth_client, organization, workspace, user, monkeypatch
):
    original_config = _synthetic_create_payload("Active Synthetic Dataset")
    dataset = Dataset.objects.create(
        name="Active Synthetic Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        synthetic_dataset_config=original_config,
        column_order=[],
        column_config={},
    )
    column = Column.objects.create(
        name="answer",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.column_config = {str(column.id): {"is_visible": True, "is_frozen": None}}
    dataset.save(update_fields=["column_order", "column_config"])
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=column, row=row, value="keep")
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.synthetic",
    )
    queued_tasks = []
    monkeypatch.setattr(
        "model_hub.views.datasets.create.synthetic.create_synthetic_dataset.delay",
        lambda *args, **kwargs: queued_tasks.append((args, kwargs)),
    )

    response = auth_client.put(
        f"/model-hub/develops/{dataset.id}/update-synthetic-config/",
        _synthetic_create_payload(
            "Active Synthetic Dataset", num_rows=9, regenerate=True
        ),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []
    assert queued_tasks == []
    dataset.refresh_from_db()
    assert dataset.synthetic_dataset_config == original_config
    assert Row.objects.filter(dataset=dataset, deleted=False).count() == 1
    assert Column.objects.filter(dataset=dataset, deleted=False).count() == 1
    assert Cell.objects.filter(dataset=dataset, deleted=False).count() == 1
