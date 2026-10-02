"""
Test cases for V2 Experiment API endpoints: Stop, Suggest Name, and Validate Name.

Run with: pytest model_hub/tests/test_experiment_v2_api.py -v
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable

# ==================== Fixtures ====================


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Organization")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="test@example.com",
        password="testpassword123",
        name="Test User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def auth_client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    settings.CURRENT_WORKSPACE = workspace
    settings.ORGANIZATION = user.organization
    return client


@pytest.fixture
def dataset(db, organization, workspace):
    ds = Dataset.objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def snapshot_dataset(db, organization, workspace):
    ds = Dataset.objects.create(
        name="Snapshot Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
    )
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def output_column(db, snapshot_dataset):
    return Column.objects.create(
        name="Output Column",
        dataset=snapshot_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value,
        status=StatusType.RUNNING.value,
    )


@pytest.fixture
def row(db, snapshot_dataset):
    return Row.objects.create(dataset=snapshot_dataset, order=0)


@pytest.fixture
def running_cell(db, snapshot_dataset, output_column, row):
    return Cell.objects.create(
        dataset=snapshot_dataset,
        column=output_column,
        row=row,
        value="",
        status=CellStatus.RUNNING.value,
    )


@pytest.fixture
def experiment(db, dataset, snapshot_dataset):
    return ExperimentsTable.objects.create(
        name="Test Experiment",
        dataset=dataset,
        snapshot_dataset=snapshot_dataset,
        status=StatusType.RUNNING.value,
    )


@pytest.fixture
def experiment_dataset(db, experiment, output_column):
    edt = ExperimentDatasetTable.objects.create(
        name="Experiment Dataset",
        status=StatusType.RUNNING.value,
        experiment=experiment,
    )
    edt.columns.add(output_column)
    return edt


@pytest.fixture
def eval_template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="test-eval-template",
        organization=organization,
        workspace=workspace,
        criteria="Evaluate: {{output}}",
        model="gpt-4",
        config={"output": "score"},
    )


@pytest.fixture
def eval_metric(db, dataset, organization, workspace, eval_template):
    return UserEvalMetric.objects.create(
        name="Test Eval",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        template=eval_template,
        status=StatusType.COMPLETED.value,
        config={},
    )


# ==================== ExperimentStopV2View Tests ====================


@pytest.mark.django_db
class TestExperimentStopV2View:
    """Tests for ExperimentStopV2View - POST /experiments/v2/<id>/stop/"""

    def test_stop_running_experiment_success(
        self, auth_client, experiment, experiment_dataset, running_cell
    ):
        """Test successfully stopping a running experiment."""
        with patch(
            "tfc.temporal.experiments.cancel_all_experiment_workflows"
        ) as mock_cancel:
            mock_cancel.return_value = {"main_cancelled": True, "rerun_cancelled": 0}
            response = auth_client.post(
                f"/model-hub/experiments/v2/{experiment.id}/stop/",
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["workflows_cancelled"]["main"] is True
        mock_cancel.assert_called_once_with(str(experiment.id))

    def test_stop_queued_experiment_success(
        self, auth_client, experiment, experiment_dataset
    ):
        """Test stopping a queued experiment."""
        experiment.status = StatusType.QUEUED.value
        experiment.save(update_fields=["status"])

        with patch(
            "tfc.temporal.experiments.cancel_all_experiment_workflows"
        ) as mock_cancel:
            mock_cancel.return_value = {"main_cancelled": True, "rerun_cancelled": 0}
            response = auth_client.post(
                f"/model-hub/experiments/v2/{experiment.id}/stop/",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_stop_completed_experiment_fails(self, auth_client, experiment):
        """Test that stopping a completed experiment returns error."""
        experiment.status = StatusType.COMPLETED.value
        experiment.save(update_fields=["status"])

        response = auth_client.post(
            f"/model-hub/experiments/v2/{experiment.id}/stop/",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_stop_failed_experiment_fails(self, auth_client, experiment):
        """Test that stopping a failed experiment returns error."""
        experiment.status = StatusType.FAILED.value
        experiment.save(update_fields=["status"])

        response = auth_client.post(
            f"/model-hub/experiments/v2/{experiment.id}/stop/",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_stop_nonexistent_experiment(self, auth_client):
        """Test stopping a non-existent experiment returns 404."""
        response = auth_client.post(
            f"/model-hub/experiments/v2/{uuid.uuid4()}/stop/",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_stop_experiment_unauthenticated(self, experiment):
        """Test that unauthenticated users cannot stop experiments."""
        client = APIClient()
        response = client.post(
            f"/model-hub/experiments/v2/{experiment.id}/stop/",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_stop_other_org_experiment(self, auth_client, dataset, snapshot_dataset):
        """Test that users cannot stop another org's experiment."""
        other_org = Organization.objects.create(name="Other Org")
        other_ds = Dataset.objects.create(
            name="Other DS",
            organization=other_org,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_exp = ExperimentsTable.objects.create(
            name="Other Exp",
            dataset=other_ds,
            status=StatusType.RUNNING.value,
        )

        response = auth_client.post(
            f"/model-hub/experiments/v2/{other_exp.id}/stop/",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_stop_with_rerun_workflows(
        self, auth_client, experiment, experiment_dataset
    ):
        """Test stopping cancels both main and rerun workflows."""
        with patch(
            "tfc.temporal.experiments.cancel_all_experiment_workflows"
        ) as mock_cancel:
            mock_cancel.return_value = {"main_cancelled": True, "rerun_cancelled": 3}
            response = auth_client.post(
                f"/model-hub/experiments/v2/{experiment.id}/stop/",
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["workflows_cancelled"]["reruns"] == 3


# ==================== ExperimentEvaluationStatsView V2 Tests ====================


@pytest.mark.django_db
class TestExperimentEvaluationStatsV2View:
    """Tests for GET /experiments/v2/<id>/evaluations/<id>/stats/."""

    def test_v2_eval_stats_reads_fk_experiment_datasets(
        self,
        auth_client,
        experiment,
        experiment_dataset,
        snapshot_dataset,
        row,
        eval_metric,
    ):
        experiment.user_eval_template_ids.add(eval_metric)
        eval_column = Column.objects.create(
            name="quality-score",
            dataset=snapshot_dataset,
            data_type=DataTypeChoices.FLOAT.value,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{experiment_dataset.id}-sourceid-{eval_metric.id}",
            status=StatusType.COMPLETED.value,
        )
        experiment_dataset.columns.add(eval_column)
        Cell.objects.create(
            dataset=snapshot_dataset,
            column=eval_column,
            row=row,
            value="0.8",
            value_infos=json.dumps(
                {
                    "metadata": {
                        "usage": {
                            "completion_tokens": 4,
                            "prompt_tokens": 6,
                            "total_tokens": 10,
                        },
                        "response_time": 20,
                    }
                }
            ),
            status=CellStatus.PASS.value,
        )

        response = auth_client.get(
            f"/model-hub/experiments/v2/{experiment.id}/"
            f"evaluations/{eval_metric.id}/stats/",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["experiment_id"] == str(experiment.id)
        assert data["evaluation_id"] == str(eval_metric.id)
        assert len(data["evaluation_columns"]) == 1
        stats = data["evaluation_columns"][0]
        assert stats["column_id"] == str(eval_column.id)
        assert stats["total_rows"] == 1
        assert stats["success_rate"] == 100
        assert stats["avg_response_time"] == 20
        assert stats["token_usage"]["total_tokens"] == 10


# ==================== ExperimentNameSuggestionView Tests ====================


@pytest.mark.django_db
class TestExperimentNameSuggestionView:
    """Tests for ExperimentNameSuggestionView - GET /experiments/v2/suggest-name/<id>/"""

    def test_suggest_name_first_experiment(self, auth_client, dataset):
        """Test name suggestion when no experiments exist today."""
        response = auth_client.get(
            f"/model-hub/experiments/v2/suggest-name/{dataset.id}/",
        )

        assert response.status_code == status.HTTP_200_OK
        name = response.json()["result"]["suggested_name"]
        today = datetime.now(timezone.utc).strftime("%y/%m/%d")
        assert name == f"DS_Test Dataset_exp_{today}"

    def test_suggest_name_second_experiment(self, auth_client, dataset):
        """Test name suggestion with one existing experiment today → _v2."""
        ExperimentsTable.objects.create(
            name="Existing",
            dataset=dataset,
            status=StatusType.COMPLETED.value,
        )

        response = auth_client.get(
            f"/model-hub/experiments/v2/suggest-name/{dataset.id}/",
        )

        assert response.status_code == status.HTTP_200_OK
        name = response.json()["result"]["suggested_name"]
        today = datetime.now(timezone.utc).strftime("%y/%m/%d")
        assert name == f"DS_Test Dataset_exp_{today}_v2"

    def test_suggest_name_third_experiment(self, auth_client, dataset):
        """Test name suggestion with two existing experiments today → _v3."""
        for i in range(2):
            ExperimentsTable.objects.create(
                name=f"Existing {i}",
                dataset=dataset,
                status=StatusType.COMPLETED.value,
            )

        response = auth_client.get(
            f"/model-hub/experiments/v2/suggest-name/{dataset.id}/",
        )

        assert response.status_code == status.HTTP_200_OK
        name = response.json()["result"]["suggested_name"]
        today = datetime.now(timezone.utc).strftime("%y/%m/%d")
        assert name == f"DS_Test Dataset_exp_{today}_v3"

    def test_suggest_name_long_dataset_name_cropped(
        self, auth_client, organization, workspace
    ):
        """Test that long dataset names are cropped to fit 98 char max."""
        long_name = "A" * 200
        ds = Dataset.objects.create(
            name=long_name,
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )

        response = auth_client.get(
            f"/model-hub/experiments/v2/suggest-name/{ds.id}/",
        )

        assert response.status_code == status.HTTP_200_OK
        name = response.json()["result"]["suggested_name"]
        assert len(name) <= 98

    def test_suggest_name_min_4_chars_dataset_name(
        self, auth_client, organization, workspace
    ):
        """Test that dataset name is at least 4 chars even when cropped."""
        long_name = "ABCD" + "X" * 200
        ds = Dataset.objects.create(
            name=long_name,
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )

        response = auth_client.get(
            f"/model-hub/experiments/v2/suggest-name/{ds.id}/",
        )

        assert response.status_code == status.HTTP_200_OK
        name = response.json()["result"]["suggested_name"]
        # Extract dataset name portion: between "DS_" and "_exp_"
        ds_part = name.split("DS_")[1].split("_exp_")[0]
        assert len(ds_part) >= 4

    def test_suggest_name_nonexistent_dataset(self, auth_client):
        """Test with a non-existent dataset returns 404."""
        response = auth_client.get(
            f"/model-hub/experiments/v2/suggest-name/{uuid.uuid4()}/",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_suggest_name_unauthenticated(self, dataset):
        """Test that unauthenticated users get rejected."""
        client = APIClient()
        response = client.get(
            f"/model-hub/experiments/v2/suggest-name/{dataset.id}/",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_suggest_name_other_org_dataset(self, auth_client):
        """Test that users cannot get suggestions for another org's dataset."""
        other_org = Organization.objects.create(name="Other Org")
        other_ds = Dataset.objects.create(
            name="Other DS",
            organization=other_org,
            source=DatasetSourceChoices.BUILD.value,
        )

        response = auth_client.get(
            f"/model-hub/experiments/v2/suggest-name/{other_ds.id}/",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_suggest_name_deleted_experiments_not_counted(self, auth_client, dataset):
        """Test that soft-deleted experiments are not counted."""
        exp = ExperimentsTable.objects.create(
            name="Deleted",
            dataset=dataset,
            status=StatusType.COMPLETED.value,
        )
        exp.deleted = True
        exp.save()

        response = auth_client.get(
            f"/model-hub/experiments/v2/suggest-name/{dataset.id}/",
        )

        assert response.status_code == status.HTTP_200_OK
        name = response.json()["result"]["suggested_name"]
        today = datetime.now(timezone.utc).strftime("%y/%m/%d")
        # Deleted experiment shouldn't count, so no version suffix
        assert name == f"DS_Test Dataset_exp_{today}"


# ==================== ExperimentNameValidationView Tests ====================


@pytest.mark.django_db
class TestExperimentNameValidationView:
    """Tests for ExperimentNameValidationView - GET /experiments/v2/validate-name/"""

    URL = "/model-hub/experiments/v2/validate-name/"

    def test_valid_unique_name(self, auth_client, dataset):
        """Test that a unique name returns is_valid=True."""
        response = auth_client.get(
            self.URL, {"dataset_id": str(dataset.id), "name": "New Experiment"}
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["is_valid"] is True

    def test_duplicate_name(self, auth_client, dataset):
        """Test that an existing name returns is_valid=False."""
        ExperimentsTable.objects.create(
            name="Taken Name",
            dataset=dataset,
            status=StatusType.COMPLETED.value,
        )

        response = auth_client.get(
            self.URL, {"dataset_id": str(dataset.id), "name": "Taken Name"}
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["is_valid"] is False
        assert "already exists" in result["message"]

    def test_same_name_different_dataset(
        self, auth_client, dataset, organization, workspace
    ):
        """Test that the same name in a different dataset is valid."""
        ExperimentsTable.objects.create(
            name="Shared Name",
            dataset=dataset,
            status=StatusType.COMPLETED.value,
        )
        other_ds = Dataset.objects.create(
            name="Other DS",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )

        response = auth_client.get(
            self.URL, {"dataset_id": str(other_ds.id), "name": "Shared Name"}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["is_valid"] is True

    def test_deleted_experiment_not_counted(self, auth_client, dataset):
        """Test that a soft-deleted experiment's name is available."""
        exp = ExperimentsTable.objects.create(
            name="Deleted Exp",
            dataset=dataset,
            status=StatusType.COMPLETED.value,
        )
        exp.deleted = True
        exp.save()

        response = auth_client.get(
            self.URL, {"dataset_id": str(dataset.id), "name": "Deleted Exp"}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["is_valid"] is True

    def test_missing_dataset_id(self, auth_client):
        """Test that missing dataset_id returns 400."""
        response = auth_client.get(self.URL, {"name": "Some Name"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_name(self, auth_client, dataset):
        """Test that missing name returns 400."""
        response = auth_client.get(self.URL, {"dataset_id": str(dataset.id)})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_empty_name(self, auth_client, dataset):
        """Test that empty/whitespace name returns 400."""
        response = auth_client.get(
            self.URL, {"dataset_id": str(dataset.id), "name": "   "}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_dataset(self, auth_client):
        """Test with a non-existent dataset returns 404."""
        response = auth_client.get(
            self.URL, {"dataset_id": str(uuid.uuid4()), "name": "Test"}
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_other_org_dataset(self, auth_client):
        """Test that users cannot validate against another org's dataset."""
        other_org = Organization.objects.create(name="Other Org")
        other_ds = Dataset.objects.create(
            name="Other DS",
            organization=other_org,
            source=DatasetSourceChoices.BUILD.value,
        )

        response = auth_client.get(
            self.URL, {"dataset_id": str(other_ds.id), "name": "Test"}
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, dataset):
        """Test that unauthenticated users get rejected."""
        client = APIClient()
        response = client.get(self.URL, {"dataset_id": str(dataset.id), "name": "Test"})

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]
