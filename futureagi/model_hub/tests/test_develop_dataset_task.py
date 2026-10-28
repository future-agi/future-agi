"""Tests for synthetic dataset creation task exception handling.

Verifies that the create_synthetic_dataset task correctly persists failure
information to the Dataset model and cleans up stale task-manager cache
entries.  See issue #1585.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Dataset


@pytest.mark.django_db
class TestCreateSyntheticDatasetFailureHandling(TestCase):
    """Exception handler in create_synthetic_dataset must persist the
    failure so the frontend can read it via the dataset-detail API."""

    def setUp(self):
        """Create a minimal Dataset row that the except block can update."""
        # Dataset has required FK fields (organization, user via BaseModel
        # auto-assignment).  We cannot create one inline without a full
        # setup, so we mock Dataset.objects.get and verify via the mock.
        self.dataset_id = "00000000-0000-0000-0000-000000000001"

    @patch("model_hub.tasks.develop_dataset.Dataset.objects")
    @patch("model_hub.tasks.develop_dataset.SyntheticTaskManager")
    def test_exception_handler_sets_status_and_failure_reason(
        self, mock_task_manager_cls, mock_dataset_objects
    ):
        """When the task body raises, the except block writes FAILED +
        failure_reason and clears the task-manager cache."""
        from model_hub.tasks.develop_dataset import create_synthetic_dataset

        # Arrange
        mock_dataset = MagicMock(spec=Dataset)
        mock_dataset_objects.get.return_value = mock_dataset
        mock_task_manager = MagicMock()
        mock_task_manager_cls.return_value = mock_task_manager

        # Make the task body blow up immediately (Organization lookup).
        side_effect = ValueError("Usage limit exceeded for this organization")

        # Act – the except block should catch ValueError and persist reason.
        with patch(
            "model_hub.tasks.develop_dataset.Organization.objects.get",
            side_effect=side_effect,
        ):
            with pytest.raises(ValueError):
                create_synthetic_dataset(
                    validated_data={},
                    dataset_id=self.dataset_id,
                    organization_id="org-1",
                    creating_synthetic_dataset=True,
                )

        # Assert – model updates.
        assert mock_dataset.status == StatusType.FAILED.value  # noqa: PT017
        assert mock_dataset.failure_reason == str(side_effect)  # noqa: PT017
        mock_dataset.save.assert_called()
        # Organization lookup failed before task_manager was initialised, so
        # clear_task_data was never called — the except block guards against
        # task_manager being None.
        mock_task_manager.clear_task_data.assert_not_called()

    @patch("model_hub.tasks.develop_dataset.Dataset.objects")
    @patch("model_hub.tasks.develop_dataset.SyntheticTaskManager")
    def test_exception_handler_truncates_long_reason(
        self, mock_task_manager_cls, mock_dataset_objects
    ):
        """failure_reason is capped at 10000 chars to stay within TextField
        limits and avoid bloating the DB row."""
        from model_hub.tasks.develop_dataset import create_synthetic_dataset

        mock_dataset = MagicMock(spec=Dataset)
        mock_dataset_objects.get.return_value = mock_dataset
        mock_task_manager_cls.return_value = MagicMock()

        long_message = "x" * 15000
        side_effect = RuntimeError(long_message)

        with patch(
            "model_hub.tasks.develop_dataset.Organization.objects.get",
            side_effect=side_effect,
        ):
            with pytest.raises(RuntimeError):
                create_synthetic_dataset(
                    validated_data={},
                    dataset_id=self.dataset_id,
                    organization_id="org-1",
                    creating_synthetic_dataset=True,
                )

        assert len(mock_dataset.failure_reason) == 10000  # noqa: PT017
        assert mock_dataset.failure_reason == long_message[:10000]  # noqa: PT017

    @patch("model_hub.tasks.develop_dataset.Dataset.objects")
    @patch("model_hub.tasks.develop_dataset.SyntheticTaskManager")
    def test_exception_handler_still_raises_after_persisting(
        self, mock_task_manager_cls, mock_dataset_objects
    ):
        """The original exception must propagate even when persistence
        succeeds – Celery needs to see the failure for retries/seeds."""
        from model_hub.tasks.develop_dataset import create_synthetic_dataset

        mock_dataset = MagicMock(spec=Dataset)
        mock_dataset_objects.get.return_value = mock_dataset
        mock_task_manager_cls.return_value = MagicMock()

        original_error = RuntimeError("agent execution failed")

        with patch(
            "model_hub.tasks.develop_dataset.Organization.objects.get",
            side_effect=original_error,
        ):
            with pytest.raises(RuntimeError) as exc_info:
                create_synthetic_dataset(
                    validated_data={},
                    dataset_id=self.dataset_id,
                    organization_id="org-1",
                    creating_synthetic_dataset=True,
                )

        assert exc_info.value is original_error

    @patch("model_hub.tasks.develop_dataset.Dataset.objects")
    @patch("model_hub.tasks.develop_dataset.SyntheticTaskManager")
    def test_exception_handler_survives_dataset_lookup_failure(
        self, mock_task_manager_cls, mock_dataset_objects
    ):
        """If even Dataset.objects.get fails, the handler logs the inner
        error and re-raises the original – it must not double-fault."""
        from model_hub.tasks.develop_dataset import create_synthetic_dataset

        # Dataset.objects.get raises, so the except block's inner try/except
        # kicks in.
        mock_dataset_objects.get.side_effect = Dataset.DoesNotExist(
            "Dataset matching query does not exist."
        )
        mock_task_manager_cls.return_value = MagicMock()

        original_error = ValueError("something broke")

        with patch(
            "model_hub.tasks.develop_dataset.Organization.objects.get",
            side_effect=original_error,
        ):
            with pytest.raises(ValueError) as exc_info:
                create_synthetic_dataset(
                    validated_data={},
                    dataset_id=self.dataset_id,
                    organization_id="org-1",
                    creating_synthetic_dataset=True,
                )

        # The *original* error propagates, not the DoesNotExist.
        assert exc_info.value is original_error
        # Organization lookup failed before task_manager was initialised;
        # the except block guards against task_manager being None.
        mock_task_manager_cls.return_value.clear_task_data.assert_not_called()
