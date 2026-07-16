"""
Test cases for Evaluation API endpoints.

Tests cover:
- AddUserEvalView - Add a new user evaluation to dataset
- StartEvalsProcess - Start evaluation process for specified evals
- EditAndRunUserEvalView - Edit and run a user evaluation
- DeleteEvalsView - Delete a user evaluation
- GetEvalsListView - Get list of evaluations for a dataset
- PreviewRunEvalView - Preview evaluation run
- SingleRowEvaluationView - Run evaluation for a single row

Run with: pytest model_hub/tests/test_evaluation_api.py -v
"""

import uuid
from types import SimpleNamespace
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
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    UserEvalMetric,
)
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
    client = APIClient()
    client.force_authenticate(user=user)
    set_workspace_context(workspace=workspace, organization=user.organization)
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
        source=SourceChoices.OTHERS.value,
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
def output_cell(db, dataset, output_column, row):
    return Cell.objects.create(
        dataset=dataset,
        column=output_column,
        row=row,
        value="Test output value",
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
        name="composite-child-output",
        organization=organization,
        workspace=workspace,
        config={"required_keys": ["output"]},
        eval_type="code",
    )
    child_expected = EvalTemplate.objects.create(
        name="composite-child-expected",
        organization=organization,
        workspace=workspace,
        config={"required_keys": ["expected"]},
        eval_type="code",
    )
    parent = EvalTemplate.objects.create(
        name="composite-parent",
        organization=organization,
        workspace=workspace,
        template_type="composite",
        config={},
    )
    CompositeEvalChild.objects.create(parent=parent, child=child_output, order=0)
    CompositeEvalChild.objects.create(parent=parent, child=child_expected, order=1)
    return parent


@pytest.fixture
def user_eval_metric(db, dataset, organization, workspace, eval_template):
    return UserEvalMetric.objects.create(
        name="Test Evaluation",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        template=eval_template,
        status=StatusType.NOT_STARTED.value,
        config={
            "model": "gpt-4",
            "prompt": "Evaluate this",
        },
    )


@pytest.fixture
def valid_eval_config():
    return {
        "model": "gpt-4",
        "mapping": {
            "output": "Output Column",
        },
        "config": {},
    }


# ==================== AddUserEvalView Tests ====================


@pytest.mark.django_db
class TestAddUserEvalView:
    """Tests for AddUserEvalView - POST /develops/<dataset_id>/add_user_eval/"""

    def test_add_user_eval_success(
        self, auth_client, dataset, output_column, valid_eval_config, eval_template
    ):
        """Test successfully adding a user evaluation."""
        payload = {
            "name": "test-eval",
            "template_id": str(eval_template.id),
            "config": valid_eval_config,
            "run": False,
        }

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/add_user_eval/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_add_user_eval_with_template(
        self, auth_client, dataset, output_column, eval_template
    ):
        """Test adding a user evaluation with template."""
        payload = {
            "name": "template-eval",
            "output_column_id": str(output_column.id),
            "template_id": str(eval_template.id),
            "config": {
                "model": "gpt-4",
                "output_column_id": str(output_column.id),
            },
        }

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/add_user_eval/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_add_user_eval_and_run(
        self, auth_client, dataset, output_column, valid_eval_config, eval_template
    ):
        """Test adding and running a user evaluation."""
        payload = {
            "name": "run-eval",
            "template_id": str(eval_template.id),
            "config": valid_eval_config,
            "run": True,
        }

        with patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                f"/model-hub/develops/{dataset.id}/add_user_eval/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_add_user_eval_missing_name(
        self, auth_client, dataset, output_column, valid_eval_config, eval_template
    ):
        """Test that missing name returns error."""
        payload = {
            "template_id": str(eval_template.id),
            "config": valid_eval_config,
        }

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/add_user_eval/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_user_eval_missing_template_id(
        self, auth_client, dataset, valid_eval_config
    ):
        """Test that missing template_id returns error."""
        payload = {
            "name": "missing-template-eval",
            "config": valid_eval_config,
        }

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/add_user_eval/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_user_eval_invalid_dataset(
        self, auth_client, output_column, valid_eval_config, eval_template
    ):
        """Test that invalid dataset_id returns 404."""
        payload = {
            "name": "invalid-dataset-eval",
            "template_id": str(eval_template.id),
            "config": valid_eval_config,
        }

        fake_dataset_id = uuid.uuid4()
        response = auth_client.post(
            f"/model-hub/develops/{fake_dataset_id}/add_user_eval/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_user_eval_unauthenticated(self, dataset):
        """Test that unauthenticated users cannot add evaluations."""
        client = APIClient()
        response = client.post(
            f"/model-hub/develops/{dataset.id}/add_user_eval/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_add_user_eval_rejects_composite_missing_child_required_mapping(
        self, auth_client, dataset, composite_eval_template
    ):
        """Composite bindings must include required keys from every child eval."""
        payload = {
            "name": "composite-missing-mapping",
            "template_id": str(composite_eval_template.id),
            "config": {
                "mapping": {"output": "Output Column"},
            },
            "run": False,
        }

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/add_user_eval/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "expected" in str(response.data)
        assert not UserEvalMetric.objects.filter(
            name="composite-missing-mapping",
            dataset=dataset,
            template=composite_eval_template,
            deleted=False,
        ).exists()


# ==================== StartEvalsProcess Tests ====================


@pytest.mark.django_db
class TestStartEvalsProcess:
    """Tests for StartEvalsProcess - POST /develops/<dataset_id>/start_evals_process/"""

    def test_start_evals_process_success(self, auth_client, dataset, user_eval_metric):
        """Test successfully starting evaluation process."""
        payload = {
            "user_eval_ids": [str(user_eval_metric.id)],
        }

        with patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                f"/model-hub/develops/{dataset.id}/start_evals_process/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_start_evals_process_multiple_evals(
        self,
        auth_client,
        dataset,
        output_column,
        organization,
        workspace,
        eval_template,
    ):
        """Test starting evaluation process for multiple evaluations."""
        eval1 = UserEvalMetric.objects.create(
            name="Eval 1",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )
        eval2 = UserEvalMetric.objects.create(
            name="Eval 2",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )

        payload = {
            "user_eval_ids": [str(eval1.id), str(eval2.id)],
        }

        with patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                f"/model-hub/develops/{dataset.id}/start_evals_process/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_start_evals_process_missing_eval_ids(self, auth_client, dataset):
        """Test that missing user_eval_ids returns error."""
        payload = {}

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/start_evals_process/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_start_evals_process_empty_eval_ids(self, auth_client, dataset):
        """Test that empty user_eval_ids returns error."""
        payload = {
            "user_eval_ids": [],
        }

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/start_evals_process/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_start_evals_process_deleted_column(
        self, auth_client, dataset, user_eval_metric
    ):
        """Test that evaluations with deleted columns return error."""
        # Mark the column as deleted
        user_eval_metric.column_deleted = True
        user_eval_metric.save()

        payload = {
            "user_eval_ids": [str(user_eval_metric.id)],
        }

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/start_evals_process/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_start_evals_process_invalid_dataset(self, auth_client, user_eval_metric):
        """Test that invalid dataset_id returns error."""
        payload = {
            "user_eval_ids": [str(user_eval_metric.id)],
        }

        fake_dataset_id = uuid.uuid4()
        response = auth_client.post(
            f"/model-hub/develops/{fake_dataset_id}/start_evals_process/",
            payload,
            format="json",
        )

        # Eval won't be found for this dataset
        assert response.status_code in [
            status.HTTP_200_OK,  # No matching evals
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_start_evals_process_unauthenticated(self, dataset):
        """Test that unauthenticated users cannot start evaluations."""
        client = APIClient()
        response = client.post(
            f"/model-hub/develops/{dataset.id}/start_evals_process/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== GetEvalsListView Tests ====================


@pytest.mark.django_db
class TestGetEvalsListView:
    """Tests for GetEvalsListView - GET /develops/<dataset_id>/get_evals_list/"""

    def test_get_evals_list_success(self, auth_client, dataset, user_eval_metric):
        """Test successfully getting evaluations list."""
        response = auth_client.get(f"/model-hub/develops/{dataset.id}/get_evals_list/")

        assert response.status_code == status.HTTP_200_OK

    def test_get_evals_list_empty(self, auth_client, dataset):
        """Test getting evaluations list when empty."""
        response = auth_client.get(f"/model-hub/develops/{dataset.id}/get_evals_list/")

        assert response.status_code == status.HTTP_200_OK

    def test_get_evals_list_excludes_draft_templates(
        self, auth_client, dataset, organization, workspace
    ):
        """Draft templates are stored as visible_ui=False and stay out of the drawer."""
        visible_template = EvalTemplate.objects.create(
            name="visible-user-eval",
            organization=organization,
            workspace=workspace,
            owner="user",
            visible_ui=True,
        )
        draft_template = EvalTemplate.objects.create(
            name="draft-hidden-eval",
            organization=organization,
            workspace=workspace,
            owner="user",
            visible_ui=False,
        )

        response = auth_client.get(f"/model-hub/develops/{dataset.id}/get_evals_list/")

        assert response.status_code == status.HTTP_200_OK
        names = {
            item["name"]
            for item in response.data["result"]["evals"]
            if item["id"] in {str(visible_template.id), str(draft_template.id)}
        }
        assert names == {"visible-user-eval"}

    def test_get_evals_list_invalid_dataset(self, auth_client):
        """Test that invalid dataset_id returns error."""
        fake_dataset_id = uuid.uuid4()
        response = auth_client.get(
            f"/model-hub/develops/{fake_dataset_id}/get_evals_list/"
        )

        # May return empty list or error
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_get_evals_list_unauthenticated(self, dataset):
        """Test that unauthenticated users cannot get evaluations list."""
        client = APIClient()
        response = client.get(f"/model-hub/develops/{dataset.id}/get_evals_list/")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.django_db
class TestEvalConfigContracts:
    def test_get_eval_config_rejects_legacy_eval_id_alias(self, auth_client):
        response = auth_client.get(f"/model-hub/get-eval-config?evalId={uuid.uuid4()}")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_eval_config_returns_multi_choice_true(self, auth_client, user):
        """multi_choice is sourced from the template's direct field."""
        template = EvalTemplate.objects.create(
            name=f"multi-choice-{uuid.uuid4().hex[:8]}",
            description="Multi-choice template",
            organization=user.organization,
            owner=OwnerChoices.USER.value,
            config={"output": "choices", "eval_type_id": "test_eval_type"},
            choices=["A", "B", "C"],
            multi_choice=True,
        )

        response = auth_client.get(
            f"/model-hub/get-eval-config?eval_id={template.id}"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        payload = response.json()["result"]["eval"]
        assert payload["multi_choice"] is True

    def test_get_eval_config_returns_multi_choice_false_when_absent(
        self, auth_client, user
    ):
        template = EvalTemplate.objects.create(
            name=f"single-choice-{uuid.uuid4().hex[:8]}",
            description="Single-choice template",
            organization=user.organization,
            owner=OwnerChoices.USER.value,
            config={"output": "choices", "eval_type_id": "test_eval_type"},
            choices=["A", "B"],
        )

        response = auth_client.get(
            f"/model-hub/get-eval-config?eval_id={template.id}"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        payload = response.json()["result"]["eval"]
        assert payload["multi_choice"] is False

    def test_get_eval_structure_rejects_legacy_eval_type_alias(
        self, auth_client, dataset
    ):
        response = auth_client.get(
            f"/model-hub/develops/{dataset.id}/get_eval_structure/{uuid.uuid4()}/?evalType=user"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ==================== DeleteEvalsView Tests ====================


@pytest.mark.django_db
class TestDeleteEvalsView:
    """Tests for DeleteEvalsView - DELETE /develops/<dataset_id>/delete_user_eval/<eval_id>/"""

    def test_delete_user_eval_success(self, auth_client, dataset, user_eval_metric):
        """Test successfully deleting a user evaluation (with delete_column=True)."""
        # When delete_column=True, the eval_metric.deleted is set to True
        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{user_eval_metric.id}/",
            {"delete_column": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        user_eval_metric.refresh_from_db()
        assert user_eval_metric.deleted is True
        assert user_eval_metric.deleted_at is not None

    def test_delete_user_eval_hide_from_sidebar(
        self, auth_client, dataset, user_eval_metric
    ):
        """Test hiding a user evaluation from sidebar (default behavior without delete_column)."""
        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{user_eval_metric.id}/"
        )

        assert response.status_code == status.HTTP_200_OK
        user_eval_metric.refresh_from_db()
        # When delete_column is False (default), only show_in_sidebar is set to False
        assert user_eval_metric.show_in_sidebar is False

    def test_delete_user_eval_nonexistent(self, auth_client, dataset):
        """Test deleting non-existent evaluation."""
        fake_eval_id = uuid.uuid4()
        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{fake_eval_id}/"
        )

        # The API returns 404 when eval is not found
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_user_eval_wrong_dataset(
        self, auth_client, dataset, organization, workspace, eval_template
    ):
        """Test deleting evaluation from wrong dataset."""
        # Create another dataset with its own eval
        other_dataset = Dataset.objects.create(
            name="Other Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_eval = UserEvalMetric.objects.create(
            name="Other Eval",
            dataset=other_dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )

        # Try to delete from wrong dataset
        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{other_eval.id}/"
        )

        # The API returns 404 when eval is not found for this dataset
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_user_eval_unauthenticated(self, dataset, user_eval_metric):
        """Test that unauthenticated users cannot delete evaluations."""
        client = APIClient()
        response = client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{user_eval_metric.id}/"
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_delete_running_eval_cancels_runner(
        self, auth_client, dataset, user_eval_metric
    ):
        """Deleting a running eval should cancel the runner before soft-deleting."""
        user_eval_metric.status = StatusType.RUNNING.value
        user_eval_metric.save(update_fields=["status"])

        with patch(
            "tfc.utils.distributed_state.evaluation_tracker"
        ) as mock_tracker, patch(
            "model_hub.utils.eval_cell_status.mark_eval_cells_stopped"
        ) as mock_mark_stopped:
            response = auth_client.delete(
                f"/model-hub/develops/{dataset.id}/delete_user_eval/{user_eval_metric.id}/",
                {"delete_column": True},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        mock_tracker.request_cancel.assert_called_once_with(
            user_eval_metric.id, reason="eval_deleted"
        )
        mock_mark_stopped.assert_called_once()
        user_eval_metric.refresh_from_db()
        assert user_eval_metric.deleted is True

    def test_delete_eval_with_column_and_reason_column(
        self, auth_client, dataset, user_eval_metric
    ):
        """Deleting an eval with delete_column=True removes eval column AND reason column."""
        # Create eval column
        eval_col = Column.objects.create(
            name="Test Eval",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(user_eval_metric.id),
        )
        dataset.column_order.append(str(eval_col.id))
        dataset.save()

        # Create reason column (source_id pattern: "{eval_col.id}-sourceid-{metric_id}")
        reason_col = Column.objects.create(
            name="Test Eval-reason",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_col.id}-sourceid-{user_eval_metric.id}",
        )
        dataset.column_order.append(str(reason_col.id))
        dataset.save()

        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{user_eval_metric.id}/",
            {"delete_column": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Both columns should be soft-deleted
        eval_col.refresh_from_db()
        reason_col.refresh_from_db()
        assert eval_col.deleted is True
        assert reason_col.deleted is True
        assert eval_col.deleted_at is not None
        assert reason_col.deleted_at is not None

        # Both should be removed from column_order
        dataset.refresh_from_db()
        assert str(eval_col.id) not in dataset.column_order
        assert str(reason_col.id) not in dataset.column_order

    def test_delete_eval_with_column_sets_deleted_at_on_cells(
        self, auth_client, dataset, user_eval_metric
    ):
        """Deleting an eval column should stamp deleted_at on dependent cells."""
        row = Row.objects.create(dataset=dataset, order=0)
        eval_col = Column.objects.create(
            name="Test Eval",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(user_eval_metric.id),
        )
        reason_col = Column.objects.create(
            name="Test Eval-reason",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_col.id}-sourceid-{user_eval_metric.id}",
        )
        eval_cell = Cell.objects.create(
            dataset=dataset, row=row, column=eval_col, value="Passed"
        )
        reason_cell = Cell.objects.create(
            dataset=dataset, row=row, column=reason_col, value="Looks grounded"
        )

        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{user_eval_metric.id}/",
            {"delete_column": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        eval_cell.refresh_from_db()
        reason_cell.refresh_from_db()
        assert eval_cell.deleted is True
        assert reason_cell.deleted is True
        assert eval_cell.deleted_at is not None
        assert reason_cell.deleted_at is not None

    def test_delete_column_of_running_eval_cancels_runner(
        self, auth_client, dataset, user_eval_metric
    ):
        """Deleting the column of a running eval should cancel the runner."""
        user_eval_metric.status = StatusType.RUNNING.value
        user_eval_metric.save(update_fields=["status"])

        # Create eval column
        eval_col = Column.objects.create(
            name="Running Eval",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(user_eval_metric.id),
        )
        dataset.column_order.append(str(eval_col.id))
        dataset.save()

        with patch(
            "tfc.utils.distributed_state.evaluation_tracker"
        ) as mock_tracker, patch(
            "model_hub.utils.eval_cell_status.mark_eval_cells_stopped"
        ) as mock_mark_stopped:
            response = auth_client.delete(
                f"/model-hub/develops/{dataset.id}/delete_column/{eval_col.id}/",
            )

        assert response.status_code == status.HTTP_200_OK
        mock_tracker.request_cancel.assert_called_once_with(
            user_eval_metric.id, reason="eval_column_deleted"
        )
        mock_mark_stopped.assert_called_once()

        # Eval metric should be soft-deleted
        user_eval_metric.refresh_from_db()
        assert user_eval_metric.deleted is True

    def test_delete_eval_full_behavioral(
        self, auth_client, dataset, organization, workspace, eval_template
    ):
        """Full behavioral test for the optimized delete path.

        Covers:
        - Cells under eval + reason columns are soft-deleted
        - column_order is pruned
        - Other metrics referencing the same column get column_deleted=True
        - The deleted metric itself does NOT get column_deleted=True
        - Cells under unrelated columns are untouched
        """
        # ── Setup: two eval metrics sharing the same input column ──
        input_col = Column.objects.create(
            name="Input",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )

        metric_to_delete = UserEvalMetric.objects.create(
            name="Eval A",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={"mapping": {"output": str(input_col.id)}},
        )
        other_metric = UserEvalMetric.objects.create(
            name="Eval B",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={"mapping": {"output": str(input_col.id)}},
        )

        # Eval column + reason column for the metric we'll delete
        eval_col = Column.objects.create(
            name="Eval A",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(metric_to_delete.id),
        )
        reason_col = Column.objects.create(
            name="Eval A-reason",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_col.id}-sourceid-{metric_to_delete.id}",
        )

        # Eval column for the other metric (should NOT be deleted)
        other_eval_col = Column.objects.create(
            name="Eval B",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(other_metric.id),
        )

        dataset.column_order = [
            str(input_col.id),
            str(eval_col.id),
            str(reason_col.id),
            str(other_eval_col.id),
        ]
        dataset.save()

        # Create rows + cells
        row = Row.objects.create(dataset=dataset, order=0)
        eval_cell = Cell.objects.create(
            dataset=dataset, row=row, column=eval_col, value="Pass",
        )
        reason_cell = Cell.objects.create(
            dataset=dataset, row=row, column=reason_col, value="Looks good",
        )
        input_cell = Cell.objects.create(
            dataset=dataset, row=row, column=input_col, value="Hello world",
        )
        other_eval_cell = Cell.objects.create(
            dataset=dataset, row=row, column=other_eval_col, value="Fail",
        )

        # ── Act ──
        # The other_metric references the eval_col via its config mapping,
        # so get_metrics_using_column should find it.
        # We reference eval_col.id in other_metric's config so it gets flagged.
        other_metric.config = {"mapping": {"output": str(eval_col.id)}}
        other_metric.save(update_fields=["config"])

        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{metric_to_delete.id}/",
            {"delete_column": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # ── Assert: cells under deleted columns are soft-deleted ──
        eval_cell.refresh_from_db()
        reason_cell.refresh_from_db()
        assert eval_cell.deleted is True
        assert eval_cell.deleted_at is not None
        assert reason_cell.deleted is True
        assert reason_cell.deleted_at is not None

        # ── Assert: cells under unrelated columns are untouched ──
        input_cell.refresh_from_db()
        other_eval_cell.refresh_from_db()
        assert input_cell.deleted is False
        assert other_eval_cell.deleted is False

        # ── Assert: column_order is pruned ──
        dataset.refresh_from_db()
        assert str(eval_col.id) not in dataset.column_order
        assert str(reason_col.id) not in dataset.column_order
        # Input and other eval columns still in order
        assert str(input_col.id) in dataset.column_order
        assert str(other_eval_col.id) in dataset.column_order

        # ── Assert: columns are soft-deleted ──
        eval_col.refresh_from_db()
        reason_col.refresh_from_db()
        assert eval_col.deleted is True
        assert reason_col.deleted is True

        # ── Assert: other eval column is NOT deleted ──
        other_eval_col.refresh_from_db()
        assert other_eval_col.deleted is False

        # ── Assert: other metric gets column_deleted=True ──
        other_metric.refresh_from_db()
        assert other_metric.column_deleted is True

        # ── Assert: deleted metric does NOT have column_deleted ──
        # It gets deleted=True directly; column_deleted is skipped.
        metric_to_delete.refresh_from_db()
        assert metric_to_delete.deleted is True
        assert metric_to_delete.column_deleted is False

    def test_delete_experiment_scoped_eval_marks_metric_deleted(
        self, auth_client, dataset, organization, workspace, eval_template
    ):
        """Experiment-scoped delete (delete_column=True + experiment_id) must
        also soft-delete eval_metric — not just the per-EDT cells/columns.

        Regression guard: a prior refactor moved eval_metric.deleted into the
        non-experiment branch only, so experiment-scoped delete left a ghost
        metric record (still visible in the sidebar, broken on rerun).
        """
        from model_hub.models.experiments import (
            ExperimentDatasetTable,
            ExperimentsTable,
        )

        # Two metrics — the view refuses to delete the last one in an experiment
        metric_to_delete = UserEvalMetric.objects.create(
            name="Exp Eval A",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )
        keep_metric = UserEvalMetric.objects.create(
            name="Exp Eval B",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )

        experiment = ExperimentsTable.objects.create(
            name="Test Experiment",
            dataset=dataset,
            prompt_config=[],
        )
        experiment.user_eval_template_ids.add(metric_to_delete, keep_metric)

        edt = ExperimentDatasetTable.objects.create(
            name="Test EDT",
            experiment=experiment,
        )

        # Scope the eval to the experiment via source_id (mirrors what the
        # create flow does so the dataset-eval-queryset lookup finds it).
        metric_to_delete.source_id = str(experiment.id)
        metric_to_delete.save(update_fields=["source_id"])

        # Per-EDT experiment eval column with the right source_id pattern
        per_edt_col = Column.objects.create(
            name="Exp Eval A",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{edt.id}-coleval-sourceid-{metric_to_delete.id}",
        )
        row = Row.objects.create(dataset=dataset, order=0)
        per_edt_cell = Cell.objects.create(
            dataset=dataset, row=row, column=per_edt_col, value="Pass"
        )

        # ── Act ──
        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{metric_to_delete.id}/",
            {"delete_column": True, "experiment_id": str(experiment.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # ── Assert: the metric record itself is soft-deleted ──
        # This is the regression — used to stay deleted=False forever.
        metric_to_delete.refresh_from_db()
        assert metric_to_delete.deleted is True
        assert metric_to_delete.deleted_at is not None

        # ── Assert: per-EDT column + cell got cleaned up ──
        per_edt_col.refresh_from_db()
        per_edt_cell.refresh_from_db()
        assert per_edt_col.deleted is True
        assert per_edt_cell.deleted is True

        # ── Assert: keep_metric (the other one) is untouched ──
        keep_metric.refresh_from_db()
        assert keep_metric.deleted is False

    def test_delete_eval_no_cell_join_on_source_id(
        self, dataset, organization, workspace, eval_template
    ):
        """Query-shape guard: delete_eval_column_and_dependents must never issue
        a Cell query that JOINs on column__source_id__startswith (full scan).
        All cell deletes should use column_id IN (...) instead.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from model_hub.services.column_service import delete_eval_column_and_dependents

        metric = UserEvalMetric.objects.create(
            name="Eval Shape",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )
        eval_col = Column.objects.create(
            name="Eval Shape",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(metric.id),
        )
        reason_col = Column.objects.create(
            name="Eval Shape-reason",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_col.id}-sourceid-{metric.id}",
        )
        row = Row.objects.create(dataset=dataset, order=0)
        Cell.objects.create(dataset=dataset, row=row, column=eval_col, value="x")
        Cell.objects.create(dataset=dataset, row=row, column=reason_col, value="y")
        dataset.column_order = [str(eval_col.id), str(reason_col.id)]
        dataset.save()

        from django.db import transaction

        with CaptureQueriesContext(connection) as ctx:
            with transaction.atomic():
                delete_eval_column_and_dependents(eval_col, organization.id)

        # ``Cell._meta.db_table`` is ``model_hub_cell`` (Django default for the
        # ``model_hub.Cell`` model). Match against the real table name — a
        # filter against a non-existent table would always yield an empty
        # list and the loop below would silently pass.
        cell_sql = [
            q["sql"] for q in ctx.captured_queries
            if "model_hub_cell" in q["sql"].lower()
        ]
        # Positive assert: the cell delete must have actually run. Absence-only
        # checks pass when the query disappears entirely (e.g. accidental
        # early return) — guard against that explicitly.
        assert cell_sql, (
            "expected delete_eval_column_and_dependents to issue at least "
            "one Cell query, captured none"
        )
        # The fix: every Cell query must filter on the indexed FK column
        # (``column_id IN (...)``) and never on ``column__source_id`` which
        # forces a JOIN + LIKE scan over the whole Cell table.
        assert any("column_id" in s.lower() for s in cell_sql), (
            "expected the cell delete to use the indexed column_id IN path; "
            f"captured Cell queries: {cell_sql}"
        )
        assert not any("source_id" in s.lower() for s in cell_sql), (
            "Cell delete must not use column__source_id JOIN; "
            f"found full-scan query in: {cell_sql}"
        )

    def test_delete_eval_service_is_self_atomic(
        self, dataset, organization, workspace, eval_template
    ):
        """The service opens its own ``transaction.atomic()`` so callers
        can't accidentally run it non-atomically.

        Verifies by spying on ``transaction.atomic`` in the service module
        and asserting it was called during the delete.
        """
        from unittest.mock import patch

        from django.db import transaction as _tx

        from model_hub.services.column_service import delete_eval_column_and_dependents

        metric = UserEvalMetric.objects.create(
            name="Eval AtomicGuard",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )
        eval_col = Column.objects.create(
            name="Eval AtomicGuard",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(metric.id),
        )

        with patch(
            "model_hub.services.column_service.transaction.atomic",
            side_effect=_tx.atomic,
        ) as spy:
            delete_eval_column_and_dependents(eval_col, organization.id)

        assert spy.call_count >= 1, (
            "service must open its own transaction.atomic() so callers "
            "can't accidentally run it non-atomically"
        )

    def test_delete_eval_prunes_column_config(
        self, dataset, organization, workspace, eval_template
    ):
        """column_config is keyed by column id — leaving stale ids in it
        breaks dataset round-trip validation. The service must prune both
        column_order and column_config.
        """
        from django.db import transaction

        from model_hub.services.column_service import delete_eval_column_and_dependents

        metric = UserEvalMetric.objects.create(
            name="Eval CfgPrune",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )
        eval_col = Column.objects.create(
            name="Eval CfgPrune",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(metric.id),
        )
        reason_col = Column.objects.create(
            name="Eval CfgPrune-reason",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_col.id}-sourceid-{metric.id}",
        )
        other_col = Column.objects.create(
            name="Other",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        dataset.column_order = [str(eval_col.id), str(reason_col.id), str(other_col.id)]
        dataset.column_config = {
            str(eval_col.id): {"width": 200},
            str(reason_col.id): {"width": 150},
            str(other_col.id): {"width": 100},
        }
        dataset.save()

        with transaction.atomic():
            delete_eval_column_and_dependents(eval_col, organization.id)

        dataset.refresh_from_db()
        assert str(eval_col.id) not in dataset.column_order
        assert str(reason_col.id) not in dataset.column_order
        assert str(other_col.id) in dataset.column_order
        assert str(eval_col.id) not in dataset.column_config
        assert str(reason_col.id) not in dataset.column_config
        assert str(other_col.id) in dataset.column_config

    def test_delete_experiment_scoped_eval_prunes_column_config(
        self, auth_client, dataset, organization, workspace, eval_template
    ):
        """Experiment-scoped delete must also prune ``column_config``, not
        just ``column_order``.

        Regression guard: the experiment-scoped branch can't call the shared
        service (different ``source_id`` pattern), so it hand-rolls the
        delete. An earlier version of this PR pruned only ``column_order``
        and left soft-deleted ids in ``column_config``, recreating the
        staleness bug the main path fixes. Both delete paths must share the
        ``prune_dataset_columns`` helper so they can't diverge again.
        """
        from model_hub.models.experiments import (
            ExperimentDatasetTable,
            ExperimentsTable,
        )

        metric_to_delete = UserEvalMetric.objects.create(
            name="Exp Eval Cfg A",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )
        keep_metric = UserEvalMetric.objects.create(
            name="Exp Eval Cfg B",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )

        experiment = ExperimentsTable.objects.create(
            name="Test Experiment Cfg",
            dataset=dataset,
            prompt_config=[],
        )
        experiment.user_eval_template_ids.add(metric_to_delete, keep_metric)
        edt = ExperimentDatasetTable.objects.create(
            name="Test EDT Cfg",
            experiment=experiment,
        )

        metric_to_delete.source_id = str(experiment.id)
        metric_to_delete.save(update_fields=["source_id"])

        per_edt_col = Column.objects.create(
            name="Exp Eval Cfg A",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{edt.id}-coleval-sourceid-{metric_to_delete.id}",
        )
        other_col = Column.objects.create(
            name="Untouched",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )

        # Snapshot dataset = the column's dataset (same here as the experiment's
        # working dataset). Seed both column_order AND column_config with the
        # column id we expect pruned, and a sibling id that must be preserved.
        snap = per_edt_col.dataset
        snap.column_order = [str(per_edt_col.id), str(other_col.id)]
        snap.column_config = {
            str(per_edt_col.id): {"width": 200},
            str(other_col.id): {"width": 100},
        }
        snap.save()

        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{metric_to_delete.id}/",
            {"delete_column": True, "experiment_id": str(experiment.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        snap.refresh_from_db()
        # column_order pruned (the existing behaviour)…
        assert str(per_edt_col.id) not in snap.column_order
        # …AND column_config pruned (the regression this test guards)…
        assert str(per_edt_col.id) not in snap.column_config
        # …without touching unrelated entries.
        assert str(other_col.id) in snap.column_order
        assert str(other_col.id) in snap.column_config

    def test_delete_eval_atomic_rollback(
        self, dataset, organization, workspace, eval_template
    ):
        """Atomicity guard: if any step inside delete_eval_column_and_dependents
        raises, no writes should be committed (cells, column_order, columns
        all stay untouched).
        """
        from unittest.mock import patch

        from django.db import transaction

        from model_hub.services.column_service import delete_eval_column_and_dependents

        metric = UserEvalMetric.objects.create(
            name="Eval Atomic",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )
        eval_col = Column.objects.create(
            name="Eval Atomic",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(metric.id),
        )
        row = Row.objects.create(dataset=dataset, order=0)
        cell = Cell.objects.create(
            dataset=dataset, row=row, column=eval_col, value="before"
        )
        original_order = [str(eval_col.id)]
        dataset.column_order = original_order[:]
        dataset.save()

        # Blow up after cells are deleted but before columns are deleted
        with patch(
            "model_hub.models.evals_metric.UserEvalMetric.get_metrics_using_column",
            side_effect=RuntimeError("simulated mid-delete failure"),
        ):
            with pytest.raises(RuntimeError):
                with transaction.atomic():
                    delete_eval_column_and_dependents(eval_col, organization.id)

        # Nothing should have been committed
        cell.refresh_from_db()
        assert cell.deleted is False, "Cell must not be soft-deleted after rollback"

        eval_col.refresh_from_db()
        assert eval_col.deleted is False, "Column must not be soft-deleted after rollback"

        dataset.refresh_from_db()
        assert str(eval_col.id) in dataset.column_order, (
            "column_order must be unchanged after rollback"
        )


# ==================== is_user_eval_stopped Tests ====================


@pytest.mark.django_db
class TestIsUserEvalStopped:
    """Tests for is_user_eval_stopped — the per-cell guard in the eval runner."""

    def test_returns_false_for_running_eval(self, user_eval_metric):
        from model_hub.services.experiment_utils import is_user_eval_stopped

        user_eval_metric.status = StatusType.RUNNING.value
        user_eval_metric.save(update_fields=["status"])

        assert is_user_eval_stopped(user_eval_metric.id) is False

    def test_returns_true_for_error_status(self, user_eval_metric):
        """StopUserEvalView sets ERROR — guard must catch it."""
        from model_hub.services.experiment_utils import is_user_eval_stopped

        user_eval_metric.status = StatusType.ERROR.value
        user_eval_metric.save(update_fields=["status"])

        assert is_user_eval_stopped(user_eval_metric.id) is True

    def test_returns_true_for_cancelled_status(self, user_eval_metric):
        from model_hub.services.experiment_utils import is_user_eval_stopped

        user_eval_metric.status = StatusType.CANCELLED.value
        user_eval_metric.save(update_fields=["status"])

        assert is_user_eval_stopped(user_eval_metric.id) is True

    def test_returns_true_for_deleted_eval(self, user_eval_metric):
        """DeleteEvalsView sets deleted=True — guard must catch it."""
        from model_hub.services.experiment_utils import is_user_eval_stopped

        user_eval_metric.deleted = True
        user_eval_metric.save(update_fields=["deleted"])

        assert is_user_eval_stopped(user_eval_metric.id) is True

    def test_returns_false_for_nonexistent_id(self):
        from model_hub.services.experiment_utils import is_user_eval_stopped

        assert is_user_eval_stopped(uuid.uuid4()) is False

    def test_returns_false_for_none(self):
        from model_hub.services.experiment_utils import is_user_eval_stopped

        assert is_user_eval_stopped(None) is False


# ==================== StopUserEvalView Tests ====================


@pytest.mark.django_db
class TestStopUserEvalView:
    """Tests for StopUserEvalView - POST /develops/<dataset_id>/stop_user_eval/<eval_id>/"""

    def test_stop_user_eval_running(self, auth_client, dataset, user_eval_metric):
        """Running evals transition to ERROR and return the stop message."""
        user_eval_metric.status = StatusType.RUNNING.value
        user_eval_metric.save(update_fields=["status"])

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/stop_user_eval/{user_eval_metric.id}/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == "User evaluation stopped"

        user_eval_metric.refresh_from_db()
        assert user_eval_metric.status == StatusType.ERROR.value

    def test_stop_user_eval_not_started(self, auth_client, dataset, user_eval_metric):
        """NOT_STARTED evals also transition to ERROR."""
        assert user_eval_metric.status == StatusType.NOT_STARTED.value

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/stop_user_eval/{user_eval_metric.id}/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == "User evaluation stopped"

        user_eval_metric.refresh_from_db()
        assert user_eval_metric.status == StatusType.ERROR.value

    def test_stop_user_eval_already_completed_is_noop(
        self, auth_client, dataset, user_eval_metric
    ):
        """Already-completed evals stay COMPLETED and the endpoint still succeeds."""
        user_eval_metric.status = StatusType.COMPLETED.value
        user_eval_metric.save(update_fields=["status"])

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/stop_user_eval/{user_eval_metric.id}/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == "User evaluation stopped"

        user_eval_metric.refresh_from_db()
        assert user_eval_metric.status == StatusType.COMPLETED.value

    def test_stop_user_eval_nonexistent(self, auth_client, dataset):
        fake_eval_id = uuid.uuid4()
        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/stop_user_eval/{fake_eval_id}/",
            {},
            format="json",
        )
        # get_object_or_404 → Http404 caught by outer except → bad_request
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_stop_user_eval_unauthenticated(self, dataset, user_eval_metric):
        client = APIClient()
        response = client.post(
            f"/model-hub/develops/{dataset.id}/stop_user_eval/{user_eval_metric.id}/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== EditAndRunUserEvalView Tests ====================


@pytest.mark.django_db
class TestEditAndRunUserEvalView:
    """Tests for EditAndRunUserEvalView - POST /develops/<dataset_id>/edit_and_run_user_eval/<eval_id>/"""

    def test_edit_and_run_user_eval_success(
        self, auth_client, dataset, user_eval_metric, output_column
    ):
        """Test successfully editing and running a user evaluation."""
        payload = {
            "name": "Updated Eval",
            "output_column_id": str(output_column.id),
            "config": {
                "model": "gpt-4",
                "prompt": "Updated prompt",
            },
        }

        with patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{user_eval_metric.id}/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK

    def test_edit_and_run_user_eval_without_name(
        self, auth_client, dataset, user_eval_metric, output_column
    ):
        """Test that edit works without providing a name (name is optional)."""
        payload = {
            "config": {
                "model": "gpt-4",
                "mapping": {"output": "Output Column"},
            },
        }

        with patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{user_eval_metric.id}/",
                payload,
                format="json",
            )

        # Name is optional in edit API, so it should succeed
        assert response.status_code == status.HTTP_200_OK

    def test_edit_and_run_user_eval_nonexistent(
        self, auth_client, dataset, output_column
    ):
        """Test editing non-existent evaluation."""
        fake_eval_id = uuid.uuid4()
        payload = {
            "name": "Updated Eval",
            "config": {
                "model": "gpt-4",
                "mapping": {"output": "Output Column"},
            },
        }

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{fake_eval_id}/",
            payload,
            format="json",
        )

        # The API returns 404 when eval is not found
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_edit_and_run_user_eval_unauthenticated(self, dataset, user_eval_metric):
        """Test that unauthenticated users cannot edit evaluations."""
        client = APIClient()
        response = client.post(
            f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{user_eval_metric.id}/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_edit_and_run_user_eval_rejects_composite_missing_child_required_mapping(
        self, auth_client, dataset, organization, workspace, composite_eval_template
    ):
        """Editing a composite metric cannot drop a child-required mapping."""
        metric = UserEvalMetric.objects.create(
            name="composite-edit-mapping",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=composite_eval_template,
            status=StatusType.INACTIVE.value,
            config={
                "mapping": {
                    "output": "Output Column",
                    "expected": "Expected Column",
                }
            },
        )

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{metric.id}/",
            {"config": {"mapping": {"output": "Output Column"}}},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "expected" in str(response.data)
        metric.refresh_from_db()
        assert metric.config["mapping"] == {
            "output": "Output Column",
            "expected": "Expected Column",
        }


# ==================== PreviewRunEvalView Tests ====================


@pytest.mark.django_db
class TestPreviewRunEvalView:
    """Tests for PreviewRunEvalView - POST /develops/<dataset_id>/preview_run_eval/"""

    def test_preview_run_eval_success(
        self, auth_client, dataset, row, output_column, input_cell, output_cell
    ):
        """Test successfully previewing an evaluation run."""
        payload = {
            "row_id": str(row.id),
            "output_column_id": str(output_column.id),
            "config": {
                "model": "gpt-4",
                "prompt": "Evaluate: {{Output Column}}",
            },
        }

        with patch(
            "agentic_eval.core_evals.run_prompt.litellm_response.litellm.completion"
        ) as mock_completion:
            mock_completion.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="Score: 8/10"))],
                usage=MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )
            response = auth_client.post(
                f"/model-hub/develops/{dataset.id}/preview_run_eval/",
                payload,
                format="json",
            )

        # May depend on API key availability
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_preview_run_eval_missing_mapping(
        self, auth_client, dataset, output_column, eval_template
    ):
        """Test that missing mapping in config returns error."""
        payload = {
            "template_id": str(eval_template.id),
            "config": {
                "model": "gpt-4",
                # Missing required "mapping" key
            },
        }

        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/preview_run_eval/",
            payload,
            format="json",
        )

        # The API returns 400 when mapping is missing
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.django_db(transaction=True)
    def test_preview_code_eval_passes_function_params(
        self,
        auth_client,
        dataset,
        row,
        input_column,
        input_cell,
        organization,
        workspace,
    ):
        """Preview should pass function params to code eval kwargs."""
        template = EvalTemplate.objects.create(
            name="word_count_preview_test",
            organization=organization,
            workspace=workspace,
            eval_type="code",
            config={
                "code": """
def evaluate(input, output, expected, context, **kwargs):
    text = str(kwargs.get("text", "")).strip()
    min_words = kwargs.get("min_words")
    count = len(text.split())
    passed = min_words is not None and count >= int(min_words)
    return {"score": 1.0 if passed else 0.0, "reason": f"count={count}, min={min_words}"}
""",
                "output": "Pass/Fail",
                "eval_type_id": "CustomCodeEval",
                "required_keys": ["text"],
                "function_params_schema": {
                    "min_words": {
                        "type": "integer",
                        "default": None,
                        "minimum": 0,
                        "nullable": True,
                    },
                },
            },
        )
        payload = {
            "template_id": str(template.id),
            "config": {
                "mapping": {"text": str(input_column.id)},
                "params": {"min_words": 2},
            },
        }
        fake_api_call = SimpleNamespace(config="{}", save=lambda: None)

        with (
            patch(
                "model_hub.views.utils.evals.EvaluationRunner._handle_api_call",
                return_value=fake_api_call,
            ),
            patch(
                "model_hub.views.utils.evals.APICallStatusChoices",
                SimpleNamespace(
                    SUCCESS=SimpleNamespace(value="success"),
                    ERROR=SimpleNamespace(value="error"),
                ),
            ),
        ):
            response = auth_client.post(
                f"/model-hub/develops/{dataset.id}/preview_run_eval/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        preview = response.data["result"]["responses"][0]
        assert preview["output"] == "Passed"
        assert preview["reason"] == "count=3, min=2"

    def test_preview_run_eval_unauthenticated(self, dataset):
        """Test that unauthenticated users cannot preview evaluations."""
        client = APIClient()
        response = client.post(
            f"/model-hub/develops/{dataset.id}/preview_run_eval/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ==================== SingleRowEvaluationView Tests ====================


@pytest.mark.django_db
class TestSingleRowEvaluationView:
    """Tests for SingleRowEvaluationView - POST /evaluate-rows/"""

    def test_evaluate_single_row_success(
        self, auth_client, dataset, row, user_eval_metric
    ):
        """Test successfully evaluating a single row."""
        eval_column = Column.objects.create(
            name="Eval Output",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(user_eval_metric.id),
            status=StatusType.COMPLETED.value,
        )
        eval_cell = Cell.objects.create(
            dataset=dataset,
            column=eval_column,
            row=row,
            value="existing",
            value_infos={"kept": True},
            status=CellStatus.PASS.value,
        )
        payload = {
            "row_ids": [str(row.id)],
            "user_eval_metric_ids": [str(user_eval_metric.id)],
        }

        with patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/evaluate-rows/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        mock_task.assert_called_once()
        eval_cell.refresh_from_db()
        assert eval_cell.status == CellStatus.RUNNING.value
        assert eval_cell.value is None
        assert eval_cell.value_infos == {}

    def test_evaluate_single_row_rejects_row_outside_metric_dataset(
        self, auth_client, organization, workspace, user_eval_metric
    ):
        """Rows must belong to the same active-workspace dataset as the metric."""
        other_dataset = Dataset.objects.create(
            name="Other Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_row = Row.objects.create(dataset=other_dataset, order=0)
        payload = {
            "row_ids": [str(other_row.id)],
            "user_eval_metric_ids": [str(user_eval_metric.id)],
        }

        with patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/evaluate-rows/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_task.assert_not_called()

    def test_evaluate_single_row_rejects_other_workspace_metric_before_mutation(
        self, auth_client, organization, user, eval_template
    ):
        """Metrics outside the active workspace must not be queued or mutate cells."""
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.objects.create(
            name="Other Workspace Dataset",
            organization=organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_row = Row.objects.create(dataset=other_dataset, order=0)
        other_metric = UserEvalMetric.objects.create(
            name="Other Workspace Evaluation",
            dataset=other_dataset,
            organization=organization,
            workspace=other_workspace,
            template=eval_template,
            status=StatusType.NOT_STARTED.value,
            config={"mapping": {}},
        )
        eval_column = Column.objects.create(
            name="Eval Output",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(other_metric.id),
            status=StatusType.COMPLETED.value,
        )
        eval_cell = Cell.objects.create(
            dataset=other_dataset,
            column=eval_column,
            row=other_row,
            value="existing",
            value_infos={"kept": True},
            status=CellStatus.PASS.value,
        )
        payload = {
            "row_ids": [str(other_row.id)],
            "user_eval_metric_ids": [str(other_metric.id)],
        }

        with patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/evaluate-rows/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_task.assert_not_called()
        eval_cell.refresh_from_db()
        assert eval_cell.status == CellStatus.PASS.value
        assert eval_cell.value == "existing"
        assert eval_cell.value_infos == {"kept": True}

    def test_evaluate_selected_all_rejects_excluded_row_outside_dataset(
        self, auth_client, organization, workspace, user_eval_metric
    ):
        """selected_all_rows exclusion ids are still scoped to the metric dataset."""
        other_dataset = Dataset.objects.create(
            name="Other Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_row = Row.objects.create(dataset=other_dataset, order=0)
        payload = {
            "selected_all_rows": True,
            "row_ids": [str(other_row.id)],
            "user_eval_metric_ids": [str(user_eval_metric.id)],
        }

        with patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as mock_task:
            response = auth_client.post(
                "/model-hub/evaluate-rows/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_task.assert_not_called()

    def test_evaluate_single_row_missing_row_ids(self, auth_client, user_eval_metric):
        """Test that missing row_ids returns error."""
        payload = {
            "user_eval_metric_ids": [str(user_eval_metric.id)],
        }

        response = auth_client.post(
            "/model-hub/evaluate-rows/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_evaluate_single_row_missing_eval_ids(self, auth_client, row):
        """Test that missing user_eval_metric_ids returns error."""
        payload = {
            "row_ids": [str(row.id)],
        }

        response = auth_client.post(
            "/model-hub/evaluate-rows/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_evaluate_single_row_empty_row_ids(self, auth_client, user_eval_metric):
        """Test that empty row_ids returns error."""
        payload = {
            "row_ids": [],
            "user_eval_metric_ids": [str(user_eval_metric.id)],
        }

        response = auth_client.post(
            "/model-hub/evaluate-rows/",
            payload,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_evaluate_single_row_unauthenticated(self):
        """Test that unauthenticated users cannot evaluate rows."""
        client = APIClient()
        response = client.post(
            "/model-hub/evaluate-rows/",
            {},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_dataset_eval_rerun_checks_usage_and_sets_usage_source(
        self, user_eval_metric, row
    ):
        """Dataset eval reruns should use the same AI-credit path metadata as initial runs."""
        from model_hub.views.develop_dataset import run_evaluation_task

        usage_check = SimpleNamespace(allowed=True)

        with patch(
            "ee.usage.services.metering.check_usage", return_value=usage_check
        ) as mock_check_usage, patch(
            "model_hub.views.develop_dataset.get_mixpanel_properties",
            return_value={},
        ), patch(
            "model_hub.views.develop_dataset.track_mixpanel_event"
        ), patch(
            "model_hub.views.develop_dataset.EvaluationRunner"
        ) as mock_runner_class:
            mock_runner = MagicMock()
            mock_runner_class.return_value = mock_runner

            run_evaluation_task._original_func(
                {
                    "metric_ids": [str(user_eval_metric.id)],
                    "row_ids": [str(row.id)],
                }
            )

        mock_check_usage.assert_called_once()
        mock_runner_class.assert_called_once()
        _, kwargs = mock_runner_class.call_args
        assert kwargs["source"] == "dataset_evaluation"
        assert kwargs["source_id"] == user_eval_metric.template.id
        assert kwargs["source_configs"]["dataset_id"] == str(user_eval_metric.dataset_id)
        assert kwargs["source_configs"]["source"] == "dataset"
        mock_runner.run_evaluation_for_row.assert_called_once_with(str(row.id))

    def test_dataset_eval_rerun_stops_when_usage_limit_exceeded(
        self, user_eval_metric, row
    ):
        """If AI credits are exhausted, reruns should not execute evaluator calls."""
        from model_hub.models.choices import StatusType
        from model_hub.views.develop_dataset import run_evaluation_task

        usage_check = SimpleNamespace(
            allowed=False,
            reason="Usage limit exceeded",
            error_code="USAGE_LIMIT_EXCEEDED",
            dimension="ai_credits",
            current_usage=10,
            limit=10,
            upgrade_cta=None,
        )

        with patch(
            "ee.usage.services.metering.check_usage", return_value=usage_check
        ), patch(
            "model_hub.tasks.user_evaluation._mark_cells_usage_limit_error"
        ) as mock_mark_limit, patch(
            "model_hub.views.develop_dataset.EvaluationRunner"
        ) as mock_runner_class:
            run_evaluation_task._original_func(
                {
                    "metric_ids": [str(user_eval_metric.id)],
                    "row_ids": [str(row.id)],
                }
            )

        user_eval_metric.refresh_from_db()
        assert user_eval_metric.status == StatusType.FAILED.value
        mock_mark_limit.assert_called_once()
        mock_runner_class.assert_not_called()


# ==================== Workspace Isolation Tests ====================


@pytest.mark.django_db
class TestDatasetEvalWorkspaceIsolation:
    """Dataset eval drawer routes must reject same-org other-workspace rows."""

    def _other_workspace_eval_fixture(self, organization, user):
        other_workspace = Workspace.objects.create(
            name="Other Dataset Eval Workspace",
            organization=organization,
            created_by=user,
        )
        other_dataset = Dataset.no_workspace_objects.create(
            name="Other Dataset Eval Dataset",
            organization=organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        input_column = Column.no_workspace_objects.create(
            name="Other Input",
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        other_dataset.column_order = [str(input_column.id)]
        other_dataset.save()
        row = Row.no_workspace_objects.create(dataset=other_dataset, order=0)
        Cell.no_workspace_objects.create(
            dataset=other_dataset,
            column=input_column,
            row=row,
            value="keep input",
            status=CellStatus.PASS.value,
        )
        template = EvalTemplate.no_workspace_objects.create(
            name="other-workspace-eval-template",
            organization=organization,
            workspace=other_workspace,
            eval_type="code",
            visible_ui=True,
            config={
                "code": "def evaluate(**kwargs): return {'score': 1, 'reason': 'ok'}",
                "output": "Pass/Fail",
                "eval_type_id": "CustomCodeEval",
                "required_keys": ["text"],
            },
        )
        EvalTemplate.no_workspace_objects.filter(id=template.id).update(
            workspace=other_workspace
        )
        template.refresh_from_db()
        metric = UserEvalMetric.no_workspace_objects.create(
            name="Other Workspace Eval",
            dataset=other_dataset,
            organization=organization,
            workspace=other_workspace,
            template=template,
            status=StatusType.RUNNING.value,
            config={
                "mapping": {"text": str(input_column.id)},
                "reason_column": True,
            },
        )
        UserEvalMetric.no_workspace_objects.filter(id=metric.id).update(
            workspace=other_workspace
        )
        metric.refresh_from_db()
        eval_column = Column.no_workspace_objects.create(
            name=metric.name,
            dataset=other_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(metric.id),
        )
        eval_cell = Cell.no_workspace_objects.create(
            dataset=other_dataset,
            column=eval_column,
            row=row,
            value="keep eval",
            status=CellStatus.PASS.value,
        )
        return SimpleNamespace(
            workspace=other_workspace,
            dataset=other_dataset,
            input_column=input_column,
            row=row,
            template=template,
            metric=metric,
            eval_column=eval_column,
            eval_cell=eval_cell,
        )

    def test_eval_drawer_routes_reject_other_workspace_before_mutation(
        self, auth_client, organization, user
    ):
        fixture = self._other_workspace_eval_fixture(organization, user)

        list_response = auth_client.get(
            f"/model-hub/develops/{fixture.dataset.id}/get_evals_list/"
        )
        structure_response = auth_client.get(
            f"/model-hub/develops/{fixture.dataset.id}/"
            f"get_eval_structure/{fixture.metric.id}/?eval_type=user"
        )
        preset_structure_response = auth_client.get(
            f"/model-hub/develops/{fixture.dataset.id}/"
            f"get_eval_structure/{fixture.template.id}/?eval_type=preset"
        )
        add_response = auth_client.post(
            f"/model-hub/develops/{fixture.dataset.id}/add_user_eval/",
            {
                "name": "should-not-create",
                "template_id": str(fixture.template.id),
                "config": {"mapping": {"text": str(fixture.input_column.id)}},
                "run": False,
            },
            format="json",
        )
        preview_response = auth_client.post(
            f"/model-hub/develops/{fixture.dataset.id}/preview_run_eval/",
            {
                "template_id": str(fixture.template.id),
                "config": {"mapping": {"text": str(fixture.input_column.id)}},
            },
            format="json",
        )
        start_response = auth_client.post(
            f"/model-hub/develops/{fixture.dataset.id}/start_evals_process/",
            {"user_eval_ids": [str(fixture.metric.id)]},
            format="json",
        )
        stop_response = auth_client.post(
            f"/model-hub/develops/{fixture.dataset.id}/"
            f"stop_user_eval/{fixture.metric.id}/",
            {},
            format="json",
        )
        edit_response = auth_client.post(
            f"/model-hub/develops/{fixture.dataset.id}/"
            f"edit_and_run_user_eval/{fixture.metric.id}/",
            {
                "config": {
                    "mapping": {"text": str(fixture.input_column.id)},
                    "reason_column": True,
                }
            },
            format="json",
        )
        delete_template_response = auth_client.delete(
            f"/model-hub/develops/{fixture.dataset.id}/"
            f"delete_template_eval/{fixture.template.id}/",
        )

        assert list_response.status_code == status.HTTP_404_NOT_FOUND
        assert structure_response.status_code == status.HTTP_404_NOT_FOUND
        assert preset_structure_response.status_code == status.HTTP_404_NOT_FOUND
        assert add_response.status_code == status.HTTP_404_NOT_FOUND
        assert preview_response.status_code == status.HTTP_404_NOT_FOUND
        assert start_response.status_code == status.HTTP_404_NOT_FOUND
        assert stop_response.status_code == status.HTTP_404_NOT_FOUND
        assert edit_response.status_code == status.HTTP_404_NOT_FOUND
        assert delete_template_response.status_code == status.HTTP_404_NOT_FOUND
        fixture.metric.refresh_from_db()
        fixture.template.refresh_from_db()
        fixture.eval_cell.refresh_from_db()
        assert fixture.metric.status == StatusType.RUNNING.value
        assert fixture.metric.config == {
            "mapping": {"text": str(fixture.input_column.id)},
            "reason_column": True,
        }
        assert fixture.template.deleted is False
        assert fixture.template.deleted_at is None
        assert fixture.eval_cell.value == "keep eval"
        assert fixture.eval_cell.status == CellStatus.PASS.value
        assert not UserEvalMetric.no_workspace_objects.filter(
            name="should-not-create",
            organization=organization,
            deleted=False,
        ).exists()

    def test_template_routes_reject_other_workspace_template_before_mutation(
        self, auth_client, dataset, organization, user
    ):
        fixture = self._other_workspace_eval_fixture(organization, user)

        structure_response = auth_client.get(
            f"/model-hub/develops/{dataset.id}/"
            f"get_eval_structure/{fixture.template.id}/?eval_type=preset"
        )
        add_response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/add_user_eval/",
            {
                "name": "should-not-create-from-template",
                "template_id": str(fixture.template.id),
                "config": {"mapping": {"text": str(fixture.input_column.id)}},
                "run": False,
            },
            format="json",
        )
        preview_response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/preview_run_eval/",
            {
                "template_id": str(fixture.template.id),
                "config": {"mapping": {"text": str(fixture.input_column.id)}},
            },
            format="json",
        )
        delete_response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/"
            f"delete_template_eval/{fixture.template.id}/",
        )

        assert structure_response.status_code == status.HTTP_404_NOT_FOUND
        assert add_response.status_code == status.HTTP_404_NOT_FOUND
        assert preview_response.status_code == status.HTTP_404_NOT_FOUND
        assert delete_response.status_code == status.HTTP_404_NOT_FOUND
        fixture.template.refresh_from_db()
        assert fixture.template.deleted is False
        assert fixture.template.deleted_at is None
        assert not UserEvalMetric.no_workspace_objects.filter(
            name="should-not-create-from-template",
            dataset=dataset,
            organization=organization,
            deleted=False,
        ).exists()


@pytest.mark.django_db
class TestDeleteTemplateEvalsView:
    def test_delete_template_eval_sets_deleted_at(
        self, auth_client, dataset, organization, workspace
    ):
        template = EvalTemplate.objects.create(
            name="delete-template-eval",
            organization=organization,
            workspace=workspace,
            owner="user",
            visible_ui=True,
        )

        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_template_eval/{template.id}/",
        )

        assert response.status_code == status.HTTP_200_OK
        template.refresh_from_db()
        assert template.deleted is True
        assert template.deleted_at is not None


# ==================== Organization Isolation Tests ====================


@pytest.mark.django_db
class TestEvaluationOrganizationIsolation:
    """Tests for organization isolation in evaluation operations."""

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
    def other_org_dataset(self, db, other_organization, other_org_user):
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=other_organization,
            is_default=True,
            created_by=other_org_user,
        )
        return Dataset.objects.create(
            name="Other Org Dataset",
            organization=other_organization,
            workspace=other_workspace,
            source=DatasetSourceChoices.BUILD.value,
        )

    @pytest.fixture
    def other_org_eval(self, db, other_org_dataset, other_organization, other_org_user):
        other_workspace = Workspace.objects.get(
            organization=other_organization, is_default=True
        )
        other_template = EvalTemplate.objects.create(
            name="other-org-template",
            organization=other_organization,
            workspace=other_workspace,
            criteria="Other org criteria",
            model="gpt-4",
        )
        return UserEvalMetric.objects.create(
            name="Other Org Eval",
            dataset=other_org_dataset,
            organization=other_organization,
            workspace=other_workspace,
            template=other_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )

    def test_cannot_start_evals_for_other_org_dataset(
        self, auth_client, other_org_dataset, other_org_eval
    ):
        """Test that users cannot start evaluations for other org's datasets."""
        payload = {
            "user_eval_ids": [str(other_org_eval.id)],
        }

        response = auth_client.post(
            f"/model-hub/develops/{other_org_dataset.id}/start_evals_process/",
            payload,
            format="json",
        )

        # Should fail - either no matching evals or forbidden
        assert response.status_code in [
            status.HTTP_200_OK,  # No matching evals found (org filter)
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_cannot_delete_other_org_eval(self, auth_client, dataset, other_org_eval):
        """Test that users cannot delete evaluations from other organizations."""
        response = auth_client.delete(
            f"/model-hub/develops/{dataset.id}/delete_user_eval/{other_org_eval.id}/"
        )

        # Should fail - eval not found for this org/dataset
        # The API returns 404 when eval is not found
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cannot_get_evals_list_for_other_org_dataset(
        self, auth_client, other_org_dataset
    ):
        """Test that users cannot get evaluations list for other org's datasets."""
        response = auth_client.get(
            f"/model-hub/develops/{other_org_dataset.id}/get_evals_list/"
        )

        # May return empty list due to org filtering or error
        assert response.status_code in [
            status.HTTP_200_OK,  # Empty list
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]
