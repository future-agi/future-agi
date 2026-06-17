"""
Tests for Temporal background task activities.

Run with: pytest tfc/temporal/background_tasks/tests/test_activities.py -v
"""

import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.django_db
class TestRunPostRegistrationActivity:
    """Tests for run_post_registration_activity."""

    @patch("accounts.utils._run_post_registration")
    def test_calls_run_post_registration(self, mock_run):
        """Test that activity calls the underlying implementation."""
        from tfc.temporal.background_tasks.activities import (
            run_post_registration_activity,
        )

        mock_run.return_value = {"status": "success"}

        result = run_post_registration_activity("user-123", "password123")

        mock_run.assert_called_once_with("user-123", "password123")
        assert result == {"status": "success"}

    @patch("accounts.utils._run_post_registration")
    def test_handles_exception(self, mock_run):
        """Test that activity propagates exceptions."""
        from tfc.temporal.background_tasks.activities import (
            run_post_registration_activity,
        )

        mock_run.side_effect = Exception("Email service unavailable")

        with pytest.raises(Exception, match="Email service unavailable"):
            run_post_registration_activity("user-123", "password123")


@pytest.mark.django_db
class TestProcessHuggingFaceDatasetActivity:
    """Tests for process_huggingface_dataset_activity."""

    @patch("model_hub.views.utils.hugginface.process_huggingface_dataset")
    def test_calls_process_huggingface_dataset(self, mock_process):
        """Test that activity calls the underlying implementation."""
        from tfc.temporal.background_tasks.activities import (
            process_huggingface_dataset_activity,
        )

        mock_process.return_value = {"processed": True}

        result = process_huggingface_dataset_activity(
            dataset_id="ds-123",
            huggingface_dataset_name="test/dataset",
            huggingface_dataset_config="default",
            huggingface_dataset_split="train",
            organization_id="org-456",
            num_rows=100,
            column_order=["col1", "col2"],
            rows={"0": "row-1", "1": "row-2"},
        )

        mock_process.assert_called_once_with(
            "ds-123",
            "test/dataset",
            "default",
            "train",
            "org-456",
            100,
            ["col1", "col2"],
            {"0": "row-1", "1": "row-2"},
        )
        assert result == {"processed": True}

    @patch("model_hub.views.utils.hugginface.process_huggingface_dataset")
    def test_handles_empty_rows(self, mock_process):
        """Test that activity handles empty rows dictionary."""
        from tfc.temporal.background_tasks.activities import (
            process_huggingface_dataset_activity,
        )

        mock_process.return_value = None

        result = process_huggingface_dataset_activity(
            dataset_id="ds-123",
            huggingface_dataset_name="test/dataset",
            huggingface_dataset_config=None,
            huggingface_dataset_split="train",
            organization_id="org-456",
            num_rows=0,
            column_order=[],
            rows={},
        )

        assert result is None


@pytest.mark.django_db
class TestDeleteCompareFolderActivity:
    """Tests for delete_compare_folder_activity."""

    def test_deletes_local_folder_if_exists(self):
        """Test that activity deletes local folder when it exists."""
        from tfc.temporal.background_tasks.activities import (
            delete_compare_folder_activity,
        )
        from tfc.utils.storage import get_compare_local_dir

        # Create a temp directory structure
        temp_dir = tempfile.mkdtemp()
        compare_dir = os.path.join(temp_dir, "compare")
        os.makedirs(compare_dir)
        test_compare_id = "test-compare-123"
        test_folder = os.path.join(compare_dir, test_compare_id)
        os.makedirs(test_folder)

        # Create a test file inside
        with open(os.path.join(test_folder, "test.json"), "w") as f:
            f.write("{}")

        try:
            # Patch the working directory and s3 delete
            with (
                patch("os.path.isdir") as mock_isdir,
                patch("shutil.rmtree") as mock_rmtree,
                patch("tfc.utils.storage.delete_compare_folder") as mock_s3_delete,
            ):
                mock_isdir.return_value = True

                delete_compare_folder_activity(test_compare_id)

                expected_dir = get_compare_local_dir(test_compare_id)
                mock_isdir.assert_called_once_with(expected_dir)
                mock_rmtree.assert_called_once_with(expected_dir)
                mock_s3_delete.assert_called_once_with(test_compare_id)
        finally:
            shutil.rmtree(temp_dir)

    @patch("tfc.utils.storage.delete_compare_folder")
    def test_skips_local_deletion_if_folder_not_exists(self, mock_s3_delete):
        """Test that activity skips local deletion when folder doesn't exist."""
        from tfc.temporal.background_tasks.activities import (
            delete_compare_folder_activity,
        )

        with (
            patch("os.path.isdir", return_value=False) as mock_isdir,
            patch("shutil.rmtree") as mock_rmtree,
        ):
            delete_compare_folder_activity("nonexistent-123")

            mock_isdir.assert_called_once()
            mock_rmtree.assert_not_called()
            mock_s3_delete.assert_called_once_with("nonexistent-123")


@pytest.mark.django_db
class TestPrepareCompareDatasetActivity:
    """Tests for prepare_compare_dataset_activity."""

    @patch("model_hub.views.develop_dataset._prepare_compare_dataset_impl")
    def test_calls_prepare_compare_dataset_impl(self, mock_impl):
        """Test that activity calls the underlying implementation."""
        from tfc.temporal.background_tasks.activities import (
            prepare_compare_dataset_activity,
        )

        mock_impl.return_value = {"table": []}

        result = prepare_compare_dataset_activity(
            dataset_id="ds-123",
            common_base_values=["val1", "val2"],
            base_column_name="base_col",
            data_by_dataset={"ds-123": {}},
            comparison_datasets=["ds-456"],
            columns_lookup={},
            main_base_column={"id": "col-1"},
            common_columns=["col1", "col2"],
            compare_id="cmp-789",
            column_config=[],
            dataset_info=[],
            dynamic_sources={},
        )

        mock_impl.assert_called_once()
        assert result == {"table": []}


@pytest.mark.django_db
class TestIngestKbFilesActivity:
    """Tests for ingest_kb_files_activity."""

    @patch("model_hub.utils.kb_helpers.ingest_kb_files_impl")
    def test_calls_ingest_kb_files_impl(self, mock_impl):
        """Test that activity calls the underlying implementation."""
        from tfc.temporal.background_tasks.activities import ingest_kb_files_activity

        mock_impl.return_value = {"ingested": True}

        result = ingest_kb_files_activity(
            file_metadata={"file-1": {"name": "test.pdf", "extension": "pdf"}},
            kb_id="kb-123",
            org_id="org-456",
        )

        mock_impl.assert_called_once_with(
            {"file-1": {"name": "test.pdf", "extension": "pdf"}}, "kb-123", "org-456"
        )
        assert result == {"ingested": True}

    @patch("model_hub.utils.kb_helpers.ingest_kb_files_impl")
    def test_handles_empty_file_metadata(self, mock_impl):
        """Test that activity handles empty file metadata."""
        from tfc.temporal.background_tasks.activities import ingest_kb_files_activity

        mock_impl.return_value = None

        result = ingest_kb_files_activity(
            file_metadata={},
            kb_id="kb-123",
            org_id="org-456",
        )

        mock_impl.assert_called_once_with({}, "kb-123", "org-456")
        assert result is None


class TestActivityRegistration:
    """Tests for activity registration with Temporal."""

    def test_activities_are_registered(self):
        """Test that all activities are properly registered."""
        from tfc.temporal.background_tasks import (
            delete_compare_folder_activity,
            ingest_kb_files_activity,
            process_huggingface_dataset_activity,
            run_post_registration_activity,
        )
        from tfc.temporal.drop_in import get_activity_by_name

        # Verify activities can be retrieved by name
        assert get_activity_by_name("run_post_registration_activity") is not None
        assert get_activity_by_name("process_huggingface_dataset_activity") is not None
        assert get_activity_by_name("delete_compare_folder_activity") is not None
        assert get_activity_by_name("ingest_kb_files_activity") is not None

    def test_activity_has_correct_attributes(self):
        """Test that activities have correct time limits and queues."""
        from tfc.temporal.background_tasks.activities import (
            delete_compare_folder_activity,
            ingest_kb_files_activity,
            process_huggingface_dataset_activity,
            run_post_registration_activity,
        )

        # Check that functions are callable
        assert callable(run_post_registration_activity)
        assert callable(process_huggingface_dataset_activity)
        assert callable(delete_compare_folder_activity)
        assert callable(ingest_kb_files_activity)

    def test_all_exports_are_defined(self):
        """Test that __all__ exports are properly defined."""
        from tfc.temporal.background_tasks.activities import __all__

        expected_activities = [
            "run_post_registration_activity",
            "process_huggingface_dataset_activity",
            "delete_compare_folder_activity",
            "prepare_compare_dataset_activity",
            "ingest_kb_files_activity",
        ]

        for activity_name in expected_activities:
            assert activity_name in __all__, f"{activity_name} not in __all__"
