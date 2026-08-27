"""
Tests for tfc/utils/distributed_state.py (lease renewal semantics).

Run with: pytest tfc/utils/tests/test_distributed_state.py -v
"""

from unittest.mock import patch

from tfc.utils.distributed_state import (
    DistributedEvaluationTracker,
    RunningTaskInfo,
)


def _make_tracker() -> DistributedEvaluationTracker:
    """Build a tracker without touching Redis (methods are patched per-test)."""
    tracker = DistributedEvaluationTracker.__new__(DistributedEvaluationTracker)
    tracker.key_prefix = "running_eval:"
    tracker.default_ttl = 300
    tracker._instance_id = "this-instance"
    tracker._redis_available = False
    return tracker


class TestRefreshRunning:
    """refresh_running is the lease-renewal primitive: only the owner may
    renew, and each renewal stamps metadata["renewed_at"] so other instances
    can distinguish live owners from dead ones."""

    def test_owner_renewal_stamps_renewed_at_and_ttl(self):
        tracker = _make_tracker()
        info = RunningTaskInfo(
            task_id="1",
            instance_id="this-instance",
            started_at="2024-01-01T00:00:00",
        )
        with patch.object(tracker, "get_running_info", return_value=info), patch.object(
            tracker, "set", return_value=True
        ) as mock_set:
            assert tracker.refresh_running(1, ttl=300) is True

        args, kwargs = mock_set.call_args
        assert args[0] == "1"
        assert "renewed_at" in args[1]["metadata"]
        assert kwargs["ttl"] == 300

    def test_non_owner_cannot_renew(self):
        tracker = _make_tracker()
        info = RunningTaskInfo(
            task_id="1",
            instance_id="other-instance",
            started_at="2024-01-01T00:00:00",
        )
        with patch.object(tracker, "get_running_info", return_value=info), patch.object(
            tracker, "set"
        ) as mock_set:
            assert tracker.refresh_running(1) is False
            mock_set.assert_not_called()

    def test_missing_entry_returns_false(self):
        tracker = _make_tracker()
        with patch.object(tracker, "get_running_info", return_value=None), patch.object(
            tracker, "set"
        ) as mock_set:
            assert tracker.refresh_running(1) is False
            mock_set.assert_not_called()

    def test_redis_error_returns_false(self):
        tracker = _make_tracker()
        with patch.object(
            tracker, "get_running_info", side_effect=Exception("redis down")
        ):
            assert tracker.refresh_running(1) is False
