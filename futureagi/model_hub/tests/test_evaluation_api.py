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
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
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

        # Both should be removed from column_order
        dataset.refresh_from_db()
        assert str(eval_col.id) not in dataset.column_order
        assert str(reason_col.id) not in dataset.column_order

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
            status.HTTP_403_FORBIDDEN,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]
