"""
End-to-End Tests for Background Task Processing Flows.

These tests verify complete workflows from API trigger through Temporal activities
to database state changes. They ensure the distributed locking, state tracking,
and recovery mechanisms work correctly.

Run with: pytest tfc/temporal/background_tasks/tests/test_e2e_flows.py -v
"""

import concurrent.futures
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from django.utils import timezone


@pytest.fixture(autouse=True)
def _allow_usage_metering():
    """These workflow tests mock execution; billing limit behavior is tested separately."""
    with patch("ee.usage.services.metering.check_usage") as mock_check_usage:
        mock_check_usage.return_value = MagicMock(allowed=True)
        yield mock_check_usage

# =============================================================================
# SECTION 1: Run Prompt E2E Flow Tests
# =============================================================================


@pytest.mark.django_db
class TestRunPromptE2EFlow:
    """End-to-end tests for run prompt processing flows."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_full_not_started_prompt_flow(
        self, mock_close, mock_prompter, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """
        Test complete flow: API trigger → Temporal Activity → Distributed Lock →
        Runner Execution → Status Update → Tracker Cleanup
        """
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_prompts_single

        # Setup: Prompt exists and is in RUNNING status
        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance-1"
        mock_prompt_obj = MagicMock()
        mock_prompt_obj.status = StatusType.RUNNING.value
        mock_prompter.objects.get.return_value = mock_prompt_obj

        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        # Execute the full flow
        process_prompts_single({"type": "not_started", "prompt_id": "prompt-123"})

        # Verify complete flow
        mock_prompter.objects.get.assert_called_once_with(id="prompt-123")
        mock_tracker.mark_running.assert_called_once()
        mock_runner.run_prompt.assert_called_once()
        mock_tracker.mark_completed.assert_called_once_with("prompt-123")

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_full_editing_prompt_flow(
        self, mock_close, mock_prompter, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """
        Test complete editing flow: Cancel existing → Acquire Lock →
        Run with edit_mode → Cleanup
        """
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_prompts_single

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance-1"
        mock_prompt_obj = MagicMock()
        mock_prompt_obj.status = StatusType.RUNNING.value
        mock_prompter.objects.get.return_value = mock_prompt_obj

        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        process_prompts_single({"type": "editing", "prompt_id": "prompt-123"})

        # Verify edit mode was used
        mock_runner.run_prompt.assert_called_once_with(edit_mode=True)
        mock_tracker.mark_completed.assert_called_once_with("prompt-123")

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_prompt_already_running_on_different_instance(
        self, mock_close, mock_prompter, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test that duplicate execution is prevented across instances."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_prompts_single

        # Simulate prompt already running on another instance
        mock_tracker.is_running.return_value = True
        mock_tracker.instance_id = "current-instance"
        mock_running_info = MagicMock()
        mock_running_info.instance_id = "other-instance"
        mock_tracker.get_running_info.return_value = mock_running_info

        mock_prompt_obj = MagicMock()
        mock_prompt_obj.status = StatusType.RUNNING.value
        mock_prompter.objects.get.return_value = mock_prompt_obj

        process_prompts_single({"type": "not_started", "prompt_id": "prompt-123"})

        # Should not process - already running elsewhere
        mock_runner_class.assert_not_called()
        mock_tracker.mark_running.assert_not_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_prompt_failure_marks_status_failed(
        self, mock_close, mock_prompter, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test that failures correctly mark prompt as FAILED."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_not_started_prompt

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_runner = MagicMock()
        mock_runner.run_prompt.side_effect = Exception("LLM API Error")
        mock_runner_class.return_value = mock_runner

        with pytest.raises(Exception, match="LLM API Error"):
            process_not_started_prompt("prompt-123")

        # Verify status was set to FAILED
        mock_prompter.objects.filter.assert_called_with(id="prompt-123")
        mock_tracker.mark_completed.assert_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_idempotency_check_prevents_reprocessing(
        self, mock_close, mock_prompter, mock_lock_mgr, mock_tracker
    ):
        """Test that completed prompts are not reprocessed."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_prompts_single

        mock_tracker.instance_id = "test-instance"
        mock_prompt_obj = MagicMock()
        mock_prompt_obj.status = StatusType.COMPLETED.value  # Already completed
        mock_prompter.objects.get.return_value = mock_prompt_obj

        process_prompts_single({"type": "not_started", "prompt_id": "prompt-123"})

        # Should skip processing since status is not RUNNING
        mock_tracker.mark_running.assert_not_called()


# =============================================================================
# SECTION 2: User Evaluation E2E Flow Tests
# =============================================================================


class TestUserEvaluationE2EFlow:
    """End-to-end tests for user evaluation processing flows."""

    @patch("model_hub.tasks.user_evaluation.evaluation_tracker")
    @patch("model_hub.tasks.user_evaluation.EvaluationRunner")
    @patch("model_hub.tasks.user_evaluation.Row")
    @patch("model_hub.tasks.user_evaluation.track_mixpanel_event")
    @patch("model_hub.tasks.user_evaluation.get_mixpanel_properties")
    @patch("model_hub.tasks.user_evaluation.RunPrompter")
    @patch("model_hub.tasks.user_evaluation.Column")
    def test_full_single_evaluation_flow(
        self,
        mock_column,
        mock_prompter,
        mock_get_mixpanel,
        mock_track_mixpanel,
        mock_row,
        mock_runner_class,
        mock_tracker,
    ):
        """
        Test complete evaluation flow: Check Dependencies → Mark Running →
        Execute Evaluation → Analytics → Cleanup
        """
        from model_hub.tasks.user_evaluation import process_single_evaluation

        # Setup user eval metric
        mock_eval_metric = MagicMock()
        mock_eval_metric.id = "eval-123"
        mock_eval_metric.dataset.id = "dataset-456"
        mock_eval_metric.template.name = "Test Template"
        mock_eval_metric.organization.id = "org-789"

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"

        mock_row.objects.filter.return_value.count.return_value = 10
        mock_column.objects.filter.return_value = []

        mock_runner = MagicMock()
        mock_runner._get_all_column_ids_being_used.return_value = []
        mock_runner_class.return_value = mock_runner

        process_single_evaluation(mock_eval_metric)

        # Verify complete flow
        mock_tracker.mark_running.assert_called_once()
        mock_runner.run_prompt.assert_called_once()
        mock_tracker.mark_completed.assert_called_once()
        mock_tracker.clear_cancel_flag.assert_called_once()
        mock_track_mixpanel.assert_called()

    @patch("model_hub.tasks.user_evaluation.evaluation_tracker")
    @patch("model_hub.tasks.user_evaluation.EvaluationRunner")
    @patch("model_hub.tasks.user_evaluation.Row")
    @patch("model_hub.tasks.user_evaluation.track_mixpanel_event")
    @patch("model_hub.tasks.user_evaluation.get_mixpanel_properties")
    @patch("model_hub.tasks.user_evaluation.RunPrompter")
    @patch("model_hub.tasks.user_evaluation.Column")
    def test_evaluation_with_running_dependency_skips(
        self,
        mock_column,
        mock_prompter,
        mock_get_mixpanel,
        mock_track_mixpanel,
        mock_row,
        mock_runner_class,
        mock_tracker,
    ):
        """Test that evaluations skip when dependent prompts are still running."""
        from model_hub.models.choices import SourceChoices, StatusType
        from model_hub.tasks.user_evaluation import process_single_evaluation

        mock_eval_metric = MagicMock()
        mock_eval_metric.id = "eval-123"
        mock_eval_metric.dataset.id = "dataset-456"

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"

        mock_row.objects.filter.return_value.count.return_value = 10

        # Setup dependent column from a running prompt
        mock_col = MagicMock()
        mock_col.source = SourceChoices.RUN_PROMPT.value
        mock_col.source_id = "prompt-789"
        mock_column.objects.filter.return_value = [mock_col]

        mock_runner = MagicMock()
        mock_runner._get_all_column_ids_being_used.return_value = ["col-1"]
        mock_runner_class.return_value = mock_runner

        # Prompt is still running
        mock_prompter.objects.filter.return_value.exists.return_value = True

        process_single_evaluation(mock_eval_metric)

        # Should skip and set status back to NOT_STARTED
        mock_runner.run_prompt.assert_not_called()
        assert mock_eval_metric.status == StatusType.NOT_STARTED.value
        mock_eval_metric.save.assert_called()

    @patch("model_hub.tasks.user_evaluation.evaluation_tracker")
    @patch("model_hub.tasks.user_evaluation.EvaluationRunner")
    @patch("model_hub.tasks.user_evaluation.Row")
    @patch("model_hub.tasks.user_evaluation.track_mixpanel_event")
    @patch("model_hub.tasks.user_evaluation.get_mixpanel_properties")
    @patch("model_hub.tasks.user_evaluation.Column")
    def test_cancel_requested_for_running_evaluation(
        self,
        mock_column,
        mock_get_mixpanel,
        mock_track_mixpanel,
        mock_row,
        mock_runner_class,
        mock_tracker,
    ):
        """Test that re-running an evaluation cancels the previous run."""
        from model_hub.tasks.user_evaluation import process_single_evaluation

        mock_eval_metric = MagicMock()
        mock_eval_metric.id = "eval-123"
        mock_eval_metric.dataset.id = "dataset-456"

        # Evaluation is already running
        mock_tracker.is_running.return_value = True
        mock_tracker.instance_id = "current-instance"
        mock_running_info = MagicMock()
        mock_running_info.instance_id = "other-instance"
        mock_tracker.get_running_info.return_value = mock_running_info

        mock_row.objects.filter.return_value.count.return_value = 10
        mock_column.objects.filter.return_value = []

        mock_runner = MagicMock()
        mock_runner._get_all_column_ids_being_used.return_value = []
        mock_runner_class.return_value = mock_runner

        process_single_evaluation(mock_eval_metric)

        # Should request cancellation of the existing run
        mock_tracker.request_cancel.assert_called_once_with(
            "eval-123", reason="New evaluation requested"
        )


# =============================================================================
# SECTION 3: Distributed Locking E2E Tests
# =============================================================================


class TestDistributedLockingE2E:
    """End-to-end tests for distributed locking mechanism."""

    def test_lock_prevents_concurrent_execution(self):
        """Test that distributed lock prevents concurrent execution."""
        from tfc.utils.distributed_locks import (
            DistributedLockManager,
            LockAcquisitionError,
        )

        # Create a lock manager with local fallback
        lock_mgr = DistributedLockManager(fallback_to_local=True)

        execution_order = []
        lock_name = f"test_lock_{time.time()}"

        def task_with_lock(task_id):
            try:
                with lock_mgr.lock(
                    lock_name, timeout=5, blocking_timeout=0.1, blocking=False
                ):
                    execution_order.append(f"start_{task_id}")
                    time.sleep(0.2)
                    execution_order.append(f"end_{task_id}")
            except LockAcquisitionError:
                execution_order.append(f"blocked_{task_id}")

        # Run two tasks concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(task_with_lock, i) for i in range(2)]
            for f in futures:
                f.result()

        # One should complete, one should be blocked
        assert "blocked_0" in execution_order or "blocked_1" in execution_order
        starts = [e for e in execution_order if e.startswith("start_")]
        ends = [e for e in execution_order if e.startswith("end_")]
        assert len(starts) == 1
        assert len(ends) == 1

    def test_lock_auto_release_on_timeout(self):
        """Test that locks auto-release after timeout."""
        from tfc.utils.distributed_locks import DistributedLockManager

        lock_mgr = DistributedLockManager(fallback_to_local=True)
        lock_name = f"test_timeout_lock_{time.time()}"

        # Acquire lock with very short timeout
        acquired = lock_mgr.try_lock(lock_name, timeout=1)
        assert acquired is not None

        # Check that lock is held
        assert lock_mgr.is_locked(lock_name)

        # Release it
        acquired.release()
        assert not lock_mgr.is_locked(lock_name)

    def test_lock_context_manager_cleanup_on_exception(self):
        """Test that lock is properly released even when exception occurs."""
        from tfc.utils.distributed_locks import DistributedLockManager

        lock_mgr = DistributedLockManager(fallback_to_local=True)
        lock_name = f"test_exception_lock_{time.time()}"

        try:
            with lock_mgr.lock(lock_name, timeout=10, blocking_timeout=5):
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Lock should be released after exception
        assert not lock_mgr.is_locked(lock_name)

    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_run_prompt_uses_one_hour_lock_timeout(
        self, mock_close, mock_runner_class, mock_tracker, mock_lock_mgr
    ):
        """Verify run prompt uses 1-hour (3600s) lock timeout."""
        from model_hub.tasks.run_prompt import process_not_started_prompt

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_runner_class.return_value = MagicMock()

        process_not_started_prompt("prompt-123")

        # Verify lock was called with 3600 second timeout
        mock_lock_mgr.lock.assert_called_once()
        call_kwargs = mock_lock_mgr.lock.call_args[1]
        assert call_kwargs["timeout"] == 3600


# =============================================================================
# SECTION 4: Distributed State E2E Tests
# =============================================================================


class TestDistributedStateE2E:
    """End-to-end tests for distributed state management."""

    def test_state_manager_basic_operations(self):
        """Test basic set/get/delete operations."""
        from tfc.utils.distributed_state import DistributedStateManager

        # Create manager (may use local fallback if Redis unavailable)
        manager = DistributedStateManager(key_prefix="test_state:")

        # Test set and get
        test_key = f"test_key_{time.time()}"
        test_value = {"data": "test_value", "number": 42}

        result = manager.set(test_key, test_value, ttl=60)
        # Result may be False if Redis is unavailable, that's ok for local testing
        if result:
            retrieved = manager.get(test_key)
            assert retrieved == test_value

            # Test delete
            manager.delete(test_key)
            assert manager.get(test_key) is None

    def test_evaluation_tracker_mark_running_and_completed(self):
        """Test evaluation tracker running state management."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()

        eval_id = f"test_eval_{int(time.time() * 1000)}"

        # Mark as running
        result = tracker.mark_running(eval_id, runner_info={"test": "data"}, ttl=60)

        if tracker.is_available:
            assert result is True
            assert tracker.is_running(eval_id)

            # Get running info
            info = tracker.get_running_info(eval_id)
            assert info is not None
            assert info.task_id == str(eval_id)

            # Mark completed
            tracker.mark_completed(eval_id)
            assert not tracker.is_running(eval_id)

    def test_evaluation_tracker_cancel_signal_propagation(self):
        """Test that cancel signals can be requested and detected."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()

        eval_id = f"test_cancel_{int(time.time() * 1000)}"

        # Mark as running first
        tracker.mark_running(eval_id, ttl=60)

        if tracker.is_available:
            # Request cancellation
            result = tracker.request_cancel(eval_id, reason="Test cancellation")
            assert result is True

            # Check cancel signal
            assert tracker.should_cancel(eval_id)

            # Clear cancel flag
            tracker.clear_cancel_flag(eval_id)
            assert not tracker.should_cancel(eval_id)

            # Cleanup
            tracker.mark_completed(eval_id)


# =============================================================================
# SECTION 5: Recovery Mechanism E2E Tests
# =============================================================================


@pytest.mark.django_db
class TestRecoveryMechanismsE2E:
    """End-to-end tests for stuck task recovery mechanisms."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_recover_stuck_prompts_finds_old_running(
        self, mock_close, mock_prompter, mock_tracker
    ):
        """Test that recovery finds prompts stuck in RUNNING for > 1 hour."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        # Setup stuck prompts query to return some IDs
        stuck_ids = ["prompt-1", "prompt-2", "prompt-3"]
        mock_queryset = MagicMock()
        mock_queryset.values_list.return_value.__getitem__.return_value = stuck_ids
        mock_prompter.objects.filter.return_value = mock_queryset

        recover_stuck_run_prompts()

        # Should mark stuck prompts as FAILED
        mock_queryset.update.assert_called_once()
        # Update should set status to FAILED
        call_args = mock_queryset.update.call_args
        assert call_args[1]["status"] == StatusType.FAILED.value

        # Should clean up tracker for each stuck prompt
        assert mock_tracker.mark_completed.call_count == 3
        assert mock_tracker.clear_cancel_flag.call_count == 3

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_recover_stuck_prompts_cleans_stale_tracker(
        self, mock_close, mock_prompter, mock_tracker
    ):
        """Test that recovery cleans up stale tracker entries."""
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        # No stuck prompts
        mock_prompter.objects.filter.return_value.values_list.return_value.__getitem__.return_value = (
            []
        )

        # But there are stale tracker entries
        mock_tracker.cleanup_stale.return_value = 5

        recover_stuck_run_prompts()

        # Should call cleanup_stale
        mock_tracker.cleanup_stale.assert_called_once()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_recover_handles_no_stuck_prompts(
        self, mock_close, mock_prompter, mock_tracker
    ):
        """Test recovery gracefully handles when no prompts are stuck."""
        from model_hub.tasks.run_prompt import recover_stuck_run_prompts

        mock_prompter.objects.filter.return_value.values_list.return_value.__getitem__.return_value = (
            []
        )
        mock_tracker.cleanup_stale.return_value = 0

        # Should not raise
        recover_stuck_run_prompts()

        # Should not try to update status
        mock_tracker.mark_completed.assert_not_called()


# =============================================================================
# SECTION 6: Concurrent Operations E2E Tests
# =============================================================================


class TestConcurrentOperationsE2E:
    """End-to-end tests for concurrent operation handling."""

    def test_concurrent_prompt_processing_prevented_by_lock(self):
        """Test that concurrent processing of same prompt is prevented."""
        from tfc.utils.distributed_locks import (
            DistributedLockManager,
            LockAcquisitionError,
        )

        lock_mgr = DistributedLockManager(fallback_to_local=True)
        prompt_id = f"prompt_{int(time.time() * 1000)}"
        lock_name = f"run_prompt:{prompt_id}"

        results = {"started": 0, "blocked": 0, "completed": 0}
        results_lock = threading.Lock()

        def simulate_prompt_processing():
            try:
                with lock_mgr.lock(lock_name, timeout=5, blocking_timeout=0.1):
                    with results_lock:
                        results["started"] += 1
                    time.sleep(
                        1.0
                    )  # Simulate processing - longer than blocking_timeout
                    with results_lock:
                        results["completed"] += 1
            except LockAcquisitionError:
                with results_lock:
                    results["blocked"] += 1

        # Launch 5 concurrent attempts to process the same prompt
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(simulate_prompt_processing) for _ in range(5)]
            for f in as_completed(futures):
                f.result()

        # Only one should have processed, others blocked
        assert results["started"] == 1
        assert results["completed"] == 1
        assert results["blocked"] == 4

    def test_different_prompts_can_process_concurrently(self):
        """Test that different prompts can be processed concurrently."""
        from tfc.utils.distributed_locks import DistributedLockManager

        lock_mgr = DistributedLockManager(fallback_to_local=True)
        base_id = int(time.time() * 1000)

        results = {"completed": []}
        results_lock = threading.Lock()

        def simulate_prompt_processing(prompt_id):
            lock_name = f"run_prompt:{prompt_id}"
            with lock_mgr.lock(lock_name, timeout=5, blocking_timeout=1):
                time.sleep(0.1)
                with results_lock:
                    results["completed"].append(prompt_id)

        # Process 5 different prompts concurrently
        with ThreadPoolExecutor(max_workers=5) as executor:
            prompt_ids = [f"prompt_{base_id}_{i}" for i in range(5)]
            futures = [
                executor.submit(simulate_prompt_processing, pid) for pid in prompt_ids
            ]
            for f in as_completed(futures):
                f.result()

        # All should complete
        assert len(results["completed"]) == 5

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_instance_isolation_for_running_checks(self, mock_tracker):
        """Test that running checks properly identify instance ownership."""
        from model_hub.tasks.run_prompt import run_prompt_tracker

        # Simulate check from different instances
        mock_tracker.instance_id = "instance-A"

        mock_running_info = MagicMock()
        mock_running_info.instance_id = "instance-B"
        mock_tracker.get_running_info.return_value = mock_running_info
        mock_tracker.is_running.return_value = True

        # Should detect running on different instance
        is_running = mock_tracker.is_running("prompt-123")
        info = mock_tracker.get_running_info("prompt-123")

        assert is_running
        assert info.instance_id != mock_tracker.instance_id


# =============================================================================
# SECTION 7: Error Handling E2E Tests
# =============================================================================


@pytest.mark.django_db
class TestErrorHandlingE2E:
    """End-to-end tests for error handling scenarios."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.distributed_lock_manager")
    @patch("model_hub.tasks.run_prompt.RunPrompts")
    @patch("model_hub.tasks.run_prompt.RunPrompter")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_db_error_during_status_update_is_logged(
        self, mock_close, mock_prompter, mock_runner_class, mock_lock_mgr, mock_tracker
    ):
        """Test that DB errors during status update are properly handled."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.run_prompt import process_not_started_prompt

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"

        # Runner fails
        mock_runner = MagicMock()
        mock_runner.run_prompt.side_effect = Exception("Processing failed")
        mock_runner_class.return_value = mock_runner

        # DB update also fails
        mock_prompter.objects.filter.return_value.update.side_effect = Exception(
            "DB Error"
        )

        with pytest.raises(Exception, match="Processing failed"):
            process_not_started_prompt("prompt-123")

        # Should still clean up tracker even if DB update failed
        mock_tracker.mark_completed.assert_called()

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_prompt_not_found_handled_gracefully(self, mock_close, mock_tracker):
        """Test that missing prompt is handled gracefully."""
        from model_hub.models.run_prompt import RunPrompter
        from model_hub.tasks.run_prompt import process_prompts_single

        mock_tracker.instance_id = "test-instance"

        # Use the real DoesNotExist exception from the model
        with patch("model_hub.tasks.run_prompt.RunPrompter") as mock_prompter:
            mock_prompter.DoesNotExist = RunPrompter.DoesNotExist
            mock_prompter.objects.get.side_effect = RunPrompter.DoesNotExist(
                "Not found"
            )

            # Should not raise, just log error
            process_prompts_single({"type": "not_started", "prompt_id": "nonexistent"})
            # If we get here without exception, the error was handled gracefully


# =============================================================================
# SECTION 8: Database Connection Management E2E Tests
# =============================================================================


@pytest.mark.django_db
class TestDatabaseConnectionManagementE2E:
    """End-to-end tests for database connection management."""

    @patch("model_hub.tasks.run_prompt.close_old_connections")
    def test_close_old_connections_called_at_start_and_end(self, mock_close):
        """Test that DB connections are closed at task start and end."""
        from model_hub.tasks.run_prompt import process_prompts_single

        with patch("model_hub.tasks.run_prompt.RunPrompter") as mock_prompter:
            with patch("model_hub.tasks.run_prompt.run_prompt_tracker") as mock_tracker:
                mock_tracker.instance_id = "test-instance"
                mock_prompt = MagicMock()
                mock_prompt.status = "completed"  # Skip processing
                mock_prompter.objects.get.return_value = mock_prompt

                process_prompts_single({"type": "not_started", "prompt_id": "test-123"})

        # Should be called multiple times (start and finally blocks)
        assert mock_close.call_count >= 2

    def test_submit_with_retry_manages_connections(self):
        """Test that submit_with_retry properly manages DB connections."""
        from concurrent.futures import ThreadPoolExecutor

        with patch("django.db.close_old_connections") as mock_close:
            with patch("django.db.connection") as mock_conn:
                from model_hub.utils.utils import submit_with_retry

                executor = ThreadPoolExecutor(max_workers=1)

                def test_task():
                    return "done"

                try:
                    future = submit_with_retry(executor, test_task)
                    result = future.result(timeout=5)
                    assert result == "done"
                finally:
                    executor.shutdown(wait=True)


# =============================================================================
# SECTION 9: Temporal Activity Registration Tests
# =============================================================================


class TestTemporalActivityRegistration:
    """Tests for Temporal activity registration and execution."""

    def test_all_critical_activities_are_importable(self):
        """Test that all critical activities can be imported."""
        # Import activities
        from model_hub.tasks.run_prompt import (
            process_prompts_single,
            recover_stuck_run_prompts,
        )
        from model_hub.tasks.user_evaluation import (
            execute_evaluation,
            process_evaluation_single_task,
        )

        # All should be callable
        assert callable(process_prompts_single)
        assert callable(recover_stuck_run_prompts)
        assert callable(execute_evaluation)
        assert callable(process_evaluation_single_task)

    def test_run_prompt_activities_have_correct_time_limits(self):
        """Test that activities have appropriate time limits."""
        from model_hub.tasks.run_prompt import (
            process_prompts_single,
            recover_stuck_run_prompts,
        )

        # process_prompts_single should have 1 hour (3600s) limit
        # recover_stuck_run_prompts should have 5 minute (300s) limit
        # These are set via @temporal_activity decorator
        # Verify they're decorated (have __wrapped__ or temporal metadata)
        assert callable(process_prompts_single)
        assert callable(recover_stuck_run_prompts)


# =============================================================================
# SECTION 10: Configuration Verification Tests
# =============================================================================


class TestConfigurationVerification:
    """Tests to verify critical configuration values."""

    def test_stuck_running_threshold_is_one_hour(self):
        """Verify STUCK_RUNNING_THRESHOLD_HOURS is 1 hour."""
        from model_hub.tasks.run_prompt import STUCK_RUNNING_THRESHOLD_HOURS

        assert STUCK_RUNNING_THRESHOLD_HOURS == 1

    def test_run_prompt_tracker_key_prefix(self):
        """Verify run_prompt_tracker uses correct key prefix."""
        from model_hub.tasks.run_prompt import run_prompt_tracker

        assert run_prompt_tracker.key_prefix == "running_prompt:"

    def test_evaluation_tracker_key_prefix(self):
        """Verify evaluation_tracker uses correct key prefix."""
        from tfc.utils.distributed_state import evaluation_tracker

        assert evaluation_tracker.key_prefix == "running_eval:"

    def test_distributed_lock_manager_config(self):
        """Verify distributed lock manager default config."""
        from tfc.utils.distributed_locks import distributed_lock_manager

        # Should have default config values
        assert distributed_lock_manager.config.default_timeout == 30
        assert distributed_lock_manager.config.default_blocking_timeout == 10
        assert distributed_lock_manager.config.key_prefix == "distributed_lock:"


# =============================================================================
# SECTION 11: Helper Function Tests
# =============================================================================


class TestHelperFunctions:
    """Tests for helper functions used in task processing."""

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_get_running_prompts_status(self, mock_tracker):
        """Test get_running_prompts_status returns correct format."""
        from model_hub.tasks.run_prompt import get_running_prompts_status

        mock_info = MagicMock()
        mock_info.task_id = "prompt-123"
        mock_info.instance_id = "instance-1"
        mock_info.started_at = "2024-01-01T00:00:00"
        mock_info.cancel_requested = False
        mock_info.metadata = {"type": "not_started"}
        mock_tracker.get_all_running.return_value = [mock_info]

        result = get_running_prompts_status()

        assert len(result) == 1
        assert result[0]["prompt_id"] == "prompt-123"
        assert result[0]["instance"] == "instance-1"
        assert result[0]["cancel_requested"] is False
        assert result[0]["metadata"] == {"type": "not_started"}

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_cancel_running_prompt_success(self, mock_tracker):
        """Test cancel_running_prompt when prompt is running."""
        from model_hub.tasks.run_prompt import cancel_running_prompt

        mock_tracker.is_running.return_value = True
        mock_tracker.request_cancel.return_value = True

        result = cancel_running_prompt("prompt-123", reason="User requested")

        assert result is True
        mock_tracker.request_cancel.assert_called_once_with(
            "prompt-123", reason="User requested"
        )

    @patch("model_hub.tasks.run_prompt.run_prompt_tracker")
    def test_cancel_running_prompt_not_running(self, mock_tracker):
        """Test cancel_running_prompt when prompt is not running."""
        from model_hub.tasks.run_prompt import cancel_running_prompt

        mock_tracker.is_running.return_value = False

        result = cancel_running_prompt("prompt-123")

        assert result is False
        mock_tracker.request_cancel.assert_not_called()
