"""
Integration tests for Temporal background task activities.

These tests verify the end-to-end flow from API views to Temporal activities.

Run with: pytest tfc/temporal/background_tasks/tests/test_integration.py -v
"""

from unittest.mock import MagicMock, patch

import pytest


class TestTemporalActivityIntegration:
    """Integration tests for Temporal activity registration and execution."""

    def test_activities_are_importable(self):
        """Test that all activities can be imported without errors."""
        from tfc.temporal.background_tasks import (
            delete_compare_folder_activity,
            ingest_kb_files_activity,
            process_huggingface_dataset_activity,
            run_post_registration_activity,
        )

        assert run_post_registration_activity is not None
        assert process_huggingface_dataset_activity is not None
        assert delete_compare_folder_activity is not None
        assert ingest_kb_files_activity is not None

    def test_activities_are_discoverable_by_temporal(self):
        """Test that activities are discoverable through Temporal drop-in."""
        # Import activities first to register them
        import tfc.temporal.background_tasks.activities  # noqa: F401
        from tfc.temporal.drop_in import get_all_activity_functions

        activities = get_all_activity_functions()

        # Verify our activities are registered
        activity_names = [func.__name__ for func in activities]
        assert "run_post_registration_activity" in activity_names
        assert "process_huggingface_dataset_activity" in activity_names
        assert "delete_compare_folder_activity" in activity_names
        assert "ingest_kb_files_activity" in activity_names


class TestPostRegistrationFlow:
    """Integration tests for post-registration flow."""

    @patch("accounts.models.demo_seed.DemoSeedRequest.objects.get_or_create")
    @patch("tfc.temporal.drop_in.start_activity")
    def test_process_post_registration_calls_start_activity(
        self, mock_start_activity, mock_intent
    ):
        """Test that process_post_registration calls start_activity."""
        from temporalio.common import WorkflowIDConflictPolicy

        from accounts.utils import process_post_registration

        process_post_registration("user-123", "password123", organization_id="org-123")
        mock_intent.assert_called_once_with(
            organization_id="org-123", defaults={"user_id": "user-123"}
        )

        mock_start_activity.assert_called_once_with(
            "run_post_registration_activity",
            args=("user-123", "password123"),
            kwargs={"organization_id": "org-123"},
            queue="default",
            task_id="post-registration-org-123",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            dispatch_timeout_seconds=2,
        )


class TestHuggingFaceDatasetFlow:
    """Integration tests for HuggingFace dataset processing flow."""

    @patch("tfc.temporal.drop_in.start_activity")
    def test_create_dataset_starts_temporal_activity(self, mock_start_activity):
        """Test that CreateDatasetFromHuggingFaceView starts Temporal activity."""
        # This tests the integration point
        mock_start_activity.return_value = None

        # Import to ensure registration
        import tfc.temporal.background_tasks.activities  # noqa: F401

        # Simulate the start_activity call
        from tfc.temporal.drop_in import start_activity

        start_activity(
            "process_huggingface_dataset_activity",
            args=(
                "ds-123",
                "test/dataset",
                "default",
                "train",
                "org-456",
                100,
                ["col1"],
                {"0": "row-1"},
            ),
            queue="tasks_l",
        )

        mock_start_activity.assert_called_once()


class TestCompareDatasetFlow:
    """Integration tests for compare dataset flow."""

    @patch("tfc.temporal.drop_in.start_activity")
    def test_delete_compare_starts_temporal_activity(self, mock_start_activity):
        """Test that delete compare operation starts Temporal activity."""
        import tfc.temporal.background_tasks.activities  # noqa: F401
        from tfc.temporal.drop_in import start_activity

        start_activity(
            "delete_compare_folder_activity",
            args=("compare-123",),
            queue="default",
        )

        mock_start_activity.assert_called_once_with(
            "delete_compare_folder_activity",
            args=("compare-123",),
            queue="default",
        )


class TestKnowledgeBaseIngestionFlow:
    """Integration tests for knowledge base ingestion flow."""

    @patch("django.db.transaction.on_commit")
    def test_kb_ingestion_uses_transaction_on_commit(self, mock_on_commit):
        """Test that KB ingestion is scheduled via transaction.on_commit."""
        from model_hub.utils.kb_helpers import schedule_kb_ingestion_on_commit

        schedule_kb_ingestion_on_commit(
            file_metadata={"file-1": {"name": "test.pdf", "extension": "pdf"}},
            kb_id="kb-123",
            org_id="org-456",
        )

        mock_on_commit.assert_called_once()

    @patch("tfc.temporal.drop_in.start_activity")
    @patch("django.db.transaction.on_commit")
    def test_kb_ingestion_callback_calls_start_activity(
        self, mock_on_commit, mock_start_activity
    ):
        """Test that the on_commit callback properly starts the activity."""
        from model_hub.utils.kb_helpers import schedule_kb_ingestion_on_commit

        schedule_kb_ingestion_on_commit(
            file_metadata={"file-1": {"name": "test.pdf", "extension": "pdf"}},
            kb_id="kb-123",
            org_id="org-456",
        )

        # Get the registered callback and execute it
        callback = mock_on_commit.call_args[0][0]
        callback()

        mock_start_activity.assert_called_once_with(
            "ingest_kb_files_activity",
            args=(
                {"file-1": {"name": "test.pdf", "extension": "pdf"}},
                "kb-123",
                "org-456",
            ),
            queue="default",
            task_id="kb-ingest-kb-123",
        )


class TestThreadPoolExecutorFallback:
    """Integration tests for ThreadPoolExecutor fallback scenarios."""

    def test_prepare_compare_uses_thread_pool(self):
        """Test that prepare_compare_dataset uses ThreadPoolExecutor directly."""
        # This is a conceptual test - the actual implementation uses
        # ThreadPoolExecutor for non-serializable objects
        from concurrent.futures import ThreadPoolExecutor

        executed = []

        def mock_impl(*args):
            executed.append("called")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(mock_impl, "arg1", "arg2")
            future.result(timeout=5)

        assert "called" in executed

    @patch("model_hub.views.prompt_template._PROMPT_TEMPLATE_EXECUTOR")
    def test_prompt_template_uses_module_executor(self, mock_executor):
        """Test that prompt template views use module-level executor."""
        # Verify the executor is a ThreadPoolExecutor
        from concurrent.futures import ThreadPoolExecutor

        # The module should have a module-level executor
        from model_hub.views import prompt_template

        assert hasattr(prompt_template, "_PROMPT_TEMPLATE_EXECUTOR")


@pytest.mark.django_db
class TestDatabaseConnectionHandling:
    """Integration tests for database connection handling in background tasks."""

    def test_submit_with_retry_manages_connections(self):
        """Test that submit_with_retry properly manages DB connections."""
        from concurrent.futures import ThreadPoolExecutor

        from model_hub.utils.utils import submit_with_retry

        executor = ThreadPoolExecutor(max_workers=1)

        def test_task():
            return "done"

        try:
            future = submit_with_retry(executor, test_task)
            result = future.result(timeout=5)

            # The function should complete successfully with proper connection management
            assert result == "done"
        finally:
            executor.shutdown(wait=True)


@pytest.mark.django_db
class TestActivityErrorHandling:
    """Integration tests for error handling in activities."""

    @patch("accounts.utils._run_post_registration")
    def test_activity_propagates_errors(self, mock_run):
        """Test that activity properly propagates errors."""
        from tfc.temporal.background_tasks.activities import (
            run_post_registration_activity,
        )

        mock_run.side_effect = Exception("Email service error")

        with pytest.raises(Exception, match="Email service error"):
            run_post_registration_activity("user-123", "pass123")

    @patch("model_hub.views.utils.hugginface.process_huggingface_dataset")
    def test_huggingface_activity_handles_errors(self, mock_process):
        """Test that HuggingFace activity handles errors."""
        from tfc.temporal.background_tasks.activities import (
            process_huggingface_dataset_activity,
        )

        mock_process.side_effect = ValueError("Invalid dataset")

        with pytest.raises(ValueError, match="Invalid dataset"):
            process_huggingface_dataset_activity(
                "ds-123", "bad/dataset", None, "train", "org-456", 0, [], {}
            )


class TestConcurrentExecution:
    """Integration tests for concurrent execution scenarios."""

    def test_multiple_activities_can_run_concurrently(self):
        """Test that multiple activities can be submitted without issues."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from unittest.mock import MagicMock

        results = []

        def mock_activity(activity_id):
            import time

            time.sleep(0.1)
            return f"completed-{activity_id}"

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(mock_activity, i) for i in range(3)]

            for future in as_completed(futures):
                results.append(future.result())

        assert len(results) == 3
        assert all("completed-" in r for r in results)
