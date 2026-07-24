"""
Test cases for Experiment API endpoints.

Tests cover:
- ExperimentsTableView - List and create experiments
- ExperimentRerunView - Re-run experiments
- ExperimentDeleteView - Delete experiments
- DatasetExperimentsView - Get experiment data for a dataset
- ExperimentStatsView - Get experiment statistics
- AddExperimentEvalView - Add evaluation to experiment
- RunAdditionalEvaluationsView - Run additional evaluations on experiment

Run with: pytest model_hub/tests/test_experiment_api.py -v
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
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
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    UserEvalMetric,
)
from model_hub.models.experiments import (
    ExperimentComparison,
    ExperimentDatasetTable,
    ExperimentsTable,
)
from model_hub.models.run_prompt import RunPrompter
from tfc.middleware.workspace_context import set_workspace_context


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
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    set_workspace_context(workspace=workspace, organization=user.organization)
    yield client
    client.stop_workspace_injection()


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
def input_column(db, dataset):
    col = Column.objects.create(
        name="Input Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(col.id))
    dataset.save()
    return col


@pytest.fixture
def output_column(db, dataset):
    col = Column.objects.create(
        name="Output Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )
    dataset.column_order.append(str(col.id))
    dataset.save()
    return col


@pytest.fixture
def row(db, dataset):
    return Row.objects.create(dataset=dataset, order=0)


@pytest.fixture
def input_cell(db, dataset, input_column, row):
    return Cell.objects.create(
        dataset=dataset,
        column=input_column,
        row=row,
        value="Test input value",
    )


@pytest.fixture
def run_prompter(db, dataset, organization, workspace):
    return RunPrompter.objects.create(
        name="Test Run Prompter",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.NOT_STARTED.value,
        model="gpt-4",
        messages=[{"role": "user", "content": "Test prompt"}],
        run_prompt_config={},
    )


@pytest.fixture
def eval_template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="test-eval-template",
        organization=organization,
        workspace=workspace,
        criteria="Evaluate the following: {{output}}",
        model="gpt-4",
    )


@pytest.fixture
def composite_eval_template(db, organization, workspace):
    child_output = EvalTemplate.objects.create(
        name="experiment-composite-child-output",
        organization=organization,
        workspace=workspace,
        config={"required_keys": ["output"]},
        eval_type="code",
    )
    child_expected = EvalTemplate.objects.create(
        name="experiment-composite-child-expected",
        organization=organization,
        workspace=workspace,
        config={"required_keys": ["expected"]},
        eval_type="code",
    )
    parent = EvalTemplate.objects.create(
        name="experiment-composite-parent",
        organization=organization,
        workspace=workspace,
        template_type="composite",
        config={},
    )
    CompositeEvalChild.objects.create(parent=parent, child=child_output, order=0)
    CompositeEvalChild.objects.create(parent=parent, child=child_expected, order=1)
    return parent


@pytest.fixture
def experiment(db, dataset, output_column):
    return ExperimentsTable.objects.create(
        name="Test Experiment",
        dataset=dataset,
        column=output_column,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def experiment_dataset(db, experiment):
    exp_dataset = ExperimentDatasetTable.objects.create(
        name="Experiment Dataset",
        status=StatusType.COMPLETED.value,
        experiment=experiment,
    )
    return exp_dataset


# ==================== ExperimentsTableView Tests ====================


@pytest.mark.django_db
class TestExperimentsTableView:
    """Tests for ExperimentsTableView - GET/POST /experiments/"""

    def test_get_experiment_success(self, auth_client, experiment):
        """Test successfully getting a specific experiment."""
        response = auth_client.get(
            f"/model-hub/experiments/?experiment_id={experiment.id}"
        )

        assert response.status_code == status.HTTP_200_OK

    def test_get_experiment_not_found(self, auth_client):
        """Test getting experiment with invalid ID returns 404."""
        response = auth_client.get(
            f"/model-hub/experiments/?experiment_id={uuid.uuid4()}"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_experiment_success(self, auth_client, dataset, output_column):
        """Test successfully creating an experiment."""
        payload = {
            "name": "New Experiment",
            "dataset_id": str(dataset.id),
            "column_id": str(output_column.id),
            "prompt_config": {"model": "gpt-4", "temperature": 0.7},
        }

        with patch("tfc.temporal.experiments.start_experiment_workflow"):
            response = auth_client.post(
                "/model-hub/experiments/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.data

    def test_update_experiment_success(
        self, auth_client, experiment, dataset, output_column
    ):
        """Test successfully updating a legacy experiment."""
        payload = {
            "experiment_id": str(experiment.id),
            "name": "Updated Legacy Experiment",
            "dataset_id": str(dataset.id),
            "column_id": str(output_column.id),
            "prompt_config": {"model": "gpt-4"},
            "re_run": False,
        }

        response = auth_client.put(
            "/model-hub/experiments/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        experiment.refresh_from_db()
        assert experiment.name == "Updated Legacy Experiment"
        assert experiment.dataset_id == dataset.id
        assert experiment.column_id == output_column.id

    def test_create_experiment_missing_name(self, auth_client, dataset, output_column):
        """Test that missing name returns error."""
        payload = {
            "dataset_id": str(dataset.id),
            "column_id": str(output_column.id),
            "prompt_config": {"model": "gpt-4"},
        }

        response = auth_client.post(
            "/model-hub/experiments/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_experiment_missing_dataset_id(self, auth_client, output_column):
        """Test that missing dataset_id returns error."""
        payload = {
            "name": "New Experiment",
            "column_id": str(output_column.id),
            "prompt_config": {"model": "gpt-4"},
        }

        response = auth_client.post(
            "/model-hub/experiments/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_experiment_missing_column_id(self, auth_client, dataset):
        """Test that missing column_id returns error."""
        payload = {
            "name": "New Experiment",
            "dataset_id": str(dataset.id),
            "prompt_config": {"model": "gpt-4"},
        }

        response = auth_client.post(
            "/model-hub/experiments/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_experiment_invalid_dataset(self, auth_client, output_column):
        """Test that invalid dataset_id returns error."""
        payload = {
            "name": "New Experiment",
            "dataset_id": str(uuid.uuid4()),
            "column_id": str(output_column.id),
            "prompt_config": {"model": "gpt-4"},
        }

        response = auth_client.post(
            "/model-hub/experiments/",
            payload,
            format="json",
        )

        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_list_experiments_unauthenticated(self):
        """Test that unauthenticated users cannot list experiments."""
        client = APIClient()
        response = client.get("/model-hub/experiments/")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_create_experiment_unauthenticated(self):
        """Test that unauthenticated users cannot create experiments."""
        client = APIClient()
        response = client.post("/model-hub/experiments/", {}, format="json")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== ExperimentRerunView Tests ====================


@pytest.mark.django_db
class TestExperimentRerunView:
    """Tests for ExperimentRerunView - POST /experiments/re-run/"""

    def test_rerun_experiment_success(
        self, auth_client, experiment, experiment_dataset
    ):
        """Test successfully re-running an experiment."""
        payload = {
            "experiment_ids": [str(experiment.id)],
        }

        with patch("tfc.temporal.experiments.start_experiment_workflow"):
            response = auth_client.post(
                "/model-hub/experiments/re-run/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_rerun_experiment_missing_id(self, auth_client):
        """Test that missing experiment_ids returns error."""
        payload = {}

        response = auth_client.post(
            "/model-hub/experiments/re-run/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rerun_experiment_invalid_id(self, auth_client):
        """Test that invalid experiment_ids returns error."""
        payload = {
            "experiment_ids": [str(uuid.uuid4())],
        }

        with patch("tfc.temporal.experiments.start_experiment_workflow"):
            response = auth_client.post(
                "/model-hub/experiments/re-run/",
                payload,
                format="json",
            )

        # API may start workflow even for non-existent ID (async processing)
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_rerun_experiment_unauthenticated(self):
        """Test that unauthenticated users cannot re-run experiments."""
        client = APIClient()
        response = client.post("/model-hub/experiments/re-run/", {}, format="json")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== ExperimentDeleteView Tests ====================


@pytest.mark.django_db
class TestExperimentDeleteView:
    """Tests for ExperimentDeleteView - DELETE /experiments/delete/"""

    def test_delete_experiment_success(
        self, auth_client, experiment, experiment_dataset
    ):
        """Test successfully deleting an experiment."""
        payload = {
            "experiment_ids": [str(experiment.id)],
        }

        response = auth_client.delete(
            "/model-hub/experiments/delete/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_delete_experiment_multiple(self, auth_client, dataset, output_column):
        """Test deleting multiple experiments."""
        exp1 = ExperimentsTable.objects.create(
            name="Exp 1",
            dataset=dataset,
            column=output_column,
            status=StatusType.COMPLETED.value,
        )
        ExperimentDatasetTable.objects.create(
            name="Exp Dataset 1",
            status=StatusType.COMPLETED.value,
            experiment=exp1,
        )

        exp2 = ExperimentsTable.objects.create(
            name="Exp 2",
            dataset=dataset,
            column=output_column,
            status=StatusType.COMPLETED.value,
        )
        ExperimentDatasetTable.objects.create(
            name="Exp Dataset 2",
            status=StatusType.COMPLETED.value,
            experiment=exp2,
        )

        payload = {
            "experiment_ids": [str(exp1.id), str(exp2.id)],
        }

        response = auth_client.delete(
            "/model-hub/experiments/delete/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_delete_experiment_missing_ids(self, auth_client):
        """Test that missing experiment_ids returns error."""
        payload = {}

        response = auth_client.delete(
            "/model-hub/experiments/delete/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_experiment_empty_ids(self, auth_client):
        """Test that empty experiment_ids returns error."""
        payload = {
            "experiment_ids": [],
        }

        response = auth_client.delete(
            "/model-hub/experiments/delete/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_experiment_unauthenticated(self):
        """Test that unauthenticated users cannot delete experiments."""
        client = APIClient()
        response = client.delete("/model-hub/experiments/delete/", {}, format="json")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.django_db
class TestExperimentActionWorkspaceIsolation:
    def _create_experiment_in_workspace(
        self,
        organization,
        user,
        workspace,
        *,
        status_value=StatusType.COMPLETED.value,
        v2=False,
    ):
        dataset = Dataset.objects.create(
            name=f"Dataset {workspace.name}",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        source_column = Column.objects.create(
            name="Source Column",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        experiment = ExperimentsTable.objects.create(
            name=f"Experiment {workspace.name}",
            dataset=dataset,
            column=source_column,
            status=status_value,
        )
        if not v2:
            return experiment, dataset, source_column, None

        snapshot_dataset = Dataset.objects.create(
            name=f"Snapshot {workspace.name}",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
        )
        snapshot_column = Column.objects.create(
            name="Snapshot Column",
            dataset=snapshot_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT.value,
            source_id=str(source_column.id),
            status=StatusType.COMPLETED.value,
        )
        experiment.snapshot_dataset = snapshot_dataset
        experiment.column = snapshot_column
        experiment.save(update_fields=["snapshot_dataset", "column"])
        return experiment, dataset, snapshot_column, snapshot_dataset

    def _create_legacy_row_diff_fixture(self, organization, user, workspace):
        experiment, dataset, _, _ = self._create_experiment_in_workspace(
            organization, user, workspace
        )
        base_column = Column.objects.create(
            name="Prompt A",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT.value,
            status=StatusType.COMPLETED.value,
        )
        other_column = Column.objects.create(
            name="Prompt B",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT.value,
            status=StatusType.COMPLETED.value,
        )
        row = Row.objects.create(dataset=dataset, order=0)
        base_cell = Cell.objects.create(
            dataset=dataset,
            row=row,
            column=base_column,
            value="base answer alpha",
            status=CellStatus.PASS.value,
            value_infos={"metadata": {"prompt": "a"}},
        )
        other_cell = Cell.objects.create(
            dataset=dataset,
            row=row,
            column=other_column,
            value="other answer beta",
            status=CellStatus.PASS.value,
            value_infos={"metadata": {"prompt": "b"}},
        )
        experiment_dataset = ExperimentDatasetTable.objects.create(
            name="Prompt A",
            status=StatusType.COMPLETED.value,
            experiment=experiment,
        )
        experiment_dataset.columns.add(base_column, other_column)
        experiment.experiments_datasets.add(experiment_dataset)
        return {
            "experiment": experiment,
            "dataset": dataset,
            "base_column": base_column,
            "other_column": other_column,
            "row": row,
            "base_cell": base_cell,
            "other_cell": other_cell,
        }

    @pytest.fixture
    def other_workspace(self, organization, user):
        return Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            created_by=user,
        )

    def test_legacy_delete_rejects_other_workspace_experiment(
        self, auth_client, organization, user, other_workspace
    ):
        other_experiment, _, _, _ = self._create_experiment_in_workspace(
            organization, user, other_workspace
        )

        response = auth_client.delete(
            "/model-hub/experiments/delete/",
            {"experiment_ids": [str(other_experiment.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        other_experiment.refresh_from_db()
        assert other_experiment.deleted is False

    def test_legacy_get_rejects_other_workspace_experiment(
        self, auth_client, organization, user, other_workspace
    ):
        other_experiment, _, _, _ = self._create_experiment_in_workspace(
            organization, user, other_workspace
        )

        response = auth_client.get(
            f"/model-hub/experiments/?experiment_id={other_experiment.id}"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch("tfc.temporal.experiments.start_experiment_workflow")
    def test_legacy_create_rejects_column_from_another_dataset(
        self, mock_workflow, auth_client, organization, user, workspace, dataset
    ):
        other_dataset = Dataset.objects.create(
            name="Other Same Workspace Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_column = Column.objects.create(
            name="Other Dataset Column",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )

        response = auth_client.post(
            "/model-hub/experiments/",
            {
                "name": "Blocked Legacy Create",
                "dataset_id": str(dataset.id),
                "column_id": str(other_column.id),
                "prompt_config": {"model": "gpt-4"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_workflow.assert_not_called()
        assert not ExperimentsTable.objects.filter(
            name="Blocked Legacy Create"
        ).exists()

    @patch("tfc.temporal.experiments.start_experiment_workflow")
    def test_legacy_create_rejects_metric_from_another_dataset(
        self,
        mock_workflow,
        auth_client,
        organization,
        user,
        workspace,
        dataset,
        eval_template,
    ):
        output_column = Column.objects.create(
            name="Output Column",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
        )
        other_dataset = Dataset.objects.create(
            name="Other Metric Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_metric = UserEvalMetric.objects.create(
            name="Other Dataset Metric",
            organization=organization,
            workspace=workspace,
            dataset=other_dataset,
            template=eval_template,
            config={},
            status=StatusType.NOT_STARTED.value,
        )

        response = auth_client.post(
            "/model-hub/experiments/",
            {
                "name": "Blocked Metric Legacy Create",
                "dataset_id": str(dataset.id),
                "column_id": str(output_column.id),
                "prompt_config": {"model": "gpt-4"},
                "user_eval_template_ids": [str(other_metric.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_workflow.assert_not_called()
        assert not ExperimentsTable.objects.filter(
            name="Blocked Metric Legacy Create"
        ).exists()

    @patch("tfc.temporal.experiments.start_experiment_workflow")
    def test_legacy_create_rejects_other_workspace_dataset_before_mutation(
        self, mock_workflow, auth_client, organization, user, other_workspace
    ):
        other_dataset = Dataset.objects.create(
            name="Other Workspace Create Dataset",
            organization=organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_column = Column.objects.create(
            name="Other Workspace Column",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )

        response = auth_client.post(
            "/model-hub/experiments/",
            {
                "name": "Blocked Other Workspace Legacy Create",
                "dataset_id": str(other_dataset.id),
                "column_id": str(other_column.id),
                "prompt_config": {"model": "gpt-4"},
            },
            format="json",
        )

        assert response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        )
        mock_workflow.assert_not_called()
        assert not ExperimentsTable.objects.filter(
            name="Blocked Other Workspace Legacy Create"
        ).exists()

    def test_legacy_update_rejects_other_workspace_experiment(
        self, auth_client, organization, user, other_workspace, dataset, output_column
    ):
        other_experiment, _, _, _ = self._create_experiment_in_workspace(
            organization, user, other_workspace
        )

        response = auth_client.put(
            "/model-hub/experiments/",
            {
                "experiment_id": str(other_experiment.id),
                "name": "Should Not Update",
                "dataset_id": str(dataset.id),
                "column_id": str(output_column.id),
                "prompt_config": {"model": "gpt-4"},
                "re_run": False,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        other_experiment.refresh_from_db()
        assert other_experiment.name != "Should Not Update"

    def test_legacy_update_rejects_column_from_another_dataset(
        self, auth_client, organization, workspace, experiment, dataset
    ):
        other_dataset = Dataset.objects.create(
            name="Other Same Workspace Update Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_column = Column.objects.create(
            name="Other Update Column",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )

        response = auth_client.put(
            "/model-hub/experiments/",
            {
                "experiment_id": str(experiment.id),
                "name": "Blocked Update Name",
                "dataset_id": str(dataset.id),
                "column_id": str(other_column.id),
                "prompt_config": {"model": "gpt-4"},
                "re_run": False,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        experiment.refresh_from_db()
        assert experiment.name != "Blocked Update Name"
        assert experiment.column_id != other_column.id

    @patch("tfc.temporal.experiments.start_experiment_workflow")
    def test_legacy_rerun_rejects_other_workspace_experiment(
        self, mock_workflow, auth_client, organization, user, other_workspace
    ):
        other_experiment, _, _, _ = self._create_experiment_in_workspace(
            organization, user, other_workspace
        )

        response = auth_client.post(
            "/model-hub/experiments/re-run/",
            {"experiment_ids": [str(other_experiment.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_workflow.assert_not_called()
        other_experiment.refresh_from_db()
        assert other_experiment.status == StatusType.COMPLETED.value

    @patch("tfc.temporal.experiments.cancel_experiment_workflow")
    def test_v2_delete_rejects_other_workspace_experiment(
        self, mock_cancel, auth_client, organization, user, other_workspace
    ):
        other_experiment, _, _, _ = self._create_experiment_in_workspace(
            organization, user, other_workspace, v2=True
        )

        response = auth_client.delete(
            "/model-hub/experiments/v2/delete/",
            {"experiment_ids": [str(other_experiment.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_cancel.assert_not_called()
        other_experiment.refresh_from_db()
        assert other_experiment.deleted is False

    @patch("tfc.temporal.experiments.start_experiment_v2_workflow")
    def test_v2_rerun_rejects_other_workspace_experiment(
        self, mock_workflow, auth_client, organization, user, other_workspace
    ):
        other_experiment, _, _, _ = self._create_experiment_in_workspace(
            organization, user, other_workspace, v2=True
        )

        response = auth_client.post(
            "/model-hub/experiments/v2/re-run/",
            {"experiment_ids": [str(other_experiment.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_workflow.assert_not_called()
        other_experiment.refresh_from_db()
        assert other_experiment.status == StatusType.COMPLETED.value

    def test_v2_row_diff_rejects_rows_and_columns_outside_snapshot(
        self, auth_client, organization, user, workspace
    ):
        experiment, _, _, snapshot_dataset = self._create_experiment_in_workspace(
            organization, user, workspace, v2=True
        )
        outside_dataset = Dataset.objects.create(
            name="Outside Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        outside_row = Row.objects.create(dataset=outside_dataset, order=0)
        outside_column = Column.objects.create(
            name="Outside Column",
            dataset=outside_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        Cell.objects.create(
            dataset=outside_dataset,
            row=outside_row,
            column=outside_column,
            value="outside value should not leak",
            status=CellStatus.PASS.value,
        )

        response = auth_client.post(
            "/model-hub/experiments/v2/row-diff/",
            {
                "experiment_id": str(experiment.id),
                "column_ids": [str(outside_column.id)],
                "row_ids": [str(outside_row.id)],
                "compare_column_ids": [str(outside_column.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "outside value should not leak" not in str(response.data)
        assert snapshot_dataset.id == experiment.snapshot_dataset_id

    def test_legacy_row_diff_returns_dataset_scoped_cells(
        self, auth_client, organization, user, workspace
    ):
        fixture = self._create_legacy_row_diff_fixture(organization, user, workspace)

        response = auth_client.post(
            "/model-hub/develops/get-row-diff/",
            {
                "experiment_id": str(fixture["experiment"].id),
                "column_ids": [
                    str(fixture["base_column"].id),
                    str(fixture["other_column"].id),
                ],
                "row_ids": [str(fixture["row"].id)],
                "compare_column_ids": [
                    str(fixture["base_column"].id),
                    str(fixture["other_column"].id),
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        row_payload = response.data["result"][str(fixture["row"].id)]
        assert (
            row_payload[str(fixture["base_column"].id)]["cell_value"]
            == fixture["base_cell"].value
        )
        assert row_payload[str(fixture["base_column"].id)]["cell_diff_value"] is None
        diff = row_payload[str(fixture["other_column"].id)]["cell_diff_value"]
        assert {part["status"] for part in diff} >= {"removed", "added"}
        assert row_payload[str(fixture["other_column"].id)]["value_infos"] == {
            "metadata": {"prompt": "b"}
        }

    def test_legacy_row_diff_rejects_other_workspace_experiment(
        self, auth_client, organization, user, other_workspace
    ):
        fixture = self._create_legacy_row_diff_fixture(
            organization, user, other_workspace
        )

        response = auth_client.post(
            "/model-hub/develops/get-row-diff/",
            {
                "experiment_id": str(fixture["experiment"].id),
                "column_ids": [
                    str(fixture["base_column"].id),
                    str(fixture["other_column"].id),
                ],
                "row_ids": [str(fixture["row"].id)],
                "compare_column_ids": [
                    str(fixture["base_column"].id),
                    str(fixture["other_column"].id),
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_legacy_row_diff_rejects_rows_and_columns_outside_experiment_dataset(
        self, auth_client, organization, user, workspace
    ):
        fixture = self._create_legacy_row_diff_fixture(organization, user, workspace)
        outside_dataset = Dataset.objects.create(
            name="Outside Legacy Diff Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        outside_row = Row.objects.create(dataset=outside_dataset, order=0)
        outside_column = Column.objects.create(
            name="Outside Column",
            dataset=outside_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT.value,
        )
        Cell.objects.create(
            dataset=outside_dataset,
            row=outside_row,
            column=outside_column,
            value="outside legacy diff value should not leak",
            status=CellStatus.PASS.value,
        )

        response = auth_client.post(
            "/model-hub/develops/get-row-diff/",
            {
                "experiment_id": str(fixture["experiment"].id),
                "column_ids": [
                    str(fixture["base_column"].id),
                    str(outside_column.id),
                ],
                "row_ids": [str(outside_row.id)],
                "compare_column_ids": [
                    str(fixture["base_column"].id),
                    str(outside_column.id),
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "outside legacy diff value should not leak" not in str(response.data)

    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_v2_rerun_cells_rejects_column_outside_snapshot(
        self, mock_workflow, auth_client, organization, user, workspace
    ):
        experiment, _, _, _ = self._create_experiment_in_workspace(
            organization, user, workspace, v2=True
        )
        outside_dataset = Dataset.objects.create(
            name="Outside Rerun Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        outside_row = Row.objects.create(dataset=outside_dataset, order=0)
        outside_column = Column.objects.create(
            name="Outside Rerun Column",
            dataset=outside_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT.value,
            source_id=str(uuid.uuid4()),
            status=StatusType.COMPLETED.value,
        )
        outside_cell = Cell.objects.create(
            dataset=outside_dataset,
            row=outside_row,
            column=outside_column,
            value="outside rerun cell",
            status=CellStatus.PASS.value,
        )

        response = auth_client.post(
            f"/model-hub/experiments/v2/{experiment.id}/rerun-cells/",
            {
                "cells": [
                    {
                        "column_id": str(outside_column.id),
                        "row_id": str(outside_row.id),
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_workflow.assert_not_called()
        outside_column.refresh_from_db()
        outside_cell.refresh_from_db()
        assert outside_column.status == StatusType.COMPLETED.value
        assert outside_cell.status == CellStatus.PASS.value

    @patch("tfc.temporal.experiments.start_experiment_v2_workflow")
    @patch("model_hub.views.experiments.ExperimentRunner")
    def test_run_evaluations_rejects_other_workspace_metric(
        self,
        mock_runner,
        mock_workflow,
        auth_client,
        experiment,
        organization,
        user,
        other_workspace,
        eval_template,
    ):
        other_dataset = Dataset.objects.create(
            name="Other Metric Dataset",
            organization=organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_metric = UserEvalMetric.objects.create(
            name="Other Workspace Metric",
            organization=organization,
            workspace=other_workspace,
            dataset=other_dataset,
            template=eval_template,
            config={},
            status=StatusType.NOT_STARTED.value,
        )

        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/run-evaluations/",
            {"eval_template_ids": [str(other_metric.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_runner.assert_not_called()
        mock_workflow.assert_not_called()
        other_metric.refresh_from_db()
        assert other_metric.source_id in ("", None)
        assert not experiment.user_eval_template_ids.filter(id=other_metric.id).exists()


# ==================== DatasetExperimentsView Tests ====================


@pytest.mark.django_db
class TestDatasetExperimentsView:
    """Tests for DatasetExperimentsView - GET /experiments/<experiment_id>/"""

    def test_get_experiment_data_success(self, auth_client, experiment):
        """Test successfully getting experiment data."""
        response = auth_client.get(f"/model-hub/experiments/{experiment.id}/")

        assert response.status_code == status.HTTP_200_OK

    def test_get_experiment_data_invalid_id(self, auth_client):
        """Test that invalid experiment_id returns error."""
        fake_experiment_id = uuid.uuid4()
        response = auth_client.get(f"/model-hub/experiments/{fake_experiment_id}/")

        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_get_experiment_data_unauthenticated(self, experiment):
        """Test that unauthenticated users cannot get experiment data."""
        client = APIClient()
        response = client.get(f"/model-hub/experiments/{experiment.id}/")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_column_config_only_false_returns_table(
        self, auth_client, experiment, dataset
    ):
        """Test that column_config_only=false returns full payload with table data,
        not just column config. Regression: the old code treated the string 'false'
        as truthy."""
        rows = [Row.objects.create(dataset=dataset, order=i) for i in range(2)]
        exp_col = experiment.column
        col_a = Column.objects.create(
            name="A",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        for r in rows:
            Cell.objects.create(
                dataset=dataset, row=r, column=col_a, value=f"a-{r.order}"
            )
            Cell.objects.create(
                dataset=dataset, row=r, column=exp_col, value=f"exp-{r.order}"
            )

        resp = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/?column_config_only=false"
        )
        assert resp.status_code == status.HTTP_200_OK

        payload = resp.json()
        assert payload["status"] is True
        result = payload["result"]
        assert "table" in result
        assert "metadata" in result

    def test_paginates_rows_not_cells(self, auth_client, experiment, dataset):
        """Test that pagination is row-based, returning exactly page_size rows
        even when a column is missing a cell for some rows. Regression: the old
        code sliced cells per-column, causing inconsistent row counts."""
        rows = [Row.objects.create(dataset=dataset, order=i) for i in range(11)]
        col_a = Column.objects.create(
            name="A",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        col_b = experiment.column

        # Populate all cells for col_b; omit row order=2 for col_a
        for r in rows:
            if r.order != 2:
                Cell.objects.create(
                    dataset=dataset, row=r, column=col_a, value=f"a-{r.order}"
                )
            Cell.objects.create(
                dataset=dataset, row=r, column=col_b, value=f"b-{r.order}"
            )

        resp = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/?page_size=10&current_page_index=0"
        )
        assert resp.status_code == status.HTTP_200_OK

        result = resp.json()["result"]
        table = result["table"]

        # Correct behavior: exactly 10 rows (orders 0..9) in stable order
        assert len(table) == 10
        returned_row_ids = [row_obj["row_id"] for row_obj in table]
        expected_row_ids = [str(r.id) for r in rows[:10]]
        assert returned_row_ids == expected_row_ids

    def test_value_infos_dict_is_supported(self, auth_client, experiment, dataset):
        """Test that cells with value_infos stored as a dict (Django JSONField)
        are processed correctly. Regression: the old code did json.loads(dict)
        which raises TypeError, silently dropping the cell."""
        r = Row.objects.create(dataset=dataset, order=0)
        col_a = Column.objects.create(
            name="A",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        Cell.objects.create(
            dataset=dataset,
            row=r,
            column=col_a,
            value="v",
            value_infos={
                "metadata": {"response_time": 123, "usage": {"total_tokens": 7}}
            },
            status="pass",
        )
        Cell.objects.create(
            dataset=dataset, row=r, column=experiment.column, value="base"
        )

        resp = auth_client.get(f"/model-hub/experiments/{experiment.id}/")
        assert resp.status_code == status.HTTP_200_OK

        result = resp.json()["result"]
        table = result["table"]
        assert len(table) == 1
        row_obj = table[0]
        cell_obj = row_obj[str(col_a.id)]
        assert cell_obj["metadata"]["response_time_ms"] == 123
        assert cell_obj["metadata"]["token_count"] == 7


# ==================== ExperimentStatsView Tests ====================


@pytest.mark.django_db
class TestExperimentStatsView:
    """Tests for ExperimentStatsView - GET /experiments/<experiment_id>/stats/"""

    def test_get_experiment_stats_success(self, auth_client, experiment):
        """Test successfully getting experiment statistics."""
        response = auth_client.get(f"/model-hub/experiments/{experiment.id}/stats/")

        assert response.status_code == status.HTTP_200_OK

    def test_get_experiment_stats_invalid_id(self, auth_client):
        """Test that invalid experiment_id returns error."""
        fake_experiment_id = uuid.uuid4()
        response = auth_client.get(
            f"/model-hub/experiments/{fake_experiment_id}/stats/"
        )

        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_get_experiment_stats_unauthenticated(self, experiment):
        """Test that unauthenticated users cannot access experiment stats."""
        client = APIClient()
        response = client.get(f"/model-hub/experiments/{experiment.id}/stats/")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== AddExperimentEvalView Tests ====================


@pytest.mark.django_db
class TestAddExperimentEvalView:
    """Tests for AddExperimentEvalView - POST /experiments/<experiment_id>/add-eval/"""

    def test_add_experiment_eval_success(
        self, auth_client, experiment, experiment_dataset, eval_template
    ):
        """Test successfully adding evaluation to experiment."""
        payload = {
            "name": "experiment-eval",
            "template_id": str(eval_template.id),
            "config": {
                "mapping": {"output": "output_column"},
            },
        }

        with patch("model_hub.views.experiments.ExperimentRunner") as mock_runner:
            # Mock the experiment runner to avoid actually running evaluations
            mock_instance = MagicMock()
            mock_runner.return_value = mock_instance
            response = auth_client.post(
                f"/model-hub/experiments/{experiment.id}/add-eval/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        metric = UserEvalMetric.objects.get(
            name="experiment-eval",
            template=eval_template,
            source_id=str(experiment.id),
        )
        assert metric.config["reason_column"] is True

    def test_add_experiment_eval_missing_name(
        self, auth_client, experiment, experiment_dataset, eval_template
    ):
        """Test that missing name returns error."""
        payload = {
            "template_id": str(eval_template.id),
            "config": {},
        }

        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/add-eval/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_experiment_eval_unauthenticated(self, experiment):
        """Test that unauthenticated users cannot add experiment evaluations."""
        client = APIClient()
        response = client.post(
            f"/model-hub/experiments/{experiment.id}/add-eval/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_add_experiment_eval_rejects_composite_missing_child_required_mapping(
        self, auth_client, experiment, composite_eval_template
    ):
        """Experiment eval binding must validate child-required composite keys."""
        payload = {
            "name": "experiment-composite-missing",
            "template_id": str(composite_eval_template.id),
            "config": {
                "mapping": {"output": "output_column"},
            },
        }

        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/add-eval/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "expected" in str(response.data)
        assert not UserEvalMetric.objects.filter(
            name="experiment-composite-missing",
            template=composite_eval_template,
            source_id=str(experiment.id),
            deleted=False,
        ).exists()

    def test_add_experiment_eval_rejects_other_org_experiment(
        self, auth_client, eval_template
    ):
        """A known experiment UUID from another org cannot receive eval metrics."""
        other_org = Organization.objects.create(name="Other Organization")
        other_user = User.objects.create_user(
            email="other@example.com",
            password="testpassword123",
            name="Other User",
            organization=other_org,
        )
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=other_org,
            is_default=True,
            created_by=other_user,
        )
        other_dataset = Dataset.objects.create(
            name="Other Dataset",
            organization=other_org,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_output = Column.objects.create(
            name="Other Output",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
        )
        other_experiment = ExperimentsTable.objects.create(
            name="Other Experiment",
            dataset=other_dataset,
            column=other_output,
            status=StatusType.COMPLETED.value,
        )

        response = auth_client.post(
            f"/model-hub/experiments/{other_experiment.id}/add-eval/",
            {
                "name": "other-org-experiment-eval",
                "template_id": str(eval_template.id),
                "config": {"mapping": {"output": "output_column"}},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not UserEvalMetric.objects.filter(
            name="other-org-experiment-eval",
            source_id=str(other_experiment.id),
        ).exists()


@pytest.mark.django_db
class TestExperimentInlineEvalMetrics:
    def test_create_eval_metrics_inline_defaults_reason_column(
        self,
        experiment,
        dataset,
        organization,
        user,
        workspace,
        eval_template,
    ):
        from model_hub.views.experiments import _create_eval_metrics_inline

        metrics = _create_eval_metrics_inline(
            eval_entries=[
                {
                    "name": "inline-default-reason",
                    "template_id": eval_template.id,
                    "config": {"mapping": {"output": "output_column"}},
                    "model": "turing_small",
                }
            ],
            experiment=experiment,
            snapshot_dataset=dataset,
            organization=organization,
            user=user,
            workspace=workspace,
        )

        assert metrics[0].config["reason_column"] is True

    def test_create_eval_metrics_inline_rejects_composite_missing_child_required_mapping(
        self,
        experiment,
        dataset,
        organization,
        user,
        workspace,
        composite_eval_template,
    ):
        from model_hub.views.experiments import _create_eval_metrics_inline

        with pytest.raises(ValueError, match="expected"):
            _create_eval_metrics_inline(
                eval_entries=[
                    {
                        "name": "inline-composite-missing",
                        "template_id": composite_eval_template.id,
                        "config": {"mapping": {"output": "output_column"}},
                        "model": "turing_small",
                    }
                ],
                experiment=experiment,
                snapshot_dataset=dataset,
                organization=organization,
                user=user,
                workspace=workspace,
            )

        assert not UserEvalMetric.objects.filter(
            name="inline-composite-missing",
            template=composite_eval_template,
            source_id=str(experiment.id),
            deleted=False,
        ).exists()

    def test_diff_and_update_evals_rejects_composite_missing_child_required_mapping(
        self,
        experiment,
        dataset,
        organization,
        user,
        workspace,
        composite_eval_template,
    ):
        from model_hub.views.experiments import _diff_and_update_evals

        metric = UserEvalMetric.objects.create(
            name="existing-inline-composite",
            organization=organization,
            workspace=workspace,
            dataset=dataset,
            template=composite_eval_template,
            config={
                "mapping": {
                    "output": "output_column",
                    "expected": "expected_column",
                }
            },
            status=StatusType.EXPERIMENT_EVALUATION.value,
            source_id=str(experiment.id),
            user=user,
        )
        experiment.user_eval_template_ids.add(metric)

        with pytest.raises(ValueError, match="expected"):
            _diff_and_update_evals(
                experiment=experiment,
                new_eval_entries=[
                    {
                        "id": metric.id,
                        "name": "existing-inline-composite",
                        "template_id": composite_eval_template.id,
                        "config": {"mapping": {"output": "output_column"}},
                        "model": "turing_small",
                    }
                ],
                organization=organization,
                user=user,
                workspace=workspace,
            )

        metric.refresh_from_db()
        assert metric.config["mapping"] == {
            "output": "output_column",
            "expected": "expected_column",
        }

    # ==================== TH-6979: pinned_version_id parity ====================

    @pytest.fixture
    def user_eval_template(self, db, organization, workspace):
        from model_hub.models.choices import OwnerChoices

        return EvalTemplate.objects.create(
            name="th-6979-user-template",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            criteria="Check {{output}}",
            model="turing_large",
        )

    def test_create_eval_metrics_inline_pins_v1_for_user_template(
        self, experiment, dataset, organization, user, workspace, user_eval_template
    ):
        """Baseline: creation without an explicit pin still creates + pins V1
        (parity with the dataset EditAndRunUserEvalView side)."""
        from model_hub.models.evals_metric import EvalTemplateVersion
        from model_hub.views.experiments import _create_eval_metrics_inline

        metrics = _create_eval_metrics_inline(
            eval_entries=[
                {
                    "name": "pinned-parity-v1",
                    "template_id": user_eval_template.id,
                    "config": {"mapping": {"output": "output_column"}},
                    "model": "turing_small",
                }
            ],
            experiment=experiment,
            snapshot_dataset=dataset,
            organization=organization,
            user=user,
            workspace=workspace,
        )
        m = metrics[0]
        assert m.pinned_version_id is not None
        v1 = EvalTemplateVersion.objects.get(id=m.pinned_version_id)
        assert v1.version_number == 1
        assert v1.is_default is True

    def test_create_eval_metrics_inline_honors_explicit_pinned_version(
        self, experiment, dataset, organization, user, workspace, user_eval_template
    ):
        """TH-6979 core: passing `pinned_version_id` on the entry re-baselines
        the pin so `maybe_pin_new_version` dedups against the user's pick
        instead of silently creating a new version."""
        from model_hub.models.evals_metric import EvalTemplateVersion
        from model_hub.views.experiments import _create_eval_metrics_inline

        # Two pre-existing versions on the template. v1's snapshot mirrors
        # what `maybe_pin_new_version` would build for the entry below so
        # dedup can hit exactly (`template.config | inner | {"model": ...}`).
        v1_snap = {"foo": "one", "model": user_eval_template.model}
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=user_eval_template,
            config_snapshot=v1_snap,
            model=user_eval_template.model,
            user=user,
            organization=organization,
            workspace=workspace,
        )
        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=user_eval_template,
            config_snapshot={"foo": "two", "model": user_eval_template.model},
            model=user_eval_template.model,
            user=user,
            organization=organization,
            workspace=workspace,
        )

        # Entry replays v1's snapshot exactly (so dedup fires) and asks
        # the FE version pin to snap back to v1.
        metrics = _create_eval_metrics_inline(
            eval_entries=[
                {
                    "name": "pinned-parity-explicit",
                    "template_id": user_eval_template.id,
                    "config": {
                        "mapping": {"output": "output_column"},
                        "config": {"foo": "one"},
                    },
                    "model": user_eval_template.model,
                    "pinned_version_id": str(v1.id),
                }
            ],
            experiment=experiment,
            snapshot_dataset=dataset,
            organization=organization,
            user=user,
            workspace=workspace,
        )
        m = metrics[0]
        # Dedup fired: pinned_version_id == v1, no v3 created.
        assert str(m.pinned_version_id) == str(v1.id)
        assert (
            EvalTemplateVersion.objects.filter(eval_template=user_eval_template).count()
            == 2
        )
        # v2 stays around and still exists; v1 is the pin.
        assert EvalTemplateVersion.objects.filter(id=v2.id).exists()

    def test_apply_pinned_version_baseline_rejects_foreign_template(
        self, experiment, dataset, organization, user, workspace, user_eval_template
    ):
        """Guard: pin id belonging to a different template raises so the
        wrapping view surfaces it as a 400 rather than silently ignoring."""
        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import EvalTemplateVersion
        from model_hub.views.experiments import (
            _apply_entry_pinned_version_baseline,
        )

        other_tpl = EvalTemplate.objects.create(
            name="th-6979-other-template",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
        )
        other_v1 = EvalTemplateVersion.objects.create_version(
            eval_template=other_tpl,
            user=user,
            organization=organization,
            workspace=workspace,
        )
        # Create a metric bound to user_eval_template but reference a version
        # from other_tpl in the entry — this should error out.
        metric = UserEvalMetric.objects.create(
            name="th-6979-mismatch",
            organization=organization,
            workspace=workspace,
            dataset=dataset,
            template=user_eval_template,
            config={"mapping": {"output": "output_column"}},
            status=StatusType.EXPERIMENT_EVALUATION.value,
            source_id=str(experiment.id),
            user=user,
        )
        with pytest.raises(ValueError, match="not found for template"):
            _apply_entry_pinned_version_baseline(
                {"pinned_version_id": str(other_v1.id)}, metric
            )

    def test_has_eval_changed_detects_version_only_switch(
        self, experiment, dataset, organization, user, workspace, user_eval_template
    ):
        """Switching versions without editing config still triggers re-run —
        otherwise `_diff_and_update_evals` would skip the update branch and
        the pin would never be re-applied."""
        from model_hub.models.evals_metric import EvalTemplateVersion
        from model_hub.views.experiments import _has_eval_changed

        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=user_eval_template,
            user=user, organization=organization, workspace=workspace,
        )
        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=user_eval_template,
            user=user, organization=organization, workspace=workspace,
        )
        metric = UserEvalMetric.objects.create(
            name="th-6979-switch",
            organization=organization,
            workspace=workspace,
            dataset=dataset,
            template=user_eval_template,
            config={"mapping": {"output": "output_column"}, "config": {}},
            status=StatusType.EXPERIMENT_EVALUATION.value,
            source_id=str(experiment.id),
            user=user,
            pinned_version=v1,
        )
        entry = {
            "id": str(metric.id),
            "name": metric.name,
            "template_id": str(user_eval_template.id),
            "model": metric.model or "",
            "kb_id": None,
            "error_localizer": False,
            "config": {"mapping": {"output": "output_column"}, "config": {}},
            "pinned_version_id": str(v2.id),
        }
        assert (
            _has_eval_changed(metric, entry, {"output": "output_column"}) is True
        )


@pytest.mark.django_db
class TestExperimentComparisonWeights:
    def test_rank_and_persist_comparisons_defaults_empty_weights(
        self, experiment, experiment_dataset
    ):
        from model_hub.views.experiments import rank_and_persist_comparisons

        metrics = [
            {
                "dataset_id": str(experiment_dataset.id),
                "avg_completion_tokens": 4,
                "avg_total_tokens": 10,
                "avg_response_time": 2,
                "avg_score": 8,
            }
        ]

        rank_and_persist_comparisons(str(experiment.id), metrics, {})

        assert metrics[0]["rank"] == 1
        assert metrics[0]["normalized_scores"]["completion_tokens"] == 5.0
        comparison = ExperimentComparison.objects.get(
            experiment_id=experiment.id,
            experiment_dataset_id=experiment_dataset.id,
            deleted=False,
        )
        assert comparison.completion_tokens_weight == 1
        assert comparison.total_tokens_weight == 1
        assert comparison.response_time_weight == 1


# ==================== RunAdditionalEvaluationsView Tests ====================


@pytest.mark.django_db
class TestRunAdditionalEvaluationsView:
    """Tests for RunAdditionalEvaluationsView - POST /experiments/<experiment_id>/run-evaluations/"""

    def test_run_additional_evaluations_success(
        self,
        auth_client,
        experiment,
        experiment_dataset,
        dataset,
        organization,
        workspace,
    ):
        """Test successfully running additional evaluations."""
        from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric

        # Create eval template and metric for the dataset
        eval_template = EvalTemplate.objects.create(
            name="test-eval-template",
            organization=organization,
            workspace=workspace,
            criteria="Test criteria",
            model="gpt-4",
        )
        eval_metric = UserEvalMetric.objects.create(
            name="Test Eval",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )

        payload = {
            "eval_template_ids": [str(eval_metric.id)],
        }

        with patch("tfc.temporal.experiments.start_experiment_workflow"):
            response = auth_client.post(
                f"/model-hub/experiments/{experiment.id}/run-evaluations/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_run_additional_evaluations_missing_eval_ids(
        self, auth_client, experiment, experiment_dataset
    ):
        """Test that missing eval_template_ids still processes (with empty list).

        Note: The API uses 'eval_template_ids' (not 'eval_ids') and doesn't
        validate that it's empty - it just processes an empty list.
        """
        payload = {}

        with patch("model_hub.views.experiments.ExperimentRunner") as mock_runner:
            mock_instance = MagicMock()
            mock_runner.return_value = mock_instance
            response = auth_client.post(
                f"/model-hub/experiments/{experiment.id}/run-evaluations/",
                payload,
                format="json",
            )

        # The API doesn't validate empty eval_template_ids, it processes successfully
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_run_additional_evaluations_empty_eval_ids(
        self, auth_client, experiment, experiment_dataset
    ):
        """Test that empty eval_template_ids still processes successfully.

        Note: The API uses 'eval_template_ids' (not 'eval_ids') and doesn't
        validate that it's empty - it just processes an empty list.
        """
        payload = {
            "eval_template_ids": [],
        }

        with patch("model_hub.views.experiments.ExperimentRunner") as mock_runner:
            mock_instance = MagicMock()
            mock_runner.return_value = mock_instance
            response = auth_client.post(
                f"/model-hub/experiments/{experiment.id}/run-evaluations/",
                payload,
                format="json",
            )

        # The API doesn't validate empty eval_template_ids, it processes successfully
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_run_additional_evaluations_unauthenticated(
        self, experiment, experiment_dataset
    ):
        """Test that unauthenticated users cannot run additional evaluations."""
        client = APIClient()

        with patch("model_hub.views.experiments.ExperimentRunner") as mock_runner:
            mock_instance = MagicMock()
            mock_runner.return_value = mock_instance
            response = client.post(
                f"/model-hub/experiments/{experiment.id}/run-evaluations/",
                {},
                format="json",
            )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== Organization Isolation Tests ====================


@pytest.mark.django_db
class TestExperimentOrganizationIsolation:
    """Tests for organization isolation in experiment operations."""

    @pytest.fixture
    def other_organization(self, db):
        return Organization.objects.create(name="Other Organization")

    @pytest.fixture
    def other_org_user(self, db, other_organization):
        return User.objects.create_user(
            email="otherorg@example.com",
            password="testpassword123",
            name="Other Org User",
            organization=other_organization,
        )

    @pytest.fixture
    def other_org_experiment(self, db, other_organization, other_org_user):
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=other_organization,
            is_default=True,
            created_by=other_org_user,
        )
        other_dataset = Dataset.objects.create(
            name="Other Org Dataset",
            organization=other_organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_column = Column.objects.create(
            name="Other Output Column",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
        )
        return ExperimentsTable.objects.create(
            name="Other Org Experiment",
            dataset=other_dataset,
            column=other_column,
            status=StatusType.COMPLETED.value,
        )

    def test_cannot_access_other_org_experiment(
        self, auth_client, other_org_experiment
    ):
        """Test that users cannot access experiments from other organizations."""
        response = auth_client.get(f"/model-hub/experiments/{other_org_experiment.id}/")

        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_cannot_get_stats_for_other_org_experiment(
        self, auth_client, other_org_experiment
    ):
        """Test that users cannot access stats for other organization's experiments."""
        response = auth_client.get(
            f"/model-hub/experiments/{other_org_experiment.id}/stats/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_experiments_only_shows_own_org(
        self, auth_client, experiment, other_org_experiment
    ):
        """Test that users can access their own org's experiments but cannot
        access other org's experiments.
        """
        # Test that own org's experiment can be accessed
        response = auth_client.get(
            f"/model-hub/experiments/?experiment_id={experiment.id}"
        )
        assert response.status_code == status.HTTP_200_OK

        # Test that other org's experiment cannot be accessed (returns 404)
        response = auth_client.get(
            f"/model-hub/experiments/?experiment_id={other_org_experiment.id}"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
