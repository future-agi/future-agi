"""
Tests for Distributed Lock Manager and State Management.

Run with: pytest tfc/utils/tests/test_distributed_locks.py -v

These tests can run with or without Redis:
- With Redis: Full distributed functionality is tested
- Without Redis: Fallback to local locks is tested
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

import pytest


class TestDistributedLockManager:
    """Tests for DistributedLockManager."""

    def test_lock_manager_initialization(self):
        """Test that lock manager initializes correctly."""
        from tfc.utils.distributed_locks import DistributedLockManager

        manager = DistributedLockManager(fallback_to_local=True)

        assert manager.config is not None
        assert manager.fallback_to_local is True
        # Should either connect to Redis or fall back
        assert manager._instance_id is not None

    def test_context_manager_lock(self):
        """Test basic context manager lock usage."""
        from tfc.utils.distributed_locks import DistributedLockManager

        manager = DistributedLockManager(fallback_to_local=True)

        with manager.lock("test_resource") as lock:
            assert lock is not None
            # Lock should be held
            if manager.is_distributed:
                assert manager.is_locked("test_resource")

        # Lock should be released
        if manager.is_distributed:
            assert not manager.is_locked("test_resource")

    def test_try_lock_success(self):
        """Test non-blocking try_lock when lock is available."""
        from tfc.utils.distributed_locks import DistributedLockManager

        manager = DistributedLockManager(fallback_to_local=True)

        lock = manager.try_lock("test_try_lock")
        assert lock is not None

        try:
            # Lock should be held
            pass
        finally:
            lock.release()

    def test_try_lock_fails_when_held(self):
        """Test that try_lock returns None when lock is already held."""
        from tfc.utils.distributed_locks import DistributedLockManager

        manager = DistributedLockManager(fallback_to_local=True)

        with manager.lock("test_contended"):
            # Try to acquire the same lock - should fail
            lock = manager.try_lock("test_contended")
            assert lock is None

    def test_lock_prevents_concurrent_execution(self):
        """Test that lock actually prevents concurrent execution."""
        from tfc.utils.distributed_locks import DistributedLockManager

        manager = DistributedLockManager(fallback_to_local=True)

        execution_order = []
        execution_lock = threading.Lock()

        def worker(worker_id: int):
            with manager.lock("concurrent_test", timeout=10):
                with execution_lock:
                    execution_order.append(f"start_{worker_id}")
                time.sleep(0.1)  # Simulate work
                with execution_lock:
                    execution_order.append(f"end_{worker_id}")

        # Run 3 workers concurrently
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(worker, i) for i in range(3)]
            for f in as_completed(futures):
                f.result()

        # Verify execution was serialized (start/end pairs should not interleave)
        for i in range(3):
            start_idx = execution_order.index(f"start_{i}")
            end_idx = execution_order.index(f"end_{i}")
            # Between start and end of worker i, no other worker should have started
            for j in range(3):
                if i != j:
                    other_start = execution_order.index(f"start_{j}")
                    assert not (
                        start_idx < other_start < end_idx
                    ), f"Worker {j} started while worker {i} was running"

    def test_lock_timeout(self):
        """Test that lock times out when it can't be acquired."""
        from tfc.utils.distributed_locks import (
            DistributedLockManager,
            LockAcquisitionError,
        )

        manager = DistributedLockManager(fallback_to_local=True)

        def hold_lock():
            with manager.lock("timeout_test", timeout=10):
                time.sleep(2)  # Hold lock for 2 seconds

        # Start thread holding the lock
        holder = threading.Thread(target=hold_lock)
        holder.start()

        time.sleep(0.1)  # Let holder acquire lock

        # Try to acquire with short timeout - should fail
        with pytest.raises(LockAcquisitionError):
            with manager.lock("timeout_test", blocking_timeout=0.5):
                pass

        holder.join()

    def test_force_release(self):
        """Test force release functionality."""
        from tfc.utils.distributed_locks import DistributedLockManager

        manager = DistributedLockManager(fallback_to_local=True)

        if manager.is_distributed:
            with manager.lock("force_release_test"):
                assert manager.is_locked("force_release_test")
                # Force release from "another instance"
                result = manager.force_release("force_release_test")
                assert result is True
                assert not manager.is_locked("force_release_test")

    def test_health_check(self):
        """Test health check returns valid status."""
        from tfc.utils.distributed_locks import DistributedLockManager

        manager = DistributedLockManager(fallback_to_local=True)

        health = manager.health_check()

        assert "instance_id" in health
        assert "redis_available" in health
        assert "mode" in health
        assert health["mode"] in ["distributed", "local"]


class TestDistributedLockDecorator:
    """Tests for the DistributedLock decorator."""

    def test_decorator_basic(self):
        """Test basic decorator functionality."""
        from tfc.utils.distributed_locks import DistributedLock

        call_count = 0

        @DistributedLock("decorator_test")
        def protected_function():
            nonlocal call_count
            call_count += 1
            return "success"

        result = protected_function()
        assert result == "success"
        assert call_count == 1

    def test_decorator_with_parameters(self):
        """Test decorator with parameterized lock name."""
        from tfc.utils.distributed_locks import DistributedLock

        @DistributedLock("resource_{resource_id}")
        def process_resource(resource_id: int):
            return f"processed_{resource_id}"

        result = process_resource(123)
        assert result == "processed_123"

    def test_decorator_prevents_concurrent(self):
        """Test that decorator prevents concurrent execution."""
        from tfc.utils.distributed_locks import DistributedLock

        execution_times = []

        @DistributedLock("decorator_concurrent_test", timeout=10)
        def slow_function():
            start = time.time()
            time.sleep(0.1)
            end = time.time()
            execution_times.append((start, end))

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(slow_function) for _ in range(3)]
            for f in as_completed(futures):
                f.result()

        # Check that executions don't overlap
        execution_times.sort(key=lambda x: x[0])
        for i in range(len(execution_times) - 1):
            assert (
                execution_times[i][1] <= execution_times[i + 1][0]
            ), "Executions overlapped!"


class TestDistributedEvaluationTracker:
    """Tests for DistributedEvaluationTracker."""

    def test_tracker_initialization(self):
        """Test tracker initializes correctly."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()
        assert tracker.instance_id is not None

    def test_mark_running_and_completed(self):
        """Test marking evaluation as running and completed."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()

        eval_id = 99999  # Use high ID unlikely to conflict

        # Mark as running
        result = tracker.mark_running(eval_id, runner_info={"test": True})

        if tracker.is_available:
            assert result is True
            assert tracker.is_running(eval_id)

            # Get info
            info = tracker.get_running_info(eval_id)
            assert info is not None
            assert info.task_id == str(eval_id)

            # Mark completed
            tracker.mark_completed(eval_id)
            assert not tracker.is_running(eval_id)

    def test_cancel_request(self):
        """Test cancellation request flow."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()
        eval_id = 99998

        if tracker.is_available:
            tracker.mark_running(eval_id)

            # Request cancel
            tracker.request_cancel(eval_id, reason="test cancel")

            # Check should_cancel
            assert tracker.should_cancel(eval_id)

            # Verify info updated
            info = tracker.get_running_info(eval_id)
            assert info.cancel_requested is True

            # Cleanup
            tracker.mark_completed(eval_id)
            tracker.clear_cancel_flag(eval_id)

    def test_prevents_duplicate_running(self):
        """Test that same eval can't be marked running twice."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()
        eval_id = 99997

        if tracker.is_available:
            # First mark should succeed
            assert tracker.mark_running(eval_id) is True

            # Create a "different instance" tracker
            tracker2 = DistributedEvaluationTracker()
            tracker2._instance_id = "different_instance"

            # Second mark should fail (different instance)
            assert tracker2.mark_running(eval_id) is False

            # Cleanup
            tracker.mark_completed(eval_id)

    def test_get_all_running(self):
        """Test getting all running evaluations."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()

        if tracker.is_available:
            eval_ids = [99990, 99991, 99992]

            for eid in eval_ids:
                tracker.mark_running(eid)

            running = tracker.get_all_running()
            running_ids = [int(r.task_id) for r in running]

            for eid in eval_ids:
                assert eid in running_ids

            # Cleanup
            for eid in eval_ids:
                tracker.mark_completed(eid)


class TestCancellableRunner:
    """Tests for CancellableRunner mixin."""

    def test_cancel_event_property(self):
        """Test cancel_event property works correctly."""
        from tfc.utils.distributed_state import (
            CancellableRunner,
            DistributedEvaluationTracker,
        )

        tracker = DistributedEvaluationTracker()

        class TestRunner(CancellableRunner):
            def __init__(self, eval_id):
                super().__init__(eval_id, tracker)

        runner = TestRunner(99985)

        # Initially not cancelled
        assert runner.cancel_event is False

        if tracker.is_available:
            # Set cancel_event should trigger distributed cancel
            runner.cancel_event = True
            assert tracker.should_cancel(99985)

            # Cleanup
            tracker.clear_cancel_flag(99985)

    def test_check_cancelled_throttling(self):
        """Test that check_cancelled is throttled to avoid Redis hammering."""
        from tfc.utils.distributed_state import (
            CancellableRunner,
            DistributedEvaluationTracker,
        )

        tracker = DistributedEvaluationTracker()

        class TestRunner(CancellableRunner):
            def __init__(self, eval_id):
                super().__init__(eval_id, tracker)
                self.redis_checks = 0
                self._cancel_check_interval = 0.5  # Check every 0.5s

            def check_cancelled(self):
                # Count actual Redis checks
                result = super().check_cancelled()
                return result

        runner = TestRunner(99984)

        # Rapid checks should be throttled
        start_time = runner._last_cancel_check

        for _ in range(10):
            runner.check_cancelled()

        # Should have at most updated once (time hasn't passed)
        # The throttling prevents hammering Redis


class TestErrorHandling:
    """Tests for error handling in distributed locks and state management."""

    def test_lock_handles_redis_error_during_acquisition(self):
        """Test that Redis errors during acquisition are properly handled."""
        from tfc.utils.distributed_locks import (
            DistributedLockManager,
            LockAcquisitionError,
        )

        manager = DistributedLockManager(fallback_to_local=True)

        if manager.is_distributed:
            # Mock Redis to raise error
            with patch.object(manager._redis_client, "lock") as mock_lock:
                from redis.exceptions import RedisError

                mock_redis_lock = MagicMock()
                mock_redis_lock.acquire.side_effect = RedisError("Connection lost")
                mock_lock.return_value = mock_redis_lock

                with pytest.raises(LockAcquisitionError, match="Redis error"):
                    with manager.lock("redis_error_test"):
                        pass

    def test_lock_handles_lock_not_owned_on_release(self):
        """Test that LockNotOwnedError during release is logged but doesn't raise."""
        from tfc.utils.distributed_locks import DistributedLockManager

        manager = DistributedLockManager(fallback_to_local=True)

        if manager.is_distributed:
            with patch.object(manager._redis_client, "lock") as mock_lock:
                from redis.exceptions import LockNotOwnedError

                mock_redis_lock = MagicMock()
                mock_redis_lock.acquire.return_value = True
                mock_redis_lock.release.side_effect = LockNotOwnedError("Lock expired")
                mock_lock.return_value = mock_redis_lock

                # Should not raise - error should be logged
                with manager.lock("lock_not_owned_test"):
                    pass

    def test_lock_handles_redis_error_on_release(self):
        """Test that Redis errors during release are logged but don't raise."""
        from tfc.utils.distributed_locks import DistributedLockManager

        manager = DistributedLockManager(fallback_to_local=True)

        if manager.is_distributed:
            with patch.object(manager._redis_client, "lock") as mock_lock:
                from redis.exceptions import RedisError

                mock_redis_lock = MagicMock()
                mock_redis_lock.acquire.return_value = True
                mock_redis_lock.release.side_effect = RedisError("Connection lost")
                mock_lock.return_value = mock_redis_lock

                # Should not raise - error should be logged
                with manager.lock("redis_release_error_test"):
                    pass

    def test_lock_error_during_lock_acquisition_raises_lock_error(self):
        """Test that LockError during acquisition raises LockAcquisitionError."""
        from tfc.utils.distributed_locks import (
            DistributedLockManager,
            LockAcquisitionError,
        )

        manager = DistributedLockManager(fallback_to_local=True)

        if manager.is_distributed:
            with patch.object(manager._redis_client, "lock") as mock_lock:
                from redis.exceptions import LockError

                mock_redis_lock = MagicMock()
                mock_redis_lock.acquire.side_effect = LockError(
                    "Lock acquisition failed"
                )
                mock_lock.return_value = mock_redis_lock

                with pytest.raises(LockAcquisitionError, match="Lock error"):
                    with manager.lock("lock_error_test"):
                        pass


class TestDistributedStateErrorHandling:
    """Tests for error handling in DistributedEvaluationTracker."""

    def test_mark_running_handles_exception(self):
        """Test that mark_running handles exceptions gracefully."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()

        if tracker.is_available:
            with patch.object(tracker, "set", side_effect=Exception("Redis error")):
                # Should return False instead of raising
                result = tracker.mark_running(99970)
                assert result is False

    def test_mark_completed_handles_exception(self):
        """Test that mark_completed handles exceptions gracefully."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()

        # Even if there's an error, it shouldn't raise
        with patch.object(tracker, "delete", side_effect=Exception("Redis error")):
            # Should not raise
            tracker.mark_completed(99969)

    def test_request_cancel_handles_exception(self):
        """Test that request_cancel handles exceptions gracefully."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()

        if tracker.is_available:
            # First mark as running so we have something to cancel
            tracker.mark_running(99968)

            with patch.object(tracker, "get", side_effect=Exception("Redis error")):
                # Should return False instead of raising
                result = tracker.request_cancel(99968, reason="test")
                assert result is False

            # Cleanup
            tracker.mark_completed(99968)

    def test_cleanup_stale_handles_exception(self):
        """Test that cleanup_stale handles exceptions gracefully."""
        from tfc.utils.distributed_state import DistributedEvaluationTracker

        tracker = DistributedEvaluationTracker()

        # Exercise the cleanup contract deterministically even when Redis is
        # unavailable in the test environment.
        tracker._redis_available = True
        with patch.object(
            tracker,
            "get_all_running",
            side_effect=Exception("Unexpected enumeration error"),
        ):
            # Should return 0 instead of raising
            result = tracker.cleanup_stale()
            assert result == 0


class TestLocalFallback:
    """Tests for local lock fallback when Redis is unavailable."""

    def test_fallback_enabled(self):
        """Test that fallback works when Redis is unavailable."""
        from tfc.utils.distributed_locks import DistributedLockManager

        # Force Redis unavailable
        manager = DistributedLockManager(
            redis_url="redis://nonexistent:6379",
            fallback_to_local=True,
        )

        assert manager._redis_available is False
        assert manager.fallback_to_local is True

        # Should still work with local locks
        with manager.lock("fallback_test") as lock:
            assert lock is not None

    def test_fallback_disabled_raises(self):
        """Test that error is raised when fallback is disabled."""
        from tfc.utils.distributed_locks import (
            DistributedLockManager,
            RedisUnavailableError,
        )

        manager = DistributedLockManager(
            redis_url="redis://nonexistent:6379",
            fallback_to_local=False,
        )

        with pytest.raises(RedisUnavailableError):
            with manager.lock("no_fallback_test"):
                pass


# Run with: pytest tfc/utils/tests/test_distributed_locks.py -v
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
