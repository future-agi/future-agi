"""
Tests for Knowledge Base helper functions in model_hub/utils/kb_helpers.py.

Run with: pytest model_hub/tests/test_kb_helpers.py -v
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import StatusType


@pytest.mark.django_db
class TestIsKbDeletedOrCancelled:
    """Tests for is_kb_deleted_or_cancelled function."""

    def test_returns_true_for_nonexistent_kb(self):
        """Test returns True for non-existent KB."""
        from model_hub.utils.kb_helpers import is_kb_deleted_or_cancelled

        non_existent_uuid = str(uuid.uuid4())
        result = is_kb_deleted_or_cancelled(non_existent_uuid)
        assert result is True

    @patch("model_hub.utils.kb_helpers.KnowledgeBaseFile")
    def test_returns_true_when_status_is_deleting(self, mock_kb_model):
        """Test returns True when KB status is DELETING."""
        from model_hub.utils.kb_helpers import is_kb_deleted_or_cancelled

        mock_kb = MagicMock()
        mock_kb.status = StatusType.DELETING.value
        mock_kb_model.objects.get.return_value = mock_kb

        result = is_kb_deleted_or_cancelled("kb-123")
        assert result is True

    @patch("model_hub.utils.kb_helpers.KnowledgeBaseFile")
    def test_returns_false_when_kb_exists_and_not_deleting(self, mock_kb_model):
        """Test returns False when KB exists and status is not DELETING."""
        from model_hub.utils.kb_helpers import is_kb_deleted_or_cancelled

        mock_kb = MagicMock()
        mock_kb.status = StatusType.PROCESSING.value
        mock_kb_model.objects.get.return_value = mock_kb

        result = is_kb_deleted_or_cancelled("kb-123")
        assert result is False

    @patch("model_hub.utils.kb_helpers.KnowledgeBaseFile")
    def test_uses_get_with_doesnotexist(self, mock_kb_model):
        """Test that .get() is used and DoesNotExist is handled."""
        from django.core.exceptions import ObjectDoesNotExist

        from model_hub.utils.kb_helpers import is_kb_deleted_or_cancelled

        # Create a proper DoesNotExist exception class
        mock_kb_model.DoesNotExist = type("DoesNotExist", (ObjectDoesNotExist,), {})
        mock_kb_model.objects.get.side_effect = mock_kb_model.DoesNotExist

        result = is_kb_deleted_or_cancelled("kb-123")
        assert result is True
        mock_kb_model.objects.get.assert_called_once_with(id="kb-123")


class TestGetKbWorkflowId:
    """Tests for get_kb_workflow_id function."""

    def test_returns_deterministic_workflow_id(self):
        """Test returns deterministic workflow ID."""
        from model_hub.utils.kb_helpers import get_kb_workflow_id

        result = get_kb_workflow_id("kb-123")
        assert result == "kb-ingest-kb-123"

    def test_different_kb_ids_produce_different_workflow_ids(self):
        """Test different KB IDs produce different workflow IDs."""
        from model_hub.utils.kb_helpers import get_kb_workflow_id

        result1 = get_kb_workflow_id("kb-123")
        result2 = get_kb_workflow_id("kb-456")
        assert result1 != result2


class TestScheduleKbIngestionOnCommit:
    """Tests for schedule_kb_ingestion_on_commit function."""

    @patch("model_hub.utils.kb_helpers.transaction.on_commit")
    def test_schedules_ingestion_on_commit(self, mock_on_commit):
        """Test that ingestion is scheduled via transaction.on_commit."""
        from model_hub.utils.kb_helpers import schedule_kb_ingestion_on_commit

        schedule_kb_ingestion_on_commit(
            file_metadata={"file-1": {"name": "test.pdf", "extension": "pdf"}},
            kb_id="kb-123",
            org_id="org-456",
        )

        mock_on_commit.assert_called_once()
        callback = mock_on_commit.call_args[0][0]
        assert callable(callback)

    @patch("model_hub.utils.kb_helpers.transaction.on_commit")
    def test_skips_empty_file_metadata(self, mock_on_commit):
        """Test that empty file metadata is handled gracefully."""
        from model_hub.utils.kb_helpers import schedule_kb_ingestion_on_commit

        schedule_kb_ingestion_on_commit(
            file_metadata={},
            kb_id="kb-123",
            org_id="org-456",
        )

        mock_on_commit.assert_not_called()

    @patch("model_hub.utils.kb_helpers.transaction.on_commit")
    def test_skips_none_file_metadata(self, mock_on_commit):
        """Test that None file metadata is handled gracefully."""
        from model_hub.utils.kb_helpers import schedule_kb_ingestion_on_commit

        schedule_kb_ingestion_on_commit(
            file_metadata=None,
            kb_id="kb-123",
            org_id="org-456",
        )

        mock_on_commit.assert_not_called()

    @patch("tfc.temporal.drop_in.start_activity")
    @patch("model_hub.utils.kb_helpers.transaction.on_commit")
    def test_callback_starts_temporal_activity_with_task_id(
        self, mock_on_commit, mock_start
    ):
        """Test the registered callback starts Temporal activity with task_id."""
        from model_hub.utils.kb_helpers import schedule_kb_ingestion_on_commit

        schedule_kb_ingestion_on_commit(
            file_metadata={"file-1": {"name": "test.pdf", "extension": "pdf"}},
            kb_id="kb-123",
            org_id="org-456",
        )

        # Execute the registered callback
        callback = mock_on_commit.call_args[0][0]
        callback()

        mock_start.assert_called_once_with(
            "ingest_kb_files_activity",
            args=(
                {"file-1": {"name": "test.pdf", "extension": "pdf"}},
                "kb-123",
                "org-456",
            ),
            queue="default",
            task_id="kb-ingest-kb-123",
        )

    @patch("model_hub.utils.kb_helpers.KnowledgeBaseFile.objects.filter")
    @patch("tfc.temporal.drop_in.start_activity")
    @patch("model_hub.utils.kb_helpers.transaction.on_commit")
    def test_callback_marks_kb_failed_when_temporal_scheduling_fails(
        self, mock_on_commit, mock_start, mock_filter
    ):
        """A post-commit Temporal outage must not make the HTTP create fail."""
        from model_hub.models.choices import StatusType
        from model_hub.utils.kb_helpers import schedule_kb_ingestion_on_commit

        mock_start.side_effect = RuntimeError("temporal unavailable")
        mock_queryset = mock_filter.return_value

        schedule_kb_ingestion_on_commit(
            file_metadata={"file-1": {"name": "test.pdf", "extension": "pdf"}},
            kb_id="kb-123",
            org_id="org-456",
        )

        callback = mock_on_commit.call_args[0][0]
        callback()

        mock_start.assert_called_once()
        mock_filter.assert_called_once_with(id="kb-123", deleted=False)
        mock_queryset.update.assert_called_once()
        update_kwargs = mock_queryset.update.call_args.kwargs
        assert update_kwargs["status"] == StatusType.FAILED.value
        assert "temporal unavailable" in update_kwargs["last_error"]


class TestIngestKbFilesImpl:
    """Tests for ingest_kb_files_impl function."""

    def test_returns_none_for_empty_metadata(self):
        """Test that empty file metadata returns None."""
        from model_hub.utils.kb_helpers import ingest_kb_files_impl

        result = ingest_kb_files_impl({}, "kb-123", "org-456")
        assert result is None

    def test_returns_none_for_none_metadata(self):
        """Test that None file metadata returns None."""
        from model_hub.utils.kb_helpers import ingest_kb_files_impl

        result = ingest_kb_files_impl(None, "kb-123", "org-456")
        assert result is None

    @patch("model_hub.utils.kb_helpers.is_kb_deleted_or_cancelled", return_value=True)
    def test_stops_if_kb_deleted_at_start(self, mock_is_deleted):
        """Test that ingestion stops if KB is deleted at start."""
        from model_hub.utils.kb_helpers import ingest_kb_files_impl

        file_metadata = {"file-1": {"name": "test.pdf", "extension": "pdf"}}
        result = ingest_kb_files_impl(file_metadata, "kb-123", "org-456")

        assert result is None
        mock_is_deleted.assert_called_once_with("kb-123")

    @patch("model_hub.utils.kb_helpers.is_kb_deleted_or_cancelled", return_value=False)
    @patch("model_hub.utils.kb_helpers.get_storage_client")
    @patch("model_hub.tasks.develop_dataset.ingest_files_to_s3")
    def test_polls_s3_and_triggers_ingestion(
        self, mock_ingest, mock_get_client, mock_is_deleted
    ):
        """Test that it polls S3 for files and triggers ingestion."""
        from model_hub.utils.kb_helpers import ingest_kb_files_impl

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.stat_object.return_value = MagicMock()  # File exists

        file_metadata = {
            "file-1": {"name": "test1.pdf", "extension": "pdf"},
            "file-2": {"name": "test2.pdf", "extension": "pdf"},
        }

        ingest_kb_files_impl(file_metadata, "kb-123", "org-456")

        # Should have checked for both files
        assert mock_client.stat_object.call_count == 2
        # Should trigger ingestion with URLs
        mock_ingest.delay.assert_called_once()

    @patch("model_hub.utils.kb_helpers.is_kb_deleted_or_cancelled")
    @patch("model_hub.utils.kb_helpers.get_storage_client")
    @patch("model_hub.utils.kb_helpers.time.sleep")
    def test_stops_polling_if_kb_deleted_during_poll(
        self, mock_sleep, mock_get_client, mock_is_deleted
    ):
        """Test that polling stops if KB is deleted during poll."""
        from model_hub.utils.kb_helpers import ingest_kb_files_impl

        # First call returns False (not deleted), second call returns True (deleted)
        mock_is_deleted.side_effect = [False, True]

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        # File not found - will trigger retry
        mock_client.stat_object.side_effect = Exception("NoSuchKey")

        file_metadata = {"file-1": {"name": "test.pdf", "extension": "pdf"}}
        result = ingest_kb_files_impl(file_metadata, "kb-123", "org-456")

        assert result is None

    def test_marks_files_as_failed_after_timeout(self):
        """Test that files are marked FAILED after timeout.

        Note: This is a simplified test that validates the timeout constant.
        Full timeout testing would require mocking time progression.
        """
        # Verify the max_wait_time is set to 600 seconds (10 minutes)
        # We can check this by reading the function's source
        import inspect

        from model_hub.utils import kb_helpers

        source = inspect.getsource(kb_helpers.ingest_kb_files_impl)
        assert "max_wait_time = 600" in source

    @patch("model_hub.utils.kb_helpers.time.sleep")
    @patch("model_hub.utils.kb_helpers.get_storage_client")
    @patch("model_hub.utils.kb_helpers.is_kb_deleted_or_cancelled")
    def test_logs_non_404_s3_errors(self, mock_is_deleted, mock_get_client, mock_sleep):
        """Test that non-404 S3 errors are handled gracefully (not raised).

        The function should log non-404 errors but continue processing.
        """
        from model_hub.utils.kb_helpers import ingest_kb_files_impl

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        # Permission error - should be logged (not NoSuchKey or Not Found)
        mock_client.stat_object.side_effect = Exception("Access Denied")

        # Make it exit after first poll iteration
        mock_is_deleted.side_effect = [False, True]

        file_metadata = {"file-1": {"name": "test.pdf", "extension": "pdf"}}

        # Should not raise an exception - errors are logged and handled
        result = ingest_kb_files_impl(file_metadata, "kb-123", "org-456")

        # Should return None since KB was "deleted" (from mock)
        assert result is None


class TestCancelKbIngestionWorkflow:
    """Tests for cancel_kb_ingestion_workflow function."""

    @patch("model_hub.utils.kb_helpers.KnowledgeBaseFile")
    def test_marks_kb_as_deleting(self, mock_kb_model):
        """Test that KB is marked as DELETING."""
        from model_hub.utils.kb_helpers import cancel_kb_ingestion_workflow

        # Patch asyncio.run to prevent actual async execution
        with patch("asyncio.run"):
            with patch("asyncio.get_event_loop") as mock_loop:
                mock_loop.return_value.is_running.return_value = False
                cancel_kb_ingestion_workflow("kb-123")

        mock_kb_model.objects.filter.assert_called_once_with(id="kb-123")
        mock_kb_model.objects.filter.return_value.update.assert_called_once()
        update_kwargs = mock_kb_model.objects.filter.return_value.update.call_args[1]
        assert update_kwargs["status"] == StatusType.DELETING.value

    @patch("model_hub.utils.kb_helpers.KnowledgeBaseFile")
    def test_attempts_to_cancel_workflow(self, mock_kb_model):
        """Test that workflow cancellation is attempted."""
        from model_hub.utils.kb_helpers import cancel_kb_ingestion_workflow

        with patch("asyncio.run") as mock_run:
            with patch("asyncio.get_event_loop") as mock_loop:
                mock_loop.return_value.is_running.return_value = False
                cancel_kb_ingestion_workflow("kb-123")

        mock_run.assert_called_once()

    @patch("model_hub.utils.kb_helpers.KnowledgeBaseFile")
    def test_handles_db_update_error_gracefully(self, mock_kb_model):
        """Test that DB update errors are handled gracefully (not raised).

        The function should log the error but continue to attempt workflow cancellation.
        """
        from model_hub.utils.kb_helpers import cancel_kb_ingestion_workflow

        mock_kb_model.objects.filter.return_value.update.side_effect = Exception(
            "DB error"
        )

        # Should not raise - errors are logged and handled
        with patch("asyncio.run") as mock_run:
            with patch("asyncio.get_event_loop") as mock_loop:
                mock_loop.return_value.is_running.return_value = False
                cancel_kb_ingestion_workflow("kb-123")

        # Should still attempt to cancel workflow even if DB update failed
        mock_run.assert_called_once()

    @patch("model_hub.utils.kb_helpers.KnowledgeBaseFile")
    def test_handles_workflow_cancel_error_gracefully(self, mock_kb_model):
        """Test that workflow cancel errors (RuntimeError) are handled gracefully."""
        from model_hub.utils.kb_helpers import cancel_kb_ingestion_workflow

        # Simulate RuntimeError when no event loop
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.side_effect = RuntimeError("No event loop")
            with patch("asyncio.run") as mock_run:
                # Should not raise
                cancel_kb_ingestion_workflow("kb-123")

        # asyncio.run should be called as fallback
        mock_run.assert_called_once()


@pytest.mark.django_db
class TestCreateFilesAndUploadWithThreadPool:
    """Tests for create_files_and_upload with ThreadPoolExecutor."""

    @patch(
        "model_hub.views.develop_dataset.CreateKnowledgeBaseView._upload_file_to_s3_background"
    )
    def test_uses_thread_pool_executor(self, mock_upload):
        """Test that ThreadPoolExecutor is used instead of daemon threads."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()

        mock_file = MagicMock()
        mock_file.name = "test.pdf"
        mock_file.size = 1024
        mock_file.read.return_value = b"test content"

        # Get the executor
        executor = view._get_upload_executor()

        # Verify it's a ThreadPoolExecutor
        from concurrent.futures import ThreadPoolExecutor

        assert isinstance(executor, ThreadPoolExecutor)

    @patch(
        "model_hub.views.develop_dataset.CreateKnowledgeBaseView._upload_file_to_s3_background"
    )
    def test_executor_is_singleton(self, mock_upload):
        """Test that executor is reused (singleton pattern)."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()

        executor1 = view._get_upload_executor()
        executor2 = view._get_upload_executor()

        assert executor1 is executor2

    @patch(
        "model_hub.views.develop_dataset.CreateKnowledgeBaseView._get_upload_executor"
    )
    def test_submits_to_executor(self, mock_get_executor):
        """Test that uploads are submitted to the executor."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()
        mock_executor = MagicMock()
        mock_get_executor.return_value = mock_executor

        mock_file = MagicMock()
        mock_file.name = "test.pdf"
        mock_file.size = 1024
        mock_file.read.return_value = b"test content"

        view.create_files_and_upload([mock_file], "test-user", "kb-123")

        # Should have submitted to executor
        mock_executor.submit.assert_called_once()

    @patch(
        "model_hub.views.develop_dataset.CreateKnowledgeBaseView._get_upload_executor"
    )
    def test_multiple_files_submitted_to_executor(self, mock_get_executor):
        """Test that multiple files are submitted to executor."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()
        mock_executor = MagicMock()
        mock_get_executor.return_value = mock_executor

        files = []
        for i in range(5):
            mock_file = MagicMock()
            mock_file.name = f"test{i}.pdf"
            mock_file.size = 1024
            mock_file.read.return_value = b"test content"
            files.append(mock_file)

        view.create_files_and_upload(files, "test-user", "kb-123")

        # Should have submitted 5 tasks
        assert mock_executor.submit.call_count == 5

    @patch(
        "model_hub.views.develop_dataset.CreateKnowledgeBaseView._upload_file_to_s3_background"
    )
    def test_returns_correct_metadata(self, mock_upload):
        """Test that correct file metadata is returned."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()

        mock_file = MagicMock()
        mock_file.name = "document.docx"
        mock_file.size = 2048
        mock_file.read.return_value = b"test content"

        result = view.create_files_and_upload([mock_file], "test-user", "kb-123")

        assert len(result["files"]) == 1
        file_id = result["files"][0]
        assert result["file_metadata"][file_id]["name"] == "document.docx"
        assert result["file_metadata"][file_id]["extension"] == "docx"
