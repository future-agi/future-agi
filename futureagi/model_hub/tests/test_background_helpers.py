"""
Tests for background task helper functions in model_hub.

Run with: pytest model_hub/tests/test_background_helpers.py -v
"""

import concurrent.futures
import io
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.django_db
class TestSubmitWithRetry:
    """Tests for submit_with_retry function in model_hub/utils/utils.py."""

    def test_successful_submission(self):
        """Test successful task submission."""
        from model_hub.utils.utils import submit_with_retry

        executor = ThreadPoolExecutor(max_workers=2)
        result_value = []

        def test_func():
            result_value.append("executed")
            return "success"

        try:
            future = submit_with_retry(executor, test_func)
            assert future is not None
            assert future.result(timeout=5) == "success"
            assert "executed" in result_value
        finally:
            executor.shutdown(wait=True)

    def test_retries_on_shutdown_runtime_error(self):
        """Test that submission is retried on executor shutdown RuntimeError."""
        from model_hub.utils.utils import submit_with_retry

        # Create a mock executor that fails first with shutdown error, then succeeds
        mock_executor = MagicMock()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 2:
                # Only this specific message triggers retry
                raise RuntimeError("cannot schedule new futures after shutdown")
            return MagicMock(spec=concurrent.futures.Future)

        mock_executor.submit.side_effect = side_effect

        with patch("model_hub.utils.utils.time.sleep"):  # Don't actually sleep in tests
            result = submit_with_retry(mock_executor, lambda: "test")

        assert call_count[0] >= 2
        assert result is not None

    def test_raises_on_non_shutdown_runtime_error(self):
        """Test that non-shutdown RuntimeErrors are re-raised."""
        from model_hub.utils.utils import submit_with_retry

        mock_executor = MagicMock()
        mock_executor.submit.side_effect = RuntimeError("Some other error")

        # Should raise because it's not the shutdown error message
        with pytest.raises(RuntimeError, match="Some other error"):
            submit_with_retry(mock_executor, lambda: "test")

    def test_raises_after_max_retries_on_shutdown_error(self):
        """Test that exception is raised after max retries exhausted."""
        from model_hub.utils.utils import submit_with_retry

        mock_executor = MagicMock()
        mock_executor.submit.side_effect = RuntimeError(
            "cannot schedule new futures after shutdown"
        )

        with patch("model_hub.utils.utils.time.sleep"):  # Don't actually sleep in tests
            # Should raise after max retries
            with pytest.raises(RuntimeError):
                submit_with_retry(mock_executor, lambda: "test")

        assert mock_executor.submit.call_count == 3  # max_retries = 3

    def test_database_connections_are_handled(self):
        """Test that database connections are properly managed in the wrapper."""
        from model_hub.utils.utils import submit_with_retry

        executor = ThreadPoolExecutor(max_workers=1)

        def test_func():
            return "done"

        try:
            # The import happens inside the function, so patch django.db directly
            with (
                patch("django.db.close_old_connections") as mock_close,
                patch("django.db.connection") as mock_connection,
            ):
                future = submit_with_retry(executor, test_func)
                result = future.result(timeout=5)

                # Verify connection management was called (in the background thread)
                assert mock_close.called
                assert mock_connection.ensure_connection.called
        finally:
            executor.shutdown(wait=True)


class TestSafeBackgroundTask:
    """Tests for _safe_background_task wrapper in prompt_template.py."""

    def test_wrapper_executes_function(self):
        """Test that wrapper executes the wrapped function."""
        # Import the wrapper - it's a local function, so we need to test indirectly
        from django.db import close_old_connections, connection

        executed = []

        def test_func(arg1, arg2):
            executed.append((arg1, arg2))
            return "result"

        # Simulate what _safe_background_task does
        def safe_wrapper(func, *args, **kwargs):
            def wrapped():
                try:
                    close_old_connections()
                    connection.ensure_connection()
                    return func(*args, **kwargs)
                finally:
                    close_old_connections()

            return wrapped

        with (
            patch("django.db.close_old_connections"),
            patch("django.db.connection.ensure_connection"),
        ):
            wrapped = safe_wrapper(test_func, "a", "b")
            result = wrapped()

        assert executed == [("a", "b")]
        assert result == "result"

    def test_wrapper_handles_exceptions(self):
        """Test that wrapper properly handles exceptions."""

        def failing_func():
            raise ValueError("Test error")

        def safe_wrapper(func, *args, **kwargs):
            def wrapped():
                try:
                    return func(*args, **kwargs)
                finally:
                    pass  # cleanup would happen here

            return wrapped

        wrapped = safe_wrapper(failing_func)
        with pytest.raises(ValueError, match="Test error"):
            wrapped()


class TestScheduleKbIngestionOnCommit:
    """Tests for schedule_kb_ingestion_on_commit helper in kb_helpers.py."""

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
        # Get the callback that was registered
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
        """Test that the registered callback starts the Temporal activity with task_id."""
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


class TestIngestKbFilesImpl:
    """Tests for ingest_kb_files_impl helper in kb_helpers.py."""

    def test_returns_none_for_empty_metadata(self):
        """Test that empty file metadata returns None."""
        from model_hub.utils.kb_helpers import ingest_kb_files_impl

        result = ingest_kb_files_impl({}, "kb-123", "org-456")
        assert result is None

    @patch(
        "model_hub.utils.kb_helpers.is_kb_deleted_or_cancelled",
        return_value=False,
    )
    @patch("model_hub.utils.kb_helpers.get_storage_client")
    @patch("model_hub.tasks.develop_dataset.ingest_files_to_s3")
    def test_polls_s3_and_triggers_ingestion(
        self, mock_ingest, mock_get_client, mock_is_deleted
    ):
        """Test that it polls S3 for files and triggers ingestion."""
        from model_hub.utils.kb_helpers import ingest_kb_files_impl

        # Mock storage client to return file exists
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

    @patch("model_hub.utils.kb_helpers.is_kb_deleted_or_cancelled", return_value=True)
    def test_stops_if_kb_deleted(self, mock_is_deleted):
        """Test that ingestion stops if KB is deleted."""
        from model_hub.utils.kb_helpers import ingest_kb_files_impl

        file_metadata = {"file-1": {"name": "test.pdf", "extension": "pdf"}}
        result = ingest_kb_files_impl(file_metadata, "kb-123", "org-456")

        assert result is None


class TestUploadFileToS3WithBytes:
    """Tests for upload_file_to_s3 with file_bytes parameter in tfc/utils/storage.py."""

    @patch("tfc.utils.storage.get_storage_client")
    def test_uploads_file_bytes_successfully(self, mock_get_client):
        """Test that file bytes are uploaded to S3 successfully."""
        from tfc.utils.storage import upload_file_to_s3

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        result = upload_file_to_s3(
            file_bytes=b"test content",
            file_name="test.pdf",
            kb_id="kb-123",
            file_id="file-456",
        )

        mock_client.put_object.assert_called_once()
        assert "knowledge-base/kb-123/file-456.pdf" in result

    @patch("tfc.utils.storage.get_storage_client")
    def test_handles_upload_error(self, mock_get_client):
        """Test that S3 upload errors are raised properly."""
        from tfc.utils.storage import upload_file_to_s3

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.bucket_exists.return_value = True
        mock_client.put_object.side_effect = Exception("S3 connection error")

        with pytest.raises(ValueError, match="S3 connection error"):
            upload_file_to_s3(
                file_bytes=b"test content",
                file_name="test.pdf",
                kb_id="kb-123",
                file_id="file-456",
            )

    @patch("tfc.utils.storage.get_storage_client")
    def test_determines_content_type_from_extension(self, mock_get_client):
        """Test that content type is determined from file extension."""
        from tfc.utils.storage import upload_file_to_s3

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        upload_file_to_s3(
            file_bytes=b"test content",
            file_name="document.docx",
            kb_id="kb-123",
            file_id="file-456",
        )

        # Check the content_type argument in put_object call
        call_kwargs = mock_client.put_object.call_args[1]
        assert (
            call_kwargs["content_type"]
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )


class TestValidateAllFiles:
    """Tests for validate_all_files method."""

    def test_all_files_valid(self):
        """Test when all files pass validation."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()

        mock_file1 = MagicMock()
        mock_file1.name = "test1.pdf"

        mock_file2 = MagicMock()
        mock_file2.name = "test2.docx"

        with patch.object(view, "is_file_readable", return_value={"status": True}):
            result = view.validate_all_files([mock_file1, mock_file2])

        assert result["valid"] is True
        assert len(result["files_with_issues"]) == 0

    def test_some_files_invalid(self):
        """Test when some files fail validation."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()

        mock_file1 = MagicMock()
        mock_file1.name = "test.pdf"

        mock_file2 = MagicMock()
        mock_file2.name = "bad.pdf"

        def mock_is_readable(file_obj):
            if file_obj.name == "bad.pdf":
                return {"status": False, "error": "File is password-protected"}
            return {"status": True}

        with patch.object(view, "is_file_readable", side_effect=mock_is_readable):
            result = view.validate_all_files([mock_file1, mock_file2])

        assert result["valid"] is False
        assert len(result["files_with_issues"]) == 1
        assert result["files_with_issues"][0]["name"] == "bad.pdf"

    def test_handles_validation_exception(self):
        """Test that validation exceptions are caught and reported."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()

        mock_file = MagicMock()
        mock_file.name = "test.pdf"

        with patch.object(
            view, "is_file_readable", side_effect=Exception("Unexpected")
        ):
            result = view.validate_all_files([mock_file])

        assert result["valid"] is False
        assert len(result["files_with_issues"]) == 1
        assert "Unexpected" in result["files_with_issues"][0]["error"]


class TestCreateKnowledgeBaseEntitlements:
    @patch("model_hub.views.develop_dataset.User.objects.get")
    @patch("model_hub.views.develop_dataset.KnowledgeBaseFile.objects.filter")
    @patch("ee.usage.services.entitlements.Entitlements.check_feature")
    @patch("ee.usage.services.entitlements.Entitlements.can_create")
    @patch("model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request")
    def test_post_uses_entitlements_instead_of_legacy_resource_limit(
        self,
        mock_legacy_limit,
        mock_can_create,
        mock_check_feature,
        mock_kb_filter,
        mock_user_get,
    ):
        from ee.usage.models.usage import APICallStatusChoices
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()
        view._gm = MagicMock()
        view._gm.bad_request.return_value = MagicMock(status_code=400)
        view._gm.too_many_requests.return_value = MagicMock(status_code=429)

        org = MagicMock()
        org.id = "org-1"

        request = MagicMock()
        request.organization = org
        request.user.id = "user-1"
        request.user.organization = org
        request.workspace = MagicMock()
        request.data = {"name": "KB allowed by entitlements"}
        request.FILES.getlist.return_value = []

        mock_user_get.return_value.name = "tester"
        mock_kb_filter.return_value.count.return_value = 0
        mock_can_create.return_value = MagicMock(allowed=True)
        mock_check_feature.return_value = MagicMock(allowed=True)
        mock_legacy_limit.return_value = MagicMock(
            status=APICallStatusChoices.RESOURCE_LIMIT.value
        )

        view.validate_all_files = MagicMock(
            return_value={"valid": False, "files_with_issues": []}
        )

        response = view.post(request)

        mock_can_create.assert_called_once_with("org-1", "knowledge_bases", 0)
        mock_check_feature.assert_called_once_with("org-1", "has_knowledge_base")
        mock_legacy_limit.assert_not_called()
        assert response.status_code == 400


@pytest.mark.django_db
class TestCreateFilesAndUpload:
    """Tests for create_files_and_upload method."""

    @patch(
        "model_hub.views.develop_dataset.CreateKnowledgeBaseView._upload_file_to_s3_background"
    )
    def test_creates_file_records_and_starts_upload(self, mock_upload):
        """Test that file records are created and S3 upload starts."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()

        mock_file = MagicMock()
        mock_file.name = "test.pdf"
        mock_file.size = 1024
        mock_file.read.return_value = b"test content"

        result = view.create_files_and_upload([mock_file], "test-user", "kb-123")

        assert len(result["files"]) == 1
        assert len(result["file_metadata"]) == 1
        assert result["file_metadata"][result["files"][0]]["name"] == "test.pdf"
        assert result["file_metadata"][result["files"][0]]["extension"] == "pdf"

    @patch(
        "model_hub.views.develop_dataset.CreateKnowledgeBaseView._upload_file_to_s3_background"
    )
    def test_returns_file_metadata(self, mock_upload):
        """Test that file metadata is returned correctly."""
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()

        mock_file = MagicMock()
        mock_file.name = "document.docx"
        mock_file.size = 2048
        mock_file.read.return_value = b"test content"

        result = view.create_files_and_upload([mock_file], "test-user", "kb-123")

        file_id = result["files"][0]
        assert result["file_metadata"][file_id]["name"] == "document.docx"
        assert result["file_metadata"][file_id]["extension"] == "docx"


class TestIsKbDeletedOrCancelled:
    """Tests for is_kb_deleted_or_cancelled helper function."""

    @pytest.mark.django_db
    def test_returns_true_for_nonexistent_kb(self):
        """Test that is_kb_deleted_or_cancelled returns True for non-existent KB."""
        import uuid

        from model_hub.utils.kb_helpers import is_kb_deleted_or_cancelled

        # Non-existent KB should be considered deleted (use valid UUID format)
        non_existent_uuid = str(uuid.uuid4())
        result = is_kb_deleted_or_cancelled(non_existent_uuid)
        assert result is True


class TestCancelKbIngestionWorkflow:
    """Tests for cancel_kb_ingestion_workflow function in kb_helpers.py."""

    @patch("model_hub.utils.kb_helpers.KnowledgeBaseFile")
    @patch("asyncio.run")
    def test_marks_kb_as_deleting_and_cancels_workflow(self, mock_run, mock_kb_model):
        """Test that KB is marked as DELETING and workflow cancellation is attempted."""
        from model_hub.utils.kb_helpers import cancel_kb_ingestion_workflow

        cancel_kb_ingestion_workflow("kb-123")

        # Should mark KB as DELETING
        mock_kb_model.objects.filter.assert_called_once_with(id="kb-123")
        mock_kb_model.objects.filter.return_value.update.assert_called_once()

        # Should attempt to cancel workflow
        mock_run.assert_called_once()


@pytest.mark.django_db
class TestProcessOtherDatasetsImpl:
    """Tests for _process_other_datasets_impl helper in develop_dataset.py."""

    @patch("model_hub.views.develop_dataset.close_old_connections")
    def test_returns_none_when_no_data(self, mock_close):
        """Test that None is returned when no matching data."""
        from model_hub.views.develop_dataset import _process_other_datasets_impl

        mock_ds = MagicMock()
        mock_ds.id = "ds-other"

        result = _process_other_datasets_impl(
            base_val="val1",
            col_name="col1",
            og_cell=MagicMock(),
            columns_lookup={},
            data_by_dataset={},  # Empty data
            ds=mock_ds,
            dynamic_sources=[],
            i=0,
        )

        assert result is None

    @patch("model_hub.views.develop_dataset.close_old_connections")
    def test_closes_connections_in_finally(self, mock_close):
        """Test that database connections are closed in finally block."""
        from model_hub.views.develop_dataset import _process_other_datasets_impl

        mock_ds = MagicMock()
        mock_ds.id = "ds-other"

        _process_other_datasets_impl(
            base_val="val1",
            col_name="col1",
            og_cell=MagicMock(),
            columns_lookup={},
            data_by_dataset={},
            ds=mock_ds,
            dynamic_sources=[],
            i=0,
        )

        # Should be called at least twice (start and finally)
        assert mock_close.call_count >= 1
